"""A typed exchange rate is never replaced by the looked-up one (2.18.0
gate, skytech N1).

Choosing EUR on an invoice asks the exchange-rate feed for the day's rate.
A rate typed while that lookup was still answering was overwritten about
three seconds later (1.10 became 1.13976670), and the invoice booked at the
feed's rate without a word: 850 EUR posted as $968.80, not $935.00. The
looked-up rate now fills the field only while nobody has typed in it since
the currency was chosen, and an answer for a currency since changed is
dropped.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_looked_up_rate_never_replaces_a_typed_one():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "fx_rate_prefill_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # nobody typed: the feed's rate fills the field
    assert got["untouched"] == 1.1397667
    # typed while the lookup answered: the typed rate stays
    assert got["typed"] == "1.10"
    # EUR then GBP: the late EUR answer doesn't land on the GBP form
    assert got["changed"]["value"] == 1.27
    assert got["changed"]["paths"] == [
        "/fx/rate?from_currency=EUR",
        "/fx/rate?from_currency=GBP",
    ]
    # a new currency chosen after typing: its rate fills the field
    assert got["rechosen"] == 0.73
