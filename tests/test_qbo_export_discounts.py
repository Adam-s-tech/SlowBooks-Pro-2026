"""A line on a Discount item exports to QuickBooks Online as QBO's own
discount (2.18.0).

A discount from QBO comes in as a negative line on a Discount item. The
export wrote every line as a sales line, so that discount would have gone
back to QBO as a negative sales line with no item. It now goes as a
DiscountLineDetail on QBO's discount account (the one the Discount item
came in on), one for each Discount item, with the tax worked out after it
when it came off the taxable amount. A negative line on any other item
exports as it did.

The invoice is built as the export builds it, with the python-quickbooks
SDK's own Invoice; its save (the call to QBO) is where the test reads it.
"""

from datetime import date
from decimal import Decimal
import json

import pytest
from quickbooks.objects.invoice import Invoice as QBOInvoice

from app.models.accounts import Account, AccountType
from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.items import Item, ItemType
from app.models.qbo_mapping import QBOMapping
from app.services import qbo_export


@pytest.fixture
def sent(monkeypatch):
    """What export_invoices sends QBO: each SDK Invoice it saves."""
    saved = []

    def save(self, qb=None, request_id=None, params=None):
        saved.append(self)
        self.Id, self.SyncToken = str(900 + len(saved)), "0"
        return self

    monkeypatch.setattr(QBOInvoice, "save", save)
    monkeypatch.setattr(qbo_export, "get_qbo_client", lambda db: object())
    return saved


@pytest.fixture
def items(db_session, seed_accounts):
    """Catering (QBO item 21), a Coupon (QBO item 31), and the Discount items
    QBO's discounts came in on: Discount on "Discounts given" (QBO account
    86), Promo on "Promotions" (QBO account 87)."""
    customer = Customer(name="Acme Diner", is_active=True)
    db_session.add(customer)
    made = {"customer": customer}
    for name in ("Discounts given", "Promotions"):
        made[name] = Account(name=name, account_type=AccountType.INCOME)
        db_session.add(made[name])
    for name in ("Catering", "Coupon", "Discount", "Promo"):
        made[name] = Item(
            name=name,
            item_type=ItemType.SERVICE,
            rate=Decimal("0"),
            income_account_id=seed_accounts["4000"].id,
        )
        db_session.add(made[name])
    db_session.flush()
    for kind, qbo_id, local in [
        ("customer", "58", customer.id),
        ("account", "86", made["Discounts given"].id),
        ("account", "87", made["Promotions"].id),
        ("item", "21", made["Catering"].id),
        ("item", "31", made["Coupon"].id),
        ("discount_item", "86", made["Discount"].id),
        ("discount_item", "87", made["Promo"].id),
    ]:
        db_session.add(QBOMapping(entity_type=kind, qbo_id=qbo_id, slowbooks_id=local))
    db_session.commit()
    return made


def _invoice(db, items, number, lines, tax=0, **fields):
    """(item, description, amount, taxable) lines; quantity 1."""
    subtotal = sum((Decimal(str(amount)) for _, _, amount, _ in lines), Decimal("0"))
    total = subtotal + Decimal(str(tax))
    invoice = Invoice(
        invoice_number=number,
        customer_id=items["customer"].id,
        date=date(2026, 8, 3),
        due_date=date(2026, 8, 3),
        status=InvoiceStatus.SENT,
        subtotal=subtotal,
        tax_rate=Decimal("0"),
        tax_amount=Decimal(str(tax)),
        total=total,
        balance_due=total,
        **fields,
    )
    db.add(invoice)
    db.flush()
    for order, (item, words, amount, taxable) in enumerate(lines):
        db.add(
            InvoiceLine(
                invoice_id=invoice.id,
                item_id=items[item].id if item else None,
                description=words,
                quantity=Decimal("1"),
                rate=Decimal(str(amount)),
                amount=Decimal(str(amount)),
                is_taxable=taxable,
                line_order=order,
            )
        )
    db.commit()
    return invoice


def test_a_discount_item_line_exports_as_qbos_discount(db_session, items, sent):
    _invoice(
        db_session,
        items,
        "2001",
        [
            ("Catering", "Catering", 100, True),
            ("Discount", "Discount 10%", -10, True),
            ("Coupon", "Coupon", -5, False),
        ],
    )
    assert qbo_export.export_invoices(db_session) == {
        "exported": 1,
        "errors": [],
        "notes": [],
    }
    [invoice] = sent
    catering, discount, coupon = invoice.Line
    assert catering["SalesItemLineDetail"]["ItemRef"] == {"value": "21"}
    assert discount == {
        "DetailType": "DiscountLineDetail",
        "Amount": 10.0,
        "Description": "Discount 10%",
        "DiscountLineDetail": {
            "PercentBased": False,
            "DiscountAccountRef": {"value": "86"},
        },
    }
    # a negative line on any other item goes as it always did
    assert coupon["DetailType"] == "SalesItemLineDetail"
    assert coupon["Amount"] == -5.0
    assert coupon["SalesItemLineDetail"] == {
        "Qty": 1.0,
        "UnitPrice": -5.0,
        "TaxCodeRef": {"value": "NON"},
        "ItemRef": {"value": "31"},
    }
    assert invoice.ApplyTaxAfterDiscount is True  # it came off the taxable amount
    # and the SDK sends it as QBO reads it
    body = json.loads(invoice.to_json())
    assert body["Line"][1]["DiscountLineDetail"]["DiscountAccountRef"]["value"] == "86"
    assert body["ApplyTaxAfterDiscount"] is True


def test_a_discount_in_two_parts_goes_as_one(db_session, items, sent):
    """The import splits a discount between taxed and untaxed lines; QBO
    has one discount."""
    _invoice(
        db_session,
        items,
        "2002",
        [
            ("Catering", "Catering", 50, True),
            (None, "Delivery", 10, False),
            ("Discount", "Discount 10% on taxable lines", -5, True),
            ("Discount", "Discount 10% on non-taxable lines", -1, False),
        ],
    )
    qbo_export.export_invoices(db_session)
    [invoice] = sent
    discounts = [ln for ln in invoice.Line if ln["DetailType"] == "DiscountLineDetail"]
    assert [ln["Amount"] for ln in discounts] == [6.0]
    assert len(invoice.Line) == 3


def test_a_discount_untaxed_leaves_the_tax_worked_out_first(db_session, items, sent):
    _invoice(
        db_session,
        items,
        "2003",
        [("Catering", "Catering", 100, True), ("Discount", "Discount", -10, False)],
    )
    qbo_export.export_invoices(db_session)
    [invoice] = sent
    assert invoice.Line[1]["DetailType"] == "DiscountLineDetail"
    assert invoice.ApplyTaxAfterDiscount is False


def test_discounts_on_two_accounts_go_as_one_with_a_note(db_session, items, sent):
    """QBO takes one discount on a transaction: the two go as one, on the
    account with the most of it, and the export says where the rest was."""
    _invoice(
        db_session,
        items,
        "2004",
        [
            ("Catering", "Catering", 100, True),
            ("Discount", "Discount 10%", -10, True),
            ("Promo", "Spring promotion", -4, True),
        ],
    )
    result = qbo_export.export_invoices(db_session)
    [invoice] = sent
    [discount] = [ln for ln in invoice.Line if ln["DetailType"] == "DiscountLineDetail"]
    assert discount["Amount"] == 14.0
    assert discount["DiscountLineDetail"]["DiscountAccountRef"] == {"value": "86"}
    assert discount["Description"] == "Discount 10%"
    assert [n["message"] for n in result["notes"]] == [
        "Invoice 2004 went to QuickBooks Online with one discount of 14.00 on "
        "Discounts given (QBO #86), as QuickBooks Online takes one discount on a "
        "transaction. It includes 4.00 on Promotions (QBO #87)."
    ]
    # and "Export All" carries the note to the page
    from app.schemas.qbo import QBOExportResult

    assert QBOExportResult(**{"notes": result["notes"]}).notes == result["notes"]


def test_one_discount_account_needs_no_note(db_session, items, sent):
    _invoice(
        db_session,
        items,
        "2005",
        [("Catering", "Catering", 100, True), ("Discount", "Discount", -10, True)],
    )
    assert qbo_export.export_invoices(db_session)["notes"] == []
