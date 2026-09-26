"""The page in a real browser: what a node probe cannot see (layout, focus,
scrolling), checked in playwright's Chromium.

The page is the real index.html with the real scripts and stylesheets; the
API answers from the fixtures below. Skipped, as one module, where playwright
or its Chromium is not installed.

- NEW-3 (2.18.0 gate, macbase1): the Pay Run view opened scrolled to the
  first Stub PDF, with the Employee column out of sight, so no button said
  whose stub it was. Checked in the gate's 1280 x 800 window and a narrower
  one.
"""

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://slowbooks.test"

API = {
    "/health": {"status": "ok", "version": "2.18.0"},
    "/api/auth/status": {
        "authenticated": True,
        "setup_needed": False,
        "multi_user": False,
        "company_name": "Harbor Light Bakery Two",
    },
    "/api/system": {"version": "2.18.0", "desktop": False, "server_mode": False},
    "/api/settings": {
        "company_name": "Harbor Light Bakery Two",
        "company_type": "business",
        "default_terms": "Net 15",
        "default_tax_rate": "8.25",
        "invoice_notes": "Thank you for choosing Harbor Light!",
        "walk_in_customer_id": "9",
    },
    "/api/payroll/1": {
        "id": 1,
        "period_start": "2026-09-13",
        "period_end": "2026-09-26",
        "status": "processed",
        "total_gross": 3726.67,
        "total_taxes": 751.54,
        "total_employer_taxes": 352.17,
        "total_employer_benefits": 0,
        "total_net": 2975.13,
        "stubs": [
            {
                "id": 1,
                "employee_id": 1,
                "employee_name": "Lena Ortiz",
                "hours": 0,
                "gross_pay": 2166.67,
                "federal_tax": 91.67,
                "state_tax": 144.47,
                "state_other_employee": 2.17,
                "ss_tax": 134.33,
                "medicare_tax": 31.42,
                "pretax_deductions": 0,
                "posttax_deductions": 0,
                "garnishments": 0,
                "reimbursements": 0,
                "net_pay": 1762.61,
                "benefits": [],
            },
            {
                "id": 2,
                "employee_id": 2,
                "employee_name": "Jonah Pike",
                "hours": 80,
                "gross_pay": 1560,
                "federal_tax": 60.89,
                "state_tax": 115.69,
                "state_other_employee": 1.56,
                "ss_tax": 96.72,
                "medicare_tax": 22.62,
                "pretax_deductions": 0,
                "posttax_deductions": 0,
                "garnishments": 0,
                "reimbursements": 0,
                "net_pay": 1212.52,
                "benefits": [],
            },
        ],
    },
}


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception as exc:  # the package without its browser
            pytest.skip(f"playwright's Chromium is not installed: {exc}")
        yield chromium
        chromium.close()


def _serve(route):
    url = urlsplit(route.request.url)
    if f"{url.scheme}://{url.netloc}" != ORIGIN:
        return route.abort()  # web fonts and the like: not part of the layout
    if url.path == "/":
        return route.fulfill(path=str(ROOT / "index.html"))
    if url.path.startswith("/static/"):
        f = ROOT / "app" / url.path.lstrip("/")
        return route.fulfill(path=str(f)) if f.is_file() else route.fulfill(status=404)
    body = API.get(url.path, [])  # a list the page does not need: empty
    return route.fulfill(
        status=200, content_type="application/json", body=json.dumps(body)
    )


def _open(browser, width, height, page_hash):
    page = browser.new_page(viewport={"width": width, "height": height})
    page.route("**/*", _serve)
    page.goto(f"{ORIGIN}/{page_hash}")
    page.wait_for_function("window.App && document.readyState === 'complete'")
    page.click("#splash-dismiss")  # the splash covers the page on every start
    return page


def _open_dialog(page, call, ready):
    page.evaluate(f"() => {call}")
    page.wait_for_selector(ready)
    # openModal focuses the dialog's first control on the next tick, which
    # scrolls it into view; measure after that
    page.evaluate("() => new Promise((done) => setTimeout(done, 0))")


PAY_RUN_ROWS = """(scrollToEnd) => {
    const wrap = document.querySelector('#modal-body .table-container');
    if (scrollToEnd) wrap.scrollLeft = wrap.scrollWidth;
    const box = wrap.getBoundingClientRect();
    const rows = [...wrap.querySelectorAll('tbody tr')].map(tr => {
        const cell = tr.cells[0];
        const r = cell.getBoundingClientRect();
        // what is painted at the middle of the name: the name, not a
        // column scrolled over it
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        const button = tr.querySelector('button');
        return {
            name: cell.textContent.trim(),
            name_in_view: r.left >= box.left - 0.5 && r.right <= box.right + 0.5
                && !!hit && cell.contains(hit),
            label: button.getAttribute('aria-label'),
            text: button.textContent.trim(),
        };
    });
    return { rows, scrolls: wrap.scrollWidth > wrap.clientWidth };
}"""


def test_each_pay_run_row_says_whose_stub_it_is(browser):
    # 1280: the window of the report, where the table now fits the dialog;
    # 900: a narrower one, where it scrolls and the Employee column stays
    for width, height in ((1280, 800), (900, 700)):
        page = _open(browser, width, height, "#/payroll")
        try:
            _open_dialog(page, "PayrollPage.view(1)", "#modal-body table")
            seen = [page.evaluate(PAY_RUN_ROWS, False)]
            if width < 1280:
                assert seen[0]["scrolls"], "the table should be wider than 900"
                seen.append(page.evaluate(PAY_RUN_ROWS, True))  # all the way right
            for view in seen:
                rows = view["rows"]
                assert [r["name"] for r in rows] == ["Lena Ortiz", "Jonah Pike"]
                assert all(r["name_in_view"] for r in rows), (width, rows)
                for r in rows:
                    assert r["text"] == f"Stub PDF — {r['name']}"
                    assert r["label"] == f"Stub PDF — {r['name']}"
        finally:
            page.close()
