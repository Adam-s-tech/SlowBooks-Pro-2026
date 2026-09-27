"""A QuickBooks Online sale of a locally tracked item is costed once
(#192 review, 2.18.0).

The item import maps a QBO item to a local item of the same name, which
may track its stock. The invoice import then costs the sale at the local
average cost (DR COGS / CR Inventory) as it moves the stock, and the
ledger import posts QBO's own lines for the same invoice, QBO's cost of
goods included: the goods were costed twice.
"""

from datetime import date
from decimal import Decimal

import pytest
from quickbooks.objects.invoice import Invoice as QBOInvoice

from app.models.contacts import Customer
from app.models.items import Item, ItemType
from app.models.qbo_mapping import QBOMapping
from app.models.transactions import Transaction
from app.services import qbo_import, qbo_ledger_import
from app.services.bank_register import gl_balances
from tests.test_qbo_import_review import LedgerClient

DAY = date(2026, 8, 3)


@pytest.fixture
def widget_sale(db_session, seed_accounts, monkeypatch):
    """Invoice 1037 in QBO: two Widgets at 25.00. QBO costs them at 7.00;
    here a Widget is tracked, ten on hand at an average cost of 4.00."""
    customer = Customer(name="Acme Diner", is_active=True)
    widget = Item(
        name="Widget",
        item_type=ItemType.PRODUCT,
        rate=Decimal("25"),
        track_inventory=True,
        quantity_on_hand=Decimal("10"),
        avg_cost=Decimal("4"),
    )
    db_session.add_all([customer, widget])
    db_session.flush()
    mapped = [("customer", "58", customer.id), ("item", "7", widget.id)]
    mapped += [
        ("account", qbo_id, seed_accounts[number].id)
        for qbo_id, number in [
            ("84", "1100"),
            ("79", "4000"),
            ("80", "5000"),
            ("81", "1300"),
        ]
    ]
    for kind, qbo_id, local_id in mapped:
        db_session.add(
            QBOMapping(entity_type=kind, qbo_id=qbo_id, slowbooks_id=local_id)
        )
    db_session.flush()
    invoice = QBOInvoice.from_json(
        {
            "Id": "130",
            "DocNumber": "1037",
            "TxnDate": DAY.isoformat(),
            "DueDate": DAY.isoformat(),
            "TotalAmt": 50,
            "Balance": 50,
            "CustomerRef": {"value": "58", "name": "Acme Diner"},
            "Line": [
                {
                    "Id": "1",
                    "Amount": 50,
                    "DetailType": "SalesItemLineDetail",
                    "SalesItemLineDetail": {
                        "ItemRef": {"value": "7", "name": "Widget"},
                        "Qty": 2,
                        "UnitPrice": 25,
                    },
                }
            ],
        }
    )
    sale = ("Invoice", "130", "1037")
    ledger = LedgerClient(
        {
            "84": [(*sale, "50")],  # A/R
            "79": [(*sale, "50")],  # income
            "80": [(*sale, "7")],  # QBO's cost of goods
            "81": [(*sale, "-7")],  # inventory asset
        }
    )
    monkeypatch.setattr(qbo_import, "get_qbo_client", lambda db: object())
    monkeypatch.setattr(
        qbo_import,
        "_all_qbo_objects",
        lambda cls, client: [invoice] if cls is QBOInvoice else [],
    )
    monkeypatch.setattr(qbo_ledger_import, "get_qbo_client", lambda db: ledger)

    def documents():
        assert qbo_import.import_invoices(db_session)["errors"] == []

    def posted_ledger():
        result = qbo_ledger_import.import_ledger(db_session, start=DAY, end=DAY)
        assert result["errors"] == []

    def cogs():
        cogs_id = seed_accounts["5000"].id
        return gl_balances(db_session, [cogs_id])[cogs_id]

    return documents, posted_ledger, cogs, widget


@pytest.mark.parametrize("order", ["documents, then ledger", "ledger, then documents"])
def test_with_the_ledger_import_the_goods_cost_what_qbo_says_once(
    widget_sale, db_session, order
):
    documents, posted_ledger, cogs, widget = widget_sale
    steps = [documents, posted_ledger]
    if order.startswith("ledger"):
        steps.reverse()
    for step in steps + steps:  # and again: a re-import changes nothing
        step()
        db_session.flush()
    assert cogs() == Decimal("7.00")
    assert widget.quantity_on_hand == Decimal("8")  # the stock still moved


def test_without_the_ledger_import_the_sale_is_costed_here(widget_sale, db_session):
    """As before #192: the local average cost, 2 x 4.00."""
    documents, _, cogs, widget = widget_sale
    documents()
    documents()
    db_session.flush()
    assert cogs() == Decimal("8.00")
    assert widget.quantity_on_hand == Decimal("8")


def test_the_local_cost_is_taken_back_by_a_reversal(widget_sale, db_session):
    documents, posted_ledger, cogs, widget = widget_sale
    documents()
    posted_ledger()
    db_session.flush()
    local = db_session.query(Transaction).filter_by(source_type="invoice").one()
    reversal = (
        db_session.query(Transaction).filter_by(source_type="qbo_cogs_void").one()
    )
    assert reversal.source_id == local.id
    assert sorted((ln.debit, ln.credit) for ln in reversal.lines) == sorted(
        (ln.credit, ln.debit) for ln in local.lines
    )


def test_an_edit_makes_the_sale_ours_and_costs_it_here_once(
    widget_sale, db_session, client
):
    """The ledger import costed the sale at QBO's 7.00. Three Widgets
    instead of two: the invoice becomes ours, QBO's posting is reversed and
    the sale is costed here at 4.00 a Widget, once."""
    from app.models.invoices import Invoice

    documents, posted_ledger, cogs, widget = widget_sale
    documents()
    posted_ledger()
    db_session.commit()
    invoice = db_session.query(Invoice).one()
    r = client.put(
        f"/api/invoices/{invoice.id}",
        json={
            "lines": [
                {
                    "item_id": widget.id,
                    "description": "Widget",
                    "quantity": 3,
                    "rate": 25,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert cogs() == Decimal("12.00")
    assert db_session.get(Item, widget.id).quantity_on_hand == Decimal("7")


def test_a_receipt_edited_after_its_payment_was_voided_is_costed_here(
    db_session, seed_accounts, monkeypatch, client
):
    """Voiding a QBO sales receipt's payment reverses the receipt's import
    posting, QBO's cost of goods with it; an edit then makes the receipt
    ours, and its stock must be costed here, not left costing nothing."""
    from quickbooks.objects.salesreceipt import SalesReceipt as QBOSalesReceipt

    from app.models.invoices import Invoice

    customer = Customer(name="Acme Diner", is_active=True)
    widget = Item(
        name="Widget",
        item_type=ItemType.PRODUCT,
        rate=Decimal("25"),
        track_inventory=True,
        quantity_on_hand=Decimal("10"),
        avg_cost=Decimal("4"),
    )
    db_session.add_all([customer, widget])
    db_session.flush()
    mapped = [("customer", "58", customer.id), ("item", "7", widget.id)]
    mapped += [
        ("account", qbo_id, seed_accounts[number].id)
        for qbo_id, number in [
            ("79", "4000"),
            ("80", "5000"),
            ("81", "1300"),
            ("35", "1200"),
        ]
    ]
    for kind, qbo_id, local_id in mapped:
        db_session.add(
            QBOMapping(entity_type=kind, qbo_id=qbo_id, slowbooks_id=local_id)
        )
    db_session.flush()
    receipt = QBOSalesReceipt.from_json(
        {
            "Id": "132",
            "DocNumber": "SR-9",
            "TxnDate": DAY.isoformat(),
            "TotalAmt": 50,
            "CustomerRef": {"value": "58", "name": "Acme Diner"},
            "DepositToAccountRef": {"value": "35"},
            "Line": [
                {
                    "Id": "1",
                    "Amount": 50,
                    "DetailType": "SalesItemLineDetail",
                    "SalesItemLineDetail": {
                        "ItemRef": {"value": "7", "name": "Widget"},
                        "Qty": 2,
                        "UnitPrice": 25,
                    },
                }
            ],
        }
    )
    sale = ("Sales Receipt", "132", "SR-9")
    ledger = LedgerClient(
        {
            "35": [(*sale, "50")],
            "79": [(*sale, "50")],
            "80": [(*sale, "7")],
            "81": [(*sale, "-7")],
        }
    )
    monkeypatch.setattr(qbo_import, "get_qbo_client", lambda db: object())
    monkeypatch.setattr(
        qbo_import,
        "_all_qbo_objects",
        lambda cls, client: [receipt] if cls is QBOSalesReceipt else [],
    )
    monkeypatch.setattr(qbo_ledger_import, "get_qbo_client", lambda db: ledger)
    assert qbo_import.import_sales_receipts(db_session)["errors"] == []
    assert (
        qbo_ledger_import.import_ledger(db_session, start=DAY, end=DAY)["errors"] == []
    )
    db_session.commit()
    document = db_session.query(Invoice).one()
    payment = document.payment_allocations[0].payment
    assert client.post(f"/api/payments/{payment.id}/void").status_code == 200
    r = client.put(
        f"/api/invoices/{document.id}",
        json={
            "lines": [
                {
                    "item_id": widget.id,
                    "description": "Widget",
                    "quantity": 2,
                    "rate": 25,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    cogs_id = seed_accounts["5000"].id
    assert gl_balances(db_session, [cogs_id])[cogs_id] == Decimal("8.00")
