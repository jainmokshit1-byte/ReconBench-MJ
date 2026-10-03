"""Persist deterministic vendor resolution over the pure mapping core."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from reconbench_core.mapping.vendor import (
    VendorAliasRecord,
    VendorDocument,
    VendorRecord,
    VendorResolutionConfig,
    resolve_vendors,
)
from reconbench_core.normalize import normalize_vendor_name, validate_gstin
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from reconbench_app.authorization import requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata


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

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

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
        vendor = metadata.tables["vendor"]
        registration = metadata.tables["vendor_gstin"]
        alias = metadata.tables["vendor_alias"]
        created = registrations = aliases = 0
        for document in documents:
            if bool(document["vendor_locked"]):
                continue
            raw_gstin = document["vendor_gstin"]
            raw_name = document["vendor_name"]
            if not isinstance(raw_gstin, str):
                continue
            facts = validate_gstin(raw_gstin)
            if not facts.is_valid or facts.state_code is None or facts.pan is None:
                continue
            vendor_id = connection.scalar(
                sa.select(registration.c.vendor_id)
                .select_from(
                    registration.join(
                        vendor,
                        sa.and_(
                            vendor.c.workspace_id == registration.c.workspace_id,
                            vendor.c.id == registration.c.vendor_id,
                        ),
                    )
                )
                .where(
                    registration.c.workspace_id == workspace_id,
                    registration.c.gstin == facts.normalized,
                    vendor.c.merged_into.is_(None),
                )
                .limit(1)
            )
            if vendor_id is None:
                pan_vendor_ids = set(
                    connection.scalars(
                        sa.select(vendor.c.id)
                        .where(
                            vendor.c.workspace_id == workspace_id,
                            vendor.c.merged_into.is_(None),
                            vendor.c.pan == facts.pan,
                        )
                        .union(
                            sa.select(registration.c.vendor_id)
                            .select_from(
                                registration.join(
                                    vendor,
                                    sa.and_(
                                        vendor.c.workspace_id == registration.c.workspace_id,
                                        vendor.c.id == registration.c.vendor_id,
                                    ),
                                )
                            )
                            .where(
                                registration.c.workspace_id == workspace_id,
                                registration.c.pan == facts.pan,
                                vendor.c.merged_into.is_(None),
                            )
                        )
                    ).all()
                )
                if len(pan_vendor_ids) == 1:
                    vendor_id = next(iter(pan_vendor_ids))
                else:
                    display_name = raw_name.strip() if isinstance(raw_name, str) else ""
                    display_name = display_name or facts.normalized
                    vendor_id = cast(
                        UUID,
                        connection.scalar(
                            sa.insert(vendor)
                            .values(
                                id=new_id(),
                                workspace_id=workspace_id,
                                canonical_name=display_name,
                                normalized_name=normalize_vendor_name(display_name),
                                pan=facts.pan,
                                anchor="gstin",
                                created_by_rule="gstin",
                            )
                            .returning(vendor.c.id)
                        ),
                    )
                    created += 1
            insert_registration = pg_insert(registration).values(
                id=new_id(),
                workspace_id=workspace_id,
                vendor_id=vendor_id,
                gstin=facts.normalized,
                state_code=facts.state_code,
                pan=facts.pan,
                checksum_ok=True,
            )
            inserted_id = connection.scalar(
                insert_registration.on_conflict_do_nothing(
                    index_elements=[registration.c.workspace_id, registration.c.gstin]
                ).returning(registration.c.id)
            )
            registrations += int(inserted_id is not None)
            canonical_name = connection.scalar(
                sa.select(vendor.c.canonical_name).where(
                    vendor.c.workspace_id == workspace_id,
                    vendor.c.id == vendor_id,
                )
            )
            if isinstance(raw_name, str) and raw_name.strip() and isinstance(canonical_name, str):
                alias_norm = normalize_vendor_name(raw_name)
                if alias_norm and alias_norm != normalize_vendor_name(canonical_name):
                    alias_insert = pg_insert(alias).values(
                        id=new_id(),
                        workspace_id=workspace_id,
                        vendor_id=vendor_id,
                        alias_raw=raw_name.strip(),
                        alias_norm=alias_norm,
                        source="learned",
                    )
                    inserted_alias_id = connection.scalar(
                        alias_insert.on_conflict_do_nothing(
                            index_elements=[alias.c.workspace_id, alias.c.alias_norm]
                        ).returning(alias.c.id)
                    )
                    if inserted_alias_id is not None:
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
                            scope_id=cast(UUID, vendor_id),
                            reason="vendor_alias_learned",
                        )
                        aliases += 1
        return created, registrations, aliases

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
        active = metadata.tables["config_active"]
        version = metadata.tables["config_version"]
        body = connection.scalar(
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
