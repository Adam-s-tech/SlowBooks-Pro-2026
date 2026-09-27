"""A read-only sign-in is offered nothing it cannot do, on any page or in any
document it opens: swept in playwright's Chromium, with the admin's page as
the control.

The server refuses every write from the readonly role ("Your role doesn't
allow this action"). W-L17 hid "+ New" and locked the forms a dialog opens,
but skytech's sweep of all 52 routes at 2.18.0 round 4, read-only against
admin, still found writes on offer: Deactivate and Delete on every account;
the whole Settings page; the Batch Payments form; Quick Entry's Save & Next;
CSV Import; Finish QBO connection; Banking's Connect. macbase1 added the
Attachments "Choose File" in an invoice's and a bill's view. Each let a
read-only user fill something in and then answered 403.

Here every route is opened twice on a bakery's books (the company of
tests/test_theme_contrast.py, served by the app itself): signed in as the
administrator, and as a read-only user made in Settings -> Users. So are the
main document views and some dialogs, and a job's tabs. Every visible,
enabled control is judged:

- a write: a file chooser; anything marked data-write where it is built; a
  "+ ..." create button; a field or button of a form that saves (one not
  marked data-readonly-ok); a handler that calls a page method which sends
  a POST, PUT, PATCH or DELETE (in its own code, or a method it calls), or
  one named in App.WRITE_ACTIONS; one that opens a blank form or an import;
  a field whose value a save reads (a budget cell, a deposit's date);
- otherwise a read: View, Print, Save PDF, reports, exports, filters, and
  opening a record's form, which shows it locked.

The read-only pages must offer no write. The admin's pages must show the
writes the gate agents found (or the sweep is not looking), and every read
the admin is offered must still be offered to the read-only user: View,
Print, Save PDF, the reports, the CSV and IIF exports, the statement picker.
Two kinds of page are the exceptions, each checked for what it says
instead: Payroll and HR, which are the administrator's; and the audit log,
which the server refuses to a read-only sign-in.

Skipped, as one module, where playwright or its Chromium is not installed
(tests/test_readonly_ui.py checks the same marks without a browser).
"""

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models.preferences import UserPreference  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    REDIRECTS,
    _ok,
    _open,
    _visit,
    books_fixture,
    browser_fixture,
    company_fixture,
    settle,
)

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
READER_PW = "long-enough-pw"

# The page objects the scripts declare (const InvoicesPage = { ... }). They
# are global bindings but not window properties, and the page's CSP allows
# no eval, so the sweep is handed them by name in its own source.
OBJECTS = sorted(
    {
        name
        for path in JS.glob("*.js")
        for name in re.findall(
            r"^const ([A-Z]\w*) = \{", path.read_text(encoding="utf-8"), re.M
        )
    }
)
OBJECTS_JS = (
    "{"
    + ", ".join(f"{n}: typeof {n} === 'undefined' ? undefined : {n}" for n in OBJECTS)
    + "}"
)

SWEEP = r"""(root) => {
  const OBJ = __OBJECTS__;
  // A request the read-only role is refused
  const WRITE = /\bAPI\s*\.\s*(?:post|put|del|patch)\s*\(|\bAPI\s*\.\s*request\s*\(\s*['"`](?:POST|PUT|PATCH|DELETE)\b|\bmethod\s*:\s*['"`](?:POST|PUT|PATCH|DELETE)['"`]/;
  const CALL = /\b(this|[A-Z][\w$]*)\s*\.\s*([A-Za-z_$][\w$]*)\s*\(/g;
  // A function's own code: not its comments, nor the handlers it writes
  // into the HTML it builds (onsubmit="InvoicesPage.save(event)" is the
  // form's call, not the call of the method that opens the form).
  const code = (src) => src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:'"`\\])\/\/[^\n]*/g, '$1')
    .replace(/\bon[a-z]+\s*=\s*(\\?["'])[\s\S]*?\1/g, '');
  const memo = new Map();
  // Does Obj.method send a write itself, or through a method it calls?
  const writes = (o, m, depth) => {
    const key = `${o}.${m}/${depth}`;
    if (memo.has(key)) return memo.get(key);
    memo.set(key, false);
    const fn = OBJ[o] && OBJ[o][m];
    let yes = false;
    if (typeof fn === 'function') {
      const c = code(fn.toString());
      yes = WRITE.test(c);
      if (!yes && depth > 0) {
        for (const [, o2, m2] of c.matchAll(CALL)) {
          if (writes(o2 === 'this' ? o : o2, m2, depth - 1)) { yes = true; break; }
        }
      }
    }
    memo.set(key, yes);
    return yes;
  };
  // The source of every method that sends a write: a field whose id or
  // class one of them reads is sent by a save.
  if (!window.__writerSources) {
    window.__writerSources = [];
    for (const obj of Object.values(OBJ)) {
      if (!obj || typeof obj !== 'object') continue;
      for (const m of Object.getOwnPropertyNames(obj)) {
        const fn = obj[m];
        if (typeof fn === 'function' && WRITE.test(code(fn.toString()))) {
          window.__writerSources.push(fn.toString());
        }
      }
    }
  }
  const readBySave = (el) => {
    const refs = [];
    if (el.id) refs.push('#' + el.id, `'${el.id}'`, `"${el.id}"`);
    el.classList.forEach(c => refs.push('.' + c));
    return refs.some(r => window.__writerSources.some(src => src.includes(r)));
  };
  const handlerWhy = (h) => {
    const first = /^\s*(\w+Page\.\w+)\(/.exec(h);
    if (first && App.WRITE_ACTIONS.has(first[1])) return `calls ${first[1]} (App.WRITE_ACTIONS)`;
    for (const [, o, m] of h.matchAll(CALL)) {
      if (o !== 'this' && writes(o, m, 1)) return `calls ${o}.${m}, which saves`;
    }
    if (/\.show(?:\w*Form|Create)\(\s*(?:null\b[^)]*)?\)|\.show\w*Import\(/.test(h)) return 'opens a blank form';
    const click = /getElementById\(\s*['"]([^'"]+)['"]\s*\)\s*\.click\(\)/.exec(h);
    if (click) {
      const target = document.getElementById(click[1]);
      if (target && target.type === 'file') return 'opens a file chooser';
    }
    return null;
  };
  const HANDLERS = ['onclick', 'onchange', 'oninput', 'onblur'];
  const why = (el) => {
    if (el.matches('input[type="file"]')) return 'chooses a file to upload';
    if (el.closest('[data-write]')) return 'marked data-write';
    // "+ New Invoice", "+ Add Garnishment": the app's own mark of a create
    if (el.matches('button, a.btn') && el.textContent.trim().startsWith('+')) return 'creates ("+ …")';
    for (const attr of HANDLERS) {
      const h = el.getAttribute(attr);
      const r = h && handlerWhy(h);
      if (r) return r;
    }
    // a form's fields and buttons (a link in it, Download, is a link)
    const form = el.matches('input, select, textarea, button') && el.closest('form');
    if (form && !/closeModal\(/.test(el.getAttribute('onclick') || '')) {
      const ok = el.closest('[data-readonly-ok]');
      if (!ok) return 'part of a form that saves';
      if (ok.contains(form) && handlerWhy(form.getAttribute('onsubmit') || '')) {
        return 'part of a form that saves, marked data-readonly-ok';
      }
    }
    if (el.matches('input, select, textarea') && readBySave(el)) return 'a field a save sends';
    return null;
  };
  const label = (el) => {
    let t;
    if (el.matches('select')) {
      t = el.getAttribute('aria-label') || el.id || el.name
        || (el.options[0] ? el.options[0].textContent : 'select');
    } else if (el.matches('input, textarea')) {
      t = el.getAttribute('placeholder') || el.getAttribute('aria-label') || el.name || el.id
        || el.type;
    } else {
      t = el.getAttribute('aria-label') || el.textContent || el.title || '';
    }
    return t.trim().replace(/\s+/g, ' ').slice(0, 60);
  };
  const box = document.querySelector(root);
  const out = [];
  if (!box) return out;
  const controls = box.querySelectorAll(
    'button, a[href], input, select, textarea, summary, [onclick], [onchange], [oninput], [onblur]');
  for (const el of controls) {
    if (el.matches('form, label')) continue;
    if (!el.getClientRects().length || getComputedStyle(el).visibility === 'hidden') continue;
    if (el.disabled || el.closest('fieldset:disabled')) continue;
    if (el.matches('input, textarea') && el.readOnly) continue;
    const handler = HANDLERS.map(a => el.getAttribute(a)).find(Boolean) || '';
    const tag = el.tagName.toLowerCase() + (el.matches('input') ? `[${el.type}]` : '');
    const text = label(el);
    const siblings = el.parentElement ? [...el.parentElement.querySelectorAll('button, a.btn')] : [];
    out.push({
      key: `${tag} "${text}" ${handler || el.getAttribute('href') || ''}`.trim(),
      text,
      why: why(el),
      // the invoice row's Edit, hidden where View shows the record
      beside_view: text === 'Edit' && siblings.some(b => b.textContent.trim() === 'View'),
    });
  }
  return out;
}""".replace("__OBJECTS__", OBJECTS_JS)

OPEN = "() => !document.getElementById('modal-overlay').classList.contains('hidden')"

# What the two gate agents found still offered to a read-only sign-in on
# 079732a (skytech's W-L17 leftovers, and macbase1's Choose File). The
# admin's pages must show each as a write, or the sweep is not looking.
FOUND_ON_PAGES = {
    "#/accounts": {"Deactivate", "Delete", "Import…"},
    "#/settings": {
        "Save Settings",
        "Create Backup",
        "Seed Default Templates",
        "Create default offset accounts",
        "Load standard list",
        "Send Test Email",
        "Add Class",
        "Add Cost Type",
        "Add Cost Code",
        "Add Equipment",
        "Rename",
        "Deactivate",
        "Import CSV",
        "Save AI settings",
        "Test",
    },
    "#/batch-payments": {"Select All", "Apply Batch Payment"},
    "#/quick-entry": {"Save & Next (Ctrl+Enter)"},
    "#/csv": {"Import"},
    "#/qbo": {"Finish QBO connection"},
    "#/banking": {"Connect"},
}
FOUND_IN_DIALOGS = {
    "InvoicesPage.view({sent})": {"file"},
    "BillsPage.view({bill})": {"file"},
    "ExpensesPage.showDetail({expense})": {"file"},
    "ReportsPage.arAging()": {
        "Apply Late Fees",
        "Email All Overdue",
        "Send Collection Letters",
    },
}

# The main document views, and dialogs a read-only user opens.
DIALOGS = [
    ("#/invoices", "InvoicesPage.view({sent})"),
    ("#/invoices", "InvoicesPage.view({partial})"),
    ("#/invoices", "InvoicesPage.showForm({draft})"),
    ("#/sales-receipts", "SalesReceiptsPage.view({receipt})"),
    ("#/estimates", "EstimatesPage.view({estimate})"),
    ("#/credit-memos", "CreditMemosPage.view({memo})"),
    ("#/payments", "PaymentsPage.view({payment})"),
    ("#/deposits", "DepositsPage.view({deposit})"),
    ("#/bills", "BillsPage.view({bill})"),
    ("#/bills", "BillsPage.view({paid_bill})"),
    ("#/bills", "BillsPage.viewPayment({bill_payment})"),
    ("#/purchase-orders", "PurchaseOrdersPage.view({po})"),
    ("#/vendor-credits", "VendorCreditsPage.view({vendor_credit})"),
    ("#/expenses", "ExpensesPage.showDetail({expense})"),
    ("#/journal", "JournalPage.view({journal})"),
    ("#/job-costs", "JobCostsPage.view({job_cost})"),
    ("#/customers", "CustomersPage.showDetails({customer})"),
    ("#/vendors", "VendorsPage.showForm({vendor})"),
    ("#/items", "ItemsPage.showMovements({loaf})"),
    ("#/accounts", "App.showAccountForm({checking})"),
    ("#/banking/{checking}", "BankingPage.showReconciliations({checking})"),
    ("#/reports", "ReportsPage.arAging()"),
    ("#/reports", "ReportsPage.profitLoss()"),
    ("#/reports", "ReportsPage.customerStatementPicker()"),
    ("#/reports", "ReportsPage.report1099()"),
    ("#/settings", "SettingsPage.editTemplate({template})"),
    # a record's form, where it is the only view: shown locked, including
    # what it fills in after it opens (the lines, the pickers)
    ("#/customers", "CustomersPage.showForm({customer})"),
    ("#/items", "ItemsPage.showForm({loaf})"),
    ("#/jobs", "JobsPage.showForm({job})"),
    ("#/recurring", "RecurringPage.showForm({recurring})"),
    ("#/reseller-permits", "ResellerPermitsPage.showForm({permit_soon})"),
    ("#/bank-rules", "BankRulesPage.showForm({bank_rule})"),
    ("#/fixed-assets", "FixedAssetsPage.showAssetForm({asset})"),
    ("#/hr/pto", "PTOPage.showPolicyForm({pto_policy})"),
]
JOB_TABS = ("costs", "budget", "transactions", "time")
# A read the server refuses to a read-only sign-in: the audit log snapshots
# whole records (app.main._role_allows), and the page says so.
REFUSED_READS = {"#/audit": "Your role doesn't allow this action"}


def _reader(company, db_session):
    """A read-only user, made in Settings -> Users, signed in on a client of
    its own (the admin's client stays signed in). The dashboard's cards are
    remembered per login and a read-only user cannot save a layout, so it is
    given the admin's: every card, as seed_books left it."""
    rita = _ok(
        company.post(
            "/api/users",
            json={"username": "rita", "password": READER_PW, "role": "readonly"},
        )
    )
    layout = _ok(company.get("/api/preferences/dashboard"))["value"]
    assert layout and layout["order"]
    db_session.add(
        UserPreference(user_id=rita["id"], key="dashboard", value=json.dumps(layout))
    )
    db_session.commit()
    reader = TestClient(app)
    r = reader.post("/api/auth/login", json={"username": "rita", "password": READER_PW})
    assert r.status_code == 200, r.text
    return reader


def _signed_in(browser, client, role):
    page, handled = _open(browser, client)
    # the role arrives with /api/auth/status, after the first page
    page.wait_for_function(f"() => App.role === '{role}'")
    settle(page, handled)
    return page, handled


def _sweep_pages(browser, client, role, books):
    page, handled = _signed_in(browser, client, role)
    swept, headings, texts = {}, {}, {}
    try:
        paths = page.evaluate(
            "() => Object.keys(App.routes).filter(k => !k.includes('/:'))"
        )
        routes = [f"#{p}" for p in paths if p not in REDIRECTS]
        routes += [f"#/jobs/{books['job']}", f"#/banking/{books['checking']}"]
        # payroll and HR: the administrator's pages
        admin_only = {
            f"#{p}" for p in page.evaluate("""() => Object.keys(App.routes).filter(k =>
                    App.ADMIN_ONLY_PAGES.includes(App.routes[k].page))""")
        }
        for route in routes:
            _visit(page, handled, route)
            swept[route] = page.evaluate(SWEEP, "#page-content")
            headings[route] = page.evaluate(
                "() => (document.querySelector('#page-content h2, #page-content h3')"
                " || {}).textContent || ''"
            ).strip()
            texts[route] = page.inner_text("#page-content")
        # a job's other tabs re-render in place
        for tab in JOB_TABS:
            page.evaluate("async (t) => { await JobsPage.setTab(t); }", tab)
            settle(page, handled)
            swept[f"#/jobs/{books['job']} ({tab})"] = page.evaluate(
                SWEEP, "#page-content"
            )
        sidebar = page.evaluate(
            """() => [...document.querySelectorAll('#sidebar .nav-link')]
            .filter(a => a.getClientRects().length).map(a => a.dataset.page)"""
        )
        notes = {
            route: page.evaluate(
                "async (h) => { await App.navigate(h);"
                " return document.querySelectorAll('#page-content .readonly-note').length; }",
                route,
            )
            for route in ("#/settings", "#/quick-entry", "#/batch-payments")
        }
    finally:
        page.close()
    return {
        "swept": swept,
        "headings": headings,
        "texts": texts,
        "admin_only": admin_only,
        "sidebar": sidebar,
        "notes": notes,
    }


def _sweep_dialogs(browser, client, role, books):
    page, handled = _signed_in(browser, client, role)
    swept, unopened = {}, []
    try:
        for route, opener in DIALOGS:
            _visit(page, handled, route.format(**books))
            page.evaluate("() => closeModal()")
            try:
                page.evaluate(f"async () => {{ await {opener.format(**books)}; }}")
                page.wait_for_function(OPEN, timeout=5000)
            except Exception as exc:  # the call failed, or opened nothing
                unopened.append(f"{opener}: {str(exc).splitlines()[0]}")
                continue
            settle(page, handled)
            swept[opener] = page.evaluate(SWEEP, "#modal")
        page.evaluate("() => closeModal()")
    finally:
        page.close()
    assert unopened == [], (role, unopened)
    return swept


def _offered_writes(swept):
    """{where: ["<control> — why", ...]} for every write on offer."""
    out = {}
    for where, controls in swept.items():
        seen = sorted({f"{c['key']} — {c['why']}" for c in controls if c["why"]})
        if seen:
            out[where] = seen
    return out


def _reads(controls):
    return {c["key"] for c in controls if not c["why"] and not c["beside_view"]}


# Reads that are the administrator's alone: a backup is the whole company,
# password hashes and payroll records included (the server refuses the rest).
ADMIN_ONLY_READS = ("/api/backups/download/",)


def _lost_reads(admin, reader):
    """The reads the admin is offered that the read-only user is not."""
    out = {}
    for where, controls in admin.items():
        shown = {c["key"] for c in reader.get(where, [])}
        lost = sorted(
            k
            for k in _reads(controls) - shown
            if not any(ref in str(k) for ref in ADMIN_ONLY_READS)
        )
        if lost:
            out[where] = lost
    return out


def _unseen(admin, found):
    """The gate's findings the sweep did not see as writes on the admin's
    page."""
    out = {}
    for where, labels in found.items():
        writes = {c["text"] for c in admin.get(where, []) if c["why"]}
        writes |= {
            "file" for c in admin.get(where, []) if c["key"].startswith("input[file]")
        }
        missing = sorted(labels - writes)
        if missing:
            out[where] = missing
    return out


def test_no_page_offers_a_read_only_sign_in_a_write(
    browser, company, books, db_session
):
    reader = _reader(company, db_session)
    admin_run = _sweep_pages(browser, company, "admin", books)
    ro_run = _sweep_pages(browser, reader, "readonly", books)
    admin, ro = admin_run["swept"], ro_run["swept"]

    assert len(admin) >= 50 and set(admin) == set(ro), sorted(set(admin) ^ set(ro))
    # the sweep sees the writes on the admin's pages: the gate's findings
    assert _unseen(admin, FOUND_ON_PAGES) == {}
    # none is offered to the read-only sign-in, on any page
    assert _offered_writes(ro) == {}
    # Payroll and HR say whose they are, rather than half loading
    hr = ro_run["admin_only"]
    assert {"#/payroll", "#/employees", "#/hr/onboarding"} <= hr
    assert {
        route: ro_run["headings"][route]
        for route in hr
        if not ro_run["headings"][route].endswith("is for administrators")
    } == {}
    assert all(admin_run["headings"][r] for r in hr)
    # the audit log is a read the server refuses it, and the page says so
    for route, sentence in REFUSED_READS.items():
        assert sentence in ro_run["texts"][route], ro_run["texts"][route]
    # and it keeps every other read: View, Print, the reports, the exports
    kept = {
        route: c
        for route, c in admin.items()
        if route not in hr and route not in REFUSED_READS
    }
    assert _lost_reads(kept, ro) == {}
    # the forms on a page are shown locked, with the sentence that says why
    assert ro_run["notes"] == {
        "#/settings": 1,
        "#/quick-entry": 1,
        "#/batch-payments": 1,
    }
    assert set(admin_run["notes"].values()) == {0}
    # the sidebar does not offer the pages that only enter things
    only_enter = {"batch-payments", "opening-balances", "migrate"}
    assert only_enter <= set(admin_run["sidebar"])
    assert only_enter & set(ro_run["sidebar"]) == set()
    assert {"invoices", "reports", "accounts", "settings"} <= set(ro_run["sidebar"])


def test_no_document_or_dialog_offers_a_read_only_sign_in_a_write(
    browser, company, books, db_session
):
    reader = _reader(company, db_session)
    admin = _sweep_dialogs(browser, company, "admin", books)
    ro = _sweep_dialogs(browser, reader, "readonly", books)

    assert _unseen(admin, FOUND_IN_DIALOGS) == {}
    assert _offered_writes(ro) == {}
    assert _lost_reads(admin, ro) == {}
    # the statement picker only opens a document: it stays usable
    picker = {c["text"] for c in ro["ReportsPage.customerStatementPicker()"]}
    assert "Generate PDF" in picker


def test_a_read_only_sign_in_leaves_settings_unasked(
    browser, company, books, db_session
):
    """Settings is locked for a read-only sign-in, and a locked field is not
    in FormData. When the role arrived after the page (a bookmarked
    #/settings), the page compared as changed with the copy taken before it
    was locked, and leaving asked "You have unsaved changes in Settings" of
    someone who could not change anything."""
    reader = _reader(company, db_session)
    page, handled = _signed_in(browser, reader, "readonly")
    asked = []
    page.on("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    try:
        # the order of a first page: Settings is in before the role is known
        page.evaluate("""() => { App._roObserver.disconnect(); App._roObserver = null;
            App.role = 'admin'; document.body.classList.remove('role-readonly'); }""")
        _visit(page, handled, "#/settings")
        page.evaluate("() => App.setRole('readonly')")
        settle(page, handled)
        assert page.evaluate(
            "() => document.querySelector('#settings-form [name=company_name]').disabled"
        )
        _visit(page, handled, "#/reports")
        assert (page.evaluate("location.hash"), asked) == ("#/reports", [])
    finally:
        page.close()
