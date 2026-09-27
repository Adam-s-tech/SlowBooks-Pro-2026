"""A QuickBooks Online discount comes across with its invoice or sales
receipt (2.18.0).

The import brought a QBO document's sales lines and QBO's totals, but not
its discount line (DiscountLineDetail): the lines added up to more than
the document, and an edit here re-totalled it without the discount, or
(2.18.0's first answer) posted the difference to the income account. Now
the discount is a negative line on a Discount item whose income account is
QBO's discount account, so the lines add up to QBO's subtotal, tax and
total, an edit keeps them, the document's own posting books the discount
where QBO did, and the Sales Tax report sees QBO's taxable amount.

Built through the python-quickbooks SDK's own objects, as the import reads
them: an invoice's lines are typed, a sales receipt's are plain dicts.
"""

from datetime import date
from decimal import Decimal

import pytest
from quickbooks.objects.invoice import Invoice as QBOInvoice

from app.models.accounts import Account, AccountType
from app.models.contacts import Customer
from app.models.items import Item, ItemType
from app.models.qbo_mapping import QBOMapping
from app.models.transactions import TransactionLine
from app.services import qbo_import, qbo_progress
from app.services.bank_register import gl_balances
from tests.test_qbo_import_tax import (
    Sales,
    _document,
    _line,
    _save_as_the_form_does,
    _tax,
)

DAY = date(2026, 8, 3)
SUBTOTAL = {"Amount": 0, "DetailType": "SubTotalLineDetail", "SubTotalLineDetail": {}}
ON_NO_LINE = "Part of the QuickBooks Online total on no line here"


def _discount(n, amount, percent=None, account="86"):
    detail = {
        "PercentBased": percent is not None,
        "DiscountAccountRef": {"value": account, "name": "Discounts given"},
    }
    if percent is not None:
        detail["DiscountPercent"] = percent
    return {
        "Id": str(n),
        "Amount": amount,
        "DetailType": "DiscountLineDetail",
        "DiscountLineDetail": detail,
    }


def _discounted(qbo_id, number, lines, total, tax=None, after=None):
    document = _document(qbo_id, number, lines, total, tax)
    if after is not None:
        document["ApplyTaxAfterDiscount"] = after
    return document


class Discounts(Sales):
    """Sales, with QBO's "Discounts given" (QBO account 86) imported."""

    def __init__(self, db, seed_accounts, monkeypatch):
        super().__init__(db, seed_accounts, monkeypatch)
        self.given = Account(
            name="Discounts given",
            account_number="4950",
            account_type=AccountType.INCOME,
            balance=Decimal("0"),
        )
        db.add(self.given)
        db.flush()
        db.add(
            QBOMapping(entity_type="account", qbo_id="86", slowbooks_id=self.given.id)
        )
        db.commit()

    def lines(self, number):
        invoice = self.get(number)
        return [
            (ln.description, ln.rate, ln.is_taxable)
            for ln in sorted(invoice.lines, key=lambda ln: ln.line_order)
        ]

    def given_balance(self):
        return gl_balances(self.db, [self.given.id])[self.given.id]

    def get_customer_id(self):
        return self.db.query(Customer).filter_by(name="Acme Diner").one().id


@pytest.fixture
def books(db_session, seed_accounts, monkeypatch):
    return Discounts(db_session, seed_accounts, monkeypatch)


def _adds_up(invoice):
    return sum((ln.amount for ln in invoice.lines), Decimal("0")) == invoice.subtotal


def test_an_amount_discount_comes_across_on_the_discount_item(books):
    lines = [_line(1, 100, "Catering", "NON"), SUBTOTAL | {"Amount": 100}]
    books.invoices(_discounted("150", "1050", lines + [_discount(3, 15)], 85))
    invoice = books.get("1050")
    assert books.lines("1050") == [
        ("Catering", Decimal("100"), False),
        ("Discount", Decimal("-15"), False),
    ]
    assert (invoice.subtotal, invoice.total) == (Decimal("85"), Decimal("85"))
    assert _adds_up(invoice)
    item = books.db.get(Item, invoice.lines[1].item_id)
    assert item.name == "Discount"
    assert item.income_account_id == books.given.id


def test_a_percent_discount_comes_across_as_its_amount(books):
    lines = [_line(1, 40, "Catering", "NON"), _line(2, 20, "Linens", "NON")]
    books.invoices(
        _discounted("151", "1051", lines + [_discount(3, 6, percent=10)], 54)
    )
    assert books.lines("1051")[-1] == ("Discount 10%", Decimal("-6"), False)
    assert _adds_up(books.get("1051"))


def _sales_tax_row(client, number):
    r = client.get("/api/reports/sales-tax?start_date=2026-08-01&end_date=2026-08-31")
    assert r.status_code == 200, r.text
    [row] = [row for row in r.json()["items"] if row["number"] == number]
    return Decimal(str(row["taxable"])), Decimal(str(row["tax_amount"]))


def test_tax_after_the_discount_is_on_the_discounted_amount(client, books):
    """100.00 taxed, 10% off, then 8.9% on the 90.00: 8.01."""
    lines = [_line(1, 100, "Catering", "TAX")]
    tax = _tax(8.01, (8.9, 90, 8.01))
    books.invoices(
        _discounted("152", "1052", lines + [_discount(2, 10, 10)], 98.01, tax, True)
    )
    invoice = books.get("1052")
    assert books.lines("1052")[-1] == ("Discount 10%", Decimal("-10"), True)
    assert invoice.tax_rate == Decimal("0.089")
    assert _sales_tax_row(client, "1052") == (Decimal("90.00"), Decimal("8.01"))
    saved = _save_as_the_form_does(client, invoice.id, notes="Call first")
    assert Decimal(str(saved["total"])) == Decimal("98.01")


def test_tax_after_the_discount_with_untaxed_lines_takes_qbos_taxable_amount(
    client, books
):
    """50.00 taxed and 10.00 not, 10% off: QBO takes 5.00 off the taxed
    lines (NetAmountTaxable 45.00) and taxes 45.00 at 8.9%: 4.01."""
    lines = [_line(1, 50, "Catering", "TAX"), _line(2, 10, "Delivery", "NON")]
    tax = _tax(4.01, (8.9, 45, 4.01))
    books.invoices(
        _discounted("153", "1053", lines + [_discount(3, 6, 10)], 58.01, tax, True)
    )
    assert books.lines("1053")[2:] == [
        ("Discount 10% on taxable lines", Decimal("-5"), True),
        ("Discount 10% on non-taxable lines", Decimal("-1"), False),
    ]
    invoice = books.get("1053")
    assert invoice.tax_rate == Decimal("0.089")
    assert _adds_up(invoice)
    assert _sales_tax_row(client, "1053") == (Decimal("45.00"), Decimal("4.01"))
    saved = _save_as_the_form_does(client, invoice.id, notes="Call first")
    assert Decimal(str(saved["total"])) == Decimal("58.01")


def test_tax_before_the_discount_is_on_the_whole_amount(client, books):
    """8.9% on the 100.00, then 10.00 off: 90.00 and 8.90 tax."""
    lines = [_line(1, 100, "Catering", "TAX")]
    tax = _tax(8.90, (8.9, 100, 8.90))
    books.invoices(
        _discounted("154", "1054", lines + [_discount(2, 10)], 98.90, tax, False)
    )
    invoice = books.get("1054")
    assert books.lines("1054")[-1] == ("Discount", Decimal("-10"), False)
    assert invoice.tax_rate == Decimal("0.089")
    assert _sales_tax_row(client, "1054") == (Decimal("100.00"), Decimal("8.90"))
    saved = _save_as_the_form_does(client, invoice.id, notes="Call first")
    assert Decimal(str(saved["total"])) == Decimal("98.90")


def test_a_discount_on_a_sales_receipt_comes_across(client, books):
    lines = [_line(1, 30, "Lunch", "TAX")]
    tax = _tax(2.40, (8.9, 27, 2.40))
    books.sales_receipts(
        _discounted("155", "SR-15", lines + [_discount(2, 3, 10)], 29.40, tax, True)
    )
    receipt = books.get("SR-15")
    assert books.lines("SR-15") == [
        ("Lunch", Decimal("30"), True),
        ("Discount 10%", Decimal("-3"), True),
    ]
    assert receipt.tax_rate == Decimal("0.089")
    assert (receipt.total, receipt.balance_due) == (Decimal("29.40"), Decimal("0"))
    payment = receipt.payment_allocations[0].payment
    assert payment.amount == Decimal("29.40")
    saved = _save_as_the_form_does(client, receipt.id, notes="Paid at the door")
    assert Decimal(str(saved["total"])) == Decimal("29.40")


def test_an_adopted_invoice_books_its_discount_to_the_discount_account(client, books):
    lines = [_line(1, 100, "Catering", "TAX")]
    tax = _tax(8.01, (8.9, 90, 8.01))
    books.invoices(
        _discounted("156", "1056", lines + [_discount(2, 10, 10)], 98.01, tax, True)
    )
    invoice = books.get("1056")
    saved = _save_as_the_form_does(client, invoice.id, notes="Call first")
    assert Decimal(str(saved["total"])) == Decimal("98.01")  # unchanged
    invoice = books.get("1056")
    assert invoice.transaction_id is not None  # its own posting now
    assert books.balance("1100") == Decimal("98.01")
    assert books.balance("4000") == Decimal("100.00")
    assert books.given_balance() == Decimal("-10.00")  # the discount, a debit
    assert books.balance("2200") == Decimal("8.01")
    said = [ln.description for ln in books.db.query(TransactionLine)]
    assert ON_NO_LINE not in said


def test_a_reimport_brings_a_discount_qbo_changed_up_to_date(books):
    lines = [_line(1, 100, "Catering", "NON")]
    books.invoices(_discounted("157", "1057", lines + [_discount(2, 10, 10)], 90))
    changed = _discounted("157", "1057", lines + [_discount(2, 15, 15)], 85)
    books.invoices(changed | {"SyncToken": "1"})
    invoice = books.get("1057")
    assert books.lines("1057")[-1] == ("Discount 15%", Decimal("-15"), False)
    assert (invoice.subtotal, invoice.total, invoice.balance_due) == (
        Decimal("85"),
        Decimal("85"),
        Decimal("85"),
    )
    assert _adds_up(invoice)
    # the same Discount item, not a second one
    assert books.db.query(Item).filter(Item.name.like("Discount%")).count() == 1


def test_a_discount_whose_account_is_not_imported_says_so(books):
    lines = [_line(1, 100, "Catering", "NON")]
    books.sources[QBOInvoice] = [
        QBOInvoice.from_json(
            _discounted("158", "1058", lines + [_discount(2, 10, account="99")], 90)
        )
    ]
    [error] = qbo_import.import_invoices(books.db)["errors"]
    assert (
        "its discount account, QBO #99 (Discounts given), has not been imported; "
        "import Accounts, then this again" in error["message"]
    )


def test_a_negative_price_stays_refused_off_a_discount(client, books):
    """A refund still belongs on a credit memo: a negative line on no item,
    or on an ordinary item, is refused in the same words as before."""
    customer_id = books.get_customer_id()
    body = {"customer_id": customer_id, "date": "2026-08-03", "tax_rate": 0}
    ordinary = Item(name="Catering", item_type=ItemType.SERVICE, rate=Decimal("0"))
    books.db.add(ordinary)
    books.db.commit()
    for item_id in (None, ordinary.id):
        line = {"item_id": item_id, "description": "x", "quantity": 1, "rate": -5}
        r = client.post(
            "/api/invoices",
            json=body
            | {"lines": [{"description": "y", "quantity": 1, "rate": 20}, line]},
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"][0]["message"] == (
            "Line 2: Rate must be non-negative; use a credit memo for refunds."
        )


def test_a_discount_item_takes_a_negative_price(client, books):
    lines = [_line(1, 100, "Catering", "NON")]
    books.invoices(_discounted("159", "1059", lines + [_discount(2, 10)], 90))
    discount_item = books.get("1059").lines[1].item_id
    body = {
        "customer_id": books.get_customer_id(),
        "date": "2026-08-03",
        "tax_rate": 0,
        "lines": [
            {"description": "Catering", "quantity": 1, "rate": 50},
            {"item_id": discount_item, "description": "Discount", "rate": -5},
        ],
    }
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 201, r.text
    assert Decimal(str(r.json()["total"])) == Decimal("45")
    assert books.given_balance() == Decimal("-5.00")


def test_a_qbo_invoice_keeps_a_negative_line_of_its_own_on_an_edit(client, books):
    """QBO allows a negative sales line (a coupon item): it came in with
    the invoice, and saving the invoice keeps it."""
    coupon = Item(name="Coupon", item_type=ItemType.SERVICE, rate=Decimal("0"))
    books.db.add(coupon)
    books.db.flush()
    books.db.add(QBOMapping(entity_type="item", qbo_id="31", slowbooks_id=coupon.id))
    books.db.commit()
    off = _line(2, -5, "Coupon", "NON")
    off["SalesItemLineDetail"]["ItemRef"] = {"value": "31", "name": "Coupon"}
    lines = [_line(1, 40, "Catering", "NON"), off]
    books.invoices(_discounted("160", "1060", lines, 35))
    invoice = books.get("1060")
    saved = _save_as_the_form_does(client, invoice.id, notes="Call first")
    assert Decimal(str(saved["total"])) == Decimal("35")
    assert books.balance("1100") == Decimal("35.00")


def test_a_line_the_import_does_not_bring_is_named_and_posts_plainly(
    client, books, monkeypatch
):
    """A bundle (GroupLineDetail) is not brought across: the import log
    says so, and an edit posts that part of the total to income, said
    plainly on the entry."""
    notes = []
    emit = qbo_progress.emit
    monkeypatch.setattr(
        qbo_progress,
        "emit",
        lambda action, message, **fields: (
            notes.append((action, message, fields)),
            emit(action, message, **fields),
        ),
    )
    bundle = {
        "Id": "2",
        "Amount": 20,
        "DetailType": "GroupLineDetail",
        "GroupLineDetail": {"GroupItemRef": {"value": "40", "name": "Kit"}},
    }
    lines = [_line(1, 100, "Catering", "NON"), bundle]
    books.invoices(_discounted("161", "1061", lines, 120))
    [(action, message, fields)] = [n for n in notes if n[0] == "note"]
    assert fields == {"level": "warning", "code": "IMPORT_LINES_SHORT"}
    assert message.startswith(
        "Its lines here come to 100.00 and its total before tax in QuickBooks "
        "Online to 120.00"
    )
    invoice = books.get("1061")
    r = client.put(f"/api/invoices/{invoice.id}", json={"date": "2026-08-02"})
    assert r.status_code == 200, r.text
    assert books.balance("1100") == Decimal("120.00")
    rest = books.db.query(TransactionLine).filter_by(description=ON_NO_LINE).one()
    assert rest.credit == Decimal("20.00")
