"""An invoice or sales receipt exported to QuickBooks Online tells QBO which
lines are taxed (2.18.0).

The export sent no tax code on a line, so QBO taxed the lines by its own
defaults and could charge tax on labour this document left untaxed, or
none on the goods it taxed. Each sales line now goes with its TaxCodeRef:
TAX for a line this document taxes, NON otherwise (a document that charges
no tax taxes none of its lines). No rate goes: QBO works out its own.

The invoice is built as the export builds it, with the python-quickbooks
SDK's own Invoice; its save (the call to QBO) is where the test reads it.
"""

import json

from app.services import qbo_export
from tests.test_qbo_export_discounts import _invoice, items, sent  # noqa: F401


def _codes(invoice):
    return [
        line["SalesItemLineDetail"]["TaxCodeRef"]["value"]
        for line in invoice.Line
        if line["DetailType"] == "SalesItemLineDetail"
    ]


def test_each_line_goes_with_its_tax_code(db_session, items, sent):  # noqa: F811
    _invoice(
        db_session,
        items,
        "2101",
        [("Catering", "Catering", 100, True), (None, "Delivery", 10, False)],
        tax=8.90,
    )
    assert qbo_export.export_invoices(db_session)["errors"] == []
    [invoice] = sent
    assert _codes(invoice) == ["TAX", "NON"]
    body = json.loads(invoice.to_json())
    assert [line["SalesItemLineDetail"]["TaxCodeRef"] for line in body["Line"]] == [
        {"value": "TAX"},
        {"value": "NON"},
    ]
    assert invoice.TxnTaxDetail is None  # no rate: QBO works out its own


def test_a_document_that_charges_no_tax_taxes_no_line(
    db_session, items, sent  # noqa: F811
):
    _invoice(db_session, items, "2102", [("Catering", "Catering", 100, True)])
    qbo_export.export_invoices(db_session)
    assert _codes(sent[0]) == ["NON"]


def test_a_sales_receipt_goes_with_its_tax_codes(db_session, items, sent):  # noqa: F811
    _invoice(
        db_session,
        items,
        "SR-21",
        [("Catering", "Lunch", 30, True), (None, "Tip", 5, False)],
        tax=2.67,
        is_sales_receipt=True,
    )
    qbo_export.export_invoices(db_session)
    [receipt] = sent
    assert type(receipt).__name__ == "SalesReceipt"
    assert _codes(receipt) == ["TAX", "NON"]
