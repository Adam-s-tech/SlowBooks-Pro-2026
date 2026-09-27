"""Start connection with Intuit opens Intuit in the system browser from the
desktop app (2.18.0, #192 review). The page opened a blank window first and
then sent it to Intuit, which a browser tab allows but the desktop shell
does not: the blank window became an empty browser tab Intuit never loaded
into, or, with no window handed back, the app's own window went off to
Intuit. In the desktop app the address now goes to the system browser
through the launcher's bridge; a browser keeps the tab-first path."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
URL = "https://appcenter.intuit.com/connect/oauth2?client_id=x&state=y"

PROBE = r"""
const fs = require('fs'), vm = require('vm');
const desktop = process.argv[1] === 'desktop';
const opened = [], external = [], toasts = [];
const win = { open: (u, t) => { opened.push(u); return null; },
  location: { href: 'http://127.0.0.1:3001/#/qbo' } };
if (desktop) win.pywebview = { api: { open_external: async (u) => { external.push(u); return { success: true }; } } };
const ctx = { console, window: win, document: { addEventListener: () => {} },
  App: { setStatus: () => {} }, toast: (m, k) => toasts.push([m, k || 'ok']),
  API: { get: async (p) => ({ url: '__URL__' }) } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/qbo.js', 'utf8') + '\nthis.QBOPage = QBOPage;', ctx);
(async () => {
  await ctx.QBOPage.connect();
  console.log(JSON.stringify({ opened, external, left: win.location.href, toasts }));
})();
""".replace("__URL__", URL)


def _run(mode):
    out = subprocess.run(
        ["node", "-e", PROBE, mode],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_desktop_app_opens_intuit_in_the_system_browser():
    got = _run("desktop")
    assert got["external"] == [URL]
    assert got["opened"] == []  # no blank window
    assert got["left"] == "http://127.0.0.1:3001/#/qbo"  # the app stays put
    assert "opened in your browser" in got["toasts"][0][0]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_browser_keeps_the_tab_first_path():
    got = _run("browser")
    assert got["opened"] == ["about:blank"] and got["external"] == []
