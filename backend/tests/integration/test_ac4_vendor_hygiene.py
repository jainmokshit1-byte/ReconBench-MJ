"""AC-4 vendor hygiene on a disposable PostgreSQL 16 database (synthetic data only).

Each test creates its own workspace; documents are written directly as current
versions with header evidence, the way ingestion persists them.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from reconbench_app.authorization import ActorContext
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.db.repository import VersionConflict
from reconbench_app.mapping_service import MappingService
from reconbench_app.vendor_cleanup import clean_workspace
from reconbench_app.vendor_resolution import VendorResolutionService
from reconbench_core.normalize.gstin import calculate_gstin_checksum
from sqlalchemy.engine import Engine, make_url
from tests.integration.test_ingestion import _seed_workspace


def _gstin(state: str, pan: str) -> str:
    body = f"{state}{pan}1Z"
    return body + calculate_gstin_checksum(body)


SUPPLIER = _gstin("27", "AAPFU0939F")
OTHER = _gstin("27", "AABCS1234K")


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    dsn = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    if not (make_url(dsn).database or "").startswith("reconbench_phase2_"):
        raise RuntimeError("AC-4 checks require a disposable reconbench_phase2_* database")
    created = sa.create_engine(dsn)
    yield created
    created.dispose()


def _hash(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()


def _workspace(engine: Engine) -> ActorContext:
    workspace_id, user_id = _seed_workspace(engine)
    return ActorContext(workspace_id=workspace_id, actor_id=user_id, actor_role="manager")


def _document(
    engine: Engine,
    actor: ActorContext,
    *,
    gstin: str | None,
    name: str | None,
    gstin_confidence: str = "0.98",
    name_confidence: str = "0.65",
    method: str = "pdf_ocr",
) -> UUID:
    file_id, document_id, version_id = new_id(), new_id(), new_id()
    with engine.begin() as connection:
        connection.execute(
            sa.insert(metadata.tables["source_file"]).values(
                id=file_id,
                workspace_id=actor.workspace_id,
                sha256=_hash("file", file_id),
                display_name="synthetic.pdf",
                zone="invoice",
                zone_uploaded="invoice",
                format="pdf",
                size_bytes=1,
                object_key=f"synthetic/{file_id}",
            )
        )
        connection.execute(
            sa.insert(metadata.tables["document"]).values(
                id=document_id,
                workspace_id=actor.workspace_id,
                doc_type="invoice",
                natural_key=_hash("doc", document_id),
                current_version_id=version_id,
            )
        )
        connection.execute(
            sa.insert(metadata.tables["document_version"]).values(
                id=version_id,
                workspace_id=actor.workspace_id,
                document_id=document_id,
                seq=1,
                content_hash=_hash("version", version_id),
                extraction_method=method,
                source_file_id=file_id,
                parser_version="synthetic",
                vendor_gstin=gstin,
                vendor_name=name,
            )
        )
        for field_name, value, confidence in (
            ("vendor_gstin", gstin, gstin_confidence),
            ("vendor_name", name, name_confidence),
        ):
            if value is None:
                continue
            connection.execute(
                sa.insert(metadata.tables["field_evidence"]).values(
                    id=new_id(),
                    workspace_id=actor.workspace_id,
                    document_version_id=version_id,
                    field_name=field_name,
                    value_text=value,
                    confidence=Decimal(confidence),
                    source="rule",
                )
            )
    return document_id


def _vendors(engine: Engine, actor: ActorContext) -> list[dict[str, Any]]:
    vendor = metadata.tables["vendor"]
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.select(*vendor.c)
                .where(vendor.c.workspace_id == actor.workspace_id, vendor.c.merged_into.is_(None))
                .order_by(vendor.c.created_at, vendor.c.id)
            ).mappings()
        ]


def _aliases(engine: Engine, actor: ActorContext) -> set[str]:
    alias = metadata.tables["vendor_alias"]
    with engine.connect() as connection:
        return set(
            connection.scalars(
                sa.select(alias.c.alias_raw).where(alias.c.workspace_id == actor.workspace_id)
            )
        )


def test_gstin_without_name_creates_pending_vendor_and_a_later_name_fills_it(
    engine: Engine,
) -> None:
    actor = _workspace(engine)
    resolution = VendorResolutionService(engine)
    first = _document(engine, actor, gstin=SUPPLIER, name=None)

    summary = resolution.resolve_workspace(actor.workspace_id)

    [vendor] = _vendors(engine, actor)
    assert summary.vendors_created == 1
    assert (vendor["canonical_name"], vendor["name_status"]) == ("", "pending")
    _document(engine, actor, gstin=SUPPLIER, name="Acme Tradrs")
    _document(engine, actor, gstin=SUPPLIER, name="Acme Traders")
    _document(engine, actor, gstin=SUPPLIER, name="Acme Traders")
    resolution.resolve_workspace(actor.workspace_id)

    [vendor] = _vendors(engine, actor)
    assert (vendor["canonical_name"], vendor["name_status"]) == ("Acme Traders", "provisional")
    assert _aliases(engine, actor) == set(), "OCR spellings never become aliases"
    with engine.connect() as connection:
        assigned = connection.scalar(
            sa.select(metadata.tables["document"].c.vendor_id).where(
                metadata.tables["document"].c.id == first
            )
        )
    assert assigned == vendor["id"]


def test_unverified_gstin_creates_no_vendor(engine: Engine) -> None:
    actor = _workspace(engine)
    _document(engine, actor, gstin=SUPPLIER, name="Acme Traders", gstin_confidence="0.70")

    summary = VendorResolutionService(engine).resolve_workspace(actor.workspace_id)

    assert summary.vendors_created == 0
    assert summary.documents_unassigned == 1
    assert _vendors(engine, actor) == []


def test_manual_name_is_kept_and_a_verified_variant_becomes_an_alias(engine: Engine) -> None:
    actor = _workspace(engine)
    resolution = VendorResolutionService(engine)
    mapping = MappingService(engine)
    _document(engine, actor, gstin=SUPPLIER, name="Acme Traders")
    resolution.resolve_workspace(actor.workspace_id)
    [vendor] = _vendors(engine, actor)

    with pytest.raises(VersionConflict):
        mapping.rename_vendor(actor, vendor["id"], name="Acme", expected_version=999)
    with pytest.raises(ValueError, match="label"):
        mapping.rename_vendor(
            actor, vendor["id"], name="(Bill from)", expected_version=vendor["version"]
        )
    renamed = mapping.rename_vendor(
        actor,
        vendor["id"],
        name="Acme Industrial Supplies",
        expected_version=vendor["version"],
    )
    assert renamed is not None and renamed["name_status"] == "manual"
    assert _aliases(engine, actor) == {"Acme Traders"}, "the old readable name stays matchable"

    _document(
        engine,
        actor,
        gstin=SUPPLIER,
        name="ACME TRADERS PVT LTD (PUNE)",
        name_confidence="1",
        method="xml_profile",
    )
    resolution.resolve_workspace(actor.workspace_id)

    [vendor] = _vendors(engine, actor)
    assert (vendor["canonical_name"], vendor["name_status"]) == (
        "Acme Industrial Supplies",
        "manual",
    )
    assert "ACME TRADERS PVT LTD (PUNE)" in _aliases(engine, actor)


def test_person_creates_vendor_for_unregistered_supplier(engine: Engine) -> None:
    actor = _workspace(engine)
    mapping = MappingService(engine)

    created = mapping.create_vendor(actor, name="  Small   Service Works ")
    assert created["canonical_name"] == "Small Service Works"
    assert (created["anchor"], created["name_status"], created["gstins"]) == ("name", "manual", [])
    with_gstin = mapping.create_vendor(actor, name="Other Supplies", gstin=OTHER.lower())
    assert with_gstin["gstins"] == [OTHER] and with_gstin["pan"] == "AABCS1234K"

    with pytest.raises(ValueError, match="already belongs"):
        mapping.create_vendor(actor, name="Duplicate", gstin=OTHER)
    with pytest.raises(ValueError, match="not valid"):
        mapping.create_vendor(actor, name="Broken", gstin=OTHER[:-1] + "0")
    with pytest.raises(ValueError, match="label"):
        mapping.create_vendor(actor, name=SUPPLIER)
    with engine.connect() as connection:
        actions = set(
            connection.scalars(
                sa.select(metadata.tables["audit_log"].c.action).where(
                    metadata.tables["audit_log"].c.workspace_id == actor.workspace_id
                )
            )
        )
    assert actions == {"vendor.create"}


def test_cleanup_repairs_a_pre_ac4_gstin_named_vendor(engine: Engine) -> None:
    actor = _workspace(engine)
    vendor_id = new_id()
    with engine.begin() as connection:
        connection.execute(
            sa.insert(metadata.tables["vendor"]).values(
                id=vendor_id,
                workspace_id=actor.workspace_id,
                canonical_name=SUPPLIER,
                normalized_name=SUPPLIER.casefold(),
                pan="AAPFU0939F",
                anchor="gstin",
                created_by_rule="gstin",
            )
        )
        connection.execute(
            sa.insert(metadata.tables["vendor_gstin"]).values(
                id=new_id(),
                workspace_id=actor.workspace_id,
                vendor_id=vendor_id,
                gstin=SUPPLIER,
                state_code="27",
                pan="AAPFU0939F",
                checksum_ok=True,
            )
        )
        connection.execute(
            sa.insert(metadata.tables["vendor_alias"]).values(
                id=new_id(),
                workspace_id=actor.workspace_id,
                vendor_id=vendor_id,
                alias_raw="(Bill from)",
                alias_norm="bill from",
                source="learned",
            )
        )
    _document(engine, actor, gstin=SUPPLIER, name="Acme Traders")

    dry = clean_workspace(engine, actor, apply=False)
    assert [(item.vendor_id, sorted(item.reasons)) for item in dry] == [
        (vendor_id, ["junk_alias", "name_gstin"])
    ]
    [vendor] = _vendors(engine, actor)
    assert vendor["canonical_name"] == SUPPLIER, "a dry run changes nothing"

    applied = clean_workspace(engine, actor, apply=True)
    assert [item.outcome for item in applied] == ["repaired"]
    [vendor] = _vendors(engine, actor)
    assert (vendor["canonical_name"], vendor["name_status"]) == ("", "pending")
    assert _aliases(engine, actor) == set()

    VendorResolutionService(engine).resolve_workspace(actor.workspace_id)
    [vendor] = _vendors(engine, actor)
    assert (vendor["canonical_name"], vendor["name_status"]) == ("Acme Traders", "provisional")
    assert clean_workspace(engine, actor, apply=False) == []


def test_merge_into_a_pending_vendor_keeps_the_readable_name(engine: Engine) -> None:
    actor = _workspace(engine)
    mapping = MappingService(engine)
    pending = mapping.create_vendor(actor, name="Placeholder Name", gstin=SUPPLIER)
    with engine.begin() as connection:
        connection.execute(
            sa.update(metadata.tables["vendor"])
            .where(metadata.tables["vendor"].c.id == pending["id"])
            .values(canonical_name="", normalized_name="", name_status="pending")
        )
    named = mapping.create_vendor(actor, name="Acme Traders")

    mapping.merge_vendors(actor, source_vendor_id=named["id"], survivor_vendor_id=pending["id"])

    [survivor] = _vendors(engine, actor)
    assert survivor["id"] == pending["id"]
    assert (survivor["canonical_name"], survivor["name_status"]) == ("Acme Traders", "manual")
