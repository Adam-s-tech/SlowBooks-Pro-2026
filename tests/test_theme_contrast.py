"""Text on every page meets WCAG AA contrast, in both themes.

The macOS gate measures this off a live window (SlowBooks-Pro-Testing,
mac/macgate/check_css_regressions.py). It sweeps every visible element with
its own short text, works out the ground the text actually sits on, and
scores the pair. Its one standing FAIL at 2.18.0 was that list: 17 rules
below AA. Most were muted text: on the light theme's toolbar and status bar
gradients (2.57:1), on white (4.14), and the dark theme's grey on its panel
(4.32). The rest were the sidebar's section headings, the moon button, the
primary button, the dark skip link and the void badge.

This is the same sweep in playwright's Chromium, so the list is kept at zero
here rather than found again on a gate box:

- the gate's JavaScript, verbatim: a translucent layer is composited, a
  gradient is scored at its worst stop, a bitmap is left out;
- its thresholds: 4.5:1, or 3:1 for text of 24px, or 18.66px bold;
- its pages in its order, in dark and then light, and then the splash with
  the licence terms and What's new showing, in light and then dark.

The gate visits #/dashboard and #/pledges, which are not routes (the
dashboard is #/), so those two measure only the shell around "Page not
found". The dashboard is swept here too, and so is every other page in the
app, in both themes, with the notices (toasts) a save shows.

One thing is left out, and only off the gate's pages: a colour key, the
"■" beside each band of the dashboard's A/R aging bar. It is not text but
the band's colour, so WCAG 1.4.11 (3:1 for a graphic) applies to it rather
than 1.4.3, and the words beside it carry the band's name and amount.

The page is the real index.html, scripts and stylesheets, served by the app
itself through the test client, on a company with a chart of accounts and
invoices in each status a list can show. Skipped, as one module, where
playwright or its Chromium is not installed.
"""

import re
from urllib.parse import urlsplit

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

ORIGIN = "http://slowbooks.test"
AA = 4.5

# The gate's pages, in its order.
GATE_ROUTES = (
    "#/dashboard",
    "#/pledges",
    "#/invoices",
    "#/accounts",
    "#/reports",
    "#/settings",
)
# A route that only moves the address to another page
REDIRECTS = {"/check-register"}
# The aging bar's colour keys (see the module's docstring)
COLOUR_KEYS = {"■"}

# check_css_regressions.py's SWEEP, verbatim but for its comments.
SWEEP = r"""
(function(){
  function parse(c){
    var m = (c || '').match(/[\d.]+/g);
    if (!m || m.length < 3) return null;
    return [+m[0], +m[1], +m[2], m.length > 3 ? +m[3] : 1];
  }
  function over(fg, bg){          // composite fg (with alpha) onto bg
    var a = fg[3];
    return [fg[0]*a + bg[0]*(1-a), fg[1]*a + bg[1]*(1-a),
            fg[2]*a + bg[2]*(1-a), 1];
  }
  // A gradient between known colours is a range: its stops. Only a bitmap
  // (url(...)) is unresolvable from here.
  function stops(bgi){
    if (!bgi || bgi === 'none') return null;
    if (/url\(/.test(bgi)) return null;                 // a real image
    var found = bgi.match(/rgba?\([^)]*\)/g) || [];
    var out = [];
    for (var i = 0; i < found.length; i++) {
      var c = parse(found[i]);
      if (c && c[3] > 0) out.push(c);
    }
    return out.length ? out : null;
  }
  function bg(el){
    var layers = [], e = el, img = false, grad = null;
    while (e && e !== document.documentElement) {
      var cs = getComputedStyle(e), c = parse(cs.backgroundColor);
      var gs = stops(cs.backgroundImage);
      var hasImg = cs.backgroundImage && cs.backgroundImage !== 'none';
      if (gs && !grad) grad = gs;          // the nearest gradient wins
      if (c && c[3] > 0) {
        layers.push(c);
        if (c[3] >= 0.999) { img = img || (!!hasImg && !gs); break; }
        if (hasImg && !gs) img = true;
      } else if (hasImg && !gs) {
        img = true;                         // a bitmap: genuinely unresolvable
      }
      if (gs) break;                        // the gradient supplies the paint
      e = e.parentElement;
    }
    var base = parse(getComputedStyle(document.body).backgroundColor)
               || [255,255,255,1];
    if (base[3] < 0.999) base = [255,255,255,1];
    var acc = base;
    for (var i = layers.length - 1; i >= 0; i--) acc = over(layers[i], acc);
    var rgbs = function(c){ return 'rgb(' + Math.round(c[0]) + ', '
                   + Math.round(c[1]) + ', ' + Math.round(c[2]) + ')'; };
    // The gradient is the bottom layer; what was collected above it is
    // composited over each stop.
    var cands = [];
    if (grad) {
      for (var k = 0; k < grad.length; k++) {
        var t = over(grad[k], base);
        for (var j = layers.length - 1; j >= 0; j--) t = over(layers[j], t);
        cands.push(rgbs(t));
      }
    }
    return {color: rgbs(acc), image: img,
            candidates: cands.length ? cands : null};
  }
  var out = [], seen = {};
  var nodes = document.querySelectorAll(
    'a,button,label,td,th,h1,h2,h3,h4,h5,p,span,div,li,legend,summary,strong,em');
  for (var i = 0; i < nodes.length && out.length < 400; i++) {
    var el = nodes[i];
    // only elements with their OWN short visible text
    var own = '';
    for (var j = 0; j < el.childNodes.length; j++)
      if (el.childNodes[j].nodeType === 3) own += el.childNodes[j].nodeValue;
    own = own.trim();
    if (!own || own.length > 60) continue;
    var r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    var cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) continue;
    var key = own + '|' + cs.color;
    if (seen[key]) continue;
    seen[key] = 1;
    var b = bg(el);
    out.push({text: own.slice(0, 42), color: cs.color, background: b.color,
              candidates: b.candidates, unmeasurable: b.image,
              size: parseFloat(cs.fontSize) || 0,
              weight: cs.fontWeight, tag: el.tagName.toLowerCase(),
              cls: (el.className || '').toString().slice(0, 40)});
  }
  return out;
})()
"""


def _inside(element_id):
    """The sweep inside one element, with no length limit, as the gate
    sweeps the splash: a What's-new box of long items once went unmeasured
    at 2.33:1."""
    swept = SWEEP.replace(
        "document.querySelectorAll(",
        f"document.getElementById('{element_id}').querySelectorAll(",
        1,
    ).replace("own.length > 60", "own.length > 100000", 1)
    assert swept.count(element_id) == 1 and "100000" in swept
    return swept


SPLASH_SWEEP = _inside("splash")
TOAST_SWEEP = _inside("toast-container")


def _rgb(s):
    m = re.findall(r"[\d.]+", s or "")
    return [float(x) for x in m[:3]] if len(m) >= 3 else None


def contrast(fg, bg):
    """WCAG contrast ratio between two computed colours (the gate's)."""
    a, b = _rgb(fg), _rgb(bg)
    if not a or not b:
        return None

    def lum(c):
        out = []
        for v in c:
            v /= 255.0
            out.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]

    l1, l2 = sorted((lum(a), lum(b)), reverse=True)
    return round((l1 + 0.05) / (l2 + 0.05), 2)


# What counts as bold for the large-text rule: the gate's page sweep leaves
# 600 out, its splash sweep counts it.
PAGE_BOLD = ("700", "800", "900", "bold")
SPLASH_BOLD = ("600",) + PAGE_BOLD


def _score(item, bold):
    """(ratio, needed) for one swept element, or None where it cannot be
    measured. A gradient is a range: the stop that reads worst."""
    if item.get("unmeasurable"):
        return None
    cands = item.get("candidates") or [item.get("background")]
    ratios = [r for r in (contrast(item.get("color"), b) for b in cands) if r]
    if not ratios:
        return None
    size = item.get("size") or 0
    heavy = str(item.get("weight")) in bold
    need = 3.0 if (size >= 24 or (size >= 18.66 and heavy)) else AA
    return min(ratios), need


def below_threshold(sweeps, bold=PAGE_BOLD):
    """The gate's report: one row per rule (tag, first class, colour, ground,
    theme) with its worst ratio and the pages it was on."""
    groups = {}
    for (theme, where), items in sweeps.items():
        for it in items:
            if it["text"] in COLOUR_KEYS and where == "#/":
                continue
            scored = _score(it, bold)
            if not scored or scored[0] >= scored[1]:
                continue
            cls = (it.get("cls") or "").split()
            key = (it["tag"], cls[0] if cls else "", it["color"], it["background"])
            g = groups.setdefault(
                key + (theme,),
                {"ratio": scored[0], "need": scored[1], "text": it["text"]},
            )
            g["ratio"] = min(g["ratio"], scored[0])
            g.setdefault("where", set()).add(where)
    return sorted(
        f"{g['ratio']:>5}:1 (needs {g['need']}) {k[0]}{'.' + k[1] if k[1] else ''}"
        f"  {k[2]} on {k[3]}  [{k[4]}]  {g['text']!r}  on {sorted(g['where'])}"
        for k, g in groups.items()
    )


# --- the company: a chart of accounts and an invoice in every status ------


def _invoice(client, customer_id, amount, date, **extra):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": date,
            "tax_rate": 0,
            "lines": [{"description": "Catering", "quantity": 1, "rate": amount}],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pay(client, customer_id, invoice_id, amount):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": "2026-09-20",
            "amount": amount,
            "method": "Check",
            "allocations": [{"invoice_id": invoice_id, "amount": amount}],
        },
    )
    assert r.status_code == 201, r.text


@pytest.fixture
def company(client, db_session, seed_accounts):
    from app.models.contacts import Customer

    customer = Customer(name="Salt & Pine Catering Co.", is_active=True)
    db_session.add(customer)
    db_session.commit()
    cid = customer.id
    _invoice(client, cid, 120, "2026-09-24")  # a draft
    sent = _invoice(client, cid, 240, "2026-09-22", due_date="2026-12-31")
    assert client.post(f"/api/invoices/{sent['id']}/send").status_code == 200
    paid = _invoice(client, cid, 300, "2026-09-10")
    _pay(client, cid, paid["id"], 300)
    part = _invoice(client, cid, 500, "2026-09-12")
    _pay(client, cid, part["id"], 200)
    late = _invoice(client, cid, 80, "2026-06-01", due_date="2026-06-15")
    assert client.post(f"/api/invoices/{late['id']}/send").status_code == 200
    void = _invoice(client, cid, 999, "2026-09-05")
    assert client.post(f"/api/invoices/{void['id']}/void").status_code == 200
    return client


# --- the browser ----------------------------------------------------------


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception as exc:  # the package without its browser
            pytest.skip(f"playwright's Chromium is not installed: {exc}")
        yield chromium
        chromium.close()


_HOP_BY_HOP = {"content-length", "content-encoding", "transfer-encoding", "connection"}


def _served_by(client, handled):
    """Every request the page makes, answered by the app through the test
    client, which is signed in to the company above. Anything off the
    origin (web fonts and the like) is refused: not part of what is
    measured."""

    def serve(route):
        req = route.request
        url = urlsplit(req.url)
        if f"{url.scheme}://{url.netloc}" != ORIGIN:
            return route.abort()
        target = url.path + (f"?{url.query}" if url.query else "")
        headers = {
            k: v
            for k, v in req.headers.items()
            if k.lower() in ("content-type", "accept")
        }
        resp = client.request(
            req.method, target, headers=headers, content=req.post_data_buffer
        )
        handled.append(target)
        route.fulfill(
            status=resp.status_code,
            headers={
                k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP
            },
            body=resp.content,
        )

    return serve


def _open(browser, client):
    handled = []
    # the gate's window: 1500 x 980
    page = browser.new_page(viewport={"width": 1500, "height": 980})
    page.route("**/*", _served_by(client, handled))
    page.goto(f"{ORIGIN}/")
    page.wait_for_function("window.App && document.readyState === 'complete'")
    return page, handled


def _visit(page, handled, route):
    """The page at `route`, rendered, with whatever it fills in afterwards.
    App.navigate is what the address change runs; awaiting it directly means
    the page is in, rather than guessing how long that takes."""
    page.evaluate("async (h) => { await App.navigate(h); }", route)
    quiet = 0
    for _ in range(100):  # then a moment in which it asks for nothing more
        n = len(handled)
        page.wait_for_timeout(50)
        quiet = quiet + 1 if len(handled) == n else 0
        if quiet >= 3:
            return


def _theme(page, theme):
    # as the gate does it: the attribute and the remembered choice
    page.evaluate(
        """(t) => { document.documentElement.setAttribute('data-theme', t);
                    localStorage.setItem('slowbooks-theme', t); }""",
        theme,
    )
    # Buttons fade to the new theme's colours (transition: all 0.1s), and a
    # colour read halfway is neither theme's. The gate waits 1.2 s; here the
    # fades are run to their end, which is the colour the theme sets.
    page.evaluate("""() => document.getAnimations()
                   .filter(a => a instanceof CSSTransition)
                   .forEach(a => a.finish())""")


def test_the_gate_pages_and_the_splash_meet_aa_in_both_themes(browser, company):
    """The gate's sequence: its six pages, and the dashboard, in dark and
    then light, with the splash still up behind them as the gate leaves it;
    then the splash with its terms, in light and then dark."""
    page, handled = _open(browser, company)
    try:
        pages = {}
        for theme in ("dark", "light"):
            _theme(page, theme)
            for route in GATE_ROUTES + ("#/",):
                _visit(page, handled, route)
                pages[(theme, route)] = page.evaluate(SWEEP)
        splash = {}
        for theme in ("light", "dark"):
            _theme(page, theme)
            page.evaluate(
                """() => { document.getElementById('splash').classList.remove('hidden');
                           const t = document.getElementById('splash-terms');
                           if (t) t.hidden = false; }"""
            )
            page.wait_for_function(
                "() => !document.getElementById('splash-whatsnew').hidden"
            )
            splash[(theme, "splash")] = page.evaluate(SPLASH_SWEEP)
    finally:
        page.close()

    # the sweep read the pages, the void badge and the splash's licence block
    texts = {it["text"] for items in pages.values() for it in items}
    assert {"Chart of Accounts", "Reports", "void"} <= texts
    splash_texts = {it["text"] for items in splash.values() for it in items}
    assert {"Before you start", "I understand"} <= splash_texts
    assert all(splash.values())

    # the gate's report, which should be empty: one row per rule below AA
    assert (below_threshold(pages), below_threshold(splash, SPLASH_BOLD)) == ([], [])


def test_every_page_and_notice_meets_aa_in_both_themes(browser, company):
    """Every other page of the app, each swept in both themes, and a
    success, an error and an information notice."""
    page, handled = _open(browser, company)
    try:
        paths = page.evaluate(
            "() => Object.keys(App.routes).filter(k => !k.includes('/:'))"
        )
        routes = [
            f"#{p}"
            for p in paths
            if p not in REDIRECTS and f"#{p}" not in GATE_ROUTES + ("#/",)
        ]
        assert len(routes) >= 40, routes
        pages = {}
        for route in routes:
            _visit(page, handled, route)
            for theme in ("dark", "light"):
                _theme(page, theme)
                pages[(theme, route)] = page.evaluate(SWEEP)
        notices = {}
        for theme in ("light", "dark"):
            _theme(page, theme)
            page.evaluate("""() => { toast('Invoice saved', 'success');
                           toast('Could not save the invoice', 'error');
                           toast('Opening the report', 'info'); }""")
            # measured once they have faded in (toast-in, 0.2 s)
            page.wait_for_function(
                """() => { const t = [...document.querySelectorAll('#toast-container .toast')];
                           return t.length === 3
                               && t.every(e => getComputedStyle(e).opacity === '1'); }"""
            )
            notices[(theme, "toasts")] = page.evaluate(TOAST_SWEEP)
            page.evaluate(
                "() => document.querySelectorAll('#toast-container .toast')"
                ".forEach(e => e.remove())"
            )
    finally:
        page.close()

    # every page was in, not an error in its place
    failed = [
        where
        for where, items in pages.items()
        if any(
            it["text"] in ("Couldn't load this page", "Page not found") for it in items
        )
    ]
    assert failed == []
    texts = {it["text"] for items in pages.values() for it in items}
    assert {"All clear.", "Note:", "Click to browse"} <= texts
    assert [len(items) for items in notices.values()] == [3, 3]

    assert (below_threshold(pages), below_threshold(notices)) == ([], [])
