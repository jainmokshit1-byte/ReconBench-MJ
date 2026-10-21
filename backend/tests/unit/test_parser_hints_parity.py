"""The accuracy bench builds exactly the parser hints that upload builds (scan fix WI-1)."""

import copy
from typing import Any

import pytest
from reconbench_app.db.seed_data import DEFAULT_CONFIG
from reconbench_app.ingestion import IngestionLimits, parser_hints_from_config
from reconbench_core.schema import DocType, FileFormat
from scripts.accuracy_bench import config_body, parser_hints
from tests.support.layouts import gstin


def _custom_body() -> dict[str, Any]:
    body = copy.deepcopy(DEFAULT_CONFIG)
    body["engine"]["buyer_entities"] = [
        {"name": "Synthetic Buyer Pvt Ltd", "gstin": gstin("27AABCS1234Q1Z")}
    ]
    body["engine"]["uom"]["aliases"] = {"PCS.": "NOS", "BOXES": "BOX"}
    body["engine"]["gst_slabs"] = [{"rate": "5"}, {"rate": "18"}]
    body["app"]["limits"]["max_pages"] = 7
    return body


@pytest.mark.parametrize("body", [DEFAULT_CONFIG, _custom_body()], ids=["seed", "custom"])
@pytest.mark.parametrize("zone", [DocType.INVOICE, DocType.PO, DocType.GRN])
@pytest.mark.parametrize("file_format", [FileFormat.PDF, FileFormat.XML])
def test_bench_and_upload_build_identical_hints(
    body: dict[str, Any], zone: DocType, file_format: FileFormat
) -> None:
    upload = parser_hints_from_config(
        body,
        zone_type=zone,
        file_format=file_format,
        limits=IngestionLimits.model_validate(body["app"]["limits"]),
    )

    assert parser_hints(zone, file_format, body) == upload


def test_custom_config_reaches_the_hints() -> None:
    hints = parser_hints(DocType.INVOICE, FileFormat.PDF, _custom_body())

    assert [entity.gstin for entity in hints.buyer_entities] == [gstin("27AABCS1234Q1Z")]
    assert hints.uom_aliases["BOXES"] == "BOX"
    assert set(hints.gst_rates) == {5, 18}
    assert hints.max_pages == 7


def test_bench_buyer_entity_modes() -> None:
    truth = type("T", (), {"buyer_entities": ({"name": "B", "gstin": "X"},)})()

    assert config_body(truth, "truth")["engine"]["buyer_entities"] == [{"name": "B", "gstin": "X"}]
    assert config_body(truth, "none")["engine"]["buyer_entities"] == []
    assert (
        config_body(truth, "config")["engine"]["buyer_entities"]
        == DEFAULT_CONFIG["engine"]["buyer_entities"]
    )
    with pytest.raises(ValueError):
        config_body(truth, "bogus")
