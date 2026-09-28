"""A sign-in belongs to one company (2.18.0 gate, skytech R6-1).

Every company on an install is served under the same session secret, and a
session recorded who signed in and with what role, not where. With
last_opened pointed at another company outside the app (or after a Switch
company... whose sign-out failed), the next company opened signed in, its
own password never asked. Each company file now carries an id of its own; a
sign-in records it, and a session recorded for another company counts as
signed out."""

from pathlib import Path

import pytest

from app.services import auth as auth_service
from app.services.settings_service import set_setting

ROOT = Path(__file__).resolve().parents[1]
# The `client` fixture's password (conftest)
FIXTURE_PW = "test-password-123"


@pytest.fixture(autouse=True)
def _fresh_company_ids():
    auth_service.forget_company_ids()
    yield
    auth_service.forget_company_ids()


def _serve_another_company(db_session):
    """What the server sees when this session reaches another company file:
    that file's own id."""
    set_setting(db_session, "company_session_id", "another-company-0000000000000000")
    db_session.commit()
    auth_service.forget_company_ids()


def test_a_sign_in_does_not_open_another_company(client, db_session):
    assert client.get("/api/settings").status_code == 200
    _serve_another_company(db_session)
    r = client.get("/api/settings")
    assert r.status_code == 401
    assert "another company" in r.json()["detail"]
    status = client.get("/api/auth/status").json()
    assert status["authenticated"] is False and status["setup_needed"] is False


def test_the_page_is_told_it_is_signed_out_before_any_request_fails(client, db_session):
    _serve_another_company(db_session)
    assert client.get("/api/auth/status").json()["authenticated"] is False
    assert client.get("/api/invoices").status_code == 401


def test_signing_in_to_this_company_works(client, db_session):
    _serve_another_company(db_session)
    assert client.get("/api/settings").status_code == 401
    assert client.post("/api/auth/login", json={"password": FIXTURE_PW}).is_success
    assert client.get("/api/settings").status_code == 200


def test_the_same_company_stays_signed_in_across_a_restart(client):
    assert client.get("/api/settings").status_code == 200
    auth_service.forget_company_ids()  # the same company's server, started again
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_a_session_from_before_belongs_to_the_company_it_is_used_on(client):
    # a 2.17 cookie carried no company
    earlier = {"authenticated": True}
    assert auth_service.signed_in_to_another_company(earlier) is False
    assert earlier["company"] == auth_service._company_id()
    assert auth_service.signed_in_to_another_company({"company": "elsewhere"}) is True


def test_the_company_id_is_not_in_the_settings(client):
    client.get("/api/settings")
    assert "company_session_id" not in client.get("/api/settings").json()
    r = client.put("/api/settings", json={"company_session_id": "chosen"})
    assert r.status_code == 200
    assert auth_service._company_id() != "chosen"


def test_switch_company_waits_when_the_sign_out_fails():
    js = (ROOT / "app/static/js/companies.js").read_text(encoding="utf-8")
    switch = js[js.index("async switchCompany() {") : js.index("async render() {")]
    assert "if (!(e && e.status === 401)) {" in switch
    assert switch.index("return;") < switch.index("show_picker()")


def test_a_company_id_goes_with_its_engine():
    # Keyed by id(engine), a new engine could reuse a freed one's address and
    # inherit its company id: an order-dependent 401 "That sign-in was for
    # another company" in the full suite. The cache holds the engine weakly.
    import gc

    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    auth_service._company_ids[engine] = "an-old-company-id"
    assert len(auth_service._company_ids) == 1
    del engine
    gc.collect()
    assert len(auth_service._company_ids) == 0
