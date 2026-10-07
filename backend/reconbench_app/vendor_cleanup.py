"""One-time vendor master cleanup (accuracy plan AC-4, decision-log D36).

Vendors created before AC-4 can carry the buyer's own GSTIN, a label such as
"(Bill from)" or a GSTIN as their name, and aliases learned from such text.
This finds them and, with ``--apply``, repairs them in place with an audit
event per vendor:

- a junk name becomes empty with status ``pending``; the next vendor
  resolution gives it the best name its documents carry;
- aliases that are labels, GSTINs, line text or the buyer's own names are removed;
- a registration of a buyer GSTIN is removed (and the vendor's PAN when it is
  the buyer's PAN), and the vendor's unlocked documents are unassigned so that
  resolution places them again;
- a vendor left with no anchor and nothing referring to it is deleted; one that
  is still referred to (a manual assignment, a reconciliation case) is kept,
  reported and left for a person to merge.

Then vendor resolution and link mapping run once for the workspace. Run inside
the api container (dry run by default)::

    python -m reconbench_app.vendor_cleanup --actor-email owner@example.com
    python -m reconbench_app.vendor_cleanup --actor-email owner@example.com --apply
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from reconbench_core.normalize import validate_gstin
from reconbench_core.parsers.parties import (
    buyer_gstins,
    is_buyer_name,
    name_problem,
    party_hints_from_config,
)
from reconbench_core.parsers.types import BuyerEntityHint
from sqlalchemy.engine import Connection, Engine

from reconbench_app.audit import write_audit_event
from reconbench_app.authorization import ActorContext, Role
from reconbench_app.db.metadata import metadata
from reconbench_app.vendor_resolution import VendorResolutionService

# Tables whose rows keep a vendor alive (append-only history or a person's choice).
_KEEP_ALIVE = ("recon_case", "case_current", "layout_profile", "opening_balance")


@dataclass
class VendorFinding:
    vendor_id: UUID
    name: str
    reasons: list[str] = field(default_factory=list)
    junk_alias_ids: list[UUID] = field(default_factory=list)
    buyer_registration_ids: list[UUID] = field(default_factory=list)
    clear_pan: bool = False
    rename_to_pending: bool = False
    documents_to_unassign: list[UUID] = field(default_factory=list)
    locked_documents: int = 0
    outcome: str = "planned"


def _bad_name(name: str, buyers: Sequence[BuyerEntityHint]) -> str | None:
    if not name.strip():
        return None
    problem = name_problem(name)
    if problem is not None:
        return f"name_{problem}"
    if is_buyer_name(name, buyers):
        return "name_buyer"
    return None


def _buyers(connection: Connection, workspace_id: UUID) -> tuple[BuyerEntityHint, ...]:
    body = VendorResolutionService._config_body(connection, workspace_id)
    engine = body.get("engine") if isinstance(body, dict) else None
    return tuple(
        party_hints_from_config(engine if isinstance(engine, dict) else {})["buyer_entities"]
    )


def find_junk_vendors(connection: Connection, workspace_id: UUID) -> list[VendorFinding]:
    """Read-only: every unmerged vendor that breaks an AC-4 rule, and the planned repair."""

    vendor = metadata.tables["vendor"]
    registration = metadata.tables["vendor_gstin"]
    alias = metadata.tables["vendor_alias"]
    document = metadata.tables["document"]
    buyers = _buyers(connection, workspace_id)
    own_gstins = buyer_gstins(buyers)
    own_pans = {
        facts.pan for gstin in own_gstins if (facts := validate_gstin(gstin)).pan is not None
    }
    findings: list[VendorFinding] = []
    vendors = (
        connection.execute(
            sa.select(vendor.c.id, vendor.c.canonical_name, vendor.c.name_status, vendor.c.pan)
            .where(vendor.c.workspace_id == workspace_id, vendor.c.merged_into.is_(None))
            .order_by(vendor.c.id)
        )
        .mappings()
        .all()
    )
    for row in vendors:
        vendor_id = cast(UUID, row["id"])
        finding = VendorFinding(vendor_id=vendor_id, name=str(row["canonical_name"]))
        name_reason = _bad_name(finding.name, buyers)
        if name_reason is not None and row["name_status"] != "manual":
            finding.reasons.append(name_reason)
            finding.rename_to_pending = True
        for alias_id, alias_raw in connection.execute(
            sa.select(alias.c.id, alias.c.alias_raw).where(
                alias.c.workspace_id == workspace_id, alias.c.vendor_id == vendor_id
            )
        ).all():
            if _bad_name(str(alias_raw), buyers) is not None:
                finding.junk_alias_ids.append(cast(UUID, alias_id))
        if finding.junk_alias_ids:
            finding.reasons.append("junk_alias")
        for registration_id, gstin in connection.execute(
            sa.select(registration.c.id, registration.c.gstin).where(
                registration.c.workspace_id == workspace_id,
                registration.c.vendor_id == vendor_id,
            )
        ).all():
            if str(gstin) in own_gstins:
                finding.buyer_registration_ids.append(cast(UUID, registration_id))
        finding.clear_pan = row["pan"] is not None and str(row["pan"]) in own_pans
        if finding.buyer_registration_ids or finding.clear_pan:
            finding.reasons.append("buyer_gstin")
            for document_id, locked in connection.execute(
                sa.select(document.c.id, document.c.vendor_locked).where(
                    document.c.workspace_id == workspace_id,
                    document.c.vendor_id == vendor_id,
                    document.c.withdrawn_at.is_(None),
                )
            ).all():
                if bool(locked):
                    finding.locked_documents += 1
                else:
                    finding.documents_to_unassign.append(cast(UUID, document_id))
        if finding.reasons:
            findings.append(finding)
    return findings


def _still_referenced(connection: Connection, workspace_id: UUID, vendor_id: UUID) -> bool:
    """A document, case, profile, decided suggestion or merged vendor still points here."""

    document = metadata.tables["document"]
    suggestion = metadata.tables["vendor_merge_suggestion"]
    vendor = metadata.tables["vendor"]
    checks = [
        sa.select(document.c.id).where(
            document.c.workspace_id == workspace_id, document.c.vendor_id == vendor_id
        ),
        sa.select(suggestion.c.id).where(
            suggestion.c.state != "pending",
            sa.or_(
                suggestion.c.vendor_a_id == vendor_id,
                suggestion.c.vendor_b_id == vendor_id,
                suggestion.c.survivor_vendor_id == vendor_id,
            ),
        ),
        sa.select(vendor.c.id).where(vendor.c.merged_into == vendor_id),
        *(
            sa.select(metadata.tables[name].c.vendor_id).where(
                metadata.tables[name].c.vendor_id == vendor_id
            )
            for name in _KEEP_ALIVE
        ),
    ]
    return any(connection.execute(check.limit(1)).first() is not None for check in checks)


def _apply_one(connection: Connection, actor: ActorContext, finding: VendorFinding) -> None:
    vendor = metadata.tables["vendor"]
    registration = metadata.tables["vendor_gstin"]
    alias = metadata.tables["vendor_alias"]
    document = metadata.tables["document"]
    suggestion = metadata.tables["vendor_merge_suggestion"]
    workspace_id = actor.workspace_id
    if finding.junk_alias_ids:
        connection.execute(
            sa.delete(alias).where(
                alias.c.workspace_id == workspace_id, alias.c.id.in_(finding.junk_alias_ids)
            )
        )
    if finding.buyer_registration_ids:
        connection.execute(
            sa.delete(registration).where(
                registration.c.workspace_id == workspace_id,
                registration.c.id.in_(finding.buyer_registration_ids),
            )
        )
    if finding.documents_to_unassign:
        connection.execute(
            sa.update(document)
            .where(
                document.c.workspace_id == workspace_id,
                document.c.id.in_(finding.documents_to_unassign),
                document.c.vendor_locked.is_(False),
            )
            .values(
                vendor_id=None,
                vendor_rule="none",
                vendor_score=None,
                vendor_resolved_at=None,
                input_seq=document.c.input_seq + 1,
                updated_at=sa.func.now(),
                version=document.c.version + 1,
            )
        )
    values: dict[str, Any] = {
        "input_seq": vendor.c.input_seq + 1,
        "updated_at": sa.func.now(),
        "version": vendor.c.version + 1,
    }
    if finding.rename_to_pending:
        values.update(canonical_name="", normalized_name="", name_status="pending")
    if finding.clear_pan:
        values["pan"] = None
    connection.execute(
        sa.update(vendor)
        .where(vendor.c.workspace_id == workspace_id, vendor.c.id == finding.vendor_id)
        .values(**values)
    )
    finding.outcome = "repaired"

    anchored = connection.scalar(
        sa.select(sa.func.count())
        .select_from(registration)
        .where(
            registration.c.workspace_id == workspace_id,
            registration.c.vendor_id == finding.vendor_id,
        )
    )
    pan = connection.scalar(sa.select(vendor.c.pan).where(vendor.c.id == finding.vendor_id))
    if not anchored and pan is None and "buyer_gstin" in finding.reasons:
        if _still_referenced(connection, workspace_id, finding.vendor_id):
            finding.outcome = "repaired_shell_kept"
        else:
            connection.execute(
                sa.delete(suggestion).where(
                    sa.or_(
                        suggestion.c.vendor_a_id == finding.vendor_id,
                        suggestion.c.vendor_b_id == finding.vendor_id,
                    )
                )
            )
            connection.execute(sa.delete(alias).where(alias.c.vendor_id == finding.vendor_id))
            connection.execute(sa.delete(vendor).where(vendor.c.id == finding.vendor_id))
            finding.outcome = "deleted"
    write_audit_event(
        connection,
        actor,
        action="vendor.cleanup",
        target_type="vendor",
        target_id=finding.vendor_id,
        before_state={"canonical_name": finding.name},
        after_state={
            "reasons": finding.reasons,
            "aliases_removed": len(finding.junk_alias_ids),
            "buyer_registrations_removed": len(finding.buyer_registration_ids),
            "documents_unassigned": len(finding.documents_to_unassign),
            "outcome": finding.outcome,
        },
        reason="AC-4 vendor master cleanup",
    )


def clean_workspace(engine: Engine, actor: ActorContext, *, apply: bool) -> list[VendorFinding]:
    """Find (and with ``apply`` repair) junk vendors in one transaction."""

    with engine.begin() as connection:
        connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": f"reconbench.vendor-resolution:{actor.workspace_id}"},
        )
        findings = find_junk_vendors(connection, actor.workspace_id)
        if apply:
            for finding in findings:
                _apply_one(connection, actor, finding)
    return findings


def _actor_for(engine: Engine, email: str) -> ActorContext:
    user = metadata.tables["app_user"]
    with engine.connect() as connection:
        row = (
            connection.execute(
                sa.select(user.c.id, user.c.workspace_id, user.c.role).where(
                    sa.func.lower(user.c.email) == email.strip().lower()
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise SystemExit("no user with that email; nothing was changed")
    return ActorContext(
        workspace_id=cast(UUID, row["workspace_id"]),
        actor_id=cast(UUID, row["id"]),
        actor_role=cast(Role, row["role"]),
    )


def main(argv: Sequence[str] | None = None) -> int:
    from reconbench_app.mapping_service import MappingService
    from reconbench_app.settings import Settings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--actor-email", required=True, help="the person the audit names")
    parser.add_argument("--apply", action="store_true", help="write the repairs (default: dry run)")
    arguments = parser.parse_args(argv)

    engine = sa.create_engine(Settings().database_url)
    actor = _actor_for(engine, arguments.actor_email)
    findings = clean_workspace(engine, actor, apply=arguments.apply)
    for finding in findings:
        print(
            f"{finding.vendor_id}  {finding.name!r}  reasons={','.join(finding.reasons)}  "
            f"aliases-={len(finding.junk_alias_ids)}  "
            f"buyer-gstins-={len(finding.buyer_registration_ids)}  "
            f"unassign={len(finding.documents_to_unassign)}  locked={finding.locked_documents}  "
            f"-> {finding.outcome}"
        )
    print(f"{len(findings)} vendor(s) {'repaired' if arguments.apply else 'would be repaired'}.")
    if not arguments.apply:
        print("Dry run: nothing was changed. Re-run with --apply to write.")
        return 0
    vendors = VendorResolutionService(engine).resolve_workspace(actor.workspace_id)
    MappingService(engine).resolve_workspace(actor.workspace_id)
    print(
        f"Vendor resolution: {vendors.documents_assigned} assigned, "
        f"{vendors.documents_unassigned} pending, {vendors.vendors_created} created. "
        "Links re-mapped; run reconciliation again to refresh cases."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
