"""A QuickBooks Online change that takes an invoice below the payments
recorded against it here is left, by both imports (2.18.0).

The document import already refused to bring such a change in (the
invoice would owe less than nothing), but the ledger import posted QBO's
new version of the invoice anyway: the invoice kept its old total while
the ledger took the new one, so A/R Aging and the A/R account disagreed.
Now neither applies it, the invoice and its posting stay in step, and the
run's log says why in one line, however many of the two steps ran. Once a
payment comes off the invoice here, the next import brings the change in.

Built through the python-quickbooks SDK's own objects (tests/
test_qbo_managed_voids.Books: invoice 1037, 50.00, 20.00 paid in QBO;
invoice 1038, 40.00).
"""

from decimal import Decimal

import pytest
from quickbooks.objects.invoice import Invoice as QBOInvoice

from app.models.invoices import Invoice
from app.services import (
    qbo_import,
    qbo_import_runs as runs,
    qbo_ledger_import,
    storage,
)
from tests.test_qbo_changes_flow_in import _edit_in_qbo, _gl_amount
from tests.test_qbo_managed_voids import DAY, Books, _sale_line

HELD = (
    "Invoice QBO #133 (document 1038) was changed in QuickBooks Online to 30.00, "
    "but the payments recorded against it here come to 35.00, more than its new "
    "total. The invoice and its posting stay as they were imported; take a "
    "payment off it here, and the next import brings QuickBooks Online's change "
    "in."
)


@pytest.fixture(autouse=True)
def private_log_root(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "backups_root", lambda: tmp_path / "private")


@pytest.fixture
def books(db_session, seed_accounts, monkeypatch):
    return Books(db_session, seed_accounts, monkeypatch)


def _aging(books):
    """What A/R Aging adds up: every open invoice's balance."""
    books.db.expire_all()
    return sum(
        (inv.balance_due for inv in books.db.query(Invoice) if inv.status != "void"),
        Decimal("0"),
    )


def _run(books, steps):
    """One import run of `steps`, as the QuickBooks Online page runs it;
    the lines its log says."""
    with runs.synchronous_run(books.db, steps, "owner"):
        if "invoices" in steps:
            qbo_import.import_invoices(books.db)
            books.db.commit()
        if "ledger" in steps:
            qbo_ledger_import.import_ledger(books.db, start=DAY, end=DAY)
            books.db.commit()
    return [e["message"] for e in runs.store_for(books.db).latest()["events"]]


@pytest.fixture
def paid_here(client, books):
    """35.00 recorded here against invoice 1038 (40.00); then QBO takes the
    invoice down to 30.00."""
    books.documents()
    books.ledger()
    invoice = books.invoice("1038")
    r = client.post(
        "/api/payments",
        json={
            "customer_id": invoice.customer_id,
            "date": DAY.isoformat(),
            "amount": 35,
            "deposit_to_account_id": books.accounts["1200"].id,
            "allocations": [{"invoice_id": invoice.id, "amount": 35}],
        },
    )
    assert r.status_code == 201, r.text
    _edit_in_qbo(
        books, QBOInvoice, 1, TotalAmt=30, Balance=30, Line=[_sale_line(30, "Catering")]
    )
    _gl_amount(books, "Invoice", "133", {"84": "30", "79": "30"})
    return r.json()


@pytest.mark.parametrize(
    "steps", [["invoices", "ledger"], ["ledger"], ["invoices"]], ids="+".join
)
def test_neither_import_applies_it_and_says_so_once(books, paid_here, steps):
    said = _run(books, steps)
    assert said.count(HELD) == 1
    invoice = books.invoice("1038")
    assert (invoice.total, invoice.amount_paid, invoice.balance_due) == (
        Decimal("40"),
        Decimal("35"),
        Decimal("5"),
    )
    # the document and the ledger agree: 1037's 30.00 and 1038's 5.00
    assert books.balance("1100") == _aging(books) == Decimal("35.00")


def test_with_a_payment_off_it_the_next_import_brings_the_change_in(
    client, books, paid_here
):
    _run(books, ["invoices", "ledger"])
    r = client.post(f"/api/payments/{paid_here['id']}/void")
    assert r.status_code == 200, r.text
    said = _run(books, ["invoices", "ledger"])
    assert HELD not in said
    invoice = books.invoice("1038")
    assert (invoice.total, invoice.balance_due) == (Decimal("30"), Decimal("30"))
    assert books.balance("1100") == _aging(books) == Decimal("60.00")
