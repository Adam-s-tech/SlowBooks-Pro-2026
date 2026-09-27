"""A disabled button looks disabled, and the QBO page says why Import is
(macbase1 NEW-15, 2.18.0 gate). The stylesheet had no disabled style for
.btn: with no QBO connection, Import All Data and Import Selected were
disabled but drew at full colour with a pointer cursor, and a click did
nothing and said nothing; Restore's "Replace my books" was the same before
its tick."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_a_disabled_button_is_dimmed_with_no_hover():
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    rule = re.search(r"\.btn:disabled\s*\{([^}]*)\}", css)
    assert rule and "opacity: 0.55" in rule.group(1)
    assert "cursor: not-allowed" in rule.group(1)
    for sel in (".btn", ".btn-primary", ".btn-secondary", ".btn-danger"):
        assert f"\n{sel}:hover {{" not in css, sel
        assert f"\n{sel}:hover:not(:disabled) {{" in css, sel


PROBE = r"""
const fs = require('fs'), vm = require('vm');
const buttons = [{ disabled: false }, { disabled: false }];
const line = { textContent: '' };
const ctx = { console, window: {}, $$: () => buttons,
  document: { addEventListener: () => {}, getElementById: (id) => (id === 'qbo-import-why' ? line : null) } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/qbo.js', 'utf8') + '\nthis.QBOPage = QBOPage;', ctx);
const Q = ctx.QBOPage, out = {};
const state = (s) => { Object.assign(Q, s); Q._setImportButtons();
  return { disabled: buttons.every(b => b.disabled), why: line.textContent }; };
out.noConnection = state({ _status: { connected: false }, _monitorReady: true, _monitorBlocked: false, _starting: false, _run: null });
out.checking = state({ _status: { connected: true }, _monitorReady: false });
out.running = state({ _monitorReady: true, _run: { status: 'running' } });
out.ready = state({ _run: { status: 'complete' } });
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_qbo_page_says_why_import_is_unavailable():
    out = subprocess.run(
        ["node", "-e", PROBE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["noConnection"] == {
        "disabled": True,
        "why": "Connect to QuickBooks Online above to import.",
    }
    assert got["checking"]["disabled"] and "Checking" in got["checking"]["why"]
    assert got["running"]["disabled"] and "running" in got["running"]["why"]
    assert got["ready"] == {"disabled": False, "why": ""}
