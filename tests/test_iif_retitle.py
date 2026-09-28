"""QuickBooks IIF import: field quotes and ALL-CAPS names (#195, @TheLocalW).

- QuickBooks wraps a field holding a comma in double quotes ("ACME, Inc.",
  "99,250.02") and writes a quote inside one twice. The quotes are
  delimiters: they no longer reach the name.
- ALL-CAPS names ("BOB JONES") are imported in normal capitalization only
  when the person importing ticks "Change ALL-CAPS names": the word lists
  that decide what stays in capitals cannot know every initialism a business
  uses, so the import shows a few of the file's own names first and leaves
  the choice to them. Item names are kept as typed (they are often part
  numbers).
- As merged, the renaming reached the list rows only: a bill for "ACME
  TOOLING, INC." could not find the vendor imported as "ACME Tooling, Inc.",
  a payment for BOB JONES made a second customer, and re-importing a list an
  earlier version had imported duplicated every ALL-CAPS name. A name is now
  rewritten everywhere the file uses it, and the import matches a name
  already in the books in any case.
"""

from decimal import Decimal

from app.models.accounts import Account
from app.models.bills import Bill
from app.models.contacts import Customer, Vendor
from app.models.invoices import Invoice
from app.models.items import Item
from app.models.jobs import Job
from app.models.payments import Payment
from app.services.iif_import import (
    _fields_to_dict,
    _unquote_iif,
    import_all,
    validate_iif,
)

LISTS = (
    "!VEND\tNAME\n" 'VEND\t"ACME TOOLING, INC."\n' "!CUST\tNAME\n" "CUST\tBOB JONES\n"
)
TRANSACTIONS = (
    "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tDUEDATE\tTERMS\tMEMO\n"
    "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO\n"
    "!ENDTRNS\n"
    'TRNS\tBILL\t05/01/2026\tAccounts Payable\t"ACME TOOLING, INC."\t-40.00\tB-1'
    "\t05/31/2026\tNet 30\tbits\n"
    'SPL\tBILL\t05/01/2026\tOffice Supplies\t"ACME TOOLING, INC."\t40.00\tB-1\tbits\n'
    "ENDTRNS\n"
    "TRNS\tINVOICE\t05/02/2026\tAccounts Receivable\tBOB JONES\t100.00\tI-1\t\t\tjob\n"
    "SPL\tINVOICE\t05/02/2026\tService Income\tBOB JONES\t-100.00\tI-1\tjob\n"
    "ENDTRNS\n"
    "TRNS\tPAYMENT\t05/03/2026\tUndeposited Funds\tBOB JONES\t100.00\tP-1\t\t\tpaid\n"
    "SPL\tPAYMENT\t05/03/2026\tAccounts Receivable\tBOB JONES\t-100.00\tP-1\tpaid\n"
    "ENDTRNS\n"
)


def _names(db, model):
    return sorted(r.name for r in db.query(model).all())


# ---------------------------------------------------------------------------
# Field quotes
# ---------------------------------------------------------------------------


def test_the_quotes_around_a_field_are_not_part_of_it():
    assert _unquote_iif('"ACME TOOLING, INC."') == "ACME TOOLING, INC."
    assert _unquote_iif('"99,250.02"') == "99,250.02"
    assert _unquote_iif('"The ""Best"" Co, LLC"') == 'The "Best" Co, LLC'
    assert _unquote_iif("Plain Name") == "Plain Name"
    assert _unquote_iif('"') == '"'  # a lone quote mark is a value


def test_a_formula_guard_still_comes_off_after_the_quotes():
    row = _fields_to_dict(["!CUST", "NAME"], ["CUST", '"\'=HYPERLINK(1)"'])
    assert row["NAME"] == "=HYPERLINK(1)"


def test_a_quoted_name_imports_without_its_quotes(db_session, seed_accounts):
    result = import_all(db_session, LISTS + TRANSACTIONS)
    assert result["errors"] == []
    assert _names(db_session, Vendor) == ["ACME TOOLING, INC."]


# ---------------------------------------------------------------------------
# ALL-CAPS names: only when asked
# ---------------------------------------------------------------------------


def test_names_are_kept_as_typed_unless_asked(db_session, seed_accounts):
    result = import_all(db_session, LISTS + TRANSACTIONS)
    assert result["names_changed"] == 0
    assert _names(db_session, Customer) == ["BOB JONES"]
    assert result["bills"] == 1 and result["invoices"] == 1
    assert result["payments"] == 1


def test_asked_every_use_of_a_name_changes_together(db_session, seed_accounts):
    # the file names the vendor and the customer in its lists and again on
    # each transaction; all of them must land on the same record
    result = import_all(db_session, LISTS + TRANSACTIONS, retitle_names=True)
    assert result["errors"] == [], result["errors"]
    assert result["names_changed"] == 2
    assert (result["bills"], result["invoices"], result["payments"]) == (1, 1, 1)
    assert _names(db_session, Vendor) == ["ACME Tooling, Inc."]
    assert _names(db_session, Customer) == ["Bob Jones"]
    bill = db_session.query(Bill).one()
    assert bill.vendor.name == "ACME Tooling, Inc."
    payment = db_session.query(Payment).one()
    assert payment.customer.name == "Bob Jones"


def test_asked_names_already_in_the_books_are_matched_not_doubled(
    db_session, seed_accounts
):
    # an earlier version imported this file as typed
    db_session.add(Customer(name="BOB JONES", is_active=True))
    db_session.add(Vendor(name="ACME TOOLING, INC.", is_active=True))
    db_session.commit()

    result = import_all(db_session, LISTS + TRANSACTIONS, retitle_names=True)
    assert result["errors"] == [], result["errors"]
    assert (result["customers"], result["vendors"]) == (0, 0)
    # matched, and not renamed: the box changes what the file brings in
    assert _names(db_session, Customer) == ["BOB JONES"]
    assert _names(db_session, Vendor) == ["ACME TOOLING, INC."]
    assert db_session.query(Bill).one().vendor.name == "ACME TOOLING, INC."
    assert db_session.query(Payment).one().customer.name == "BOB JONES"


def test_a_second_import_the_other_way_adds_nothing(db_session, seed_accounts):
    first = import_all(db_session, LISTS + TRANSACTIONS, retitle_names=True)
    assert first["errors"] == []
    again = import_all(db_session, LISTS + TRANSACTIONS)
    assert again["errors"] == [], again["errors"]
    assert (again["customers"], again["vendors"]) == (0, 0)
    assert (again["invoices"], again["payments"], again["bills"]) == (0, 0, 0)
    assert _names(db_session, Customer) == ["Bob Jones"]
    assert _names(db_session, Vendor) == ["ACME Tooling, Inc."]
    assert db_session.query(Payment).count() == 1
    assert db_session.query(Bill).count() == 1


def test_item_names_are_kept_as_typed(db_session, seed_accounts):
    iif = (
        "!INVITEM\tNAME\tINVITEMTYPE\tDESC\tACCNT\tPRICE\n"
        "INVITEM\tWIDGET-A\tSERV\tA WIDGET\tService Income\t25.00\n"
        "!CUST\tNAME\n"
        "CUST\tBOB JONES\n"
        "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tINVITEM\tQNTY\tPRICE\n"
        "!ENDTRNS\n"
        "TRNS\tINVOICE\t05/02/2026\tAccounts Receivable\tBOB JONES\t50.00\tI-9\n"
        "SPL\tINVOICE\t05/02/2026\tService Income\tBOB JONES\t-50.00\tI-9"
        "\tWIDGET-A\t2\t25.00\n"
        "ENDTRNS\n"
    )
    result = import_all(db_session, iif, retitle_names=True)
    assert result["errors"] == [], result["errors"]
    assert _names(db_session, Item) == ["WIDGET-A"]
    line = db_session.query(Invoice).one().lines[0]
    assert line.item is not None and line.item.name == "WIDGET-A"


def test_a_job_keeps_its_customer(db_session, seed_accounts):
    # "Customer:Job" rows: each side is judged on its own, so the job's
    # "BOB JONES" becomes the same "Bob Jones" as the customer's own row
    iif = (
        "!CUST\tNAME\n"
        "CUST\tBOB JONES\n"
        "CUST\tBOB JONES:Kitchen remodel\n"
        "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!ENDTRNS\n"
        "TRNS\tINVOICE\t05/02/2026\tAccounts Receivable\tBOB JONES:Kitchen remodel"
        "\t300.00\tI-7\n"
        "SPL\tINVOICE\t05/02/2026\tService Income\tBOB JONES:Kitchen remodel"
        "\t-300.00\tI-7\n"
        "ENDTRNS\n"
    )
    result = import_all(db_session, iif, retitle_names=True)
    assert result["errors"] == [], result["errors"]
    assert _names(db_session, Customer) == ["Bob Jones"]
    job = db_session.query(Job).one()
    assert (job.name, job.customer.name) == ("Kitchen remodel", "Bob Jones")
    assert db_session.query(Invoice).one().customer.name == "Bob Jones"


def test_asked_accounts_and_sub_accounts_change_together(db_session, seed_accounts):
    iif = (
        "!ACCNT\tNAME\tACCNTTYPE\n"
        "ACCNT\tAUTOMOBILE EXPENSE\tEXP\n"
        "ACCNT\tAUTOMOBILE EXPENSE:GASOLINE\tEXP\n"
        "ACCNT\tPAYROLL EXPENSES:FICA\tEXP\n"
    )
    result = import_all(db_session, iif, retitle_names=True)
    assert result["errors"] == [], result["errors"]
    parent = db_session.query(Account).filter_by(name="Automobile Expense").one()
    child = db_session.query(Account).filter_by(name="Gasoline").one()
    assert child.parent_id == parent.id
    assert db_session.query(Account).filter_by(name="FICA").count() == 1


def test_an_account_already_in_the_books_is_not_doubled(db_session, seed_accounts):
    db_session.add(Account(name="GASOLINE", account_type="expense", is_active=True))
    db_session.commit()
    before = db_session.query(Account).count()
    result = import_all(
        db_session,
        "!ACCNT\tNAME\tACCNTTYPE\nACCNT\tGASOLINE\tEXP\n",
        retitle_names=True,
    )
    assert result["errors"] == []
    assert result["accounts"] == 0
    assert db_session.query(Account).count() == before


# ---------------------------------------------------------------------------
# The choice, made on the file's own names
# ---------------------------------------------------------------------------


def test_validation_shows_what_the_box_would_change():
    report = validate_iif(LISTS + TRANSACTIONS)
    assert report["valid"] is True
    assert report["caps_names"] == 2
    assert report["caps_name_examples"] == [
        {"name": "BOB JONES", "becomes": "Bob Jones"},
        {"name": "ACME TOOLING, INC.", "becomes": "ACME Tooling, Inc."},
    ]


def test_a_file_in_normal_capitalization_offers_no_box():
    report = validate_iif("!CUST\tNAME\nCUST\tBob Jones\n")
    assert report["caps_names"] == 0
    assert report["caps_name_examples"] == []


def test_the_import_route_takes_the_box(client, db_session, seed_accounts):
    def post(retitle):
        data = {"retitle_names": "true"} if retitle else {}
        return client.post(
            "/api/iif/import",
            files={"file": ("lists.iif", LISTS.encode("utf-8"), "text/plain")},
            data=data,
        )

    first = post(False)
    assert first.status_code == 200, first.text
    assert first.json()["names_changed"] == 0
    assert _names(db_session, Customer) == ["BOB JONES"]

    db_session.query(Vendor).delete()
    db_session.query(Customer).delete()
    db_session.commit()
    second = post(True)
    assert second.status_code == 200, second.text
    assert second.json()["names_changed"] == 2
    assert _names(db_session, Customer) == ["Bob Jones"]


def test_the_page_sends_the_box_and_says_what_changed():
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "iif.js"
    ).read_text(encoding="utf-8")
    assert "formData.append('retitle_names'" in js
    assert 'id="iif-retitle-names"' in js
    assert "report.caps_name_examples" in js
    assert "result.names_changed" in js


def test_totals_are_unchanged_by_the_box(db_session, seed_accounts):
    result = import_all(db_session, LISTS + TRANSACTIONS, retitle_names=True)
    assert result["errors"] == []
    assert db_session.query(Bill).one().total == Decimal("40.00")
    assert db_session.query(Invoice).one().total == Decimal("100.00")


# ---------------------------------------------------------------------------
# Found while testing the samples for the 2.18.1 gate (both predate #195)
# ---------------------------------------------------------------------------

SUB_ACCOUNT_BILL = (
    "!ACCNT\tNAME\tACCNTTYPE\n"
    "ACCNT\tAUTOMOBILE EXPENSE\tEXP\n"
    "ACCNT\tAUTOMOBILE EXPENSE:GASOLINE\tEXP\n"
    "!VEND\tNAME\n"
    "VEND\tFUEL STOP\n"
    "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
    "!ENDTRNS\n"
    "TRNS\tBILL\t05/04/2026\tAccounts Payable\tFUEL STOP\t-61.20\tF-1\n"
    "SPL\tBILL\t05/04/2026\tAUTOMOBILE EXPENSE:GASOLINE\tFUEL STOP\t61.20\tF-1\n"
    "ENDTRNS\n"
)


def test_a_bill_to_a_sub_account_finds_it(db_session, seed_accounts):
    # the list keeps "GASOLINE" under "AUTOMOBILE EXPENSE"; the bill names the
    # path, and was refused as "expense account ... not found"
    for retitle in (False, True):
        db_session.query(Bill).delete()
        result = import_all(db_session, SUB_ACCOUNT_BILL, retitle_names=retitle)
        assert result["errors"] == [], (retitle, result["errors"])
        assert result["bills"] == 1
        line = db_session.query(Bill).one().lines[0]
        assert line.account.name.lower() == "gasoline"
        assert line.account.parent.name.lower() == "automobile expense"


def test_the_path_picks_the_sub_account_under_the_right_parent(
    db_session, seed_accounts
):
    from app.services.iif_import import _find_account

    def add(name, parent=None):
        acct = Account(
            name=name,
            account_type="expense",
            is_active=True,
            parent_id=parent.id if parent else None,
        )
        db_session.add(acct)
        db_session.flush()
        return acct

    utilities, automobile = add("Utilities"), add("Automobile")
    add("Gas", utilities)
    fica = add("FICA")
    db_session.commit()

    found = _find_account(db_session, "UTILITIES:GAS")
    assert (found.name, found.parent.name) == ("Gas", "Utilities")
    # the only Gas is under Utilities: an Automobile:Gas line is refused as
    # before, not posted to Utilities
    assert _find_account(db_session, "Automobile:Gas") is None
    add("Gas", automobile)
    db_session.commit()
    found = _find_account(db_session, "Automobile:Gas")
    assert (found.name, found.parent.name) == ("Gas", "Automobile")
    # a sub-account whose parent the file's list didn't carry
    assert _find_account(db_session, "PAYROLL EXPENSES:FICA").id == fica.id


def test_a_job_already_here_is_not_counted_again(db_session, seed_accounts):
    iif = "!CUST\tNAME\nCUST\tBob Jones\nCUST\tBob Jones:Kitchen remodel\n"
    assert import_all(db_session, iif)["customers"] == 2
    again = import_all(db_session, iif)
    assert again["customers"] == 0
    assert db_session.query(Job).count() == 1


def test_a_name_outside_ascii_still_matches_itself(db_session, seed_accounts):
    # SQLite's lower() folds ASCII only: "CAFÉ" must still find "CAFÉ", so a
    # second import of the same payment is a duplicate, not a second payment
    iif = (
        "!CUST\tNAME\nCUST\tCAFÉ ROUGE\n"
        "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!ENDTRNS\n"
        "TRNS\tPAYMENT\t05/03/2026\tUndeposited Funds\tCAFÉ ROUGE\t80.00\tP-9\n"
        "SPL\tPAYMENT\t05/03/2026\tAccounts Receivable\tCAFÉ ROUGE\t-80.00\tP-9\n"
        "ENDTRNS\n"
    )
    assert import_all(db_session, iif)["payments"] == 1
    again = import_all(db_session, iif)
    assert (again["customers"], again["payments"]) == (0, 0)
    assert db_session.query(Payment).count() == 1
    assert _names(db_session, Customer) == ["CAFÉ ROUGE"]
