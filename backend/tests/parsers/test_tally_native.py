"""Native Tally "Import Data" exports (WI-IMP1, D95). Synthetic vouchers only."""

from __future__ import annotations

from decimal import Decimal

import pytest
from reconbench_core.parsers import ParseResult, parse
from reconbench_core.parsers.tally import sanitize
from reconbench_core.schema import DocType, WarnLevel

MARK = "&#4;"


def _rates(rate: str, *, igst: bool = False) -> str:
    half = str(Decimal(rate) / 2)
    heads = [("CGST", "" if igst else half), ("SGST/UTGST", "" if igst else half), ("IGST", rate)]
    return "".join(
        f"<RATEDETAILS.LIST><GSTRATEDUTYHEAD>{head}</GSTRATEDUTYHEAD>"
        f"<GSTRATEVALUATIONTYPE>{MARK} Based on Value</GSTRATEVALUATIONTYPE>"
        + (f"<GSTRATE> {value}</GSTRATE>" if value else "")
        + "</RATEDETAILS.LIST>"
        for head, value in heads
    )


def _line(
    item: str,
    user: list[str],
    qty: str,
    rate: str,
    amount: str,
    *,
    order: str | None = None,
    deemed: str = "Yes",
    gst: str = "18",
    igst: bool = False,
) -> str:
    users = "".join(f"<BASICUSERDESCRIPTION>{text}</BASICUSERDESCRIPTION>" for text in user)
    batch = (
        f"<BATCHALLOCATIONS.LIST><ORDERNO>{order}</ORDERNO></BATCHALLOCATIONS.LIST>"
        if order
        else ""
    )
    return (
        "<ALLINVENTORYENTRIES.LIST>"
        + (
            f'<BASICUSERDESCRIPTION.LIST TYPE="String">{users}</BASICUSERDESCRIPTION.LIST>'
            if user
            else ""
        )
        + f"<STOCKITEMNAME>{item}</STOCKITEMNAME>"
        + f"<GSTOVRDNINELIGIBLEITC>{MARK} Not Applicable</GSTOVRDNINELIGIBLEITC>"
        + f"<ISDEEMEDPOSITIVE>{deemed}</ISDEEMEDPOSITIVE>"
        + f"<RATE>{rate}/Nos</RATE><AMOUNT>{amount}</AMOUNT>"
        + f"<ACTUALQTY> {qty} Nos</ACTUALQTY><BILLEDQTY> {qty} Nos</BILLEDQTY>"
        + batch
        + _rates(gst, igst=igst)
        + "</ALLINVENTORYENTRIES.LIST>"
    )


def _ledger(name: str, amount: str, *, party: bool = False) -> str:
    return (
        f"<LEDGERENTRIES.LIST><LEDGERNAME>{name}</LEDGERNAME>"
        f"<ISPARTYLEDGER>{'Yes' if party else 'No'}</ISPARTYLEDGER>"
        f"<AMOUNT>{amount}</AMOUNT></LEDGERENTRIES.LIST>"
    )


def _voucher(
    kind: str,
    number: str,
    lines: list[str],
    ledgers: list[str],
    *,
    reference: str = "",
    extra: str = "",
) -> str:
    return (
        f'<VOUCHER VCHTYPE="{kind}" ACTION="Create">'
        f"<DATE>20260805</DATE><VOUCHERTYPENAME>{kind}</VOUCHERTYPENAME>"
        f"<VOUCHERNUMBER>{number}</VOUCHERNUMBER><PARTYLEDGERNAME>Vendor One</PARTYLEDGERNAME>"
        f"<PARTYGSTIN>27AAPFU0939F1ZV</PARTYGSTIN><REFERENCE>{reference}</REFERENCE>"
        f"<VCHGSTCLASS>{MARK} Not Applicable</VCHGSTCLASS>{extra}"
        + "".join(lines)
        + "".join(ledgers)
        + "</VOUCHER>"
    )


def _envelope(*vouchers: str, master: str = "") -> bytes:
    messages = "".join(f'<TALLYMESSAGE xmlns:UDF="TallyUDF">{v}</TALLYMESSAGE>' for v in vouchers)
    masters = f'<TALLYMESSAGE xmlns:UDF="TallyUDF">{master}</TALLYMESSAGE>' if master else ""
    text = (
        "<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA>"
        "<REQUESTDESC><REPORTNAME>All Masters</REPORTNAME></REQUESTDESC>"
        f"<REQUESTDATA>{masters}{messages}</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"
    )
    return text.encode("utf-16")


def _po(number: str = "PO/26-27/08-001", **kwargs: object) -> str:
    return _voucher(
        "Purchase Order",
        number,
        [
            _line("AMC", ["Annual maintenance of", "two printers"], "2", "500.00", "-1000.00"),
            _line("NETWORKING / CABLING", [], "1", "250.00", "-250.00"),
        ],
        [
            _ledger("Vendor One", "1475.00", party=True),
            _ledger("Input CGST 9%", "-112.50"),
            _ledger("Input SGST 9%", "-112.50"),
        ],
        reference=number,
        **kwargs,  # type: ignore[arg-type]
    )


def _parse(content: bytes, **hints: object) -> ParseResult:
    return parse(content, {"file_format": "xml", **hints})


def test_a_native_export_with_markers_and_masters_parses_every_voucher() -> None:
    master = (
        f'<STOCKITEM NAME="AMC"><PARENT>{MARK} Primary</PARENT><NOTE>bad\x05char</NOTE></STOCKITEM>'
    )
    content = _envelope(_po("PO/26-27/08-001"), _po("PO/26-27/08-002"), master=master)
    result = _parse(content)

    assert result.success and [d.header.doc_type for d in result.documents] == [DocType.PO] * 2
    assert [d.header.doc_no.value for d in result.documents if d.header.doc_no] == [
        "PO/26-27/08-001",
        "PO/26-27/08-002",
    ]
    removed = [w for w in result.warnings if w.code == "XML_INVALID_CHARACTERS_REMOVED"]
    by_locator = {w.locator: w for w in removed}
    for document in result.documents:
        warning = by_locator[document.source_locator]
        assert warning.level is WarnLevel.INFO and "GSTOVRDNINELIGIBLEITC" in warning.message
    masters_note = by_locator[None]
    assert "masters are ignored" in masters_note.message and "Removed 2 " in masters_note.message


def test_a_file_that_parses_today_is_read_without_sanitising() -> None:
    clean = _envelope(_po().replace(MARK, ""))
    assert sanitize(clean) is None
    result = _parse(clean)
    assert result.success
    assert not any(w.code == "XML_INVALID_CHARACTERS_REMOVED" for w in result.warnings)


def test_only_forbidden_references_go_and_a_value_hit_is_a_warning() -> None:
    voucher = _po().replace(
        "<VOUCHERNUMBER>PO/26-27/08-001", f"<VOUCHERNUMBER>PO/26-27/{MARK}08-001"
    )
    voucher = voucher.replace("Vendor One</PARTYLEDGERNAME>", "Vendor&#10;One</PARTYLEDGERNAME>")
    result = _parse(_envelope(voucher))
    [document] = result.documents
    assert document.header.doc_no is not None and document.header.doc_no.value == "PO/26-27/08-001"
    [warning] = [w for w in result.warnings if w.code == "XML_INVALID_CHARACTERS_REMOVED"]
    assert warning.level is WarnLevel.WARNING and "VOUCHERNUMBER 1" in warning.message


def test_lines_take_the_user_description_and_keep_the_item_name() -> None:
    [document] = _parse(_envelope(_po())).documents
    first, second = document.lines
    assert first.description is not None
    assert first.description.value == "Annual maintenance of two printers"
    assert first.extras["stock_item_name"] == "AMC"
    assert second.description is not None and second.description.value == "NETWORKING / CABLING"
    assert first.tax_rate is not None and first.tax_rate.value == Decimal("18")


def test_negative_debit_amounts_become_magnitudes_and_each_change_is_logged() -> None:
    result = _parse(_envelope(_po()))
    [document] = result.documents
    assert [line.amount.value for line in document.lines if line.amount] == [
        Decimal("1000.00"),
        Decimal("250.00"),
    ]
    [warning] = [w for w in result.warnings if w.code == "TALLY_SIGN_NORMALISED"]
    assert warning.locator == document.source_locator
    assert "line 1 amount '-1000.00'" in warning.message
    assert "line 2 amount '-250.00'" in warning.message
    assert not any(w.code == "EXTRACTION_QA_FAILED" for w in result.warnings)


def test_a_credit_line_without_the_debit_flag_keeps_its_sign() -> None:
    voucher = _voucher(
        "Purchase Order",
        "PO/26-27/08-003",
        [_line("AMC", ["Service"], "1", "100.00", "-100.00", deemed="No")],
        [],
    )
    result = _parse(_envelope(voucher))
    [document] = result.documents
    assert document.lines[0].amount is not None and document.lines[0].amount.value < 0
    assert not any(w.code == "TALLY_SIGN_NORMALISED" for w in result.warnings)


def test_header_totals_come_from_the_party_and_tax_ledgers_when_they_close() -> None:
    result = _parse(_envelope(_po()))
    [document] = result.documents
    header = document.header
    assert (header.taxable_total and header.taxable_total.value) == Decimal("1250.00")
    assert (header.cgst and header.cgst.value) == Decimal("112.50")
    assert (header.sgst and header.sgst.value) == Decimal("112.50")
    assert (header.grand_total and header.grand_total.value) == Decimal("1475.00")
    assert header.igst is None and header.round_off is None
    assert any(w.code == "TALLY_TOTALS_FROM_LEDGERS" for w in result.warnings)
    assert not any(w.code.startswith("GST_") for w in result.warnings)


def test_igst_and_round_off_ledgers() -> None:
    voucher = _voucher(
        "Purchase Order",
        "PO/26-27/08-004",
        [_line("AMC", ["Service"], "1", "99.50", "-99.50", igst=True)],
        [
            _ledger("Vendor One", "118.00", party=True),
            _ledger("Input IGST 18%", "-17.91"),
            _ledger("Round Off", "-0.59"),
        ],
    )
    [document] = _parse(_envelope(voucher)).documents
    header = document.header
    assert (header.igst and header.igst.value) == Decimal("17.91")
    assert (header.round_off and header.round_off.value) == Decimal("0.59")
    assert (header.grand_total and header.grand_total.value) == Decimal("118.00")
    assert document.lines[0].tax_rate is not None
    assert document.lines[0].tax_rate.value == Decimal("18")


@pytest.mark.parametrize(
    "ledgers",
    [
        [_ledger("Vendor One", "2000.00", party=True), _ledger("Input CGST 9%", "-112.50")],
        [_ledger("Vendor One", "1475.00", party=True), _ledger("Freight", "-225.00")],
    ],
)
def test_totals_that_do_not_close_give_only_the_voucher_total(ledgers: list[str]) -> None:
    voucher = _voucher(
        "Purchase Order",
        "PO/26-27/08-005",
        [_line("AMC", ["Service"], "1", "1250.00", "-1250.00")],
        ledgers,
    )
    result = _parse(_envelope(voucher))
    [document] = result.documents
    assert document.header.grand_total is not None and document.header.taxable_total is None
    assert any(w.code == "TALLY_TOTALS_NOT_CLOSED" for w in result.warnings)


def test_a_receipt_note_is_a_grn_whose_reference_and_order_numbers_are_po_refs() -> None:
    voucher = _voucher(
        "Receipt Note",
        "GRN/BCSL/25-26/08-010",
        [
            _line("AMC", ["Service"], "1", "100.00", "-100.00", order="PO/26-27/08-001"),
            _line("AMC", ["Spare"], "1", "50.00", "-50.00", order=f"{MARK} Not Applicable"),
        ],
        [_ledger("Vendor One", "150.00", party=True)],
        reference="PO/26-27/08-001",
    )
    result = _parse(_envelope(voucher))
    [document] = result.documents
    assert document.header.doc_type is DocType.GRN
    assert document.header.po_refs is not None
    assert document.header.po_refs.value == ("PO/26-27/08-001",)
    assert document.lines[0].po_ref is not None
    assert document.lines[0].po_ref.value == "PO/26-27/08-001"
    assert document.lines[1].po_ref is None
    assert not any(w.code == "TALLY_GRN_MULTIPLE_PO_REFS" for w in result.warnings)


def test_a_receipt_note_with_two_orders_is_flagged() -> None:
    voucher = _voucher(
        "Receipt Note",
        "GRN/BCSL/25-26/08-011",
        [
            _line("AMC", ["Service"], "1", "100.00", "-100.00", order="PO/26-27/08-001"),
            _line("AMC", ["Spare"], "1", "50.00", "-50.00", order="PO/26-27/08-002"),
        ],
        [_ledger("Vendor One", "150.00", party=True)],
        reference="PO/26-27/08-001",
    )
    result = _parse(_envelope(voucher))
    [document] = result.documents
    assert document.header.po_refs is not None
    assert document.header.po_refs.value == ("PO/26-27/08-001", "PO/26-27/08-002")
    [flag] = [w for w in result.warnings if w.code == "TALLY_GRN_MULTIPLE_PO_REFS"]
    assert flag.level is WarnLevel.WARNING and flag.locator == document.source_locator


def test_a_custom_po_refs_layout_still_wins() -> None:
    voucher = _voucher(
        "Receipt Note",
        "GRN/BCSL/25-26/08-012",
        [_line("AMC", ["Service"], "1", "100.00", "-100.00", order="PO/26-27/08-001")],
        [_ledger("Vendor One", "100.00", party=True)],
        reference="PO/26-27/08-009",
    )
    result = _parse(_envelope(voucher), custom_xpaths={"po_refs": "REFERENCE"})
    [document] = result.documents
    assert document.header.po_refs is not None
    assert document.header.po_refs.value == ("PO/26-27/08-009",)
    assert any(w.code == "TALLY_GRN_MULTIPLE_PO_REFS" for w in result.warnings)


def test_a_purchase_orders_own_reference_is_not_a_po_ref() -> None:
    [document] = _parse(_envelope(_po())).documents
    assert document.header.doc_type is DocType.PO
    assert getattr(document.header, "po_refs", None) is None


def test_a_marker_in_a_tag_read_only_in_another_scope_is_a_note() -> None:
    # A workspace maps "Indent No" to po_refs (a header field); Tally writes the marker in
    # the line entries' INDENTNO, which no line field reads: an info note, no value.
    line = _line("AMC", ["Service"], "1", "100.00", "-100.00", order="PO/26-27/08-001")
    line = line.replace(
        "</ALLINVENTORYENTRIES.LIST>",
        f"<BATCHALLOCATIONS.LIST><INDENTNO>{MARK} Not Applicable</INDENTNO>"
        "</BATCHALLOCATIONS.LIST></ALLINVENTORYENTRIES.LIST>",
    )
    voucher = _voucher(
        "Receipt Note",
        "GRN/BCSL/25-26/08-013",
        [line],
        [_ledger("Vendor One", "100.00", party=True)],
        reference="PO/26-27/08-001",
    )
    result = _parse(_envelope(voucher), synonyms={"po_refs": ["Indent No"]})
    [document] = result.documents
    [note] = [w for w in result.warnings if w.code == "XML_INVALID_CHARACTERS_REMOVED"]
    assert note.level is WarnLevel.INFO and "INDENTNO" in note.message
    assert document.header.po_refs is not None
    assert document.header.po_refs.value == ("PO/26-27/08-001",)


def test_a_tally_system_value_never_becomes_a_po_reference() -> None:
    voucher = _voucher(
        "Receipt Note",
        "GRN/BCSL/25-26/08-014",
        [_line("AMC", ["Service"], "1", "100.00", "-100.00", order="PO/26-27/08-001")],
        [_ledger("Vendor One", "100.00", party=True)],
        reference="PO/26-27/08-001",
        extra=f"<INDENTNO>{MARK} Not Applicable</INDENTNO>",
    )
    result = _parse(_envelope(voucher), synonyms={"po_refs": ["Indent No"]})
    [document] = result.documents
    assert document.header.po_refs is not None
    assert document.header.po_refs.value == ("PO/26-27/08-001",)
    # The marker sat in a tag the header reads: the removal is a warning.
    [note] = [w for w in result.warnings if w.code == "XML_INVALID_CHARACTERS_REMOVED"]
    assert note.level is WarnLevel.WARNING
