"""What the QuickBooks Online import log says of transactions changed in
SlowBooks (2.18.0).

Every later import keeps a transaction voided or edited here as it is
here. The log said so with a line per transaction on every run, so a
company that had voided a dozen gave a dozen lines each time. Now each run
says it in one line with the count ("12 transactions changed in SlowBooks
were kept as they are here"), and a transaction gets a line of its own
only the first time an import keeps it.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.qbo_mapping import QBOMapping
from app.models.payments import Payment
from app.models.transactions import Transaction
from app.services import (
    qbo_import,
    qbo_import_runs as runs,
    qbo_ledger_import,
    storage,
)
from app.services.qbo_documents import mark_changed_here
from tests.test_qbo_import_review import LedgerClient
from tests.test_qbo_journal_voids import journals  # noqa: F401 (fixture)
from tests.test_qbo_managed_voids import Books

KEPT = "Changed in SlowBooks; kept as it is here"
STEPS = ["invoices", "payments", "sales_receipts", "ledger"]


@pytest.fixture(autouse=True)
def private_log_root(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "backups_root", lambda: tmp_path / "private")


@pytest.fixture
def books(db_session, seed_accounts, monkeypatch):
    return Books(db_session, seed_accounts, monkeypatch)


def _run(books):
    """One import run, as the QuickBooks Online page runs it: its log."""
    with runs.synchronous_run(books.db, STEPS, "owner"):
        books.documents()
        books.ledger()
    return runs.store_for(books.db).latest()["events"]


def _said(events, message):
    return [(e["item_id"], e["item_label"]) for e in events if e["message"] == message]


def test_each_run_says_in_one_line_how_many_were_kept(client, books):
    books.documents()
    books.ledger()
    invoice = books.invoice("1038")
    assert client.post(f"/api/invoices/{invoice.id}/void").status_code == 200
    payment = books.db.query(Payment).filter_by(amount=Decimal("20")).one()
    assert client.post(f"/api/payments/{payment.id}/void").status_code == 200
    summary = "2 transactions changed in SlowBooks were kept as they are here"

    first = _run(books)
    # the first time: a line of its own for each, and the count
    assert _said(first, KEPT) == [("Invoice:133", "1038"), ("Payment:131", "")]
    assert _said(first, summary) == [("", "")]
    assert [e["action"] for e in first[-2:]] == ["summary", "finish"]

    second = _run(books)
    # later runs: the count alone
    assert _said(second, KEPT) == []
    assert _said(second, summary) == [("", "")]
    assert runs.store_for(books.db).latest()["run"]["counters"]["errors"] == 0


def test_one_kept_reads_as_one(client, books):
    books.documents()
    books.ledger()
    invoice = books.invoice("1038")
    assert client.post(f"/api/invoices/{invoice.id}/void").status_code == 200
    events = _run(books)
    assert _said(events, "1 transaction changed in SlowBooks was kept as it is here")


def test_a_run_that_keeps_nothing_says_nothing_of_it(books):
    books.documents()
    books.ledger()
    events = _run(books)
    assert not [e for e in events if "changed in SlowBooks" in e["message"]]


def test_a_journal_both_imports_keep_is_one_transaction_in_the_log(
    client, db_session, journals, monkeypatch  # noqa: F811
):
    """The JournalEntry import and the ledger import both keep a journal
    voided here: one line of its own, counted once."""
    journal = db_session.query(Transaction).one()
    assert client.post(f"/api/journal/{journal.id}/void").status_code == 200
    rows = {"2": [("Journal Entry", "227", "ADJ-7", "25.54")]}
    rows["1"] = [("Journal Entry", "227", "ADJ-7", "-25.54")]
    monkeypatch.setattr(
        qbo_ledger_import, "get_qbo_client", lambda db: LedgerClient(rows)
    )
    day = date(2026, 8, 3)
    for _ in range(2):
        with runs.synchronous_run(db_session, ["journal_entries", "ledger"], "owner"):
            assert qbo_import.import_journal_entries(db_session)["errors"] == []
            db_session.commit()
            result = qbo_ledger_import.import_ledger(db_session, start=day, end=day)
            assert result["errors"] == []
            db_session.commit()
        events = runs.store_for(db_session).latest()["events"]
        once = "1 transaction changed in SlowBooks was kept as it is here"
        assert len(_said(events, once)) == 1
        assert len(_said(events, KEPT)) == (1 if _ == 0 else 0)
    assert db_session.query(Transaction).count() == 2  # the journal and its void


def test_a_kept_transaction_changed_here_again_is_not_said_again(db_session):
    mapping = QBOMapping(
        entity_type="ledger",
        qbo_id="Invoice:133",
        slowbooks_id=1,
        qbo_sync_token="kept-in-slowbooks",
    )
    db_session.add(mapping)
    db_session.flush()
    mark_changed_here(db_session, mapping)
    assert mapping.qbo_sync_token == "kept-in-slowbooks"
