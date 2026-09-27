"""The company logo is changed by an administrator, and can be removed
(2.18.0). The logo is a company setting, and every other setting is the
administrator's; a bookkeeper could still upload or remove it. Settings
also had no way to remove a logo once uploaded."""

import io
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _upload(c):
    return c.post(
        "/api/uploads/logo", files={"file": ("logo.png", io.BytesIO(PNG), "image/png")}
    )


def test_only_an_administrator_changes_the_logo(client, seed_accounts):
    r = client.post("/api/tokens", json={"label": "keeper", "role": "bookkeeper"})
    assert r.status_code == 201, r.text
    keeper = TestClient(app)
    keeper.headers["Authorization"] = f"Bearer {r.json()['token']}"
    assert _upload(keeper).status_code == 403
    assert _upload(client).status_code in (200, 201)
    assert keeper.delete("/api/uploads/logo").status_code == 403
    assert client.delete("/api/uploads/logo").status_code in (200, 204)
    info = client.get("/api/uploads/logo")
    assert info.status_code in (200, 404)
    if info.status_code == 200:
        assert not (info.json() or {}).get("path")


def test_settings_offers_remove_logo():
    js = (ROOT / "app/static/js/settings.js").read_text(encoding="utf-8")
    assert 'onclick="SettingsPage.removeLogo()">Remove logo</button>' in js
    assert "await API.del('/uploads/logo');" in js
