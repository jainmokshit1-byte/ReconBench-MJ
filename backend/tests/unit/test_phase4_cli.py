"""The engine CLI is deterministic and fails visibly on files it cannot parse."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from reconbench_core.cli import main
from reconbench_core.version import ENGINE_VERSION


def _write_synthetic_bundle(folder: Path) -> Path:
    (folder / "po").mkdir(parents=True)
    (folder / "grn").mkdir()
    (folder / "invoice").mkdir()
    config = folder.parent / "engine-config.json"
    config.write_text(
        json.dumps(
            {
                "engine": {
                    "payable_rate_mode": "pay_at_po_rate",
                    "tax_rates": ["5", "12", "18", "28"],
                },
                "parser_hints": {},
            }
        ),
        encoding="utf-8",
    )
    (folder / "po" / "po.xml").write_text(
        """<PurchaseOrder>
          <OrderNo>PO-SYN-1</OrderNo><OrderDate>2026-04-01</OrderDate>
          <Supplier>Example Components</Supplier><SupplierGSTIN>27AAPFU0939F1ZV</SupplierGSTIN>
          <Status>Open</Status>
          <Line><Description>Steel Washer M8</Description><ItemCode>W-M8</ItemCode>
            <Qty>10</Qty><Rate>100</Rate><Amount>1000</Amount><Unit>NOS</Unit></Line>
        </PurchaseOrder>""",
        encoding="utf-8",
    )
    (folder / "grn" / "grn.xml").write_text(
        """<GRN>
          <GRNNo>GRN-SYN-1</GRNNo><GRNDate>2026-04-02</GRNDate>
          <Supplier>Example Components</Supplier><SupplierGSTIN>27AAPFU0939F1ZV</SupplierGSTIN>
          <POReference>PO-SYN-1</POReference>
          <Line><Description>Steel Washer M8</Description><ItemCode>W-M8</ItemCode>
            <ReceivedQty>10</ReceivedQty><AcceptedQty>10</AcceptedQty><Unit>NOS</Unit></Line>
        </GRN>""",
        encoding="utf-8",
    )
    (folder / "invoice" / "invoice.xml").write_text(
        """<Invoice>
          <InvoiceNo>INV-SYN-1</InvoiceNo><InvoiceDate>2026-04-03</InvoiceDate>
          <Supplier>Example Components</Supplier><SupplierGSTIN>27AAPFU0939F1ZV</SupplierGSTIN>
          <POReference>PO-SYN-1</POReference>
          <TaxableValue>1000</TaxableValue><CGST>90</CGST><SGST>90</SGST>
          <InvoiceTotal>1180</InvoiceTotal>
          <Line><Description>Steel Washer M8</Description><ItemCode>W-M8</ItemCode>
            <Qty>10</Qty><Rate>100</Rate><Amount>1000</Amount><Unit>NOS</Unit>
            <GSTRate>18</GSTRate></Line>
        </Invoice>""",
        encoding="utf-8",
    )
    return config


def test_cli_parses_links_evaluates_and_writes_byte_stable_json(tmp_path: Path) -> None:
    folder = tmp_path / "input"
    config = _write_synthetic_bundle(folder)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    args = [
        "run",
        str(folder),
        "--config",
        str(config),
        "--run-date",
        "2026-04-10",
    ]

    assert main([*args, "--out", str(first)]) == 0
    assert main([*args, "--out", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()

    report = json.loads(first.read_text(encoding="utf-8"))
    assert report["engine_version"] == ENGINE_VERSION
    assert len(report["rules_hash"]) == 64
    assert report["document_count"] == 3
    assert report["case_count"] == 1
    case = report["cases"][0]
    assert case["engine_version"] == ENGINE_VERSION
    assert case["rules_hash"] == report["rules_hash"]
    assert case["po_nos"] == ["PO-SYN-1"]
    assert case["status"] != "unlinked"
    assert case["lines"][0]["engine_version"] == ENGINE_VERSION
    assert case["lines"][0]["rules_hash"] == report["rules_hash"]


def test_cli_emits_partial_report_and_nonzero_exit_for_unparseable_file(tmp_path: Path) -> None:
    folder = tmp_path / "input"
    folder.mkdir()
    config = tmp_path / "config.json"
    config.write_text('{"engine": {}}', encoding="utf-8")
    (folder / "broken.xml").write_text("<Invoice>", encoding="utf-8")
    output = tmp_path / "report.json"

    exit_code = main(
        [
            "run",
            str(folder),
            "--config",
            str(config),
            "--run-date",
            "2026-04-10",
            "--out",
            str(output),
        ]
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["document_count"] == 0
    assert report["file_errors"][0]["code"] == "XML_PARSE_ERROR"


def test_cli_skips_duplicate_file_bytes_in_the_same_parser_zone(tmp_path: Path) -> None:
    folder = tmp_path / "input"
    config = _write_synthetic_bundle(folder)
    invoice = folder / "invoice" / "invoice.xml"
    (folder / "invoice" / "invoice-copy.xml").write_bytes(invoice.read_bytes())
    output = tmp_path / "report.json"

    exit_code = main(
        [
            "run",
            str(folder),
            "--config",
            str(config),
            "--run-date",
            "2026-04-10",
            "--out",
            str(output),
        ]
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["document_count"] == 3
    skipped = [item for item in report["files"] if item["status"] == "duplicate_bytes_skipped"]
    assert len(skipped) == 1


@pytest.mark.parametrize(
    ("contents", "expected_error"),
    [
        ("[]", "configuration root must be a JSON object"),
        ('{"body": []}', "configuration body must be a JSON object"),
        ('{"engine": []}', "configuration engine must be a JSON object"),
        ('{"parser_hints": []}', "parser_hints must be a JSON object"),
    ],
)
def test_cli_rejects_invalid_configuration_shapes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], contents: str, expected_error: str
) -> None:
    folder = tmp_path / "input"
    folder.mkdir()
    config = tmp_path / "config.json"
    config.write_text(contents, encoding="utf-8")

    exit_code = main(
        [
            "run",
            str(folder),
            "--config",
            str(config),
            "--run-date",
            "2026-04-10",
            "--out",
            str(tmp_path / "report.json"),
        ]
    )

    assert exit_code == 2
    assert expected_error in capsys.readouterr().err


def test_cli_rejects_invalid_per_zone_parser_hints(tmp_path: Path) -> None:
    folder = tmp_path / "input"
    (folder / "invoice").mkdir(parents=True)
    (folder / "invoice" / "invoice.xml").write_text(
        "<Invoice><InvoiceNo>INV-1</InvoiceNo></Invoice>", encoding="utf-8"
    )
    config = tmp_path / "config.json"
    config.write_text('{"engine": {}, "parser_hints": {"invoice": "wrong"}}', encoding="utf-8")
    output = tmp_path / "report.json"

    exit_code = main(
        [
            "run",
            str(folder),
            "--config",
            str(config),
            "--run-date",
            "2026-04-10",
            "--out",
            str(output),
        ]
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["file_errors"][0]["code"] == "PARSER_HINTS_INVALID"


def test_cli_reports_unreadable_files_and_empty_supported_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "input"
    folder.mkdir()
    (folder / "readme.txt").write_text("ignored file", encoding="utf-8")
    (folder / "one.xml").write_text("<Invoice/>", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text('{"engine": {}}', encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def fail_input_read(path: Path) -> bytes:
        if path.name == "one.xml":
            raise OSError("synthetic read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_input_read)
    output = tmp_path / "report.json"
    exit_code = main(
        [
            "run",
            str(folder),
            "--config",
            str(config),
            "--run-date",
            "2026-04-10",
            "--out",
            str(output),
        ]
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["document_count"] == 0
    assert report["file_errors"][0]["code"] == "FILE_READ_FAILED"


def test_cli_reports_missing_folder_without_writing_a_result(tmp_path: Path, capsys) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"engine": {}}', encoding="utf-8")
    output = tmp_path / "report.json"

    exit_code = main(
        [
            "run",
            str(tmp_path / "missing"),
            "--config",
            str(config),
            "--run-date",
            "2026-04-10",
            "--out",
            str(output),
        ]
    )

    assert exit_code == 2
    assert not output.exists()
    assert "input folder does not exist" in capsys.readouterr().err
