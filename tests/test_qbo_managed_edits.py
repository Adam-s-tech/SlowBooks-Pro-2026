"""Editing a document the QuickBooks Online import created makes it ours
(#192 review, 2.18.0).

The import brings QBO's invoices and sales receipts in with no posting of
their own; the ledger import posts QBO's lines for each. An edit here
changed the document and nothing in the books. Now an edit to its amounts
or date reverses the import's posting and posts the document's own entry,
so A/R is never counted twice, and a later import leaves it alone. An edit
of its words alone leaves it as it came.
"""

from decimal import Decimal

import pytest

from app.models.qbo_mapping import QBOMapping
from app.models.transactions import Transaction
from tests.test_qbo_managed_voids import Books

KEPT = "Changed in SlowBooks; kept as it is here"


@pytest.fixture
def books(db_session, seed_accounts, monkeypatch):
    return Books(db_session, seed_accounts, monkeypatch)


def _import_posting(books, key):
    mapping = books.db.query(QBOMapping).filter_by(qbo_id=key).one()
    return mapping, books.db.get(Transaction, mapping.slowbooks_id)


def _reversed(books, txn):
    return (
        books.db.query(Transaction)
        .filter(
            Transaction.source_type == "qbo_ledger_void",
            Transaction.source_id == txn.id,
        )
        .count()
    )


def test_editing_a_qbo_invoices_amount_leaves_one_ar_posting(client, books):
    books.documents()
    books.ledger()
    invoice = books.invoice("1038")
    r = client.put(
        f"/api/invoices/{invoice.id}",
        json={"lines": [{"description": "Catering", "quantity": 1, "rate": 55}]},
    )
    assert r.status_code == 200, r.text
    assert books.balance("1100") == Decimal("85.00")  # 30 (1037) + 55, not 125
    assert books.balance("4000") == Decimal("135.00")  # 50 + 55 + 30
    invoice = books.invoice("1038")
    assert invoice.transaction_id is not None  # its own posting now
    mapping, posting = _import_posting(books, "Invoice:133")
    assert _reversed(books, posting) == 1
    assert mapping.qbo_sync_token == "changed-in-slowbooks"
    # a later import leaves it alone: no second A/R, never an error
    before = books.postings()
    books.kept.clear()
    books.documents()
    assert books.ledger() == {"imported": 0, "errors": []}
    assert books.postings() == before
    assert books.balance("1100") == Decimal("85.00")
    assert books.kept.count(KEPT) == 1


def test_a_new_date_makes_it_ours_too(client, books):
    books.documents()
    books.ledger()
    invoice = books.invoice("1038")
    r = client.put(f"/api/invoices/{invoice.id}", json={"date": "2026-08-02"})
    assert r.status_code == 200, r.text
    invoice = books.invoice("1038")
    own = books.db.get(Transaction, invoice.transaction_id)
    assert own.date.isoformat() == "2026-08-02"
    assert books.balance("1100") == Decimal("70.00")


def test_editing_only_its_words_leaves_it_from_qbo(client, books):
    books.documents()
    books.ledger()
    invoice = books.invoice("1038")
    r = client.put(f"/api/invoices/{invoice.id}", json={"notes": "Call first"})
    assert r.status_code == 200, r.text
    invoice = books.invoice("1038")
    assert invoice.notes == "Call first"
    assert invoice.transaction_id is None
    mapping, posting = _import_posting(books, "Invoice:133")
    assert _reversed(books, posting) == 0
    assert mapping.qbo_sync_token != "changed-in-slowbooks"


def test_editing_a_qbo_sales_receipt_keeps_its_cash_and_income_once(client, books):
    books.documents()
    books.ledger()
    receipt = books.invoice("SR-9")
    r = client.put(
        f"/api/invoices/{receipt.id}",
        json={"lines": [{"description": "Lunch", "quantity": 1, "rate": 35}]},
    )
    assert r.status_code == 200, r.text
    assert books.balance("4000") == Decimal("125.00")  # 50 + 40 + 35
    assert books.balance("1200") == Decimal("50.00")  # 20 + the receipt's 30
    assert books.balance("1100") == Decimal("75.00")  # 70 + the 5.00 still due
    receipt = books.invoice("SR-9")
    assert receipt.balance_due == Decimal("5.00")
    assert receipt.payment_allocations[0].payment.transaction_id is not None


def test_an_edit_before_the_ledger_import_is_not_posted_again_by_it(client, books):
    books.documents()
    invoice = books.invoice("1038")
    r = client.put(
        f"/api/invoices/{invoice.id}",
        json={"lines": [{"description": "Catering", "quantity": 1, "rate": 55}]},
    )
    assert r.status_code == 200, r.text
    assert books.balance("1100") == Decimal("55.00")
    books.kept.clear()
    books.ledger()
    assert books.balance("1100") == Decimal("85.00")  # + 1037's 30, not 1038 again
    assert books.kept.count(KEPT) == 1


def _aging(books):
    """What A/R Aging adds up: every open invoice's balance."""
    from app.models.invoices import Invoice

    books.db.expire_all()
    return sum(
        (inv.balance_due for inv in books.db.query(Invoice) if inv.status != "void"),
        Decimal("0"),
    )


def test_an_edit_before_the_ledger_import_brings_the_invoices_payment_in_too(
    client, books
):
    """The ledger import has not run: nothing from QBO is in the books. The
    edited invoice posts its A/R, and its QBO payment (20.00) is posted
    with it, so A/R is right at once, and later imports keep both."""
    from app.models.payments import Payment

    books.documents()
    invoice = books.invoice("1037")
    r = client.put(
        f"/api/invoices/{invoice.id}",
        json={"lines": [{"description": "Catering", "quantity": 1, "rate": 60}]},
    )
    assert r.status_code == 200, r.text
    assert books.balance("1100") == Decimal("40.00")  # 60 less the 20 paid
    payment = books.db.query(Payment).filter_by(amount=Decimal("20")).one()
    assert payment.transaction_id is not None
    books.ledger()
    assert books.balance("1100") == Decimal("80.00") == _aging(books)  # + 1038's 40


def test_a_payment_shared_with_another_invoice_brings_that_invoice_in_too(
    client, books
):
    """The QBO payment also paid 10.00 of invoice 1038: that part of its
    credit to A/R needs 1038's debit there too."""
    from quickbooks.objects.payment import Payment as QBOPayment

    books.sources[QBOPayment] = [
        QBOPayment.from_json(
            {
                "Id": "131",
                "TxnDate": "2026-08-03",
                "TotalAmt": 30,
                "CustomerRef": {"value": "58", "name": "Acme Diner"},
                "DepositToAccountRef": {"value": "35"},
                "Line": [
                    {
                        "Amount": 20,
                        "LinkedTxn": [{"TxnId": "130", "TxnType": "Invoice"}],
                    },
                    {
                        "Amount": 10,
                        "LinkedTxn": [{"TxnId": "133", "TxnType": "Invoice"}],
                    },
                ],
            }
        )
    ]
    books.documents()
    invoice = books.invoice("1037")
    r = client.put(
        f"/api/invoices/{invoice.id}",
        json={"lines": [{"description": "Catering", "quantity": 1, "rate": 60}]},
    )
    assert r.status_code == 200, r.text
    assert books.invoice("1038").transaction_id is not None
    assert books.balance("1100") == Decimal("70.00") == _aging(books)  # 40 + 30
