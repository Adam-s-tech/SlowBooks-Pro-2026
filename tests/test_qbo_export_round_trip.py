"""What the QuickBooks Online import brought in is not exported back to QBO
(2.18.0).

The import maps a QBO sales receipt as a sales receipt, but the export
only looked for an invoice mapping: after an import, Export sent every one
of QBO's own sales receipts back to QBO as a new invoice, and its payment
as a new payment, so QBO counted the sale twice. Now the export leaves a
document the import brought in, and the payment half of one of QBO's
sales receipts.

Built through the python-quickbooks SDK's own objects (tests/
test_qbo_managed_voids.Books: invoices 1037 and 1038, QBO payment 131,
sales receipt SR-9); the export's save (the call to QBO) is where the test
reads what it sends.
"""

from decimal import Decimal

import pytest
from quickbooks.objects.invoice import Invoice as QBOInvoice
from quickbooks.objects.payment import Payment as QBOPayment

from app.services import qbo_export
from tests.test_qbo_managed_voids import Books


@pytest.fixture
def books(db_session, seed_accounts, monkeypatch):
    return Books(db_session, seed_accounts, monkeypatch)


@pytest.fixture
def sent(monkeypatch):
    """(kind, document number, amount) of everything the export sends QBO."""
    saved = []

    def save(self, qb=None, request_id=None, params=None):
        saved.append(
            (
                type(self).__name__,
                getattr(self, "DocNumber", "") or "",
                getattr(self, "TotalAmt", 0),
            )
        )
        self.Id, self.SyncToken = str(900 + len(saved)), "0"
        return self

    monkeypatch.setattr(QBOInvoice, "save", save)
    monkeypatch.setattr(QBOPayment, "save", save)
    monkeypatch.setattr(qbo_export, "get_qbo_client", lambda db: object())
    return saved


def test_qbos_own_documents_are_not_sent_back(books, sent):
    books.documents()
    books.ledger()
    assert qbo_export.export_invoices(books.db)["exported"] == 0
    assert qbo_export.export_payments(books.db)["exported"] == 0
    assert sent == []


def test_a_payment_recorded_here_on_a_qbo_invoice_still_goes(client, books, sent):
    """The rule leaves QBO's own; what is made here goes as before."""
    books.documents()
    invoice = books.invoice("1038")
    r = client.post(
        "/api/payments",
        json={
            "customer_id": invoice.customer_id,
            "date": "2026-08-03",
            "amount": 15,
            "deposit_to_account_id": books.accounts["1200"].id,
            "allocations": [{"invoice_id": invoice.id, "amount": 15}],
        },
    )
    assert r.status_code == 201, r.text
    assert qbo_export.export_payments(books.db)["exported"] == 1
    assert [(kind, amount) for kind, _, amount in sent] == [("Payment", 15.0)]
    assert Decimal(str(sent[0][2])) == Decimal("15")


def test_a_document_voided_before_it_went_is_not_sent(client, books, sent):
    """An invoice and a payment written here and voided before an export
    stay out of QBO, which would take them as live ones."""
    books.documents()
    invoice = books.invoice("1038")
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": invoice.customer_id,
            "date": "2026-08-03",
            "lines": [{"description": "Catering", "quantity": 1, "rate": 25}],
        },
    )
    assert r.status_code == 201, r.text
    written = r.json()
    r = client.post(
        "/api/payments",
        json={
            "customer_id": invoice.customer_id,
            "date": "2026-08-03",
            "amount": 25,
            "deposit_to_account_id": books.accounts["1200"].id,
            "allocations": [{"invoice_id": written["id"], "amount": 25}],
        },
    )
    assert r.status_code == 201, r.text
    assert client.post(f"/api/payments/{r.json()['id']}/void").status_code == 200
    assert client.post(f"/api/invoices/{written['id']}/void").status_code == 200
    qbo_export.export_invoices(books.db)
    qbo_export.export_payments(books.db)
    assert sent == []
