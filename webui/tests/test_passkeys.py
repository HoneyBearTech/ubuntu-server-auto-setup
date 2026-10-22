"""Passkeys: adding them, signing in with them, "Require a passkey", removing them, and every way a response
must be refused (another origin or RP ID, a replayed or expired challenge, a counter going back...). A software
authenticator (softauthn.py) stands in for the browser and the authenticator."""

import json
import logging
import stat
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from usas_web import passkeys as pk
from usas_web.config import from_env
from usas_web.main import create_app

from .conftest import PASSWORD, csrf, make_client, post, sign_up
from .softauthn import SoftAuthenticator


def begin(c, name="Laptop", password=PASSWORD):
    return post(c, "/passkeys/register-options", {"passkey_name": name, "passkey_current": password})


def add(c, authn, name="Laptop", **kw):
    r = begin(c, name)
    assert r.status_code == 200, r.text
    options = r.json()["options"]
    return post(c, "/passkeys/register", {"credential": authn.create(options, **kw)})


def login_options(c):
    r = post(c, "/passkeys/login-options")
    assert r.status_code == 200, r.text
    return r.json()["options"]


def login(c, authn, **kw):
    return post(c, "/passkeys/login", {"credential": authn.get(login_options(c), **kw), "next": "/runs"})


def sign_out(c):
    assert post(c, "/logout").status_code == 200
    assert not c.get("/api/session").json()["signed_in"]


@pytest.fixture
def authn():
    return SoftAuthenticator()


@pytest.fixture
def keyed(ui, authn):
    """Signed in, with one passkey added; then signed out."""
    assert add(ui, authn).status_code == 200
    sign_out(ui)
    return ui


# --- Settings -----------------------------------------------------------------------------------------------

def test_public_url_sets_the_rp_id_and_origin():
    s = from_env({"USAS_PUBLIC_URL": "HTTPS://USAS.example.lan:443/"})
    assert (s.public_url, s.rp_id) == ("https://usas.example.lan", "usas.example.lan")
    assert "usas.example.lan" in s.allowed_hosts
    s = from_env({"USAS_PUBLIC_URL": "https://usas.example.lan:8443", "USAS_ALLOWED_HOSTS": "usas.example.lan"})
    assert s.public_url == "https://usas.example.lan:8443" and s.allowed_hosts == ("usas.example.lan",)
    s = from_env({"USAS_PUBLIC_URL": "http://localhost:8081"})
    assert (s.public_url, s.rp_id) == ("http://localhost:8081", "localhost")
    s = from_env({})
    assert (s.public_url, s.rp_id) == ("", "localhost")


@pytest.mark.parametrize("value", [
    "http://usas.example.lan", "https://10.0.0.5", "https://[::1]:8443", "https://usas.lan/ui", "ftp://usas.lan",
    "https://user:pw@usas.lan", "https://usas.lan?x=1", "https://usas.lan:99999", "usas.lan",
])
def test_public_url_must_be_a_plain_origin_with_a_host_name(value):
    with pytest.raises(ValueError, match="USAS_PUBLIC_URL"):
        from_env({"USAS_PUBLIC_URL": value})


# --- Adding passkeys ---------------------------------------------------------------------------------------

def test_add_list_and_sign_in(ui, authn, settings):
    r = add(ui, authn, name="  Laptop‮\n(Touch ID)  ")
    assert r.status_code == 200 and "Laptop (Touch ID)" in r.json()["message"]
    account = ui.get("/api/account").json()
    assert account["rp_id"] == "localhost" and account["require_passkey"] is False
    assert account["current"] == "password"
    [entry] = account["passkeys"]
    assert entry["name"] == "Laptop (Touch ID)" and entry["last_used"] is None and entry["created"] > 0
    assert set(entry) == {"id", "name", "created", "last_used", "backed_up"}  # no public key, no counter

    stored = json.loads(settings.passkeys_file.read_text())
    assert stat.S_IMODE(settings.passkeys_file.stat().st_mode) == 0o600
    [record] = stored["credentials"]
    assert record["transports"] == ["internal", "hybrid"]  # what the browser listed that WebAuthn knows
    assert record["public_key"] and record["sign_count"] == 0

    sign_out(ui)
    session = ui.get("/api/session").json()
    assert session["passkeys"] == {"registered": True, "required": False, "rp_id": "localhost", "public_url": ""}
    r = login(ui, authn)
    assert r.status_code == 200 and r.json()["redirect"] == "/runs"
    assert ui.get("/api/session").json()["signed_in"]
    assert ui.get("/api/account").json()["current"] == record["id"]
    record = json.loads(settings.passkeys_file.read_text())["credentials"][0]
    assert record["sign_count"] == 1 and record["last_used"] >= record["created"]


def test_the_options_ask_for_user_verification_and_a_discoverable_passkey(ui, authn):
    options = begin(ui).json()["options"]
    assert options["rp"] == {"name": "USAS", "id": "localhost"}
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["authenticatorSelection"]["residentKey"] == "preferred"
    assert options["attestation"] == "none" and options["timeout"] == 300_000
    assert options["excludeCredentials"] == []
    post(ui, "/passkeys/register", {"credential": authn.create(options)})
    options = begin(ui).json()["options"]
    # The same authenticator isn't registered twice; the user handle stays the same.
    assert [c["id"] for c in options["excludeCredentials"]] == [ui.get("/api/account").json()["passkeys"][0]["id"]]
    assert options["excludeCredentials"][0]["transports"] == ["internal", "hybrid"]
    sign_out(ui)
    options = login_options(ui)
    assert options["userVerification"] == "required" and len(options["allowCredentials"]) == 1
    assert options["rpId"] == "localhost"


def test_adding_needs_the_password_and_a_name(ui, authn):
    r = begin(ui, name="", password="wrong")
    assert r.status_code == 422
    assert set(r.json()["errors"]) == {"passkey_name", "passkey_current"}
    r = begin(ui, name="x" * (pk.MAX_NAME + 1))
    assert r.status_code == 422 and "at most" in r.json()["errors"]["passkey_name"]
    # Wrong passwords here count towards the same throttle as the sign-in.
    for _ in range(10):
        begin(ui, password="wrong")
    assert begin(ui).status_code == 429
    assert ui.get("/api/account").json()["passkeys"] == []


def test_registration_responses_that_are_refused(ui, settings):
    authn = SoftAuthenticator()
    cases = [
        ({"origin": "http://evil.example"}, "another site"),
        ({"origin": "http://localhost.evil.example"}, "another site"),
        ({"origin": "http://127.0.0.1:8080"}, "another site"),
        ({"rp_id": "evil.example"}, "verified"),
        ({"uv": False}, "verified"),
        ({"kind": "webauthn.get"}, "verified"),
    ]
    for spoil, message in cases:
        r = add(ui, authn, **spoil)
        assert r.status_code == 422 and message in r.json()["error"], (spoil, r.text)
    # Without options first, or with garbage.
    r = post(ui, "/passkeys/register", {"credential": authn.create({"challenge": "AAAA", "user": {"id": "AAAA"}})})
    assert r.status_code == 422 and "Start again" in r.json()["error"]
    begin(ui)
    assert "couldn't be read" in post(ui, "/passkeys/register", {"credential": "{"}).json()["error"]
    begin(ui)
    big = json.dumps({"x": "y" * pk.MAX_RESPONSE})
    assert "too large" in post(ui, "/passkeys/register", {"credential": big}).json()["error"]
    assert ui.get("/api/account").json()["passkeys"] == []
    # Any port on localhost: Docker may publish the UI on another one.
    assert add(ui, authn, origin="http://localhost:8081").status_code == 200


def test_the_same_passkey_twice_and_the_limit(ui, authn, monkeypatch):
    assert add(ui, authn).status_code == 200
    r = add(ui, authn)
    assert r.status_code == 422 and "registered already" in r.json()["error"]
    monkeypatch.setattr(pk, "MAX_PASSKEYS", 1)
    r = begin(ui)
    assert r.status_code == 409 and "Remove one first" in r.json()["error"]


def test_the_limit_is_checked_again_at_the_end(ui, monkeypatch):
    options = begin(ui).json()["options"]
    monkeypatch.setattr(pk, "MAX_PASSKEYS", 0)
    r = post(ui, "/passkeys/register", {"credential": SoftAuthenticator().create(options)})
    assert r.status_code == 422 and "Remove one first" in r.json()["error"]


# --- Signing in ----------------------------------------------------------------------------------------------

def test_sign_in_responses_that_are_refused(keyed, authn):
    cases = [
        ({"origin": "http://evil.example"}, "another site"),
        ({"origin": "https://localhost.evil.example:8080"}, "another site"),
        ({"rp_id": "evil.example"}, "verified"),
        ({"uv": False}, "verified"),
        ({"user_handle": b"someone else"}, "isn't registered"),
    ]
    for spoil, message in cases:
        r = login(keyed, authn, **spoil)
        assert r.status_code == 401 and message in r.json()["error"], (spoil, r.text)
        assert not keyed.get("/api/session").json()["signed_in"]
        keyed.app_state.auth.clear_failures("testclient")  # the throttle has its own test
    stranger = SoftAuthenticator()
    stranger.user_handle = authn.user_handle
    r = login(keyed, stranger)
    assert r.status_code == 401 and "isn't registered" in r.json()["error"]
    assert login(keyed, authn).status_code == 200


def test_a_challenge_is_used_once(keyed, authn):
    options = login_options(keyed)
    response = authn.get(options)
    assert post(keyed, "/passkeys/login", {"credential": response}).status_code == 200
    sign_out(keyed)
    # The same response again: its challenge is gone, even though the signature is good.
    r = post(keyed, "/passkeys/login", {"credential": response})
    assert r.status_code == 401 and "Start again" in r.json()["error"]
    # A failed attempt uses the challenge up as well.
    options = login_options(keyed)
    assert post(keyed, "/passkeys/login", {"credential": authn.get(options, uv=False)}).status_code == 401
    assert post(keyed, "/passkeys/login", {"credential": authn.get(options)}).status_code == 401
    # A registration challenge isn't a sign-in challenge.
    assert login(keyed, authn).status_code == 200
    options = begin(keyed).json()["options"]
    r = post(keyed, "/passkeys/login", {"credential": authn.get(options)})
    assert r.status_code == 401 and "Start again" in r.json()["error"]


def test_a_challenge_belongs_to_its_session(keyed, authn, settings, fake_ansible):
    options = login_options(keyed)
    other = TestClient(keyed.app, base_url="http://localhost")
    r = other.post("/api/passkeys/login", data={"credential": authn.get(options)},
                   headers={"X-CSRF-Token": csrf(other)})
    assert r.status_code == 401 and "Start again" in r.json()["error"]


def test_a_challenge_expires_after_5_minutes(keyed, authn, monkeypatch):
    options = login_options(keyed)
    later = time.time() + pk.CHALLENGE_SECONDS + 1
    monkeypatch.setattr(pk.time, "time", lambda: later)
    r = post(keyed, "/passkeys/login", {"credential": authn.get(options)})
    assert r.status_code == 401 and "too long" in r.json()["error"]


def test_the_signature_counter_must_go_up(keyed, authn, settings):
    assert login(keyed, authn, count=5).status_code == 200
    sign_out(keyed)
    for count in (5, 4, 0):
        r = login(keyed, authn, count=count)
        assert r.status_code == 401 and "verified" in r.json()["error"], count
    assert login(keyed, authn, count=6).status_code == 200
    assert json.loads(settings.passkeys_file.read_text())["credentials"][0]["sign_count"] == 6


def test_authenticators_without_a_counter_always_send_zero(ui, settings):
    synced = SoftAuthenticator(backed_up=True)
    assert add(ui, synced).status_code == 200
    assert ui.get("/api/account").json()["passkeys"][0]["backed_up"] is True
    for _ in range(2):
        sign_out(ui)
        assert login(ui, synced, count=0).status_code == 200


def test_sign_in_is_throttled_like_the_password(keyed, authn):
    for _ in range(5):
        assert login(keyed, authn, uv=False).status_code == 401
    r = post(keyed, "/passkeys/login", {"credential": "{}"})
    assert r.status_code == 429
    assert post(keyed, "/passkeys/login-options").status_code == 429


def test_sign_in_without_passkeys_or_setup(client, authn):
    r = post(client, "/passkeys/login-options")
    assert r.status_code == 409 and r.json()["redirect"] == "/setup"
    assert post(client, "/passkeys/login", {"credential": "{}"}).status_code == 409
    sign_up(client)
    sign_out(client)
    r = post(client, "/passkeys/login-options")
    assert r.status_code == 409 and "No passkey" in r.json()["error"]


def test_with_a_public_url_only_its_origin_counts(settings, fake_ansible):
    public = replace(settings, public_url="https://usas.example.lan:8443",
                     allowed_hosts=("localhost", "usas.example.lan"))
    c, app = make_client(public, base_url="https://usas.example.lan:8443")
    c.app_state = app.state
    sign_up(c)
    authn = SoftAuthenticator(origin="https://usas.example.lan:8443", rp_id="usas.example.lan")
    assert begin(c).json()["options"]["rp"]["id"] == "usas.example.lan"
    r = add(c, authn, origin="http://localhost:8443")
    assert r.status_code == 422 and "another site" in r.json()["error"]
    assert add(c, authn, rp_id="localhost").status_code == 422
    assert add(c, authn).status_code == 200
    sign_out(c)
    assert login(c, authn, origin="https://usas.example.lan").status_code == 401
    assert login(c, authn).status_code == 200


# --- Require a passkey ---------------------------------------------------------------------------------------

def test_require_a_passkey(ui, authn):
    r = post(ui, "/passkeys/require", {"require_passkey": "on"})
    assert r.status_code == 409 and "Add a passkey first" in r.json()["error"]
    assert add(ui, authn).status_code == 200
    # Another browser signed in with the password is signed out when the requirement is turned on.
    other = TestClient(ui.app, base_url="http://localhost")
    post(other, "/login", {"password": PASSWORD})
    assert other.get("/api/session").json()["signed_in"]
    r = post(ui, "/passkeys/require", {"require_passkey": "on"})
    assert r.status_code == 200 and "signed out" in r.json()["message"]
    assert not other.get("/api/session").json()["signed_in"]
    assert ui.get("/api/session").json()["signed_in"]  # this one stays
    assert ui.get("/api/session").json()["passkeys"]["required"]

    sign_out(ui)
    r = post(ui, "/login", {"password": PASSWORD})
    assert r.status_code == 403 and "passkey" in r.json()["error"]
    assert not ui.get("/api/session").json()["signed_in"]
    assert login(ui, authn).status_code == 200

    # The only passkey can't be removed while one is required.
    [entry] = ui.get("/api/account").json()["passkeys"]
    r = post(ui, "/passkeys/remove", {"id": entry["id"]})
    assert r.status_code == 409 and "only passkey" in r.json()["error"]
    # Changing the password still works (it needs the current one).
    assert post(ui, "/account", {"current": PASSWORD, "password": PASSWORD, "confirm": PASSWORD}).status_code == 200

    r = post(ui, "/passkeys/require", {})
    assert r.status_code == 200 and "password works again" in r.json()["message"]
    sign_out(ui)
    assert post(ui, "/login", {"password": PASSWORD}).status_code == 200


def test_a_broken_passkeys_file_fails_closed(ui, settings):
    settings.passkeys_file.write_text("not json")
    account = ui.get("/api/account").json()
    assert account["broken"] and account["require_passkey"] and account["passkeys"] == []
    assert "Delete passkeys.json" in post(ui, "/passkeys/require", {}).json()["error"]
    assert "Delete passkeys.json" in begin(ui).json()["error"]
    sign_out(ui)
    assert post(ui, "/login", {"password": PASSWORD}).status_code == 403
    assert post(ui, "/passkeys/login-options").status_code == 409
    # The documented way back: delete the file.
    settings.passkeys_file.unlink()
    assert post(ui, "/login", {"password": PASSWORD}).status_code == 200


def test_a_passkeys_file_of_the_wrong_shape_fails_closed(ui, settings):
    settings.passkeys_file.write_text("[]")
    assert ui.get("/api/account").json()["broken"]


# --- Removing ------------------------------------------------------------------------------------------------

def test_remove_signs_out_the_sessions_that_used_it(ui, authn):
    phone = SoftAuthenticator()
    assert add(ui, authn, name="Laptop").status_code == 200
    assert add(ui, phone, name="Phone").status_code == 200
    laptop_id, phone_id = (p["id"] for p in ui.get("/api/account").json()["passkeys"])
    other = TestClient(ui.app, base_url="http://localhost")
    assert login(other, phone).status_code == 200
    third = TestClient(ui.app, base_url="http://localhost")
    assert login(third, authn).status_code == 200

    r = post(ui, "/passkeys/remove", {"id": phone_id})
    assert r.status_code == 200 and "Phone" in r.json()["message"] and "signed out" in r.json()["message"]
    assert not other.get("/api/session").json()["signed_in"]
    assert third.get("/api/session").json()["signed_in"]
    assert [p["id"] for p in ui.get("/api/account").json()["passkeys"]] == [laptop_id]
    assert login(other, phone).status_code == 401

    r = post(ui, "/passkeys/remove", {"id": phone_id})
    assert r.status_code == 409 and "isn't registered" in r.json()["error"]
    r = post(ui, "/passkeys/remove", {"id": laptop_id})
    assert r.status_code == 200 and "signed out" in r.json()["message"]
    assert not third.get("/api/session").json()["signed_in"]
    # This session signed in with the password: removing a passkey signs nobody else out.
    assert add(ui, phone, name="Phone").status_code == 200
    r = post(ui, "/passkeys/remove", {"id": ui.get("/api/account").json()["passkeys"][0]["id"]})
    assert r.status_code == 200 and "signed out" not in r.json()["message"]


# --- Guards and the log --------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["register-options", "register", "remove", "require", "login-options", "login"])
def test_every_post_needs_the_csrf_token(ui, path):
    r = ui.post(f"/api/passkeys/{path}", data={})
    assert r.status_code == 403
    r = ui.post(f"/api/passkeys/{path}", data={}, headers={"X-CSRF-Token": csrf(ui), "Origin": "http://evil.example"})
    assert r.status_code == 403


@pytest.mark.parametrize("path", ["register-options", "register", "remove", "require"])
def test_managing_passkeys_needs_a_sign_in(keyed, path):
    assert post(keyed, f"/passkeys/{path}").status_code == 401
    assert keyed.get("/api/account").status_code == 401


def test_sign_ins_are_logged_without_what_the_client_made_up(keyed, authn, caplog):
    caplog.set_level(logging.INFO, logger="usas_web")
    login(keyed, authn, origin="http://evil.example\nINFO: Signed in")
    login(keyed, authn)
    post(keyed, "/logout")
    post(keyed, "/login", {"password": "wrong"})
    post(keyed, "/login", {"password": PASSWORD})
    lines = [r.getMessage() for r in caplog.records if r.name == "usas_web"]
    assert any(line.startswith("Sign-in with a passkey failed from testclient: origin") for line in lines)
    assert all("\n" not in line for line in lines)
    assert 'Signed in with the passkey "Laptop" from testclient' in lines
    assert "Sign-in with a wrong password from testclient" in lines
    assert "Signed in with the password from testclient" in lines


def test_the_log_is_set_up_by_the_entry_point(monkeypatch, settings, capsys):
    from usas_web import __main__ as entry  # noqa: PLC0415

    monkeypatch.setattr(entry, "from_env", lambda: settings)
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **kw: None)
    entry.main()
    ui_log = logging.getLogger("usas_web")
    try:
        assert ui_log.level == logging.INFO and ui_log.handlers
    finally:
        for handler in ui_log.handlers[:]:
            ui_log.removeHandler(handler)
        ui_log.setLevel(logging.NOTSET)


def test_create_app_shares_one_store(settings):
    app = create_app(settings)
    assert isinstance(app.state.passkeys, pk.Passkeys)


def test_sign_in_responses_that_cant_be_read(keyed, authn):
    garbage = json.loads(authn.get(login_options(keyed)))
    garbage["response"]["clientDataJSON"] = "bm90IGpzb24"  # "not json"
    for response in ("{", json.dumps({"x": "y" * pk.MAX_RESPONSE}), json.dumps(garbage)):
        login_options(keyed)
        r = post(keyed, "/passkeys/login", {"credential": response})
        assert r.status_code == 401 and ("couldn't be read" in r.json()["error"] or "too large" in r.json()["error"])
        keyed.app_state.auth.clear_failures("testclient")
