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


# ---------------------------------------------------------------------------
# The legacy parent-account repair and the bank's reconciled lines
# ---------------------------------------------------------------------------


def _old_rollup_purchase(db_session, monkeypatch, *, reconciled):
    """Purchase 10 paid from the "Payroll" sub-account of Checking. An older
    ledger walker posted the payment to Checking, the parent; this build's
    walker reads it on Payroll. Checking's line may since have been
    reconciled against Checking's bank statement."""
    from datetime import date

    from app.models.banking import Reconciliation, ReconciliationStatus
    from app.services.accounting import create_journal_entry

    checking = Account(name="Checking", account_type=AccountType.ASSET, balance=0)
    expenses = Account(name="Supplies", account_type=AccountType.EXPENSE, balance=0)
    db_session.add_all([checking, expenses])
    db_session.flush()
    payroll = Account(
        name="Payroll",
        account_type=AccountType.ASSET,
        parent_id=checking.id,
        balance=0,
    )
    db_session.add(payroll)
    db_session.flush()
    for qbo_id, account in [("1", checking), ("11", payroll), ("2", expenses)]:
        db_session.add(
            QBOMapping(entity_type="account", qbo_id=qbo_id, slowbooks_id=account.id)
        )
    day = date(2026, 8, 3)
    txn = create_journal_entry(
        db_session,
        day,
        "QBO Purchase",
        [
            {"account_id": expenses.id, "debit": Decimal("50"), "credit": Decimal("0")},
            {"account_id": checking.id, "debit": Decimal("0"), "credit": Decimal("50")},
        ],
        source_type="qbo_ledger",
    )
    db_session.add(
        QBOMapping(
            entity_type="ledger",
            qbo_id="Purchase:10",
            slowbooks_id=txn.id,
            qbo_sync_token="old-rollup",
        )
    )
    db_session.flush()
    db_session.expire(txn, ["lines"])
    bank_line = next(line for line in txn.lines if line.credit)
    if reconciled:
        recon = Reconciliation(
            account_id=checking.id,
            statement_date=day,
            statement_balance=Decimal("-50"),
            status=ReconciliationStatus.COMPLETED,
        )
        db_session.add(recon)
        db_session.flush()
        bank_line.cleared = True
        bank_line.reconciliation_id = recon.id
    db_session.flush()
    client = LedgerClient(
        {"11": [("Purchase", "10", "", "-50")], "2": [("Purchase", "10", "", "50")]}
    )
    monkeypatch.setattr(qbo_ledger_import, "get_qbo_client", lambda db: client)
    return day, bank_line, checking, payroll


def test_the_repair_moves_an_unreconciled_parent_line(db_session, monkeypatch):
    day, bank_line, checking, payroll = _old_rollup_purchase(
        db_session, monkeypatch, reconciled=False
    )
    result = qbo_ledger_import.import_ledger(db_session, start=day, end=day)
    assert result == {"imported": 0, "errors": []}
    assert bank_line.account_id == payroll.id


def test_the_repair_leaves_a_line_in_a_completed_reconciliation(
    db_session, monkeypatch
):
    """Moving it would take a cleared payment out of Checking's finished
    reconciliation, which then no longer adds up; a reconciled entry cannot
    be voided for the same reason. It is reported instead."""
    day, bank_line, checking, payroll = _old_rollup_purchase(
        db_session, monkeypatch, reconciled=True
    )
    result = qbo_ledger_import.import_ledger(db_session, start=day, end=day)
    assert result["imported"] == 0
    assert result["errors"]
    assert result["errors"][0]["code"] == "IMPORT_QBO_CHANGE_NOT_APPLIED"
    assert "reconciled bank statement" in result["errors"][0]["message"]
    assert bank_line.account_id == checking.id


# ---------------------------------------------------------------------------
# Restoring a backup while a QuickBooks Online import is writing
# ---------------------------------------------------------------------------


def test_a_restore_waits_for_a_running_qbo_import(
    client, db_session, tmp_path, monkeypatch
):
    """#192 runs an import in the background, long after its page is left.
    A restore copied the backup over the books under it, and the import
    went on writing into the restored books with the accounts and mappings
    it had read before (tests/test_backups_per_company.py's live file)."""
    import app.database as db_module
    from app.services import backup_service, qbo_import_runs, storage
    from tests.test_backups_per_company import (
        _KeepTheTestConnection,
        _change,
        _make_books,
        _value,
    )

    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(storage, "backups_root", lambda: tmp_path / "private")
    live = tmp_path / "companies" / "harbor-light-bakery.db"
    live.parent.mkdir()
    _make_books(live, "Harbor Light Bakery", "original")
    backups = tmp_path / "backups"
    backups.mkdir()
    monkeypatch.setattr(backup_service, "DATABASE_URL", "sqlite:///" + live.as_posix())
    monkeypatch.setattr(backup_service, "BACKUP_DIR", backups)
    monkeypatch.setattr(db_module, "engine", _KeepTheTestConnection())
    r = client.put("/api/settings", json={"company_name": "Harbor Light Bakery"})
    assert r.status_code == 200, r.text
    name = client.post("/api/backups").json()["filename"]
    _change(live, "imported since")

    store = qbo_import_runs.store_for(db_session)
    run = store.reserve(["accounts"], "eric")
    r = client.post("/api/backups/restore", json={"filename": name})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "qbo_import_running"
    assert "QuickBooks Online import" in r.json()["detail"]["message"]
    assert _value(live) == "imported since"

    run["status"] = "completed"
    store.publish(run, "finish", "Completed")
    r = client.post("/api/backups/restore", json={"filename": name})
    assert r.status_code == 200, r.text
    assert _value(live) == "original"


# ---------------------------------------------------------------------------
# The invoice logo option (#192)
# ---------------------------------------------------------------------------


def test_the_invoice_logo_setting_is_true_or_false(client):
    """Anything but "false" printed the logo, so an API client's "no",
    "False" or "0" stored silently and changed nothing. It is one of two
    values, like the other yes/no settings."""
    for value in ["no", "False", "0", ""]:
        r = client.put("/api/settings", json={"invoice_show_logo": value})
        assert r.status_code == 422, (value, r.text)
        assert client.get("/api/settings").json()["invoice_show_logo"] == "true"
    r = client.put("/api/settings", json={"invoice_show_logo": "false"})
    assert r.status_code == 200, r.text
    assert r.json()["invoice_show_logo"] == "false"
