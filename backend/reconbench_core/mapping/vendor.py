"""Deterministic, order-independent vendor identity resolution."""

from __future__ import annotations

from decimal import Decimal
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
    display_name: str = Field(min_length=1)
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
