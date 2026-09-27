"""Changes made in QuickBooks Online after an import flow in on the next one
(#192 review, 2.18.0; owner: "I just want it to work").

#192 reported any difference between QBO and an imported posting as an
error that held back the whole batch, and ignored a transaction QBO had
voided. Now:

- a transaction QBO changed: its imported posting is reversed and the new
  version posted;
- one QBO voided (its rows all 0.00), or a journal QBO deleted (missing from
  a complete list of QBO's journals): its posting is reversed, and the
  document the import made for it is voided;
- one that can't be changed here (closed period, reconciled line, a deposit
  made here): the log says so in words and skips it; the rest goes on;
- every reversal and repost is in the import log, with the amounts.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.models.invoices import InvoiceStatus
from app.models.payments import Payment
from app.models.qbo_mapping import QBOMapping
from app.models.settings import Settings
from app.models.transactions import Transaction
from app.services import qbo_import, qbo_progress
from tests.test_qbo_journal_import import QBOClient, _entry
from tests.test_qbo_ledger_import import _import as _import_ledger
from tests.test_qbo_ledger_import import _posting, _setup
from tests.test_qbo_managed_voids import Books

DAY = date(2026, 8, 3)


@pytest.fixture
def log(monkeypatch):
    """(code, message) of every event the imports log."""
    events = []
    original = qbo_progress.emit

    def emit(action, message, **fields):
        events.append((fields.get("code", ""), message))
        return original(action, message, **fields)

    monkeypatch.setattr(qbo_progress, "emit", emit)
    return events


def _logged(log, code):
    return [message for logged, message in log if logged == code]


@pytest.fixture
def journals(db_session, monkeypatch):
    """Checking (QBO 1) and Expenses (QBO 2), and QBO journal 227 (25.54),
    imported."""
    accounts = {}
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
        accounts[qbo_id] = account
    db_session.flush()
    client = QBOClient([_entry()])
    monkeypatch.setattr(qbo_import, "get_qbo_client", lambda db: client)
    assert qbo_import.import_journal_entries(db_session)["imported"] == 1
    return client, accounts


def _journal_227(db):
    mapping = (
        db.query(QBOMapping).filter_by(entity_type="journal_entry", qbo_id="227").one()
    )
    return mapping, db.get(Transaction, mapping.slowbooks_id)


def test_a_journal_changed_in_qbo_is_posted_again_and_the_rest_imports(
    db_session, journals, log
):
    client, accounts = journals
    _, first = _journal_227(db_session)
    for line in client.entries[0]["Line"]:
        line["Amount"] = 30
    client.entries.append(_entry("228", amount=12))
    assert qbo_import.import_journal_entries(db_session) == {
        "imported": 1,
        "errors": [],
    }
    mapping, now = _journal_227(db_session)
    assert now.id != first.id
    assert accounts["2"].balance == Decimal("42.00")  # 30 + 12, not 25.54 more
    applied = _logged(log, "IMPORT_QBO_CHANGE_APPLIED")
    assert len(applied) == 1
    assert "JournalEntry QBO #227 (document ADJ-7) changed in QuickBooks Online" in (
        applied[0]
    )
    assert f"local #{first.id}, 25.54" in applied[0]
    assert f"local #{now.id}, 30.00" in applied[0]
    count = db_session.query(Transaction).count()
    assert qbo_import.import_journal_entries(db_session)["errors"] == []
    assert db_session.query(Transaction).count() == count  # applied once


def test_a_journal_voided_in_qbo_is_reversed(db_session, journals, log):
    client, accounts = journals
    for line in client.entries[0]["Line"]:
        line["Amount"] = 0  # a voided QBO journal keeps its lines at 0.00
    client.entries.append(_entry("228", amount=12))
    assert qbo_import.import_journal_entries(db_session) == {
        "imported": 1,
        "errors": [],
    }
    mapping, journal = _journal_227(db_session)
    assert mapping.qbo_sync_token == "voided-in-qbo"
    assert accounts["2"].balance == Decimal("12.00")
    assert len(_logged(log, "IMPORT_QBO_VOID_APPLIED")) == 1
    count = db_session.query(Transaction).count()
    assert qbo_import.import_journal_entries(db_session)["errors"] == []
    assert db_session.query(Transaction).count() == count


def test_a_journal_deleted_from_qbo_is_reversed(db_session, journals, log):
    client, accounts = journals
    client.entries.append(_entry("229", amount=5))
    assert qbo_import.import_journal_entries(db_session)["imported"] == 1
    # the complete list: 229 still there, 227 gone, 228 new
    client.entries = [_entry("229", amount=5), _entry("228", amount=12)]
    assert qbo_import.import_journal_entries(db_session)["errors"] == []
    mapping, _ = _journal_227(db_session)
    assert mapping.qbo_sync_token == "deleted-in-qbo"
    assert accounts["2"].balance == Decimal("17.00")  # 5 + 12
    assert "JournalEntry QBO #227" in _logged(log, "IMPORT_QBO_DELETE_APPLIED")[0]


def test_a_journal_missing_from_a_list_that_failed_to_load_stays(
    db_session, journals, monkeypatch
):
    client, accounts = journals

    def failed(query):
        raise RuntimeError("remote query failure")

    monkeypatch.setattr(client, "query", failed)
    result = qbo_import.import_journal_entries(db_session)
    assert "Failed to query QBO" in result["errors"][0]["message"]
    assert accounts["2"].balance == Decimal("25.54")


def test_a_journal_missing_from_a_list_with_an_unsound_id_stays(db_session, journals):
    client, accounts = journals
    client.entries = [_entry("228", amount=12), _entry("228", amount=12)]
    result = qbo_import.import_journal_entries(db_session)
    assert any("duplicate" in error["message"] for error in result["errors"])
    mapping, _ = _journal_227(db_session)
    assert mapping.qbo_sync_token == "0"
    assert accounts["2"].balance == Decimal("25.54")


def test_a_change_that_cannot_be_applied_is_said_and_the_rest_goes_on(
    db_session, journals
):
    client, accounts = journals
    db_session.add(Settings(key="closing_date", value="2026-08-31"))
    db_session.flush()
    for line in client.entries[0]["Line"]:
        line["Amount"] = 30
    later = _entry("228", amount=12)
    later["TxnDate"] = "2026-09-02"
    client.entries.append(later)
    result = qbo_import.import_journal_entries(db_session)
    assert result["imported"] == 1
    [error] = result["errors"]
    assert error["code"] == "IMPORT_QBO_CHANGE_NOT_APPLIED"
    assert "books are closed through 2026-08-31" in error["message"]
    assert "The books keep what was imported." in error["message"]
    assert accounts["2"].balance == Decimal("37.54")  # 25.54 kept, and 12


def test_a_ledger_posting_changed_in_qbo_is_posted_again_and_the_rest_imports(
    db_session, monkeypatch, log
):
    postings = {
        "1": [_posting("10", "Purchase", "-50")],
        "2": [_posting("10", "Purchase", "50")],
    }
    _setup(db_session, monkeypatch, postings)
    assert _import_ledger(db_session)["imported"] == 1
    postings["1"][0]["ColData"][6]["value"] = "-60"
    postings["2"][0]["ColData"][6]["value"] = "60"
    postings["1"].append(_posting("11", "Deposit", "100"))
    postings["3"] = [_posting("11", "Deposit", "100")]
    assert _import_ledger(db_session) == {"imported": 1, "errors": []}
    expenses = db_session.query(Account).filter_by(name="Expenses").one()
    assert expenses.balance == Decimal("60.00")
    [applied] = _logged(log, "IMPORT_QBO_CHANGE_APPLIED")
    assert "Purchase QBO #10 (document 10) changed in QuickBooks Online" in applied
    assert ", 50.00) was reversed" in applied and ", 60.00)" in applied


def test_a_ledger_change_in_a_closed_period_is_said_and_the_rest_goes_on(
    db_session, monkeypatch
):
    postings = {
        "1": [_posting("10", "Purchase", "-50")],
        "2": [_posting("10", "Purchase", "50")],
    }
    _setup(db_session, monkeypatch, postings)
    assert _import_ledger(db_session)["imported"] == 1
    db_session.add(Settings(key="closing_date", value="2026-08-03"))
    db_session.flush()
    postings["2"][0]["ColData"][6]["value"] = "60"
    postings["1"][0]["ColData"][6]["value"] = "-60"
    result = _import_ledger(db_session)
    [error] = result["errors"]
    assert error["code"] == "IMPORT_QBO_CHANGE_NOT_APPLIED"
    assert error["qbo_id"] == "Purchase:10"
    expenses = db_session.query(Account).filter_by(name="Expenses").one()
    assert expenses.balance == Decimal("50.00")


def test_a_transaction_qbo_no_longer_lists_stays_as_imported(db_session, monkeypatch):
    """The ledger report alone does not tell a deletion reliably (only a
    complete list of QBO's journals does), so nothing is reversed."""
    postings = {
        "1": [_posting("10", "Purchase", "-50")],
        "2": [_posting("10", "Purchase", "50")],
    }
    _setup(db_session, monkeypatch, postings)
    assert _import_ledger(db_session)["imported"] == 1
    postings["1"].clear()
    postings["2"].clear()
    assert _import_ledger(db_session) == {"imported": 0, "errors": []}
    expenses = db_session.query(Account).filter_by(name="Expenses").one()
    assert expenses.balance == Decimal("50.00")


@pytest.fixture
def books(db_session, seed_accounts, monkeypatch):
    return Books(db_session, seed_accounts, monkeypatch)


def test_an_invoice_voided_in_qbo_is_reversed_and_voided_here(books, log):
    books.documents()
    books.ledger()
    original = books._gl

    def voided():
        client = original()
        for rows in client.sections.values():
            for row in rows:
                if row[:2] == ("Invoice", "133"):
                    rows[rows.index(row)] = (*row[:3], "0.00")
        return client

    books._gl = voided
    books.ledger()
    invoice = books.invoice("1038")
    assert invoice.status == InvoiceStatus.VOID
    assert books.balance("1100") == Decimal("30.00")
    [applied] = _logged(log, "IMPORT_QBO_VOID_APPLIED")
    assert "Invoice QBO #133 (document 1038) was voided in QuickBooks Online" in applied
    assert ", 40.00) was reversed, and invoice 1038 voided" in applied
    ledger = books.db.query(QBOMapping).filter_by(qbo_id="Invoice:133").one()
    assert ledger.qbo_sync_token == "voided-in-qbo"
    before = books.postings()
    books.ledger()
    assert books.postings() == before


def test_a_payment_voided_in_qbo_opens_its_invoice_again(books):
    books.documents()
    books.ledger()
    original = books._gl

    def voided():
        client = original()
        for rows in client.sections.values():
            for row in rows:
                if row[:2] == ("Payment", "131"):
                    rows[rows.index(row)] = (*row[:3], "0.00")
        return client

    books._gl = voided
    books.ledger()
    payment = books.db.query(Payment).filter_by(amount=Decimal("20")).one()
    assert payment.is_voided is True
    paid = books.invoice("1037")
    assert (paid.amount_paid, paid.balance_due) == (Decimal("0"), Decimal("50"))
    assert books.balance("1100") == Decimal("90.00")
    assert books.balance("1200") == Decimal("30.00")


@pytest.mark.parametrize("in_qbo", ["45", "0.00"])  # changed there, or voided there
def test_a_change_to_a_document_changed_here_is_kept_as_it_is_here(
    client, books, in_qbo
):
    """Voided here, then changed or voided in QBO: kept as it is here, with
    one line saying so, never an error."""
    books.documents()
    books.ledger()
    invoice = books.invoice("1038")
    assert client.post(f"/api/invoices/{invoice.id}/void").status_code == 200
    original = books._gl

    def changed():
        client = original()
        for rows in client.sections.values():
            for row in rows:
                if row[:2] == ("Invoice", "133"):
                    rows[rows.index(row)] = (*row[:3], in_qbo)
        return client

    books._gl = changed
    before = books.postings()
    books.kept.clear()
    assert books.ledger() == {"imported": 0, "errors": []}
    assert books.postings() == before
    assert books.kept.count("Changed in SlowBooks; kept as it is here") == 1


def test_a_journal_qbo_moved_to_another_day_is_posted_on_that_day(db_session, journals):
    client, _ = journals
    client.entries[0]["TxnDate"] = "2026-08-04"
    assert qbo_import.import_journal_entries(db_session)["errors"] == []
    _, now = _journal_227(db_session)
    assert now.date == date(2026, 8, 4)


def test_a_posting_qbo_voided_and_shows_again_is_posted_again(db_session, monkeypatch):
    postings = {
        "1": [_posting("10", "Purchase", "-50")],
        "2": [_posting("10", "Purchase", "50")],
    }
    _setup(db_session, monkeypatch, postings)
    assert _import_ledger(db_session)["imported"] == 1
    expenses = db_session.query(Account).filter_by(name="Expenses").one()
    for account, amount in (("1", "0.00"), ("2", "0.00")):
        postings[account][0]["ColData"][6]["value"] = amount
    assert _import_ledger(db_session)["errors"] == []
    assert expenses.balance == Decimal("0.00")
    for account, amount in (("1", "-50"), ("2", "50")):
        postings[account][0]["ColData"][6]["value"] = amount
    assert _import_ledger(db_session)["errors"] == []
    assert expenses.balance == Decimal("50.00")
    ledger = db_session.query(QBOMapping).filter_by(qbo_id="Purchase:10").one()
    assert ledger.qbo_sync_token not in ("voided-in-qbo", "changed-in-slowbooks")


# ---------------------------------------------------------------------------
# A document the import made, changed in QBO: brought up to date with it
# ---------------------------------------------------------------------------


def _without_empty(value):
    if isinstance(value, dict):
        return {k: _without_empty(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_without_empty(v) for v in value]
    return value


def _edit_in_qbo(books, sdk, index, **fields):
    """QBO's copy of a document, changed (SyncToken moves on)."""
    data = _without_empty(books.sources[sdk][index].to_dict())
    data.update(fields, SyncToken="1")
    books.sources[sdk][index] = sdk.from_json(data)


def _gl_amount(books, qbo_type, qbo_id, amounts):
    """QBO's General Ledger with one transaction's rows changed:
    {QBO account id: new amount}."""
    original = books._gl

    def changed():
        client = original()
        for account, rows in client.sections.items():
            for row in list(rows):
                if row[:2] == (qbo_type, qbo_id) and account in amounts:
                    rows[rows.index(row)] = (*row[:3], amounts[account])
        return client

    books._gl = changed


def test_an_invoice_changed_in_qbo_is_brought_up_to_date_with_its_posting(books, log):
    from quickbooks.objects.invoice import Invoice as QBOInvoice

    from tests.test_qbo_managed_voids import _sale_line

    books.documents()
    books.ledger()
    _edit_in_qbo(
        books, QBOInvoice, 1, TotalAmt=45, Balance=45, Line=[_sale_line(45, "Catering")]
    )
    _gl_amount(books, "Invoice", "133", {"84": "45", "79": "45"})
    books.documents()
    books.ledger()
    invoice = books.invoice("1038")
    assert (invoice.total, invoice.balance_due) == (Decimal("45"), Decimal("45"))
    assert books.balance("1100") == Decimal("75.00")  # A/R Aging's 30 + 45
    applied = _logged(log, "IMPORT_QBO_CHANGE_APPLIED")
    assert any("invoice 1038 brought up to date here" in m for m in applied)
    assert any("Invoice QBO #133 (document 1038) changed" in m for m in applied)


def test_a_payment_changed_in_qbo_is_brought_up_to_date(books):
    from quickbooks.objects.payment import Payment as QBOPayment

    books.documents()
    books.ledger()
    _edit_in_qbo(
        books,
        QBOPayment,
        0,
        TotalAmt=25,
        Line=[{"Amount": 25, "LinkedTxn": [{"TxnId": "130", "TxnType": "Invoice"}]}],
    )
    _gl_amount(books, "Payment", "131", {"84": "-25", "35": "25"})
    books.documents()
    books.ledger()
    paid = books.invoice("1037")
    assert (paid.amount_paid, paid.balance_due) == (Decimal("25"), Decimal("25"))
    assert books.balance("1100") == Decimal("65.00")
    assert books.balance("1200") == Decimal("55.00")


def test_a_sales_receipt_changed_in_qbo_is_brought_up_to_date(books):
    from quickbooks.objects.salesreceipt import SalesReceipt as QBOSalesReceipt

    from tests.test_qbo_managed_voids import _sale_line

    books.documents()
    books.ledger()
    _edit_in_qbo(books, QBOSalesReceipt, 0, TotalAmt=35, Line=[_sale_line(35, "Lunch")])
    _gl_amount(books, "Sales Receipt", "132", {"79": "35", "35": "35"})
    books.documents()
    books.ledger()
    receipt = books.invoice("SR-9")
    assert (receipt.total, receipt.balance_due) == (Decimal("35"), Decimal("0"))
    assert receipt.payment_allocations[0].payment.amount == Decimal("35")
    assert books.balance("4000") == Decimal("125.00")
    assert books.balance("1200") == Decimal("55.00")


def test_a_document_change_that_cannot_be_applied_leaves_both_as_imported(books):
    from quickbooks.objects.invoice import Invoice as QBOInvoice

    from tests.test_qbo_managed_voids import _sale_line

    books.documents()
    books.ledger()
    books.db.add(Settings(key="closing_date", value="2026-08-31"))
    books.db.commit()
    _edit_in_qbo(
        books, QBOInvoice, 1, TotalAmt=45, Balance=45, Line=[_sale_line(45, "Catering")]
    )
    _gl_amount(books, "Invoice", "133", {"84": "45", "79": "45"})
    result = qbo_import.import_invoices(books.db)
    [error] = result["errors"]
    assert error["code"] == "IMPORT_QBO_CHANGE_NOT_APPLIED"
    assert "The invoice here keeps what was imported." in error["message"]
    result = qbo_ledger_import_run(books)
    assert [e["code"] for e in result["errors"]] == ["IMPORT_QBO_CHANGE_NOT_APPLIED"]
    assert books.invoice("1038").total == Decimal("40")
    assert books.balance("1100") == Decimal("70.00")


def qbo_ledger_import_run(books):
    from app.services import qbo_ledger_import

    return qbo_ledger_import.import_ledger(books.db, start=DAY, end=DAY)


def test_a_document_changed_here_is_not_brought_back_to_qbos_version(client, books):
    from quickbooks.objects.invoice import Invoice as QBOInvoice

    from tests.test_qbo_managed_voids import _sale_line

    books.documents()
    books.ledger()
    invoice = books.invoice("1038")
    r = client.put(
        f"/api/invoices/{invoice.id}",
        json={"lines": [{"description": "Catering", "quantity": 1, "rate": 55}]},
    )
    assert r.status_code == 200, r.text
    _edit_in_qbo(
        books, QBOInvoice, 1, TotalAmt=45, Balance=45, Line=[_sale_line(45, "Catering")]
    )
    books.documents()
    assert books.invoice("1038").total == Decimal("55")


def test_a_list_holding_none_of_the_imported_journals_deletes_nothing(
    db_session, journals, log
):
    """Another QBO company connected: its list is complete and sound, and
    none of the journals imported before is in it."""
    client, accounts = journals
    client.entries = [_entry("900", amount=12), _entry("901", amount=3)]
    assert qbo_import.import_journal_entries(db_session)["errors"] == []
    mapping, _ = _journal_227(db_session)
    assert mapping.qbo_sync_token == "0"
    assert accounts["2"].balance == Decimal("40.54")  # 25.54 kept, 12 + 3 new
    assert _logged(log, "IMPORT_QBO_DELETE_NOT_APPLIED")
