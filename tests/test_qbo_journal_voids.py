"""A journal the QuickBooks Online import posted voids here like any journal
(#192 review, 2.18.0; #192 had refused it).

The void is the usual reversing entry, keyed by the journal it reverses;
the import mappings are marked so a later import leaves the journal as it
is here instead of reporting a mismatch or posting it again.
"""

from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.models.qbo_mapping import QBOMapping
from app.models.transactions import Transaction
from app.services import qbo_import, qbo_progress
from tests.test_qbo_journal_import import QBOClient, _entry


@pytest.fixture
def journals(db_session, monkeypatch):
    """Checking (QBO 1) and Expenses (QBO 2), and QBO journal 227 (25.54)."""
    for qbo_id, name, kind in [
        ("1", "Checking", AccountType.ASSET),
        ("2", "Expenses", AccountType.EXPENSE),
    ]:
        account = Account(name=name, account_type=kind, balance=Decimal("0"))
        db_session.add(account)
        db_session.flush()
        db_session.add(
            QBOMapping(entity_type="account", qbo_id=qbo_id, slowbooks_id=account.id)
        )
    db_session.flush()
    client = QBOClient([_entry()])
    monkeypatch.setattr(qbo_import, "get_qbo_client", lambda db: client)
    kept = []
    original = qbo_progress.skipped
    monkeypatch.setattr(
        qbo_progress,
        "skipped",
        lambda message="": (kept.append(message), original(message)),
    )
    assert qbo_import.import_journal_entries(db_session)["imported"] == 1
    db_session.commit()
    return client, kept


def test_a_qbo_journal_voids_here_and_a_later_import_leaves_it(
    client, db_session, journals
):
    source, kept = journals
    journal = db_session.query(Transaction).one()
    r = client.post(f"/api/journal/{journal.id}/void")
    assert r.status_code == 200, r.text
    reversal = db_session.query(Transaction).filter_by(source_type="qbo_journal_void")
    assert reversal.one().source_id == journal.id
    mapping = db_session.query(QBOMapping).filter_by(entity_type="journal_entry").one()
    assert mapping.qbo_sync_token == "changed-in-slowbooks"
    # QBO changes it later: still left as it is here, and never an error
    for line in source.entries[0]["Line"]:
        line["Amount"] = 30
    kept.clear()
    assert qbo_import.import_journal_entries(db_session) == {
        "imported": 0,
        "errors": [],
    }
    assert kept == ["Changed in SlowBooks; kept as it is here"]
    assert db_session.query(Transaction).count() == 2


def test_a_journal_is_voided_once(client, db_session, journals):
    journal = db_session.query(Transaction).one()
    assert client.post(f"/api/journal/{journal.id}/void").status_code == 200
    r = client.post(f"/api/journal/{journal.id}/void")
    assert r.status_code == 400
    assert r.json()["detail"] == "This journal entry has already been voided."
    assert db_session.query(Transaction).count() == 2


def test_a_manual_journal_is_voided_once_too(client, db_session, seed_accounts):
    """The journal void never checked for an earlier void: Void on the
    Journal Entries page reversed a manual entry again on every click."""
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-08-03",
            "description": "Accrual",
            "lines": [
                {"account_id": seed_accounts["6000"].id, "debit": 12, "credit": 0},
                {"account_id": seed_accounts["1000"].id, "debit": 0, "credit": 12},
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    entry = r.json()["id"]
    assert client.post(f"/api/journal/{entry}/void").status_code == 200
    assert client.post(f"/api/journal/{entry}/void").status_code == 400
    assert (
        db_session.query(Transaction).filter_by(source_type="manual_void").count() == 1
    )


def test_an_entry_voided_by_its_own_void_is_not_voided_again_as_a_journal(
    client, db_session, seed_accounts
):
    """A bank entry opens in the journal view from the register; one its own
    void already reversed (bank_entry_void) is not reversed a second time
    through the journal's."""
    from tests.test_bank_entries import _entry

    r = _entry(client, seed_accounts)
    assert r.status_code == 201, r.text
    entry = r.json()["id"]  # the journal entry
    assert client.post(f"/api/banking/entries/{entry}/void").status_code == 200
    r = client.post(f"/api/journal/{entry}/void")
    assert r.status_code == 400
    assert r.json()["detail"] == "This journal entry has already been voided."
