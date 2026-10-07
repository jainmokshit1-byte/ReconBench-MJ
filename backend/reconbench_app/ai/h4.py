"""H4 vendor-merge opinion: the subject a request refers to and the text it may send.

Shared by the producer (vendor resolution) and the AI worker. Only names and
aliases are sent; GSTINs, PANs and contact or banking details are replaced by
placeholders, and pairs whose PANs conflict never take an AI opinion.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from reconbench_app.ai.redaction import STRICT_CATEGORIES, RedactedText, redact
from reconbench_app.db.metadata import metadata

HOOK = "h4_vendor"
SUBJECT_TYPE = "vendor_merge_suggestion"
SKIP_PAN_CONFLICT = "PAN_CONFLICT"
SKIP_SOURCE_CHANGED = "SOURCE_CHANGED"
MAX_ALIASES = 5

SUGGESTIONS = metadata.tables["vendor_merge_suggestion"]
VENDORS = metadata.tables["vendor"]
ALIASES = metadata.tables["vendor_alias"]
REGISTRATIONS = metadata.tables["vendor_gstin"]


@dataclass(frozen=True, slots=True)
class VendorPair:
    suggestion_id: UUID
    state: str
    version: int
    ai_call_id: UUID | None
    merged: bool
    pan_conflict: bool
    vendor_a: RedactedText
    vendor_b: RedactedText

    def open_at(self, subject_version: str) -> bool:
        """Still undecided, unmerged, without an opinion, and at the requested version."""

        return (
            self.state == "pending"
            and self.ai_call_id is None
            and not self.merged
            and str(self.version) == subject_version
        )


def pans_conflict(*pan_sets: Iterable[str | None]) -> bool:
    """More than one distinct PAN across both vendors (same rule as the Mapping list)."""

    pans = {str(pan).upper() for pans in pan_sets for pan in pans if pan}
    return len(pans) > 1


def _vendor_text(name: str, aliases: list[str]) -> RedactedText:
    others = sorted({alias for alias in aliases if alias and alias != name})[:MAX_ALIASES]
    lines = [f"Name: {name}"]
    if others:
        lines.append("Also known as: " + "; ".join(others))
    return redact("\n".join(lines), STRICT_CATEGORIES)


def load_vendor_pair(
    connection: Connection,
    workspace_id: UUID,
    suggestion_id: UUID,
    *,
    for_update: bool = False,
) -> VendorPair | None:
    query = sa.select(SUGGESTIONS).where(
        SUGGESTIONS.c.workspace_id == workspace_id, SUGGESTIONS.c.id == suggestion_id
    )
    if for_update:
        query = query.with_for_update()
    row = connection.execute(query).mappings().first()
    if row is None:
        return None
    vendor_ids = [row["vendor_a_id"], row["vendor_b_id"]]
    vendors = {
        vendor["id"]: vendor
        for vendor in connection.execute(
            sa.select(
                VENDORS.c.id, VENDORS.c.canonical_name, VENDORS.c.pan, VENDORS.c.merged_into
            ).where(VENDORS.c.workspace_id == workspace_id, VENDORS.c.id.in_(vendor_ids))
        ).mappings()
    }
    if len(vendors) != 2:
        return None
    aliases: dict[UUID, list[str]] = {vendor_id: [] for vendor_id in vendor_ids}
    for vendor_id, alias in connection.execute(
        sa.select(ALIASES.c.vendor_id, ALIASES.c.alias_raw).where(
            ALIASES.c.workspace_id == workspace_id, ALIASES.c.vendor_id.in_(vendor_ids)
        )
    ):
        aliases[vendor_id].append(str(alias))
    registration_pans: dict[UUID, list[str]] = {vendor_id: [] for vendor_id in vendor_ids}
    for vendor_id, pan in connection.execute(
        sa.select(REGISTRATIONS.c.vendor_id, REGISTRATIONS.c.pan).where(
            REGISTRATIONS.c.workspace_id == workspace_id,
            REGISTRATIONS.c.vendor_id.in_(vendor_ids),
        )
    ):
        registration_pans[vendor_id].append(str(pan))
    vendor_a, vendor_b = (vendors[vendor_id] for vendor_id in vendor_ids)
    return VendorPair(
        suggestion_id=suggestion_id,
        state=str(row["state"]),
        version=int(row["version"]),
        ai_call_id=row["ai_call_id"],
        merged=vendor_a["merged_into"] is not None or vendor_b["merged_into"] is not None,
        pan_conflict=pans_conflict([vendor_a["pan"], vendor_b["pan"]], *registration_pans.values()),
        vendor_a=_vendor_text(str(vendor_a["canonical_name"]), aliases[vendor_ids[0]]),
        vendor_b=_vendor_text(str(vendor_b["canonical_name"]), aliases[vendor_ids[1]]),
    )


def pending_suggestions(connection: Connection, workspace_id: UUID) -> list[UUID]:
    """Undecided pairs without an opinion, highest score first; PAN checks happen per pair."""

    return [
        UUID(str(suggestion_id))
        for suggestion_id in connection.scalars(
            sa.select(SUGGESTIONS.c.id)
            .where(
                SUGGESTIONS.c.workspace_id == workspace_id,
                SUGGESTIONS.c.state == "pending",
                SUGGESTIONS.c.ai_call_id.is_(None),
            )
            .order_by(SUGGESTIONS.c.score.desc(), SUGGESTIONS.c.created_at, SUGGESTIONS.c.id)
        )
    ]


def record_opinion(
    connection: Connection,
    workspace_id: UUID,
    pair: VendorPair,
    *,
    opinion: str,
    reason: str,
    call_log_id: UUID,
) -> bool:
    """Attach the advisory opinion; never changes state, version or any vendor."""

    updated = connection.execute(
        sa.update(SUGGESTIONS)
        .where(
            SUGGESTIONS.c.workspace_id == workspace_id,
            SUGGESTIONS.c.id == pair.suggestion_id,
            SUGGESTIONS.c.state == "pending",
            SUGGESTIONS.c.version == pair.version,
            SUGGESTIONS.c.ai_call_id.is_(None),
        )
        .values(ai_opinion=opinion, ai_reason=reason, ai_call_id=call_log_id)
    ).rowcount
    return bool(updated)


__all__ = [
    "HOOK",
    "SKIP_PAN_CONFLICT",
    "SKIP_SOURCE_CHANGED",
    "SUBJECT_TYPE",
    "VendorPair",
    "load_vendor_pair",
    "pans_conflict",
    "pending_suggestions",
    "record_opinion",
]
