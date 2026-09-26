"""A document the QuickBooks Online import created is voided in QuickBooks
Online, not here (#192 review, 2.18.0).

The import brings QBO's invoices, payments and sales receipts in as
documents with no posting of their own: their A/R, income and cash reach
the books through the QBO ledger import (Posted Ledger Activity). Voiding
one here reversed nothing, because there was nothing of its own to
reverse: the document read void (or the invoice read unpaid again) while
the books kept its amounts, so A/R Aging and the ledger drifted apart.
"""

from datetime import date
from decimal import Decimal

import pytest
from quickbooks.objects.invoice import Invoice as QBOInvoice
from quickbooks.objects.payment import Payment as QBOPayment
from quickbooks.objects.salesreceipt import SalesReceipt as QBOSalesReceipt

from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment
from app.models.qbo_mapping import QBOMapping
from app.models.transactions import Transaction
from app.services import qbo_import

CUSTOMER = {"value": "58", "name": "Acme Diner"}


def _sale_line(amount, description):
    return {
        "Id": "1",
        "Amount": amount,
        "Description": description,
        "DetailType": "SalesItemLineDetail",
        "SalesItemLineDetail": {"Qty": 1, "UnitPrice": amount},
    }


def _invoice(qbo_id, number, amount):
    return QBOInvoice.from_json(
        {
            "Id": qbo_id,
            "DocNumber": number,
            "TxnDate": "2026-08-03",
            "DueDate": "2026-09-02",
            "TotalAmt": amount,
            "Balance": amount,
            "CustomerRef": CUSTOMER,
            "Line": [_sale_line(amount, "Catering")],
        }
    )


@pytest.fixture
def from_qbo(db_session, seed_accounts, monkeypatch):
    """Invoice 1037 (paid 20.00 by a QBO payment), invoice 1038 (unpaid)
    and sales receipt SR-9, through the real document import."""
    customer = Customer(name="Acme Diner", is_active=True)
    db_session.add(customer)
    db_session.flush()
    db_session.add(
        QBOMapping(entity_type="customer", qbo_id="58", slowbooks_id=customer.id)
    )
    db_session.flush()
    sources = {
        QBOInvoice: [_invoice("130", "1037", 50), _invoice("133", "1038", 40)],
        QBOPayment: [
            QBOPayment.from_json(
                {
                    "Id": "131",
                    "TxnDate": "2026-08-10",
                    "TotalAmt": 20,
                    "CustomerRef": CUSTOMER,
                    "Line": [
                        {
                            "Amount": 20,
                            "LinkedTxn": [{"TxnId": "130", "TxnType": "Invoice"}],
                        }
                    ],
                }
            )
        ],
        QBOSalesReceipt: [
            QBOSalesReceipt.from_json(
                {
                    "Id": "132",
                    "DocNumber": "SR-9",
                    "TxnDate": "2026-08-12",
                    "TotalAmt": 30,
                    "CustomerRef": CUSTOMER,
                    "Line": [_sale_line(30, "Lunch")],
                }
            )
        ],
    }
    monkeypatch.setattr(qbo_import, "get_qbo_client", lambda db: object())
    monkeypatch.setattr(
        qbo_import, "_all_qbo_objects", lambda cls, client: sources.get(cls, [])
    )
    for step in (
        qbo_import.import_invoices,
        qbo_import.import_payments,
        qbo_import.import_sales_receipts,
    ):
        assert step(db_session)["errors"] == []
    db_session.commit()

    def invoice(number):
        return db_session.query(Invoice).filter_by(invoice_number=number).one()

    return invoice


def _refused(response, what):
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail.startswith(f"This {what} came from QuickBooks Online"), detail
    assert "Void it in QuickBooks Online" in detail, detail


def test_a_qbo_invoice_is_voided_in_quickbooks_online(client, db_session, from_qbo):
    invoice = from_qbo("1038")
    assert invoice.transaction_id is None  # what the import makes
    _refused(client.post(f"/api/invoices/{invoice.id}/void"), "invoice")
    db_session.expire_all()
    invoice = from_qbo("1038")
    assert invoice.status == InvoiceStatus.SENT
    assert invoice.balance_due == Decimal("40")
    assert db_session.query(Transaction).count() == 0


def test_a_qbo_payment_is_voided_in_quickbooks_online(client, db_session, from_qbo):
    paid = from_qbo("1037")
    payment = db_session.query(Payment).filter_by(amount=Decimal("20")).one()
    _refused(client.post(f"/api/payments/{payment.id}/void"), "payment")
    db_session.expire_all()
    assert db_session.get(Payment, payment.id).is_voided is False
    paid = from_qbo("1037")
    assert (paid.amount_paid, paid.balance_due) == (Decimal("20"), Decimal("30"))


def test_a_qbo_sales_receipt_is_voided_in_quickbooks_online(
    client, db_session, from_qbo
):
    """The Sales Receipts page voids the receipt's payment, then the receipt:
    both say where to void it (the receipt used to answer "void the
    payment first")."""
    receipt = from_qbo("SR-9")
    payment = receipt.payment_allocations[0].payment
    _refused(client.post(f"/api/payments/{payment.id}/void"), "payment")
    _refused(client.post(f"/api/invoices/{receipt.id}/void"), "sales receipt")
    db_session.expire_all()
    assert from_qbo("SR-9").status == InvoiceStatus.PAID


def test_an_invoice_with_its_own_posting_still_voids_here(
    client, db_session, seed_accounts, seed_customer
):
    """Exported to QBO (so mapped), but written here with its own posting:
    its void reverses that posting as before."""
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": date(2026, 8, 3).isoformat(),
            "lines": [{"description": "Consulting", "quantity": 1, "rate": 75}],
        },
    )
    assert r.status_code == 201, r.text
    invoice_id = r.json()["id"]
    db_session.add(
        QBOMapping(entity_type="invoice", qbo_id="555", slowbooks_id=invoice_id)
    )
    db_session.commit()
    r = client.post(f"/api/invoices/{invoice_id}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"
    assert (
        db_session.query(Transaction).filter_by(source_type="invoice_void").count() == 1
    )
