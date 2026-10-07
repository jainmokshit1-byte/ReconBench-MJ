"""XML and CSV parser contracts use synthetic, hostile, and encoded fixtures."""

from __future__ import annotations

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
import reconbench_core.parsers.common as common_parser
import reconbench_core.parsers.csv as csv_parser
import reconbench_core.parsers.xml as xml_parser
from hypothesis import given, settings
from hypothesis import strategies as st
from lxml import etree
from reconbench_core.parsers import ParseResult, ParserHints, parse
from reconbench_core.schema import FieldSource, FileFormat, InvoiceHeader


def _invoice_xml(doc_no: str, vendor: str = "Acer India Pvt Ltd") -> str:
    return f"""<VOUCHER>
      <VOUCHERTYPENAME>Sales Invoice</VOUCHERTYPENAME>
      <VOUCHERNUMBER>{doc_no}</VOUCHERNUMBER>
      <DATE>20260401</DATE>
      <PARTYLEDGERNAME>{vendor}</PARTYLEDGERNAME>
      <PARTYGSTIN>27AAPFU0939F1ZV</PARTYGSTIN>
      <TAXABLETOTAL>100.00</TAXABLETOTAL>
      <CGST>9.00</CGST><SGST>9.00</SGST>
      <VOUCHERTOTAL>118.00</VOUCHERTOTAL>
      <ALLINVENTORYENTRIES.LIST>
        <STOCKITEMNAME>Laptop Acer A515-58 16GB</STOCKITEMNAME>
        <STOCKITEMCODE>ACER-A515</STOCKITEMCODE>
        <ACTUALQTY>1 Nos</ACTUALQTY><RATE>100.00/Nos</RATE><AMOUNT>100.00</AMOUNT>
      </ALLINVENTORYENTRIES.LIST>
      <CUSTOM-TAG>keep this value</CUSTOM-TAG>
    </VOUCHER>"""


def test_tally_xml_parse_extracts_fields_lines_extras_and_locator() -> None:
    result = parse(
        f"<ENVELOPE><BODY>{_invoice_xml('INV-001')}</BODY></ENVELOPE>".encode(),
        {"doc_type": "invoice", "file_format": "xml"},
    )

    assert result.success
    assert result.file_format is FileFormat.XML
    assert len(result.documents) == 1
    document = result.documents[0]
    assert isinstance(document.header, InvoiceHeader)
    assert document.header.doc_no is not None and document.header.doc_no.value == "INV-001"
    assert document.header.vendor_name is not None
    assert document.header.vendor_name.value == "Acer India Pvt Ltd"
    assert len(document.lines) == 1
    assert document.lines[0].quantity is not None
    assert str(document.lines[0].quantity.value) == "1"
    assert document.source_locator == "/ENVELOPE/BODY/VOUCHER"
    assert document.header.extras["CUSTOM-TAG"] == "keep this value"
    assert not document.needs_verification


def test_tally_xml_parser_accepts_utf16_with_bom() -> None:
    result = parse(
        f"<ENVELOPE><BODY>{_invoice_xml('INV-UTF16')}</BODY></ENVELOPE>".encode("utf-16"),
        {"file_format": "xml", "doc_type": "invoice"},
    )

    assert result.success
    assert len(result.documents) == 1
    assert result.documents[0].header.doc_no is not None
    assert result.documents[0].header.doc_no.value == "INV-UTF16"


def test_flat_dbc_xml_groups_vouchers_and_keeps_missing_matching_evidence_in_review() -> None:
    source = b"""<ENVELOPE>
      <DBCFIXED><DBCDATE>01.08.2026</DBCDATE><DBCPARTY>Vendor One</DBCPARTY></DBCFIXED>
      <DBCBUYERNAME>Buyer One</DBCBUYERNAME><DBCVCHNO>PO-101</DBCVCHNO>
      <DBCQTY>3.00 PCS</DBCQTY><DBCRATE/><DBCAMOUNT/><DBCGROSSAMT>30.00</DBCGROSSAMT>
      <DBCLEDAMT>ledger-one</DBCLEDAMT><DBCCUSTOMINFO>opaque</DBCCUSTOMINFO>
      <DBCFIXED><DBCDATE/><DBCPARTY/></DBCFIXED>
      <DBCVCHNO/><DBCQTY>1.00 PCS</DBCQTY><DBCRATE>10.00/PCS</DBCRATE>
      <DBCAMOUNT>-10.00</DBCAMOUNT><DBCALTUNITS/><DBCGROSSAMT/>
      <DBCFIXED><DBCDATE/><DBCPARTY/></DBCFIXED>
      <DBCVCHNO/><DBCQTY>2.00 PCS</DBCQTY><DBCRATE>10.00/PCS</DBCRATE>
      <DBCAMOUNT>-20.00</DBCAMOUNT><DBCGROSSAMT/>
      <DBCFIXED><DBCDATE>02.08.2026</DBCDATE><DBCPARTY>Vendor Two</DBCPARTY></DBCFIXED>
      <DBCBUYERNAME>Buyer Two</DBCBUYERNAME><DBCVCHNO>PO-202</DBCVCHNO>
      <DBCQTY>1 PCS</DBCQTY><DBCRATE/><DBCAMOUNT/><DBCGROSSAMT>5.00</DBCGROSSAMT>
      <DBCFIXED><DBCDATE/><DBCPARTY/></DBCFIXED>
      <DBCVCHNO/><DBCQTY>1 PCS</DBCQTY><DBCRATE>5.00/PCS</DBCRATE>
      <DBCAMOUNT>-5.00</DBCAMOUNT><DBCGROSSAMT/>
    </ENVELOPE>"""

    result = parse(source, {"file_format": "xml", "doc_type": "po"})

    assert result.success
    assert len(result.documents) == 2
    first, second = result.documents
    assert first.header.doc_no is not None and first.header.doc_no.value == "PO-101"
    assert first.header.doc_date is not None
    assert str(first.header.doc_date.value) == "2026-08-01"
    assert first.header.grand_total is not None
    assert first.header.grand_total.value == Decimal("30.00")
    assert len(first.lines) == 2
    assert first.lines[0].quantity is not None
    assert first.lines[0].quantity.value == Decimal("1.00")
    assert first.lines[0].rate is not None and first.lines[0].rate.value == Decimal("10.00")
    assert first.lines[0].uom is not None and first.lines[0].uom.value == "NOS"
    assert first.lines[0].extras["dbc_quantity_raw"] == "1.00 PCS"
    assert first.lines[0].description is None
    assert first.lines[0].amount is not None
    assert first.lines[0].amount.value == Decimal("10.00")
    assert first.lines[0].extras["dbc_accounting_amount_raw"] == "-10.00"
    assert first.lines[0].extras["dbc_amount_reconciles_to_quantity_times_rate"] is True
    assert first.header.extras["dbc_line_descriptions_present"] is False
    assert first.header.extras["dbc_source_fields_raw"][0]["fields"]["DBCCUSTOMINFO"] == ["opaque"]
    assert first.needs_verification
    assert "ITEM_UNSURE" in {reason.value for reason in first.reason_codes}
    assert second.header.doc_no is not None and second.header.doc_no.value == "PO-202"
    assert second.header.vendor_name is not None
    assert second.header.vendor_name.value == "Vendor Two"
    assert any(warning.code == "DBC_LINE_DESCRIPTION_MISSING" for warning in result.warnings)
    assert any(warning.code == "DBC_PO_REFERENCE_MISSING" for warning in result.warnings)


def test_flat_dbc_xml_uses_inventory_row_party_as_item_description() -> None:
    source = b"""<ENVELOPE>
      <DBCFIXED><DBCDATE>03-Apr-26</DBCDATE><DBCPARTY>Supplier One</DBCPARTY></DBCFIXED>
      <DBCBUYERNAME>Buyer One</DBCBUYERNAME><DBCVCHNO>PO-101</DBCVCHNO>
      <DBCGSTIN>27ABCDE1234F1Z5</DBCGSTIN><DBCGROSSAMT>1180</DBCGROSSAMT>
      <DBCFIXED><DBCDATE/><DBCPARTY>500 GB SSD WD Blue</DBCPARTY></DBCFIXED>
      <DBCVCHNO/><DBCQTY>1 PCS</DBCQTY><DBCRATE>1000/PCS</DBCRATE>
      <DBCAMOUNT>-1000</DBCAMOUNT><DBCGROSSAMT/>
    </ENVELOPE>"""

    result = parse(source, {"file_format": "xml", "doc_type": "po"})

    assert result.success
    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.lines[0].description is not None
    assert document.lines[0].description.value == "500 GB SSD WD Blue"
    assert document.lines[0].amount is not None
    assert document.lines[0].amount.value == Decimal("1000")
    assert document.header.extras["dbc_line_descriptions_present"] is True
    assert not any(warning.code == "DBC_LINE_DESCRIPTION_MISSING" for warning in result.warnings)


def test_flat_dbc_xml_requires_upload_zone_and_never_infers_invoice_po_link() -> None:
    source = (
        b"<ENVELOPE><DBCFIXED><DBCDATE>01.08.2026</DBCDATE>"
        b"<DBCPARTY>Vendor</DBCPARTY></DBCFIXED><DBCVCHNO>INV-1</DBCVCHNO>"
        b"<DBCGROSSAMT>10.00</DBCGROSSAMT><DBCQTY>1 PCS</DBCQTY>"
        b"<DBCRATE>10/PCS</DBCRATE></ENVELOPE>"
    )
    missing_type = parse(source, {"file_format": "xml"})
    invoice = parse(source, {"file_format": "xml", "doc_type": "invoice"})

    assert not missing_type.success
    assert missing_type.reason_code == "DOC_TYPE_REQUIRED"
    assert invoice.success
    assert invoice.documents[0].needs_verification
    assert {reason.value for reason in invoice.documents[0].reason_codes} >= {
        "ITEM_UNSURE",
        "NO_PO_LINK",
    }
    assert invoice.documents[0].header.po_refs is None


def test_xml_layout_signature_is_stable_across_python_hash_seeds() -> None:
    source = _invoice_xml("INV-HASH-SEED").encode("utf-8")
    command = (
        "from reconbench_core.parsers import parse; "
        "import sys; "
        "print(parse(sys.stdin.buffer.read(), "
        "{'doc_type': 'invoice', 'file_format': 'xml'}).layout_signature)"
    )
    signatures = []
    for seed in ("11", "29"):
        result = subprocess.run(
            [sys.executable, "-c", command],
            input=source,
            cwd=Path(__file__).parents[2],
            env=dict(os.environ, PYTHONHASHSEED=seed),
            capture_output=True,
            check=True,
            timeout=15,
        )
        signatures.append(result.stdout.decode("ascii").strip())

    assert signatures[0] == signatures[1]


def test_xml_splits_multiple_documents_and_uses_generic_tag_names() -> None:
    xml = (
        "<Documents>"
        "<Invoice><InvoiceNo>INV-1</InvoiceNo><InvoiceDate>01/04/2026</InvoiceDate>"
        "<Supplier>One Vendor</Supplier><InvoiceTotal>10</InvoiceTotal>"
        "<Line><Description>Widget</Description><Qty>1</Qty><Rate>10</Rate></Line></Invoice>"
        "<Invoice><InvoiceNo>INV-2</InvoiceNo><InvoiceDate>01/04/2026</InvoiceDate>"
        "<Supplier>Two Vendor</Supplier><InvoiceTotal>20</InvoiceTotal>"
        "<Line><Description>Gadget</Description><Qty>2</Qty><Rate>10</Rate></Line></Invoice>"
        "</Documents>"
    )

    result = parse(xml.encode(), {"file_format": "xml"})

    assert result.success
    assert [
        document.header.doc_no.value for document in result.documents if document.header.doc_no
    ] == [
        "INV-1",
        "INV-2",
    ]
    assert result.documents[0].source_locator == "/Documents/Invoice[1]"
    assert result.documents[1].source_locator == "/Documents/Invoice[2]"
    assert result.documents[0].confidence == Decimal("0.80")


def test_xml_rejects_doctype_and_malformed_input_without_raising() -> None:
    hostile = (
        b'<!DOCTYPE x [<!ENTITY boom "expanded">]><Invoice><InvoiceNo>&boom;</InvoiceNo></Invoice>'
    )
    doctype_result = parse(hostile, {"file_format": "xml", "doc_type": "invoice"})
    malformed_result = parse(b"<Invoice><InvoiceNo>broken</Invoice>", {"file_format": "xml"})

    assert not doctype_result.success
    assert doctype_result.reason_code == "XML_DOCTYPE_FORBIDDEN"
    assert not malformed_result.success
    assert malformed_result.reason_code == "XML_PARSE_ERROR"


def test_xml_size_record_type_and_custom_xpath_boundaries() -> None:
    oversized = parse(
        b"<Invoice><InvoiceNo>INV-X</InvoiceNo></Invoice>",
        {"file_format": "xml", "max_bytes": 8},
    )
    no_records = parse(b"<Envelope><Header>test</Header></Envelope>", {"file_format": "xml"})
    custom = parse(
        (
            b"<Invoice><Number>INV-XPATH</Number><Date>2026-04-01</Date>"
            b"<Supplier>Vendor</Supplier><InvoiceTotal>10</InvoiceTotal>"
            b"<Line><Description>Widget</Description><Qty>1</Qty><Rate>10</Rate></Line>"
            b"</Invoice>"
        ),
        {
            "file_format": "xml",
            "custom_xpaths": {"doc_no": "./Number", "doc_date": "./Date"},
        },
    )

    assert oversized.reason_code == "FILE_TOO_LARGE"
    assert no_records.reason_code == "NO_DOCUMENTS"
    assert custom.success
    assert custom.documents[0].header.doc_no is not None
    assert custom.documents[0].header.doc_no.value == "INV-XPATH"


def test_xml_requires_type_when_record_type_cannot_be_inferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(xml_parser, "_document_type", lambda _element, _hints: None)

    result = parse(b"<VOUCHER><DATE>20260101</DATE></VOUCHER>", {"file_format": "xml"})

    assert not result.success
    assert result.reason_code == "DOC_TYPE_REQUIRED"


def test_xml_field_helpers_keep_nested_values_and_exclude_line_scope() -> None:
    root = etree.fromstring(
        b"<Root><DocNo>A</DocNo><DocNo>B</DocNo><DocNo>B</DocNo>"
        b"<PORef>P1</PORef><PORef>P2</PORef>"
        b"<Unknown>one</Unknown><Unknown><Nested>x</Nested><Nested>y</Nested></Unknown>"
        b"<Known><Deep>kept</Deep></Known><Known><Deep>ignored duplicate</Deep></Known>"
        b"<Line><Item><Description>line value</Description></Item></Line></Root>"
    )
    line_nodes = set(xml_parser._line_nodes(root))
    alternatives: dict[str, list[str]] = {}

    raw = xml_parser._raw_fields(
        root,
        {"doc_no", "po_refs"},
        {"DOCNO": "doc_no", "POREF": "po_refs"},
        line_nodes=line_nodes,
        alternatives=alternatives,
    )
    extras = xml_parser._extras(root, {"DOCNO", "POREF", "KNOWN", "LINE", "ITEM"})
    excluded = xml_parser._extras(root, set(), excluded={root[0]})

    assert xml_parser._tag_name(etree.Comment("comment")) == ""
    assert raw == {"doc_no": "A", "po_refs": "P1;P2"}
    assert alternatives == {"doc_no": ["B"]}
    assert len(line_nodes) == 1
    assert extras["Unknown"][0] == "one"
    assert extras["Unknown"][1] == {"Nested": ["x", "y"]}
    assert extras["Deep"] == "kept"
    assert excluded["DocNo"] == ["B", "B"]
    assert xml_parser._inside_line_node(root.find(".//Description"), line_nodes)
    assert "ignored duplicate" not in str(extras)


def test_xml_custom_xpath_accepts_scalar_elements_and_empty_values() -> None:
    root = etree.fromstring(b"<Root><Number>INV-9</Number><Empty> </Empty></Root>")

    assert xml_parser._custom_xpath_value(root, "count(./Number)") == "1.0"
    assert xml_parser._custom_xpath_value(root, "./Number") == "INV-9"
    assert xml_parser._custom_xpath_value(root, "./Empty") is None
    assert xml_parser._custom_xpath_value(root, "./Missing") is None


def test_xml_infers_purchase_order_and_grn_types_without_upload_zone() -> None:
    po = parse(
        b"<PurchaseOrder><OrderNo>PO-AUTO</OrderNo><Supplier>Vendor</Supplier></PurchaseOrder>",
        {"file_format": "xml"},
    )
    grn = parse(
        b"<GoodsReceiptNote><ReceiptNo>GRN-AUTO</ReceiptNo><Supplier>Vendor</Supplier>"
        b"</GoodsReceiptNote>",
        {"file_format": "xml"},
    )

    assert po.success and po.documents[0].header.doc_type.value == "po"
    assert grn.success and grn.documents[0].header.doc_type.value == "grn"


def test_xml_unwrapped_line_fallback_and_unknown_type_raises() -> None:
    unwrapped = parse(
        b"<Invoice><InvoiceNo>INV-UNWRAPPED</InvoiceNo><Supplier>Vendor</Supplier>"
        b"<Description>Widget</Description><Qty>2</Qty><Rate>5</Rate></Invoice>",
        {"file_format": "xml"},
    )
    unknown = etree.fromstring(b"<UnknownRecord/>")

    assert unwrapped.success and len(unwrapped.documents[0].lines) == 1
    with pytest.raises(ValueError, match="document type"):
        xml_parser._parse_one(unknown, ParserHints(), {}, set(), ())


def test_xml_bad_custom_xpath_and_all_failed_records_return_reviewable_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml = b"<Invoice><InvoiceNo>INV-X</InvoiceNo><Supplier>Vendor</Supplier></Invoice>"
    bad_xpath = parse(
        xml,
        {
            "file_format": "xml",
            "custom_xpaths": {"doc_no": "//*["},
        },
    )

    def fail_record(*_args: object, **_kwargs: object) -> None:
        raise ValueError("bad record")

    monkeypatch.setattr(xml_parser, "_parse_one", fail_record)
    no_documents = parse(xml, {"file_format": "xml"})

    assert bad_xpath.success
    assert bad_xpath.documents[0].header.doc_no is not None
    assert bad_xpath.documents[0].header.doc_no.value == "INV-X"
    assert not no_documents.success
    assert no_documents.reason_code == "DOC_TYPE_REQUIRED"


def test_parser_common_decimal_confidence_and_synonym_helpers() -> None:
    warnings = []
    assert (
        common_parser._parse_decimal_field(
            None,
            "rate",
            source=FieldSource.RULE,
            confidence=Decimal("0.9"),
            warnings=warnings,
            locator="row:1",
        )
        is None
    )
    tax_rate = common_parser._parse_decimal_field(
        "18 %",
        "tax_rate",
        source=FieldSource.RULE,
        confidence=Decimal("0.9"),
        warnings=warnings,
        locator="row:1",
    )
    unit_rate = common_parser._parse_decimal_field(
        "12.50/NOS",
        "rate",
        source=FieldSource.RULE,
        confidence=Decimal("0.9"),
        warnings=warnings,
        locator="row:2",
    )
    invalid = common_parser._parse_decimal_field(
        "not-money",
        "rate",
        source=FieldSource.RULE,
        confidence=Decimal("0.9"),
        warnings=warnings,
        locator="row:3",
    )
    field = common_parser._field(
        "invoice",
        confidence=Decimal("0.8"),
        source=FieldSource.RULE,
        snippet="invoice",
    )

    assert tax_rate is not None and tax_rate.value == Decimal("18")
    assert unit_rate is not None and unit_rate.value == Decimal("12.50")
    assert invalid is None
    assert [warning.code for warning in warnings] == ["INVALID_NUMBER"]
    assert common_parser._reduce_confidence(None, Decimal("0.1")) is None
    assert common_parser._raise_confidence(None, Decimal("0.1")) is None
    assert common_parser._reduce_confidence(field, Decimal("1")).confidence == 0
    assert common_parser._raise_confidence(field, Decimal("1")).confidence == 1
    assert common_parser.combined_synonyms({"doc_no": ("INV", "INV")})["doc_no"][-1] == "INV"
    assert common_parser._is_ambiguous_slash_date("03/04/2026")
    assert not common_parser._is_ambiguous_slash_date("13/04/2026")


@pytest.mark.parametrize(
    "exception_type,warning_code",
    [(ValueError, "DOC_TYPE_REQUIRED"), (RuntimeError, "RECORD_PARSE_ERROR")],
)
def test_xml_record_errors_are_converted_to_warnings(
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[Exception],
    warning_code: str,
) -> None:
    original = xml_parser._parse_one
    calls = 0

    def fail_first_record(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise exception_type("synthetic record failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(xml_parser, "_parse_one", fail_first_record)
    xml = f"<Documents>{_invoice_xml('INV-1')}{_invoice_xml('INV-2')}</Documents>"

    result = parse(xml.encode(), {"file_format": "xml"})

    assert result.success
    assert len(result.documents) == 1
    assert any(warning.code == warning_code for warning in result.warnings)


def test_generic_csv_splits_repeated_document_lines_and_preserves_unknown_columns() -> None:
    source = (
        "Invoice No,Invoice Date,Supplier,Grand Total,Description,Qty,Rate,Amount,Unit,Legacy Tag\n"
        "INV-10,01/04/2026,Café Supply,118.00,Widget alpha,1,100.00,100.00,PCS,extra-a\n"
        "INV-10,01/04/2026,Café Supply,118.00,Widget beta,1,100.00,100.00,PCS,extra-b\n"
        "INV-11,02/04/2026,Other Supply,236.00,Gadget,2,100.00,200.00,NOS,extra-c\n"
    ).encode("cp1252")
    content = b"\xef\xbb\xbf" + source.decode("cp1252").encode("utf-8")

    result = parse(content, {"file_format": "csv", "doc_type": "invoice"})

    assert result.success
    assert len(result.documents) == 2
    first, second = result.documents
    assert first.header.doc_no is not None and first.header.doc_no.value == "INV-10"
    assert len(first.lines) == 2
    assert first.source_locator == "rows:2-3"
    assert first.lines[0].extras["Legacy Tag"] == "extra-a"
    assert second.header.doc_no is not None and second.header.doc_no.value == "INV-11"
    assert second.lines[0].uom is not None and second.lines[0].uom.value == "NOS"


def test_csv_windows_1252_semicolon_and_required_header_mapping() -> None:
    cp1252_csv = (
        "Invoice No;Invoice Date;Supplier;Invoice Total;Description;Qty;Rate\n"
        "INV-20;12-Sep-26;Café Supply;50.00;Cable;1;50.00\n"
    ).encode("cp1252")
    parsed = parse(cp1252_csv, {"file_format": "csv", "doc_type": "invoice"})
    unknown = parse(b"one,two,three\na,b,c\n", {"file_format": "csv"})
    second_layout = parse(b"number,issued_to,net_amount\n1,vendor,10\n", {"file_format": "csv"})

    assert parsed.success
    assert parsed.documents[0].header.vendor_name is not None
    assert parsed.documents[0].header.vendor_name.value == "Café Supply"
    assert parsed.documents[0].source_locator == "rows:2-2"
    assert not unknown.success
    assert unknown.reason_code == "CSV_MAPPING_REQUIRED"
    assert unknown.layout_signature != second_layout.layout_signature


def test_csv_rejects_empty_header_only_unknown_type_and_ambiguous_fields() -> None:
    empty = parse(b"", {"file_format": "csv"})
    oversized = parse(b"a,b,c", {"file_format": "csv", "max_bytes": 2})
    header_only = parse(
        b"Invoice No,Invoice Date,Supplier,Grand Total,Description,Qty,Rate\n",
        {"file_format": "csv", "doc_type": "invoice"},
    )
    unknown_type = parse(
        b"Date,Vendor,Description\n2026-04-01,Vendor,Widget\n",
        {"file_format": "csv"},
    )
    ambiguous = parse(
        b"Invoice No,Invoice Date,Supplier,Taxable Value,Description,Qty,Rate\n"
        b"INV-1,2026-04-01,Vendor,10,Widget,1,10\n",
        {"file_format": "csv", "doc_type": "invoice"},
    )

    assert empty.reason_code == "EMPTY_CSV"
    assert oversized.reason_code == "FILE_TOO_LARGE"
    assert header_only.reason_code == "EMPTY_CSV"
    assert unknown_type.reason_code == "DOC_TYPE_REQUIRED"
    assert ambiguous.reason_code == "CSV_MAPPING_REQUIRED"


def test_csv_custom_column_mapping_and_document_type_column() -> None:
    source = (
        b"Kind,Our Number,Our Date,Party,Total,Item,Units,Unit Price,Line Amount\n"
        b"invoice,INV-41,2026-04-01,Vendor,10,Widget,1,10,10\n"
    )
    mapping = {
        "Our Number": "doc_no",
        "Our Date": "doc_date",
        "Party": "vendor_name",
        "Total": "grand_total",
        "Item": "description",
        "Units": "quantity",
        "Unit Price": "rate",
        "Line Amount": "amount",
        "Kind": "doc_type",
    }

    result = parse(source, {"file_format": "csv", "column_mapping": mapping})

    assert result.success
    assert result.documents[0].header.doc_no is not None
    assert result.documents[0].header.doc_no.value == "INV-41"


def test_csv_document_type_inference_and_row_ranges() -> None:
    from reconbench_core.schema import DocType

    aliases = {"TYPE": "doc_type"}
    assert csv_parser._document_type(None, ["Purchase Order No"], [], {}) is DocType.PO
    assert csv_parser._document_type(None, ["Receipt No"], [], {}) is DocType.GRN
    assert csv_parser._document_type(None, ["Type"], [["grn"]], aliases) is DocType.GRN
    assert csv_parser._document_type(None, ["Type"], [["unknown"]], aliases) is None
    assert csv_parser._document_type(None, ["Invoice No"], [], {}) is DocType.INVOICE
    assert csv_parser._document_type(DocType.PO, [], [], {}) is DocType.PO
    assert csv_parser._row_ranges([]) == "rows:0-0"
    assert csv_parser._row_ranges([5, 2, 3, 3, 9]) == "rows:2-3,5-5,9-9"


def test_csv_preserves_invalid_field_warnings_and_uses_safe_fallbacks() -> None:
    source = (
        b"PO No,Order Date,Supplier,Vendor GSTIN,Status,Currency,Taxable Total,Grand Total,"
        b"Line No,Description,Qty,Rate,Amount,UOM,Line Type,PO Ref\n"
        b"PO-1,not-a-date,Vendor,27AAPFU0939F1ZW,unknown,inr,broken,10,0,Widget,1,"
        b"bad,10,carton,unknown,PO-2;PO-3\n"
    )

    result = parse(source, {"file_format": "csv"})

    assert result.success
    document = result.documents[0]
    assert document.header.doc_date is None
    assert document.header.currency is not None and document.header.currency.value == "INR"
    assert document.header.status is None
    assert document.lines[0].line_no == 1
    assert document.lines[0].rate is None
    assert document.lines[0].uom is not None and document.lines[0].uom.value == "carton"
    assert document.lines[0].po_ref is not None
    assert document.lines[0].po_ref.value == "PO-2;PO-3"
    assert {warning.code for warning in result.warnings} >= {
        "INVALID_DATE",
        "INVALID_NUMBER",
        "GSTIN_INVALID",
        "INVALID_STATUS",
        "INVALID_LINE_NO",
        "UOM_UNKNOWN",
        "INVALID_LINE_TYPE",
    }


def test_grn_qty_only_is_assumed_accepted_with_warning() -> None:
    source = (
        b"GRN No,GRN Date,Supplier,Description,Qty,Unit\nGRN-1,01/04/2026,Vendor,Widget,4,NOS\n"
    )
    result = parse(source, {"file_format": "csv", "doc_type": "grn"})
    line = result.documents[0].lines[0]

    assert result.success
    assert line.received_qty is not None and str(line.received_qty.value) == "4"
    assert line.accepted_qty is not None and str(line.accepted_qty.value) == "4"
    assert any(warning.code == "GRN_QTY_ASSUMED_ACCEPTED" for warning in result.warnings)


def test_csv_retries_duplicate_numeric_reading_when_arithmetic_selects_one() -> None:
    source = (
        b"Invoice No,Invoice Date,Supplier,Invoice Total,Description,Qty,Rate,Unit Rate,Amount\n"
        b"INV-30,01/04/2026,Vendor,10,Widget,1,100,10,10\n"
    )

    result = parse(source, {"file_format": "csv", "doc_type": "invoice"})

    assert result.success
    line = result.documents[0].lines[0]
    assert line.rate is not None and str(line.rate.value) == "10"
    assert any(warning.code == "EXTRACTION_QA_ALTERNATIVE_SELECTED" for warning in result.warnings)
    assert not any(warning.code == "EXTRACTION_QA_FAILED" for warning in result.warnings)


def test_xml_retries_duplicate_numeric_tag_when_arithmetic_selects_one() -> None:
    xml = (
        "<Invoice><InvoiceNo>INV-32</InvoiceNo><InvoiceDate>2026-04-01</InvoiceDate>"
        "<Supplier>Vendor</Supplier><InvoiceTotal>10</InvoiceTotal>"
        "<Line><Description>Widget</Description><Qty>1</Qty><Rate>100</Rate>"
        "<Rate>10</Rate><Amount>10</Amount></Line></Invoice>"
    )

    result = parse(xml.encode(), {"file_format": "xml"})

    assert result.success
    line = result.documents[0].lines[0]
    assert line.rate is not None and str(line.rate.value) == "10"
    assert any(warning.code == "EXTRACTION_QA_ALTERNATIVE_SELECTED" for warning in result.warnings)


def test_xml_uses_header_scope_to_map_po_reference() -> None:
    xml = (
        "<Invoice><InvoiceNo>INV-33</InvoiceNo><InvoiceDate>2026-04-01</InvoiceDate>"
        "<Supplier>Vendor</Supplier><PORef>PO-33</PORef><InvoiceTotal>10</InvoiceTotal>"
        "<Line><Description>Widget</Description><Qty>1</Qty><Rate>10</Rate></Line></Invoice>"
    )

    result = parse(xml.encode(), {"file_format": "xml"})

    assert result.success
    assert isinstance(result.documents[0].header, InvoiceHeader)
    assert result.documents[0].header.po_refs is not None
    assert result.documents[0].header.po_refs.value == ("PO-33",)


def test_failed_arithmetic_lowers_confidence_and_flags_invoice() -> None:
    source = (
        b"Invoice No,Invoice Date,Supplier,Invoice Total,Description,Qty,Rate,Unit Rate,Amount\n"
        b"INV-31,01/04/2026,Vendor,10,Widget,1,100,110,10\n"
    )

    result = parse(source, {"file_format": "csv", "doc_type": "invoice"})

    assert result.success
    document = result.documents[0]
    assert document.needs_verification
    assert any(warning.code == "EXTRACTION_QA_FAILED" for warning in result.warnings)
    assert document.lines[0].quantity is not None
    assert document.lines[0].quantity.confidence < 0.70


def test_missing_invoice_critical_fields_require_verification() -> None:
    source = b"Invoice Date,Supplier,Description,Qty,Rate\n01/04/2026,Vendor,Widget,1,10\n"

    result = parse(source, {"file_format": "csv", "doc_type": "invoice"})

    assert result.success
    assert result.documents[0].needs_verification
    assert result.documents[0].confidence == Decimal("0")
    assert "LOW_CONFIDENCE_DATA" in {reason.value for reason in result.documents[0].reason_codes}


@settings(max_examples=100, deadline=None)
@given(st.binary(max_size=256))
def test_arbitrary_xml_and_csv_bytes_never_escape_parser_boundary(content: bytes) -> None:
    result = parse(content)

    assert isinstance(result, ParseResult)
    assert result.file_format in {FileFormat.XML, FileFormat.CSV}


def test_csv_type_comes_from_the_documents_own_number_not_its_po_reference() -> None:
    grn = (
        b"GRN No,GRN Date,Purchase Order,Vendor GSTIN,Description,Received Qty,Accepted Qty\n"
        b"GRN-1,04/09/2026,PO-1,27AAACS1234A1Z2,Hex Bolt,10,10\n"
    )
    invoice = (
        b"Invoice No,Invoice Date,Purchase Order,Vendor GSTIN,Description,Quantity,Rate,Amount\n"
        b"INV-1,05/09/2026,PO-1,27AAACS1234A1Z2,Hex Bolt,10,100,1000\n"
    )
    po = (
        b"PO No,Order Date,Vendor GSTIN,Description,Quantity,Rate,Amount\n"
        b"PO-1,01/09/2026,27AAACS1234A1Z2,Hex Bolt,10,100,1000\n"
    )

    for content, expected in ((grn, "grn"), (invoice, "invoice"), (po, "po")):
        result = parse(content, {"file_format": "csv"})
        assert result.success, result.reason_code
        assert {str(document.header.doc_type) for document in result.documents} == {expected}
