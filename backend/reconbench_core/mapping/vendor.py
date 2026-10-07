"""Deterministic, order-independent vendor identity resolution."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator
from rapidfuzz import fuzz

from reconbench_core.normalize import normalize_vendor_name, validate_gstin
from reconbench_core.schema import DecimalValue, FrozenModel, VendorRule


class VendorResolutionConfig(FrozenModel):
    """Vendor thresholds sourced from the active engine configuration."""

    fuzzy_suggestion: DecimalValue = Decimal("0.80")
    fuzzy_auto_merge: DecimalValue = Decimal("0.92")

    @model_validator(mode="after")
    def _threshold_order(self) -> Self:
        if not Decimal("0") <= self.fuzzy_suggestion < self.fuzzy_auto_merge <= Decimal("1"):
            raise ValueError("vendor fuzzy thresholds must satisfy 0 <= suggestion < auto <= 1")
        return self


class VendorRecord(FrozenModel):
    """One known vendor and its GSTIN/PAN anchors and accepted display aliases."""

    vendor_id: UUID
    # Empty while the vendor's name is pending (AC-4); never matches by name then.
    display_name: str = ""
    gstins: tuple[str, ...] = ()
    pan: str | None = None
    aliases: tuple[str, ...] = ()


class VendorAliasRecord(FrozenModel):
    """A learned or manually accepted vendor-name alias."""

    vendor_id: UUID
    alias: str = Field(min_length=1)


class VendorDocument(FrozenModel):
    """Identity facts extracted from one parsed PO, GRN, or invoice."""

    document_id: UUID
    natural_key: str = ""
    vendor_name: str | None = None
    vendor_gstin: str | None = None


class VendorMergeSuggestion(FrozenModel):
    """A candidate identity that requires a human decision before merging."""

    document_id: UUID
    vendor_id: UUID
    score: DecimalValue
    pan_conflict: bool = False
    merge_allowed: bool = True


class VendorResolution(FrozenModel):
    """Chosen vendor or explicit unassigned result plus review candidates."""

    document_id: UUID
    vendor_id: UUID | None = None
    rule: VendorRule = VendorRule.NONE
    suggestions: tuple[VendorMergeSuggestion, ...] = ()


class VendorResolutionBatch(FrozenModel):
    """Stable results in sorted document order, independent of input order."""

    resolutions: tuple[VendorResolution, ...]


def _valid_gstin(value: str | None) -> str | None:
    if value is None:
        return None
    result = validate_gstin(value)
    return result.normalized if result.is_valid else None


def _gstin_pan(value: str | None) -> str | None:
    if value is None:
        return None
    result = validate_gstin(value)
    return result.pan if result.format_valid else None


def _pans(vendor: VendorRecord) -> set[str]:
    pans = {vendor.pan.upper()} if vendor.pan else set()
    pans.update(pan for value in vendor.gstins if (pan := _gstin_pan(value)))
    return pans


def _pan_conflict(incoming_pan: str | None, vendor: VendorRecord) -> bool:
    known_pans = _pans(vendor)
    return incoming_pan is not None and bool(known_pans) and incoming_pan not in known_pans


def _name_score(left: str, right: str) -> Decimal:
    if not left or not right:
        return Decimal("0")
    score = fuzz.token_set_ratio(left, right)
    return (Decimal(str(score)) / Decimal("100")).quantize(Decimal("0.0001"))


def _suggestions(
    document_id: UUID,
    candidates: list[tuple[VendorRecord, Decimal]],
    incoming_pan: str | None,
) -> tuple[VendorMergeSuggestion, ...]:
    ordered = sorted(candidates, key=lambda item: (-item[1], str(item[0].vendor_id)))
    return tuple(
        VendorMergeSuggestion(
            document_id=document_id,
            vendor_id=vendor.vendor_id,
            score=score,
            pan_conflict=_pan_conflict(incoming_pan, vendor),
            merge_allowed=not _pan_conflict(incoming_pan, vendor),
        )
        for vendor, score in ordered
    )


def resolve_vendors(
    documents: tuple[VendorDocument, ...] | list[VendorDocument],
    vendors: tuple[VendorRecord, ...] | list[VendorRecord],
    aliases: tuple[VendorAliasRecord, ...] | list[VendorAliasRecord] = (),
    config: VendorResolutionConfig | None = None,
) -> VendorResolutionBatch:
    """Resolve a stable vendor snapshot using GSTIN, PAN, name, alias, then fuzzy.

    Fuzzy resolution never mutates the supplied vendor snapshot. Ties are returned
    as suggestions and different PANs are never auto-merged.
    """

    selected_config = config or VendorResolutionConfig()
    sorted_vendors = tuple(sorted(vendors, key=lambda item: str(item.vendor_id)))
    vendor_by_id = {vendor.vendor_id: vendor for vendor in sorted_vendors}
    alias_targets: dict[str, set[UUID]] = {}
    for alias in aliases:
        if alias.vendor_id in vendor_by_id:
            normalized_alias = normalize_vendor_name(alias.alias)
            alias_targets.setdefault(normalized_alias, set()).add(alias.vendor_id)

    output: list[VendorResolution] = []
    sorted_documents = sorted(
        documents,
        key=lambda item: (item.natural_key or str(item.document_id), str(item.document_id)),
    )
    for document in sorted_documents:
        normalized_name = normalize_vendor_name(document.vendor_name or "")
        gstin = _valid_gstin(document.vendor_gstin)
        pan = _gstin_pan(document.vendor_gstin)

        gstin_matches = [
            vendor
            for vendor in sorted_vendors
            if gstin is not None and gstin in {_valid_gstin(value) for value in vendor.gstins}
        ]
        pan_matches = [vendor for vendor in sorted_vendors if pan and pan in _pans(vendor)]
        exact_name_matches = [
            vendor
            for vendor in sorted_vendors
            if normalized_name and normalized_name == normalize_vendor_name(vendor.display_name)
        ]
        alias_matches = [
            vendor_by_id[vendor_id]
            for vendor_id in sorted(alias_targets.get(normalized_name, set()), key=str)
        ]

        chosen: VendorRecord | None = None
        fired_rule = VendorRule.NONE
        matched_candidates: list[VendorRecord] = []
        for candidates, rule in (
            (gstin_matches, VendorRule.GSTIN),
            (pan_matches, VendorRule.PAN),
            (exact_name_matches, VendorRule.NAME_EXACT),
            (alias_matches, VendorRule.ALIAS),
        ):
            if not candidates:
                continue
            matched_candidates = candidates
            safe_candidates = [
                candidate for candidate in candidates if not _pan_conflict(pan, candidate)
            ]
            if len(safe_candidates) == 1:
                chosen = safe_candidates[0]
                fired_rule = rule
            break

        if chosen is not None:
            output.append(
                VendorResolution(
                    document_id=document.document_id,
                    vendor_id=chosen.vendor_id,
                    rule=fired_rule,
                )
            )
            continue
        if matched_candidates:
            output.append(
                VendorResolution(
                    document_id=document.document_id,
                    suggestions=_suggestions(
                        document.document_id,
                        [(vendor, Decimal("1")) for vendor in matched_candidates],
                        pan,
                    ),
                )
            )
            continue

        anchored_vendors = [vendor for vendor in sorted_vendors if _pans(vendor)]
        scored: list[tuple[VendorRecord, Decimal]] = []
        if normalized_name:
            for vendor in anchored_vendors:
                names = (vendor.display_name, *vendor.aliases)
                score = max(
                    (_name_score(normalized_name, normalize_vendor_name(name)) for name in names),
                    default=Decimal("0"),
                )
                if score >= selected_config.fuzzy_suggestion:
                    scored.append((vendor, score))
        scored.sort(key=lambda item: (-item[1], str(item[0].vendor_id)))
        if scored:
            top_score = scored[0][1]
            top = [item for item in scored if item[1] == top_score]
            if (
                top_score >= selected_config.fuzzy_auto_merge
                and len(top) == 1
                and not _pan_conflict(pan, top[0][0])
            ):
                output.append(
                    VendorResolution(
                        document_id=document.document_id,
                        vendor_id=top[0][0].vendor_id,
                        rule=VendorRule.FUZZY,
                    )
                )
                continue
            output.append(
                VendorResolution(
                    document_id=document.document_id,
                    suggestions=_suggestions(document.document_id, scored, pan),
                )
            )
            continue
        output.append(VendorResolution(document_id=document.document_id))

    return VendorResolutionBatch(resolutions=tuple(output))


# --------------------------------------------------------------------------- anchors (AC-4)


class VendorNameStatus(StrEnum):
    """How far a vendor's display name can be trusted, best first."""

    MANUAL = "manual"
    VERIFIED = "verified"
    PROVISIONAL = "provisional"
    PENDING = "pending"


_NAME_RANK = {
    VendorNameStatus.MANUAL: 3,
    VendorNameStatus.VERIFIED: 2,
    VendorNameStatus.PROVISIONAL: 1,
    VendorNameStatus.PENDING: 0,
}


class AnchorDocument(FrozenModel):
    """Supplier identity read from one document, already cleaned by the caller.

    ``vendor_name`` must already be free of labels, GSTINs, line text and the
    buyer's own names. ``gstin_verified``/``name_verified`` mean the value came
    from a digital or signed source or a person, or passed its checks (L0/L1).
    """

    document_id: UUID
    natural_key: str = ""
    vendor_gstin: str | None = None
    gstin_verified: bool = False
    vendor_name: str | None = None
    name_verified: bool = False


class AnchorVendor(FrozenModel):
    """An existing (unmerged) vendor as the anchor planner sees it."""

    vendor_id: UUID
    name: str = ""
    name_status: VendorNameStatus = VendorNameStatus.PROVISIONAL
    pan: str | None = None
    gstins: tuple[str, ...] = ()
    alias_norms: tuple[str, ...] = ()


class PlannedVendor(FrozenModel):
    """A vendor to create; ``key`` (the PAN) is how the other actions refer to it."""

    key: str
    name: str
    name_status: VendorNameStatus
    pan: str


class PlannedRegistration(FrozenModel):
    vendor_id: UUID | None = None
    new_vendor_key: str | None = None
    gstin: str
    state_code: str
    pan: str


class PlannedRename(FrozenModel):
    vendor_id: UUID
    name: str
    name_status: VendorNameStatus


class PlannedAlias(FrozenModel):
    vendor_id: UUID | None = None
    new_vendor_key: str | None = None
    alias: str


class AnchorPlan(FrozenModel):
    """Deterministic vendor-master changes for one workspace snapshot."""

    new_vendors: tuple[PlannedVendor, ...] = ()
    registrations: tuple[PlannedRegistration, ...] = ()
    renames: tuple[PlannedRename, ...] = ()
    aliases: tuple[PlannedAlias, ...] = ()
    skipped: tuple[tuple[UUID, str], ...] = ()


def _best_name(
    candidates: list[tuple[str, VendorNameStatus]],
) -> tuple[str, VendorNameStatus]:
    """Highest status, then the most common spelling, then the smallest text (order-free)."""

    if not candidates:
        return "", VendorNameStatus.PENDING
    top_rank = max(_NAME_RANK[status] for _, status in candidates)
    top = [(name, status) for name, status in candidates if _NAME_RANK[status] == top_rank]
    counts = Counter(normalize_vendor_name(name) for name, _ in top)
    best_norm = min(counts, key=lambda norm: (-counts[norm], norm))
    spelling = min(name for name, _ in top if normalize_vendor_name(name) == best_norm)
    return spelling, top[0][1]


def _anchor_pans(vendor: AnchorVendor) -> set[str]:
    pans = {vendor.pan.upper()} if vendor.pan else set()
    pans.update(pan for value in vendor.gstins if (pan := _gstin_pan(value)))
    return pans


def plan_vendor_anchors(
    documents: tuple[AnchorDocument, ...] | list[AnchorDocument],
    vendors: tuple[AnchorVendor, ...] | list[AnchorVendor],
    buyer_gstins: frozenset[str] = frozenset(),
) -> AnchorPlan:
    """Plan vendor creation, GSTIN registrations, name upgrades and learned aliases.

    Accuracy plan AC-4: a vendor is created only from a verified, checksum-valid
    supplier GSTIN that is not a buyer entity; a document without one stays
    vendor-pending (resolution may still match or suggest an existing vendor).
    The name is never a GSTIN: without a usable name the vendor is ``pending``,
    and a better name later (verified over provisional over pending) replaces it.
    A person's name (``manual``) is never replaced. Aliases are learned only from
    verified names, so OCR misspellings never become aliases. GSTINs sharing a PAN
    belong to one vendor; when several vendors already hold that PAN nothing is
    created and a person decides. The result does not depend on input order.
    """

    skipped: list[tuple[UUID, str]] = []
    by_pan: dict[str, dict[str, list[AnchorDocument]]] = {}
    state_of: dict[str, str] = {}
    for document in documents:
        if document.vendor_gstin is None:
            continue
        facts = validate_gstin(document.vendor_gstin)
        if not facts.is_valid or facts.state_code is None or facts.pan is None:
            skipped.append((document.document_id, "gstin_invalid"))
            continue
        if facts.normalized in buyer_gstins:
            skipped.append((document.document_id, "buyer_gstin"))
            continue
        if not document.gstin_verified:
            skipped.append((document.document_id, "gstin_unverified"))
            continue
        by_pan.setdefault(facts.pan, {}).setdefault(facts.normalized, []).append(document)
        state_of[facts.normalized] = facts.state_code

    sorted_vendors = sorted(vendors, key=lambda item: str(item.vendor_id))
    vendor_by_gstin: dict[str, AnchorVendor] = {}
    for vendor in sorted_vendors:
        for gstin in vendor.gstins:
            normalized = _valid_gstin(gstin)
            if normalized is not None:
                vendor_by_gstin.setdefault(normalized, vendor)

    new_vendors: list[PlannedVendor] = []
    registrations: list[PlannedRegistration] = []
    renames: list[PlannedRename] = []
    aliases: list[PlannedAlias] = []
    used_aliases = {norm for vendor in sorted_vendors for norm in vendor.alias_norms}

    def add_aliases(
        vendor_id: UUID | None,
        new_vendor_key: str | None,
        canonical: str,
        group: list[AnchorDocument],
    ) -> None:
        canonical_norm = normalize_vendor_name(canonical)
        verified_names = sorted(
            {
                document.vendor_name.strip()
                for document in group
                if document.name_verified and document.vendor_name and document.vendor_name.strip()
            }
        )
        for name in verified_names:
            norm = normalize_vendor_name(name)
            if not norm or norm == canonical_norm or norm in used_aliases:
                continue
            used_aliases.add(norm)
            aliases.append(
                PlannedAlias(vendor_id=vendor_id, new_vendor_key=new_vendor_key, alias=name)
            )

    for pan in sorted(by_pan):
        groups = by_pan[pan]
        group_docs = [document for gstin in sorted(groups) for document in groups[gstin]]
        best_name, best_status = _best_name(
            [
                (
                    document.vendor_name.strip(),
                    VendorNameStatus.VERIFIED
                    if document.name_verified
                    else VendorNameStatus.PROVISIONAL,
                )
                for document in group_docs
                if document.vendor_name and document.vendor_name.strip()
            ]
        )
        known = {vendor_by_gstin[gstin].vendor_id for gstin in groups if gstin in vendor_by_gstin}
        pan_vendors = [vendor for vendor in sorted_vendors if pan in _anchor_pans(vendor)]
        target: AnchorVendor | None = None
        if len(known) == 1:
            target = next(vendor for vendor in sorted_vendors if vendor.vendor_id in known)
        elif not known and len(pan_vendors) == 1:
            target = pan_vendors[0]
        elif known or pan_vendors:
            for gstin in sorted(groups):
                if gstin not in vendor_by_gstin:
                    skipped.extend(
                        (document.document_id, "pan_ambiguous") for document in groups[gstin]
                    )
            continue

        if target is None:
            new_vendors.append(
                PlannedVendor(key=pan, name=best_name, name_status=best_status, pan=pan)
            )
            registrations.extend(
                PlannedRegistration(
                    new_vendor_key=pan, gstin=gstin, state_code=state_of[gstin], pan=pan
                )
                for gstin in sorted(groups)
            )
            add_aliases(None, pan, best_name, group_docs)
            continue

        registrations.extend(
            PlannedRegistration(
                vendor_id=target.vendor_id, gstin=gstin, state_code=state_of[gstin], pan=pan
            )
            for gstin in sorted(groups)
            if gstin not in vendor_by_gstin
        )
        canonical = target.name
        if best_name and _NAME_RANK[best_status] > _NAME_RANK[target.name_status]:
            renames.append(
                PlannedRename(vendor_id=target.vendor_id, name=best_name, name_status=best_status)
            )
            canonical = best_name
        add_aliases(target.vendor_id, None, canonical, group_docs)

    return AnchorPlan(
        new_vendors=tuple(new_vendors),
        registrations=tuple(registrations),
        renames=tuple(renames),
        aliases=tuple(aliases),
        skipped=tuple(sorted(skipped, key=lambda item: (str(item[0]), item[1]))),
    )
