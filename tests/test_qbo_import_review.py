"""Review of #192's QBO journal and ledger import (2.18.0 integration)."""

from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.models.qbo_mapping import QBOMapping
from app.models.transactions import Transaction
from app.services import qbo_import, qbo_ledger_import
from tests.test_qbo_journal_import import QBOClient, _entry


@pytest.fixture
def qbo(db_session, monkeypatch):
    """Checking (QBO 1) and Expenses (QBO 2), mapped; the QBO client the
    journal tests use (tests/test_qbo_journal_import.py)."""
    accounts = {}
    for qbo_id, name, kind in [
        ("1", "Checking", AccountType.ASSET),
        ("2", "Expenses", AccountType.EXPENSE),
    ]:
        account = Account(name=name, account_type=kind, balance=Decimal("1000"))
        db_session.add(account)
        db_session.flush()
        db_session.add(
            QBOMapping(entity_type="account", qbo_id=qbo_id, slowbooks_id=account.id)
        )
        accounts[qbo_id] = account
    db_session.flush()
    client = QBOClient([_entry()])
    monkeypatch.setattr(qbo_import, "get_qbo_client", lambda db: client)
    monkeypatch.setattr(qbo_ledger_import, "get_qbo_client", lambda db: client)
    return client, accounts


def _foreign_entry(rate, debits, credits):
    entry = _entry("fx")
    entry["ExchangeRate"] = rate
    debit, credit = entry["Line"]
    entry["Line"] = [
        {**debit, "Id": str(index), "Amount": amount}
        for index, amount in enumerate(debits)
    ] + [
        {**credit, "Id": str(len(debits) + index), "Amount": amount}
        for index, amount in enumerate(credits)
    ]
    return entry


def test_a_foreign_journal_that_balances_in_its_currency_is_posted(db_session, qbo):
    """EUR 33.33 + 33.33 + 33.34 against EUR 100.00 at 1.2345: each line
    rounds to the cent on its own (41.15 + 41.15 + 41.16 = 123.46 against
    123.45), and the import called the journal unbalanced and held back
    every journal in the batch. Converted the way every foreign-currency
    posting is (currency.convert_lines), the cent lands on the largest
    line."""
    client, accounts = qbo
    client.entries = [_foreign_entry(1.2345, [33.33, 33.33, 33.34], [100.00])]
    assert qbo_import.import_journal_entries(db_session) == {
        "imported": 1,
        "errors": [],
    }
    txn = db_session.query(Transaction).one()
    debits = sorted(line.debit for line in txn.lines if line.debit)
    credits = [line.credit for line in txn.lines if line.credit]
    assert debits == [Decimal("41.15"), Decimal("41.15"), Decimal("41.16")]
    assert credits == [Decimal("123.46")]
    assert accounts["2"].balance == Decimal("123.46")


def test_a_foreign_journal_is_balanced_in_its_own_currency(db_session, qbo):
    """EUR 10.04 against EUR 10.00 at 0.1 comes to 1.00 against 1.00 in
    dollars; it is still four cents out in the journal's own currency."""
    client, accounts = qbo
    client.entries = [_foreign_entry(0.1, [10.04], [10.00])]
    result = qbo_import.import_journal_entries(db_session)
    assert result["imported"] == 0
    assert "does not balance" in result["errors"][0]["message"]
    assert db_session.query(Transaction).count() == 0
