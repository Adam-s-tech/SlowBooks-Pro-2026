"""A sales receipt written here goes to QuickBooks Online as a QBO sales
receipt (2.18.0).

The export sent a sales receipt as a QBO invoice, then its payment as a
QBO payment applied to it: two records where QBO has one, and a receipt
that showed in QBO's invoice list. It now goes as one QBO SalesReceipt,
with its lines, tax codes, discount and the account its money went to,
and its payment isn't sent on its own. A receipt an earlier release sent
the old way (an invoice and a payment) is left as it is, not sent again.

QBO is played by tests/test_qbo_export_updates.FakeQBO: the SDK's own
objects, with their save, get and void calls answered there.
"""

from decimal import Decimal

from app.models.accounts import Account, AccountType
from app.models.items import Item, ItemType
from app.models.qbo_mapping import QBOMapping
from app.services import qbo_export
from tests.test_qbo_export_updates import _run, customer, qbo  # noqa: F401


def _receipt(client, buyer, seed_accounts, lines, tax_rate=0.089):
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": buyer["id"],
            "date": "2026-08-03",
            "tax_rate": tax_rate,
            "reference": "R-77",
            "deposit_to_account_id": seed_accounts["1200"].id,
            "lines": lines,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _discount_item(db):
    """The Discount item QBO's discounts came in on (account QBO 86)."""
    given = Account(name="Discounts given", account_type=AccountType.INCOME)
    item = Item(name="Discount", item_type=ItemType.SERVICE, rate=Decimal("0"))
    db.add_all([given, item])
    db.flush()
    db.add(QBOMapping(entity_type="account", qbo_id="86", slowbooks_id=given.id))
    db.add(QBOMapping(entity_type="discount_item", qbo_id="86", slowbooks_id=item.id))
    db.commit()
    return item.id


def test_a_sales_receipt_goes_as_one_qbo_sales_receipt(
    client, db_session, qbo, customer, seed_accounts  # noqa: F811
):
    discount = _discount_item(db_session)
    sale = _receipt(
        client,
        customer,
        seed_accounts,
        [
            {"description": "Lunch", "quantity": 1, "rate": 30, "is_taxable": True},
            {"description": "Tip", "quantity": 1, "rate": 5, "is_taxable": False},
            {
                "item_id": discount,
                "description": "Discount",
                "rate": -3,
                "is_taxable": True,
            },
        ],
    )
    mark = len(qbo.calls)
    result = _run(db_session, qbo_export.export_invoices)
    assert (result["exported"], result["sales_receipts"]) == (1, 1)
    [(call, name, _, body)] = qbo.calls[mark:]
    assert (call, name) == ("create", "SalesReceipt")
    assert body["DocNumber"] == sale["invoice"]["invoice_number"]
    lunch, tip, off = body["Line"]
    assert lunch["SalesItemLineDetail"]["TaxCodeRef"] == {"value": "TAX"}
    assert tip["SalesItemLineDetail"]["TaxCodeRef"] == {"value": "NON"}
    assert off["DetailType"] == "DiscountLineDetail"
    assert off["DiscountLineDetail"]["DiscountAccountRef"] == {"value": "86"}
    assert body["ApplyTaxAfterDiscount"] is True
    assert body["DepositToAccountRef"] == {"value": "35"}
    assert body["PaymentRefNum"] == "R-77"
    # its payment is in the receipt: not sent on its own
    mark = len(qbo.calls)
    assert _run(db_session, qbo_export.export_payments)["exported"] == 0
    assert qbo.calls[mark:] == []


def test_a_receipt_changed_or_voided_here_follows_as_a_sales_receipt(
    client, db_session, qbo, customer, seed_accounts  # noqa: F811
):
    sale = _receipt(
        client,
        customer,
        seed_accounts,
        [{"description": "Lunch", "quantity": 1, "rate": 30}],
    )
    receipt_id = sale["invoice"]["id"]
    _run(db_session, qbo_export.export_invoices)
    mark = len(qbo.calls)
    r = client.put(
        f"/api/invoices/{receipt_id}",
        json={
            "lines": [{"description": "Lunch", "quantity": 1, "rate": 30}],
            "notes": "Paid at the door",
        },
    )
    assert r.status_code == 200, r.text
    assert _run(db_session, qbo_export.export_invoices)["updated"] == 1
    [(call, name, _, body)] = qbo.calls[mark:]
    assert (call, name) == ("update", "SalesReceipt")
    assert body["CustomerMemo"] == {"value": "Paid at the door"}
    mark = len(qbo.calls)
    assert client.post(f"/api/payments/{sale['payment']['id']}/void").status_code == 200
    r = client.post(f"/api/invoices/{receipt_id}/void")
    assert r.status_code == 200, r.text
    _run(db_session, qbo_export.export_payments)
    assert _run(db_session, qbo_export.export_invoices)["voided"] == 1
    assert [(c[0], c[1]) for c in qbo.calls[mark:]] == [("void", "SalesReceipt")]


def test_a_receipt_sent_the_old_way_is_left_as_it_is(
    client, db_session, qbo, customer, seed_accounts  # noqa: F811
):
    """Sent by an earlier release as an invoice and a payment: not sent
    again as a sales receipt, and neither half is touched."""
    sale = _receipt(
        client,
        customer,
        seed_accounts,
        [{"description": "Lunch", "quantity": 1, "rate": 30}],
    )
    db_session.add(
        QBOMapping(
            entity_type="invoice",
            qbo_id="501",
            slowbooks_id=sale["invoice"]["id"],
            qbo_sync_token="0",
        )
    )
    db_session.add(
        QBOMapping(
            entity_type="payment",
            qbo_id="502",
            slowbooks_id=sale["payment"]["id"],
            qbo_sync_token="0",
        )
    )
    db_session.commit()
    r = client.put(
        f"/api/invoices/{sale['invoice']['id']}",
        json={
            "lines": [{"description": "Lunch", "quantity": 1, "rate": 30}],
            "notes": "x",
        },
    )
    assert r.status_code == 200, r.text
    mark = len(qbo.calls)
    _run(db_session, qbo_export.export_invoices)
    _run(db_session, qbo_export.export_payments)
    assert qbo.calls[mark:] == []
