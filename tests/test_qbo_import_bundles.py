"""A QuickBooks Online bundle comes across as the lines of its items
(2.18.0).

The import skipped a bundle (GroupLineDetail) on an invoice or sales
receipt, so the document's lines fell short of QBO's total by the bundle,
the import log said so, and an edit here posted the bundle's amount to the
income account. Now each item in the bundle is a line of its own (item,
quantity, price, amount and taxable flag as QBO has them), so the lines add
up to QBO's total, each item's income goes to its own account, and the
income balancing line is left for a line of a kind the import doesn't know.
When QBO's bundle amount differs from its items', the difference is a line
named for the bundle.

Built through the python-quickbooks SDK's own objects, as the import reads
them: an invoice's bundle keeps its inner lines as plain dicts, as a sales
receipt keeps all of its lines.
"""

from decimal import Decimal

import pytest

from app.models.items import Item, ItemType
from app.models.qbo_mapping import QBOMapping
from app.models.transactions import TransactionLine
from tests.test_qbo_import_tax import (
    Sales,
    _document,
    _line,
    _save_as_the_form_does,
    _tax,
)

ON_NO_LINE = "Part of the QuickBooks Online total on no line here"


def _item_line(n, qbo_item, name, qty, price, code):
    line = _line(n, qty * price, name, code, qty=qty)
    line["SalesItemLineDetail"]["ItemRef"] = {"value": qbo_item, "name": name}
    return line


def _bundle(n, parts, amount=None, name="Kit"):
    line = {
        "Id": str(n),
        "Description": None,
        "DetailType": "GroupLineDetail",
        "GroupLineDetail": {
            "GroupItemRef": {"value": "40", "name": name},
            "Quantity": 1,
            "Line": parts,
        },
    }
    if amount is not None:
        line["Amount"] = amount
    return {k: v for k, v in line.items() if v is not None}


# A pump pair (2 x 15.00, taxed) and a hose (10.00, not): 40.00.
KIT = [
    _item_line(11, "21", "Pump", 2, 15, "TAX"),
    _item_line(12, "22", "Hose", 1, 10, "NON"),
]


class Bundles(Sales):
    """Sales, with QBO's Pump (21, sold to Product Sales) and Hose (22, to
    Material Income) imported as items."""

    def __init__(self, db, seed_accounts, monkeypatch):
        super().__init__(db, seed_accounts, monkeypatch)
        self.items = {}
        for qbo_id, name, account in [("21", "Pump", "4100"), ("22", "Hose", "4200")]:
            item = Item(
                name=name,
                item_type=ItemType.SERVICE,
                rate=Decimal("0"),
                income_account_id=seed_accounts[account].id,
            )
            db.add(item)
            db.flush()
            db.add(QBOMapping(entity_type="item", qbo_id=qbo_id, slowbooks_id=item.id))
            self.items[name] = item.id
        db.commit()

    def lines(self, number):
        invoice = self.get(number)
        return [
            (ln.item_id, ln.description, ln.quantity, ln.rate, ln.is_taxable)
            for ln in sorted(invoice.lines, key=lambda ln: ln.line_order)
        ]


@pytest.fixture
def books(db_session, seed_accounts, monkeypatch):
    return Bundles(db_session, seed_accounts, monkeypatch)


def _adds_up(invoice):
    return sum((ln.amount for ln in invoice.lines), Decimal("0")) == invoice.subtotal


def test_a_bundle_comes_across_as_the_lines_of_its_items(books):
    """100.00 of catering and the 40.00 kit, 130.00 of it taxed at 8.9%."""
    lines = [_line(1, 100, "Catering", "TAX"), _bundle(2, KIT, amount=40)]
    books.invoices(
        _document("170", "1070", lines, 151.57, _tax(11.57, (8.9, 130, 11.57)))
    )
    pump, hose = books.items["Pump"], books.items["Hose"]
    assert books.lines("1070") == [
        (None, "Catering", Decimal("1"), Decimal("100"), True),
        (pump, "Pump", Decimal("2"), Decimal("15"), True),
        (hose, "Hose", Decimal("1"), Decimal("10"), False),
    ]
    invoice = books.get("1070")
    assert _adds_up(invoice)
    assert invoice.tax_rate == Decimal("0.089")


def test_a_bundle_priced_apart_from_its_items_carries_the_difference(client, books):
    """QBO's kit reads 35.00 where its items come to 40.00: a -5.00 line
    named for the kit, untaxed (the hose isn't), that an edit keeps."""
    lines = [_line(1, 100, "Catering", "NON"), _bundle(2, KIT, amount=35)]
    books.invoices(_document("171", "1071", lines, 135, None))
    assert books.lines("1071")[-1] == (None, "Kit", Decimal("1"), Decimal("-5"), False)
    invoice = books.get("1071")
    assert _adds_up(invoice)
    saved = _save_as_the_form_does(client, invoice.id, notes="Call first")
    assert Decimal(str(saved["total"])) == Decimal("135")


def test_a_bundle_on_a_sales_receipt_comes_across(books):
    lines = [_bundle(1, KIT, amount=40)]
    books.sales_receipts(
        _document("172", "SR-17", lines, 42.67, _tax(2.67, (8.9, 30, 2.67)))
    )
    receipt = books.get("SR-17")
    assert [ln[1:] for ln in books.lines("SR-17")] == [
        ("Pump", Decimal("2"), Decimal("15"), True),
        ("Hose", Decimal("1"), Decimal("10"), False),
    ]
    assert _adds_up(receipt)
    assert receipt.tax_rate == Decimal("0.089")
    assert receipt.payment_allocations[0].payment.amount == Decimal("42.67")


def test_an_adopted_invoice_books_each_bundle_item_to_its_own_income(client, books):
    lines = [_line(1, 100, "Catering", "NON"), _bundle(2, KIT, amount=40)]
    books.invoices(_document("173", "1073", lines, 140, None))
    invoice = books.get("1073")
    r = client.put(f"/api/invoices/{invoice.id}", json={"date": "2026-08-02"})
    assert r.status_code == 200, r.text
    assert books.balance("1100") == Decimal("140.00")
    assert books.balance("4000") == Decimal("100.00")
    assert books.balance("4100") == Decimal("30.00")  # the pumps
    assert books.balance("4200") == Decimal("10.00")  # the hose
    said = [ln.description for ln in books.db.query(TransactionLine)]
    assert ON_NO_LINE not in said


def test_a_reimport_brings_a_bundle_qbo_changed_up_to_date(books):
    lines = [_bundle(1, KIT, amount=40)]
    books.invoices(_document("174", "1074", lines, 40, None))
    three = [_item_line(11, "21", "Pump", 3, 15, "TAX"), KIT[1]]
    changed = _document("174", "1074", [_bundle(1, three, amount=55)], 55, None)
    books.invoices(changed | {"SyncToken": "1"})
    invoice = books.get("1074")
    assert books.lines("1074")[0][1:4] == ("Pump", Decimal("3"), Decimal("15"))
    assert (invoice.total, invoice.balance_due) == (Decimal("55"), Decimal("55"))
    assert _adds_up(invoice)


def test_a_document_imported_before_gets_its_bundle_items_on_the_next_import(
    books,
):
    """Imported when bundles were skipped: QBO's total, the catering line
    only. QBO hasn't changed it since; the next import brings the kit."""
    catering = [_line(1, 100, "Catering", "NON")]
    books.invoices(_document("175", "1075", catering, 140, None))
    assert not _adds_up(books.get("1075"))
    books.invoices(
        _document("175", "1075", catering + [_bundle(2, KIT, amount=40)], 140, None)
    )
    invoice = books.get("1075")
    assert [ln[1] for ln in books.lines("1075")] == ["Catering", "Pump", "Hose"]
    assert (invoice.total, invoice.balance_due) == (Decimal("140"), Decimal("140"))
    assert _adds_up(invoice)
