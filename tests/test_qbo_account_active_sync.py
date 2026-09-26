"""QuickBooks Online's Active flag reaches only accounts that carry nothing
but the QBO import's postings (#192 review, 2.18.0).

#192 syncs is_active on every re-import of Accounts, for accounts it maps
by QBO id and for local accounts it matches by name. A local account in
use here was switched off, and dropped out of every picker, because QBO
has an inactive account of the same name.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.models.qbo_mapping import QBOMapping
from app.services import qbo_import
from app.services.accounting import create_journal_entry
from tests.test_qbo_bank_account_import import _mock_accounts, _qbo_account


@pytest.fixture
def chart(db_session):
    checking = Account(name="Checking", account_type=AccountType.ASSET, balance=0)
    db_session.add(checking)
    db_session.flush()

    def account(name, *, qbo_id=None, active=True, posted=None):
        """A local account; `posted` = the source_type of one posting on it."""
        acct = Account(
            name=name, account_type=AccountType.EXPENSE, is_active=active, balance=0
        )
        db_session.add(acct)
        db_session.flush()
        if qbo_id:
            db_session.add(
                QBOMapping(entity_type="account", qbo_id=qbo_id, slowbooks_id=acct.id)
            )
        if posted:
            create_journal_entry(
                db_session,
                date(2026, 8, 3),
                f"{posted} posting",
                [
                    {"account_id": acct.id, "debit": Decimal("12"), "credit": 0},
                    {"account_id": checking.id, "debit": 0, "credit": Decimal("12")},
                ],
                source_type=posted,
            )
        db_session.flush()
        return acct

    return account


def _qbo(number, name, active):
    row = _qbo_account(number, name)
    row.Active = active
    return row


def _import(db_session, monkeypatch, rows):
    _mock_accounts(
        monkeypatch,
        [row for row in rows if row.Active],
        inactive=[row for row in rows if not row.Active],
    )
    result = qbo_import.import_accounts(db_session)
    db_session.flush()
    assert result["errors"] == []


def test_an_inactive_qbo_account_of_the_same_name_leaves_a_used_account_active(
    db_session, monkeypatch, chart
):
    supplies = chart("Office Supplies", posted="expense")
    _import(db_session, monkeypatch, [_qbo("201", "Office Supplies", active=False)])
    assert supplies.is_active is True
    # ...and it is mapped, so the ledger import can post to it
    assert (
        db_session.query(QBOMapping).filter_by(qbo_id="201").one().slowbooks_id
        == supplies.id
    )


def test_a_mapped_account_in_use_here_keeps_its_own_active_flag(
    db_session, monkeypatch, chart
):
    used = chart("Used Here", qbo_id="204", posted="manual")
    closed = chart("Closed Here", qbo_id="205", active=False, posted="expense")
    _import(
        db_session,
        monkeypatch,
        [
            _qbo("204", "Used Here", active=False),
            _qbo("205", "Closed Here", active=True),
        ],
    )
    assert used.is_active is True
    assert closed.is_active is False


def test_accounts_carrying_only_the_qbo_import_follow_qbo(
    db_session, monkeypatch, chart
):
    unused = chart("Old Promotions")  # matched by name, never posted to
    qbo_only = chart("QBO Only", qbo_id="203", posted="qbo_ledger")
    back = chart("Back In Use", qbo_id="206", active=False, posted="qbo_journal")
    _import(
        db_session,
        monkeypatch,
        [
            _qbo("202", "Old Promotions", active=False),
            _qbo("203", "QBO Only", active=False),
            _qbo("206", "Back In Use", active=True),
        ],
    )
    assert unused.is_active is False
    assert qbo_only.is_active is False
    assert back.is_active is True
