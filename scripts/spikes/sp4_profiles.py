"""Synthetic Tally-style XML and text-only CSV profile/header detection spike."""

from __future__ import annotations

import csv
import io
from xml.etree import ElementTree

from lxml import etree

XML_BYTES = b"""<?xml version='1.0' encoding='UTF-8'?>
<ENVELOPE><BODY><DATA><TALLYMESSAGE><VOUCHER VCHTYPE='Purchase'>
<DATE>20260928</DATE><VOUCHERNUMBER>SYN-PO-0142</VOUCHERNUMBER>
<PARTYLEDGERNAME>Example Components Private Limited</PARTYLEDGERNAME>
<ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>Monitor 24 inch</STOCKITEMNAME>
<BILLEDQTY>10 Nos</BILLEDQTY><RATE>48000/Nos</RATE></ALLINVENTORYENTRIES.LIST>
</VOUCHER></TALLYMESSAGE></DATA></BODY></ENVELOPE>"""
CSV_TEXT = "PO No,Item,Qty,Rate\n0142,Monitor 24 inch,10,48000.00\n"


def main() -> int:
    parser = etree.XMLParser(
        resolve_entities=False, load_dtd=False, no_network=True, recover=False
    )
    root = etree.fromstring(XML_BYTES, parser=parser)
    xml_tags = {element.tag for element in root.iter()}
    required_tags = {
        "VOUCHER",
        "VOUCHERNUMBER",
        "ALLINVENTORYENTRIES.LIST",
        "BILLEDQTY",
    }
    if not required_tags.issubset(xml_tags):
        raise SystemExit(
            f"Synthetic Tally tag profile did not detect: {sorted(required_tags - xml_tags)}"
        )

    reader = csv.DictReader(io.StringIO(CSV_TEXT, newline=""))
    rows = list(reader)
    if (
        reader.fieldnames != ["PO No", "Item", "Qty", "Rate"]
        or rows[0]["PO No"] != "0142"
    ):
        raise SystemExit(
            "Synthetic CSV profile failed to retain headers or leading-zero document numbers."
        )

    # ElementTree import is used as a second-parser compatibility guard for ordinary XML values.
    stdlib_root = ElementTree.fromstring(XML_BYTES)
    if stdlib_root.findtext(".//VOUCHERNUMBER") != "SYN-PO-0142":
        raise SystemExit(
            "Synthetic XML sample did not survive the stdlib parser check."
        )
    print(
        "PASS: synthetic Tally XML and CSV header/text profiles detected; "
        "no real samples were read."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
