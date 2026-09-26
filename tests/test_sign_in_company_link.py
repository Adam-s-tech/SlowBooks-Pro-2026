"""The sign-in screen the desktop app starts on offers another company
(2.18.0 gate, macbase1 NEW-8).

"Choose a different company →" was drawn only if the launcher's bridge
(window.pywebview.api.show_picker) was already there. On macOS pywebview
injects the bridge after the page has loaded, later than the sign-in screen
is drawn, and nothing drew it again: 4 of 4 starts, and the unlock screen
reached from the picker, had no way back. Opened the wrong company, or one
whose password you do not have, and the next start reopened it.

/api/auth/status now says when a request comes from the desktop app's own
window (`desktop`), so the link is drawn at once; a click that beats the
bridge waits for pywebviewready; and a screen drawn before the bridge on a
server that did not say gets the link when the bridge arrives.
"""

import functools
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


def _status(host):
    from app.main import app

    c = TestClient(app, client=(host, 40000))
    r = c.get("/api/auth/status")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_status_says_when_the_request_is_the_desktop_apps_window(
    unauthed_client, monkeypatch
):
    monkeypatch.setenv("SLOWBOOKS_DESKTOP", "1")
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE", raising=False)
    assert _status("127.0.0.1")["desktop"] is True
    # another machine is a browser, whatever the flag
    assert _status("192.168.68.60")["desktop"] is False
    # Server Edition's --serve-lan has no window at all
    monkeypatch.setenv("SLOWBOOKS_SERVER_MODE", "1")
    assert _status("127.0.0.1")["desktop"] is False
    # a Docker or browser install
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE")
    monkeypatch.delenv("SLOWBOOKS_DESKTOP")
    assert _status("127.0.0.1")["desktop"] is False
    assert unauthed_client.get("/api/auth/status").json()["desktop"] is False


@functools.lru_cache(maxsize=None)
def _scenarios():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "auth_picker_link_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",  # node writes UTF-8; Windows would read cp1252
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return {s["scenario"]: s for s in map(json.loads, out.stdout.splitlines())}


@needs_node
def test_the_window_offers_another_company_before_the_bridge_arrives():
    mac = _scenarios()["mac-window"]
    assert mac["link_at_first_paint"] is True
    # clicked before the bridge: nothing yet, then the picker once it is there
    assert mac["picker_calls_before_the_bridge"] == 0
    assert mac["picker_calls_after_ready"] == 1
    assert mac["message"] == ""


@needs_node
def test_a_screen_drawn_before_the_bridge_gets_the_link_when_it_arrives():
    late = _scenarios()["late-bridge-no-flag"]
    assert late["link_at_first_paint"] is False
    assert late["link_after_ready"] is True
    assert late["picker_calls_after_ready"] == 1


@needs_node
def test_a_bridge_that_is_there_first_opens_the_picker_at_once():
    first = _scenarios()["bridge-first"]
    assert first["link_at_first_paint"] is True
    assert first["picker_calls_before_the_bridge"] == 1
    assert first["picker_calls_after_ready"] == 1  # not twice


@needs_node
def test_a_browser_is_never_offered_the_picker():
    browser = _scenarios()["browser"]
    assert browser["link_at_first_paint"] is False
    assert browser["link_after_ready"] is False


@needs_node
def test_a_click_that_never_finds_the_bridge_says_where_the_list_is():
    lost = _scenarios()["no-bridge-ever"]
    assert lost["picker_calls_after_ready"] == 0
    assert lost["message"] == "The company list opens in the SlowBooks Pro window."
