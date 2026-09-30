"""Every form field has a name a screen reader can say (#198).

Most labels sat beside their field without being tied to it (a `<label>`
with no `for`, the field its sibling), so a screen reader announced "combo
box" where it should have said "Customer": about 700 fields across 52 pages
in 2.18.1, and 14 to 24 in each of the New Invoice, New Bill and New Customer
dialogs. A grid of inputs (a budget, a batch of payments) had no names at
all.

Every page of the app and every dialog the contrast sweeps open, on the same
books (tests/test_theme_contrast.py and tests/test_dialog_contrast.py), are
checked here for a visible field without an accessible name, worked out as a
browser does: aria-labelledby, aria-label, a <label>, then title, then (not
for a select) placeholder.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_dialog_contrast import (  # noqa: E402,F401  (and the nonprofit books)
    BANKING,
    NONPROFIT_DIALOGS,
    NONPROFIT_PAGES,
    OPEN,
    PEOPLE,
    PURCHASES,
    REPORTS,
    SALES,
    SETTINGS,
    nonprofit,
)
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    REDIRECTS,
    SERVED,
    _open,
    _visit,
    books_fixture,
    browser_fixture,
    company_fixture,
    settle,
)

# What a screen reader would say for each visible field in `root`, and the
# fields that come out with nothing: "<tag>[name=…] in <where>".
UNNAMED = r"""(rootSel) => {
    const root = rootSel ? document.querySelector(rootSel) : document;
    const text = n => (n ? n.textContent : '').replace(/\s+/g, ' ').trim();
    const nameOf = el => {
        const by = el.getAttribute('aria-labelledby');
        if (by) {
            const t = by.split(/\s+/).map(id => text(document.getElementById(id))).join(' ').trim();
            if (t) return t;
        }
        const aria = (el.getAttribute('aria-label') || '').trim();
        if (aria) return aria;
        if (el.labels && el.labels.length) {
            const t = [...el.labels].map(text).join(' ').trim();
            if (t) return t;
        }
        const title = (el.getAttribute('title') || '').trim();
        if (title) return title;
        if (el.tagName !== 'SELECT') {
            const ph = (el.getAttribute('placeholder') || '').trim();
            if (ph) return ph;
        }
        return '';
    };
    const sel = 'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]),'
        + ' select, textarea';
    const out = [];
    for (const el of root.querySelectorAll(sel)) {
        if (!el.getClientRects().length) continue;  // not shown
        if (nameOf(el)) continue;
        const group = el.closest('.form-group, td, th, label, div');
        const near = group ? text(group).slice(0, 40) : '';
        out.push(`${el.tagName.toLowerCase()}[${el.name || el.id || el.type || ''}] near "${near}"`);
    }
    return out;
}"""


def _unnamed_on_pages(page, handled, books):
    paths = page.evaluate(
        "() => Object.keys(App.routes).filter(k => !k.includes('/:'))"
    )
    routes = [f"#{p}" for p in paths if p not in REDIRECTS]
    routes += [f"#/jobs/{books['job']}", f"#/banking/{books['checking']}"]
    found = {}
    for route in routes:
        _visit(page, handled, route)
        names = page.evaluate(UNNAMED, None)
        if names:
            found[route] = names
    return routes, found


def _unnamed_in_dialogs(page, handled, books, groups):
    found, opened = {}, 0
    for route, openers in groups:
        _visit(page, handled, route.format(**books))
        for opener in openers:
            call = opener.format(**books)
            page.evaluate("() => closeModal()")
            try:
                page.evaluate(f"async () => {{ await {call}; }}")
                page.wait_for_function(OPEN, timeout=5000)
            except Exception:  # the contrast sweep reports a dialog that won't open
                continue
            settle(page, handled)
            opened += 1
            names = page.evaluate(UNNAMED, "#modal")
            if names:
                title = page.evaluate(
                    "() => document.getElementById('modal-title').textContent"
                )
                found[f"{title} — {call}"] = names
        page.evaluate("() => closeModal()")
    return opened, found


def test_every_field_on_every_page_has_a_name(browser, company, books):
    page, handled = _open(browser, company)
    try:
        routes, found = _unnamed_on_pages(page, handled, books)
    finally:
        page.close()
    assert len(routes) >= 45, routes
    assert found == {}


def test_every_field_in_every_dialog_has_a_name(browser, company, books):
    groups = [
        ("#/invoices", SALES),
        ("#/bills", PURCHASES),
        ("#/banking/{checking}", BANKING),
        ("#/reports", REPORTS),
        ("#/employees", PEOPLE),
        ("#/settings", SETTINGS),
    ]
    page, handled = _open(browser, company)
    try:
        opened, found = _unnamed_in_dialogs(page, handled, books, groups)
    finally:
        page.close()
    assert opened >= 100, opened
    assert found == {}


def test_a_nonprofits_fields_have_names_too(
    browser, client, nonprofit  # noqa: F811  (the fixture, imported above)
):
    """The same books kept by a nonprofit: its own pages (donors, pledges,
    releases, allocations, in-kind gifts) and dialogs."""
    page, handled = _open(browser, client)
    found = {}
    try:
        for route in NONPROFIT_PAGES:
            _visit(page, handled, route)
            names = page.evaluate(UNNAMED, None)
            if names:
                found[route] = names
        opened, dialogs = _unnamed_in_dialogs(
            page, handled, nonprofit, [("#/invoices", NONPROFIT_DIALOGS)]
        )
    finally:
        page.close()
    assert opened == len(NONPROFIT_DIALOGS)
    assert {**found, **dialogs} == {}
