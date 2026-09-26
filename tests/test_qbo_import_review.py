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


# ---------------------------------------------------------------------------
# A QBO transaction that is a local document with its own posting
# ---------------------------------------------------------------------------

_GL_COLUMNS = [
    "Date",
    "Transaction Type",
    "Num",
    "Name",
    "Memo/Description",
    "Split",
    "Amount",
    "Balance",
]


class LedgerClient:
    """A General Ledger report: {QBO account id: [(type, id, number, amount)]}."""

    def __init__(self, sections):
        self.sections = sections

    def get_report(self, name, qs):
        assert name == "GeneralLedger"

        def row(txn_type, txn_id, number, amount):
            values = [qs["start_date"], txn_type, number, "", "", "", amount, "0"]
            return {
                "type": "Data",
                "ColData": [
                    {"value": value, **({"id": txn_id} if index == 1 else {})}
                    for index, value in enumerate(values)
                ],
            }

        return {
            "Header": {
                "StartPeriod": qs["start_date"],
                "EndPeriod": qs["end_date"],
                "ReportBasis": "Accrual",
            },
            "Columns": {"Column": [{"ColTitle": title} for title in _GL_COLUMNS]},
            "Rows": {
                "Row": [
                    {
                        "type": "Section",
                        "Header": {"ColData": [{"id": account_id}]},
                        "Rows": {"Row": [row(*line) for line in lines]},
                    }
                    for account_id, lines in self.sections.items()
                ]
            },
        }


def test_a_qbo_invoice_that_is_a_posted_local_invoice_is_not_posted_again(
    db_session, seed_accounts, seed_customer, monkeypatch
):
    """An invoice written in SlowBooks and exported to QBO (export_invoices
    maps it), or a QBO invoice the import matched to a local one by its
    number, is one invoice: QBO's ledger lines for it are its own posting a
    second time. A QBO invoice imported as a document has no posting of its
    own, so its ledger lines are posted."""
    from datetime import date

    from app.models.invoices import Invoice, InvoiceStatus
    from app.services.accounting import create_journal_entry
    from app.services.bank_register import gl_balances

    ar, income = seed_accounts["1100"], seed_accounts["4000"]
    for qbo_id, account in [("84", ar), ("79", income)]:
        db_session.add(
            QBOMapping(entity_type="account", qbo_id=qbo_id, slowbooks_id=account.id)
        )
    day = date(2026, 8, 3)
    written_here = Invoice(
        invoice_number="1001",
        customer_id=seed_customer.id,
        date=day,
        status=InvoiceStatus.SENT,
        subtotal=Decimal("100"),
        total=Decimal("100"),
        balance_due=Decimal("100"),
    )
    imported = Invoice(
        invoice_number="1002",
        customer_id=seed_customer.id,
        date=day,
        status=InvoiceStatus.SENT,
        subtotal=Decimal("40"),
        total=Decimal("40"),
        balance_due=Decimal("40"),
    )
    db_session.add_all([written_here, imported])
    db_session.flush()
    written_here.transaction_id = create_journal_entry(
        db_session,
        day,
        "Invoice 1001",
        [
            {"account_id": ar.id, "debit": Decimal("100"), "credit": Decimal("0")},
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("100")},
        ],
        source_type="invoice",
        source_id=written_here.id,
    ).id
    for qbo_id, invoice in [("55", written_here), ("56", imported)]:
        db_session.add(
            QBOMapping(entity_type="invoice", qbo_id=qbo_id, slowbooks_id=invoice.id)
        )
    db_session.flush()
    client = LedgerClient(
        {
            "84": [("Invoice", "55", "1001", "100"), ("Invoice", "56", "1002", "40")],
            "79": [("Invoice", "55", "1001", "100"), ("Invoice", "56", "1002", "40")],
        }
    )
    monkeypatch.setattr(qbo_ledger_import, "get_qbo_client", lambda db: client)

    for _ in range(2):
        result = qbo_ledger_import.import_ledger(db_session, start=day, end=day)
        assert result["errors"] == []
        balances = gl_balances(db_session, [ar.id, income.id])
        assert balances[ar.id] == Decimal("140")
        assert balances[income.id] == Decimal("140")
    posted = db_session.query(QBOMapping).filter_by(entity_type="ledger").all()
    assert [mapping.qbo_id for mapping in posted] == ["Invoice:56"]
