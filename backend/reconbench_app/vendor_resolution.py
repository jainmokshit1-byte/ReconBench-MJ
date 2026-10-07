"""Persist deterministic vendor resolution over the pure mapping core."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from reconbench_core.extraction.extract import VERIFIED_CONFIDENCE
from reconbench_core.mapping.vendor import (
    AnchorDocument,
    AnchorVendor,
    VendorAliasRecord,
    VendorDocument,
    VendorNameStatus,
    VendorRecord,
    VendorResolutionConfig,
    plan_vendor_anchors,
    resolve_vendors,
)
from reconbench_core.normalize import normalize_vendor_name
from reconbench_core.parsers.parties import (
    buyer_gstins,
    is_buyer_name,
    name_problem,
    party_hints_from_config,
)
from reconbench_core.parsers.types import BuyerEntityHint
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from reconbench_app.ai.enqueue import AiHookEnqueuer
from reconbench_app.authorization import requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata

_LOGGER = logging.getLogger(__name__)


# Digital sources whose header values count as verified (L0) for vendor identity.
STRUCTURED_METHODS = frozenset({"xml_profile", "csv_mapping", "manual"})


def field_verified(confidence: object, source: object, *, structured: bool) -> bool:
    """L0/L1 for vendor identity: a digital source, a person, or a checked rule value.

    AI-read values stay suggestions (D11/D21) until AC-6 cross-checks them.
    """

    if structured or source == "manual":
        return True
    if source not in (None, "rule") or confidence is None:
        return False
    return Decimal(str(confidence)) >= VERIFIED_CONFIDENCE


def anchor_document(
    *,
    document_id: UUID,
    natural_key: str,
    vendor_gstin: str | None,
    vendor_name: str | None,
    gstin_verified: bool,
    name_verified: bool,
    buyers: Sequence[BuyerEntityHint],
) -> AnchorDocument:
    """Supplier identity of one document for :func:`plan_vendor_anchors`.

    A label, GSTIN, line text or the buyer's own name is never a vendor name.
    """

    name = vendor_name.strip() if isinstance(vendor_name, str) else None
    if name and (name_problem(name) is not None or is_buyer_name(name, buyers)):
        name = None
    return AnchorDocument(
        document_id=document_id,
        natural_key=natural_key,
        vendor_gstin=vendor_gstin,
        gstin_verified=gstin_verified,
        vendor_name=name or None,
        name_verified=name_verified and bool(name),
    )


def vendor_config_from_body(body: object) -> VendorResolutionConfig:
    """Build vendor fuzzy thresholds from an active config body (defaults when absent)."""

    if not isinstance(body, Mapping):
        return VendorResolutionConfig()
    engine_body = body.get("engine")
    thresholds = engine_body.get("thresholds") if isinstance(engine_body, Mapping) else None
    if not isinstance(thresholds, Mapping):
        return VendorResolutionConfig()
    return VendorResolutionConfig(
        fuzzy_suggestion=Decimal(str(thresholds.get("vendor_fuzzy_suggest", "0.80"))),
        fuzzy_auto_merge=Decimal(str(thresholds.get("vendor_fuzzy_merge", "0.92"))),
    )


@dataclass(frozen=True, slots=True)
class VendorResolutionSummary:
    documents_seen: int
    documents_assigned: int
    documents_unassigned: int
    vendors_created: int
    registrations_added: int
    aliases_learned: int
    merge_suggestions_added: int


class VendorResolutionService:
    """Create GSTIN anchors, resolve documents and persist review candidates.

    The pure resolver decides matches. This adapter only reads/writes rows and
    records dirty scopes in the same transaction as a mapping change.
    """

    def __init__(self, engine: Engine, ai_hooks: AiHookEnqueuer | None = None) -> None:
        self.engine = engine
        self.ai_hooks = ai_hooks

    @requires("links.edit")
    def resolve_workspace(self, workspace_id: UUID) -> VendorResolutionSummary:
        document = metadata.tables["document"]
        version = metadata.tables["document_version"]
        with self.engine.begin() as connection:
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
                {"lock_name": f"reconbench.vendor-resolution:{workspace_id}"},
            )
            rows = (
                connection.execute(
                    sa.select(
                        document.c.id.label("document_id"),
                        document.c.doc_type,
                        document.c.natural_key,
                        document.c.vendor_id,
                        document.c.vendor_rule,
                        document.c.vendor_score,
                        document.c.vendor_locked,
                        document.c.vendor_resolved_at,
                        document.c.input_seq,
                        document.c.version.label("document_version"),
                        version.c.id.label("version_id"),
                        version.c.extraction_method,
                        version.c.vendor_name,
                        version.c.vendor_gstin,
                    )
                    .select_from(
                        document.join(
                            version,
                            sa.and_(
                                document.c.workspace_id == version.c.workspace_id,
                                document.c.current_version_id == version.c.id,
                            ),
                        )
                    )
                    .where(
                        document.c.workspace_id == workspace_id,
                        document.c.withdrawn_at.is_(None),
                    )
                    .order_by(document.c.natural_key, document.c.id)
                )
                .mappings()
                .all()
            )
            if not rows:
                return VendorResolutionSummary(0, 0, 0, 0, 0, 0, 0)

            vendors_created, registrations_added, aliases_learned = self._ensure_gstin_anchors(
                connection, workspace_id, rows
            )
            vendor_records, alias_records = self._vendor_snapshot(connection, workspace_id)
            documents = [
                VendorDocument(
                    document_id=cast(UUID, row["document_id"]),
                    natural_key=str(row["natural_key"]),
                    vendor_name=cast(str | None, row["vendor_name"]),
                    vendor_gstin=cast(str | None, row["vendor_gstin"]),
                )
                for row in rows
                if not bool(row["vendor_locked"])
            ]
            config = self._config(connection, workspace_id)
            resolved = resolve_vendors(documents, vendor_records, alias_records, config)
            by_document = {item.document_id: item for item in resolved.resolutions}
            assigned = unassigned = 0
            suggestions_added = 0
            dirty_vendors: set[UUID] = set()
            for row in rows:
                document_id = cast(UUID, row["document_id"])
                if bool(row["vendor_locked"]):
                    assigned += int(row["vendor_id"] is not None)
                    unassigned += int(row["vendor_id"] is None)
                    continue
                result = by_document[document_id]
                selected_vendor_id = result.vendor_id
                selected_rule = result.rule.value
                top_score = result.suggestions[0].score if result.suggestions else None
                current_vendor_id = cast(UUID | None, row["vendor_id"])
                score_value = top_score if selected_vendor_id is None else None
                metadata_changed = (
                    current_vendor_id != selected_vendor_id
                    or row["vendor_rule"] != selected_rule
                    or row["vendor_score"] != score_value
                    or row["vendor_resolved_at"] is None
                )
                if metadata_changed:
                    mapping_changed = current_vendor_id != selected_vendor_id
                    values: dict[str, Any] = {
                        "vendor_id": selected_vendor_id,
                        "vendor_rule": selected_rule,
                        "vendor_score": score_value,
                        "vendor_resolved_at": sa.func.now(),
                        "updated_at": sa.func.now(),
                        "version": document.c.version + 1,
                    }
                    if mapping_changed:
                        values["input_seq"] = document.c.input_seq + 1
                    connection.execute(
                        sa.update(document)
                        .where(
                            document.c.workspace_id == workspace_id,
                            document.c.id == document_id,
                            document.c.vendor_locked.is_(False),
                        )
                        .values(**values)
                    )
                    if mapping_changed:
                        self._mark_document_dirty(
                            connection,
                            workspace_id=workspace_id,
                            document_id=document_id,
                            doc_type=str(row["doc_type"]),
                            old_vendor_id=current_vendor_id,
                            new_vendor_id=selected_vendor_id,
                            dirty_vendors=dirty_vendors,
                        )
                if selected_vendor_id is None:
                    unassigned += 1
                else:
                    assigned += 1
                suggestions_added += self._record_document_candidates(
                    connection,
                    workspace_id=workspace_id,
                    document_id=document_id,
                    vendor_id=selected_vendor_id,
                    suggestions=result.suggestions,
                )

            suggestions_added += self._record_vendor_pair_candidates(
                connection,
                workspace_id=workspace_id,
                vendors=vendor_records,
                aliases=alias_records,
                config=config,
            )
            self._queue_vendor_opinions(connection, workspace_id)
            return VendorResolutionSummary(
                documents_seen=len(rows),
                documents_assigned=assigned,
                documents_unassigned=unassigned,
                vendors_created=vendors_created,
                registrations_added=registrations_added,
                aliases_learned=aliases_learned,
                merge_suggestions_added=suggestions_added,
            )

    def _ensure_gstin_anchors(
        self,
        connection: Connection,
        workspace_id: UUID,
        documents: Sequence[RowMapping],
    ) -> tuple[int, int, int]:
        """Apply the pure AC-4 anchor plan: verified GSTINs only, never a GSTIN as name."""

        vendor = metadata.tables["vendor"]
        registration = metadata.tables["vendor_gstin"]
        alias = metadata.tables["vendor_alias"]
        buyers = party_hints_from_config(self._engine_body(connection, workspace_id))[
            "buyer_entities"
        ]
        open_rows = [row for row in documents if not bool(row["vendor_locked"])]
        trust = self._identity_trust(
            connection, workspace_id, [cast(UUID, row["version_id"]) for row in open_rows]
        )
        anchors = []
        for row in open_rows:
            structured = str(row["extraction_method"]) in STRUCTURED_METHODS
            evidence = trust.get(cast(UUID, row["version_id"]), {})
            gstin_confidence, gstin_source = evidence.get("vendor_gstin", (None, None))
            name_confidence, name_source = evidence.get("vendor_name", (None, None))
            anchors.append(
                anchor_document(
                    document_id=cast(UUID, row["document_id"]),
                    natural_key=str(row["natural_key"]),
                    vendor_gstin=cast(str | None, row["vendor_gstin"]),
                    vendor_name=cast(str | None, row["vendor_name"]),
                    gstin_verified=field_verified(
                        gstin_confidence, gstin_source, structured=structured
                    ),
                    name_verified=field_verified(
                        name_confidence, name_source, structured=structured
                    ),
                    buyers=buyers,
                )
            )
        plan = plan_vendor_anchors(
            anchors, self._anchor_vendors(connection, workspace_id), buyer_gstins(buyers)
        )

        new_ids: dict[str, UUID] = {}
        for planned in plan.new_vendors:
            new_ids[planned.key] = cast(
                UUID,
                connection.scalar(
                    sa.insert(vendor)
                    .values(
                        id=new_id(),
                        workspace_id=workspace_id,
                        canonical_name=planned.name,
                        normalized_name=normalize_vendor_name(planned.name),
                        pan=planned.pan,
                        anchor="gstin",
                        created_by_rule="gstin",
                        name_status=planned.name_status.value,
                    )
                    .returning(vendor.c.id)
                ),
            )
        registrations = 0
        for planned_registration in plan.registrations:
            registration_vendor = (
                planned_registration.vendor_id
                or new_ids[cast(str, planned_registration.new_vendor_key)]
            )
            inserted_id = connection.scalar(
                pg_insert(registration)
                .values(
                    id=new_id(),
                    workspace_id=workspace_id,
                    vendor_id=registration_vendor,
                    gstin=planned_registration.gstin,
                    state_code=planned_registration.state_code,
                    pan=planned_registration.pan,
                    checksum_ok=True,
                )
                .on_conflict_do_nothing(
                    index_elements=[registration.c.workspace_id, registration.c.gstin]
                )
                .returning(registration.c.id)
            )
            registrations += int(inserted_id is not None)
        for rename in plan.renames:
            connection.execute(
                sa.update(vendor)
                .where(
                    vendor.c.workspace_id == workspace_id,
                    vendor.c.id == rename.vendor_id,
                    vendor.c.name_status != VendorNameStatus.MANUAL.value,
                )
                .values(
                    canonical_name=rename.name,
                    normalized_name=normalize_vendor_name(rename.name),
                    name_status=rename.name_status.value,
                    updated_at=sa.func.now(),
                    version=vendor.c.version + 1,
                )
            )
        aliases = 0
        for planned_alias in plan.aliases:
            alias_vendor = (
                planned_alias.vendor_id or new_ids[cast(str, planned_alias.new_vendor_key)]
            )
            inserted_alias_id = connection.scalar(
                pg_insert(alias)
                .values(
                    id=new_id(),
                    workspace_id=workspace_id,
                    vendor_id=alias_vendor,
                    alias_raw=planned_alias.alias,
                    alias_norm=normalize_vendor_name(planned_alias.alias),
                    source="learned",
                )
                .on_conflict_do_nothing(index_elements=[alias.c.workspace_id, alias.c.alias_norm])
                .returning(alias.c.id)
            )
            if inserted_alias_id is None:
                continue
            connection.execute(
                sa.update(vendor)
                .where(vendor.c.workspace_id == workspace_id, vendor.c.id == alias_vendor)
                .values(
                    input_seq=vendor.c.input_seq + 1,
                    updated_at=sa.func.now(),
                    version=vendor.c.version + 1,
                )
            )
            self._mark_scope(
                connection,
                workspace_id=workspace_id,
                scope_kind="vendor",
                scope_id=alias_vendor,
                reason="vendor_alias_learned",
            )
            aliases += 1
        return len(plan.new_vendors), registrations, aliases

    @staticmethod
    def _identity_trust(
        connection: Connection, workspace_id: UUID, version_ids: list[UUID]
    ) -> dict[UUID, dict[str, tuple[Any, Any]]]:
        """Header evidence (confidence, source) of the vendor GSTIN and name per version."""

        if not version_ids:
            return {}
        evidence = metadata.tables["field_evidence"]
        rows = connection.execute(
            sa.select(
                evidence.c.document_version_id,
                evidence.c.field_name,
                evidence.c.confidence,
                evidence.c.source,
            )
            .where(
                evidence.c.workspace_id == workspace_id,
                evidence.c.document_version_id.in_(version_ids),
                evidence.c.doc_line_id.is_(None),
                evidence.c.field_name.in_(("vendor_gstin", "vendor_name")),
            )
            .order_by(
                evidence.c.document_version_id,
                evidence.c.field_name,
                evidence.c.created_at,
                evidence.c.id,
            )
        ).all()
        trust: dict[UUID, dict[str, tuple[Any, Any]]] = {}
        for version_id, field_name, confidence, source in rows:
            # The last row wins: a correction is written after the reading it replaces.
            trust.setdefault(cast(UUID, version_id), {})[str(field_name)] = (
                confidence,
                str(source),
            )
        return trust

    @staticmethod
    def _anchor_vendors(connection: Connection, workspace_id: UUID) -> list[AnchorVendor]:
        vendor = metadata.tables["vendor"]
        registration = metadata.tables["vendor_gstin"]
        alias = metadata.tables["vendor_alias"]
        rows = (
            connection.execute(
                sa.select(vendor.c.id, vendor.c.canonical_name, vendor.c.name_status, vendor.c.pan)
                .where(vendor.c.workspace_id == workspace_id, vendor.c.merged_into.is_(None))
                .order_by(vendor.c.id)
            )
            .mappings()
            .all()
        )
        gstins: dict[UUID, list[str]] = {}
        for vendor_id, gstin in connection.execute(
            sa.select(registration.c.vendor_id, registration.c.gstin).where(
                registration.c.workspace_id == workspace_id
            )
        ).all():
            gstins.setdefault(cast(UUID, vendor_id), []).append(str(gstin))
        aliases: dict[UUID, list[str]] = {}
        for vendor_id, alias_norm in connection.execute(
            sa.select(alias.c.vendor_id, alias.c.alias_norm).where(
                alias.c.workspace_id == workspace_id
            )
        ).all():
            aliases.setdefault(cast(UUID, vendor_id), []).append(str(alias_norm))
        return [
            AnchorVendor(
                vendor_id=cast(UUID, row["id"]),
                name=str(row["canonical_name"]),
                name_status=VendorNameStatus(str(row["name_status"])),
                pan=cast(str | None, row["pan"]),
                gstins=tuple(sorted(gstins.get(cast(UUID, row["id"]), []))),
                alias_norms=tuple(sorted(aliases.get(cast(UUID, row["id"]), []))),
            )
            for row in rows
        ]

    def _queue_vendor_opinions(self, connection: Connection, workspace_id: UUID) -> None:
        """Queue advisory H4 opinions; a failure never blocks vendor resolution."""

        if self.ai_hooks is None:
            return
        try:
            with connection.begin_nested():
                self.ai_hooks.vendor_opinions(connection, workspace_id)
        except Exception as exc:  # noqa: BLE001 - AI is optional; rules continue.
            _LOGGER.warning("ai_enqueue_failed hook=h4_vendor error_type=%s", type(exc).__name__)

    def _vendor_snapshot(
        self,
        connection: Connection,
        workspace_id: UUID,
    ) -> tuple[list[VendorRecord], list[VendorAliasRecord]]:
        vendor = metadata.tables["vendor"]
        registration = metadata.tables["vendor_gstin"]
        alias = metadata.tables["vendor_alias"]
        vendor_rows = (
            connection.execute(
                sa.select(*vendor.c)
                .where(vendor.c.workspace_id == workspace_id, vendor.c.merged_into.is_(None))
                .order_by(vendor.c.normalized_name, vendor.c.id)
            )
            .mappings()
            .all()
        )
        vendor_ids = [cast(UUID, row["id"]) for row in vendor_rows]
        if not vendor_ids:
            return [], []
        gstins = connection.execute(
            sa.select(registration.c.vendor_id, registration.c.gstin)
            .where(
                registration.c.workspace_id == workspace_id,
                registration.c.vendor_id.in_(vendor_ids),
            )
            .order_by(registration.c.vendor_id, registration.c.gstin)
        ).all()
        aliases = connection.execute(
            sa.select(alias.c.vendor_id, alias.c.alias_raw)
            .where(alias.c.workspace_id == workspace_id, alias.c.vendor_id.in_(vendor_ids))
            .order_by(alias.c.vendor_id, alias.c.alias_norm)
        ).all()
        gstins_by_vendor: dict[UUID, list[str]] = {}
        for vendor_id, gstin in gstins:
            gstins_by_vendor.setdefault(cast(UUID, vendor_id), []).append(str(gstin))
        aliases_by_vendor: dict[UUID, list[str]] = {}
        alias_records: list[VendorAliasRecord] = []
        for vendor_id, name in aliases:
            alias_name = str(name)
            aliases_by_vendor.setdefault(cast(UUID, vendor_id), []).append(alias_name)
            alias_records.append(
                VendorAliasRecord(vendor_id=cast(UUID, vendor_id), alias=alias_name)
            )
        records = [
            VendorRecord(
                vendor_id=cast(UUID, row["id"]),
                display_name=str(row["canonical_name"]),
                gstins=tuple(gstins_by_vendor.get(cast(UUID, row["id"]), [])),
                pan=cast(str | None, row["pan"]),
                aliases=tuple(aliases_by_vendor.get(cast(UUID, row["id"]), [])),
            )
            for row in vendor_rows
        ]
        return records, alias_records

    @staticmethod
    def _config(connection: Connection, workspace_id: UUID) -> VendorResolutionConfig:
        return vendor_config_from_body(
            VendorResolutionService._config_body(connection, workspace_id)
        )

    @staticmethod
    def _engine_body(connection: Connection, workspace_id: UUID) -> Mapping[str, Any]:
        body = VendorResolutionService._config_body(connection, workspace_id)
        engine = body.get("engine") if isinstance(body, Mapping) else None
        return engine if isinstance(engine, Mapping) else {}

    @staticmethod
    def _config_body(connection: Connection, workspace_id: UUID) -> Any:
        active = metadata.tables["config_active"]
        version = metadata.tables["config_version"]
        return connection.scalar(
            sa.select(version.c.body)
            .select_from(
                active.join(
                    version,
                    sa.and_(
                        active.c.workspace_id == version.c.workspace_id,
                        active.c.config_version_id == version.c.id,
                    ),
                )
            )
            .where(active.c.workspace_id == workspace_id)
        )

    def _record_document_candidates(
        self,
        connection: Connection,
        *,
        workspace_id: UUID,
        document_id: UUID,
        vendor_id: UUID | None,
        suggestions: tuple[Any, ...],
    ) -> int:
        warning = metadata.tables["document_warning"]
        active_warning = connection.scalar(
            sa.select(warning.c.id).where(
                warning.c.workspace_id == workspace_id,
                warning.c.document_id == document_id,
                warning.c.code == "VENDOR_MATCH_SUGGESTED",
                warning.c.resolved_at.is_(None),
            )
        )
        if vendor_id is None and suggestions:
            if active_warning is None:
                connection.execute(
                    sa.insert(warning).values(
                        id=new_id(),
                        workspace_id=workspace_id,
                        document_id=document_id,
                        code="VENDOR_MATCH_SUGGESTED",
                        level="warning",
                        message="Possible vendor matches need a person to review them.",
                        detail={
                            "candidates": [
                                {
                                    "vendor_id": str(candidate.vendor_id),
                                    "score": format(candidate.score, "f"),
                                    "pan_conflict": candidate.pan_conflict,
                                    "merge_allowed": candidate.merge_allowed,
                                }
                                for candidate in suggestions[:5]
                            ]
                        },
                    )
                )
            return 0
        if active_warning is not None:
            connection.execute(
                sa.update(warning)
                .where(warning.c.id == active_warning)
                .values(resolved_at=sa.func.now())
            )
        return 0

    def _record_vendor_pair_candidates(
        self,
        connection: Connection,
        *,
        workspace_id: UUID,
        vendors: list[VendorRecord],
        aliases: list[VendorAliasRecord],
        config: VendorResolutionConfig,
    ) -> int:
        suggestion = metadata.tables["vendor_merge_suggestion"]
        added = 0
        for source in vendors:
            if not source.gstins and not source.pan:
                continue
            pool = [vendor for vendor in vendors if vendor.vendor_id != source.vendor_id]
            if not pool:
                continue
            source_gstin = source.gstins[0] if source.gstins else None
            result = resolve_vendors(
                [
                    VendorDocument(
                        document_id=source.vendor_id,
                        natural_key=str(source.vendor_id),
                        vendor_name=source.display_name,
                        vendor_gstin=source_gstin,
                    )
                ],
                pool,
                [alias for alias in aliases if alias.vendor_id != source.vendor_id],
                config,
            ).resolutions[0]
            for candidate in result.suggestions:
                if candidate.vendor_id == source.vendor_id:
                    continue
                vendor_a_id, vendor_b_id = sorted((source.vendor_id, candidate.vendor_id), key=str)
                inserted_id = connection.scalar(
                    pg_insert(suggestion)
                    .values(
                        id=new_id(),
                        workspace_id=workspace_id,
                        vendor_a_id=vendor_a_id,
                        vendor_b_id=vendor_b_id,
                        score=candidate.score,
                        state="pending",
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            suggestion.c.workspace_id,
                            suggestion.c.vendor_a_id,
                            suggestion.c.vendor_b_id,
                        ]
                    )
                    .returning(suggestion.c.id)
                )
                added += int(inserted_id is not None)
        return added

    def _mark_document_dirty(
        self,
        connection: Connection,
        *,
        workspace_id: UUID,
        document_id: UUID,
        doc_type: str,
        old_vendor_id: UUID | None,
        new_vendor_id: UUID | None,
        dirty_vendors: set[UUID],
    ) -> None:
        document = metadata.tables["document"]
        vendor = metadata.tables["vendor"]
        for vendor_id in {item for item in (old_vendor_id, new_vendor_id) if item is not None}:
            if vendor_id in dirty_vendors:
                continue
            connection.execute(
                sa.update(vendor)
                .where(vendor.c.workspace_id == workspace_id, vendor.c.id == vendor_id)
                .values(
                    input_seq=vendor.c.input_seq + 1,
                    updated_at=sa.func.now(),
                    version=vendor.c.version + 1,
                )
            )
            self._mark_scope(
                connection,
                workspace_id=workspace_id,
                scope_kind="vendor",
                scope_id=vendor_id,
                reason="document_vendor_resolved",
            )
            dirty_vendors.add(vendor_id)
        if doc_type == "po":
            connection.execute(
                sa.update(document)
                .where(document.c.workspace_id == workspace_id, document.c.id == document_id)
                .values(input_seq=document.c.input_seq + 1)
            )
            self._mark_scope(
                connection,
                workspace_id=workspace_id,
                scope_kind="po",
                scope_id=document_id,
                reason="document_vendor_resolved",
            )
        elif doc_type == "invoice":
            self._mark_scope(
                connection,
                workspace_id=workspace_id,
                scope_kind="invoice",
                scope_id=document_id,
                reason="document_vendor_resolved",
            )

    @staticmethod
    def _mark_scope(
        connection: Connection,
        *,
        workspace_id: UUID,
        scope_kind: str,
        scope_id: UUID,
        reason: str,
    ) -> None:
        table = metadata.tables["dirty_scope"]
        statement = pg_insert(table).values(
            id=new_id(),
            workspace_id=workspace_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            reason=reason,
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.workspace_id, table.c.scope_kind, table.c.scope_id],
                index_where=table.c.processed_at.is_(None),
                set_={
                    "mark_count": table.c.mark_count + 1,
                    "last_marked_at": sa.func.now(),
                    "reason": statement.excluded.reason,
                },
            )
        )


__all__ = ["VendorResolutionService", "VendorResolutionSummary"]
