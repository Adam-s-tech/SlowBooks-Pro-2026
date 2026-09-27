"""A toast stays long enough to be read (2.18.0 gate, macbase1 NEW-13).

Every toast was removed after three seconds whatever it said, so a two-line
refusal (the closing-date lock) was gone before it was read. A toast now
stays three seconds for a few words and longer for more, at least six for
an error and never more than fifteen; hovering holds it and a click
dismisses it."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PROBE = r"""
const fs = require('fs'), vm = require('vm');
const timers = [];
function node() {
  const listeners = {};
  return { className: '', textContent: '', removed: false,
    remove() { this.removed = true; },
    addEventListener(t, fn) { listeners[t] = fn; }, fire(t) { listeners[t](); } };
}
const shown = [];
const container = { appendChild: (el) => shown.push(el) };
const ctx = { console, window: {},
  document: { addEventListener: () => {}, createElement: () => node(),
              querySelector: (s) => (s === '#toast-container' ? container : null) },
  setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
  clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].cleared = true; } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/utils.js', 'utf8')
  + '\nthis.toast = toast; this.toastMs = toastMs;', ctx);
const lock = 'Transaction date 2026-08-17 is on or before the closing date (2026-08-27). '
  + 'Modifications to closed periods are not allowed. The closing-date password you entered '
  + 'is not correct. Too many wrong closing-date passwords were entered; try again in 10 minutes.';
const ms = {
  short: ctx.toastMs('Invoice saved', 'success'),
  shortError: ctx.toastMs('Not found', 'error'),
  lock: ctx.toastMs(lock, 'error'),
  huge: ctx.toastMs('x'.repeat(2000), 'success'),
};
ctx.toast(lock, 'error');
const el = shown[0];
const first = timers[0].ms;
el.fire('mouseenter');
const heldCleared = !!timers[0].cleared;
el.fire('mouseleave');
const after = timers[1].ms;
el.fire('click');
console.log(JSON.stringify({ ms, first, heldCleared, after, removed: el.removed }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_toast_stays_long_enough_to_read():
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
    assert got["ms"]["short"] == 3000
    assert got["ms"]["shortError"] == 6000
    assert 12000 <= got["ms"]["lock"] <= 15000  # the two-line lock refusal
    assert got["ms"]["huge"] == 15000
    assert got["first"] == got["ms"]["lock"]
    assert got["heldCleared"] is True and got["after"] == 2000
    assert got["removed"] is True
