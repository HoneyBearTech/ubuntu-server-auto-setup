"""Password hashing, throttling, sessions and CSRF tokens."""

import json
import time

import pytest
from usas_web import auth as a
from usas_web.auth import Auth, csrf_ok, password_problem


@pytest.fixture
def auth(settings):
    settings.data_dir.mkdir(parents=True)
    return Auth(settings)


def test_password_rules():
    assert password_problem("short", "short").startswith("Use at least")
    assert password_problem("x" * 2000, "x" * 2000).startswith("Use at most")
    assert password_problem("long enough pass", "long enough pasS") == "The two passwords don't match."
    assert password_problem("long enough pass", "long enough pass") is None


def test_first_run_has_a_setup_code(auth):
    assert not auth.is_configured()
    code = auth.setup_code
    assert len(code) == 14 and code.count("-") == 2
    assert auth.check_setup_code(code.lower().replace("-", "-"))
    assert not auth.check_setup_code("AAAA-BBBB-CCCC")
    auth.set_password("a long enough password")
    assert auth.is_configured() and auth.setup_code == ""
    assert not auth.check_setup_code(code)


def test_password_is_stored_as_argon2id(auth, settings):
    auth.set_password("a long enough password")
    record = json.loads(settings.auth_file.read_text())
    assert record["password_hash"].startswith("$argon2id$")
    assert "a long enough password" not in settings.auth_file.read_text()
    assert auth.verify_password("a long enough password")
    assert not auth.verify_password("a long enough passworD")
    assert Auth(settings).setup_code == ""


def test_a_broken_auth_file_refuses_every_password(auth, settings):
    settings.auth_file.write_text(json.dumps({"password_hash": "not a hash"}))
    assert not auth.verify_password("anything at all")


def test_old_parameters_are_rehashed(auth, settings):
    weak = a.PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    settings.auth_file.write_text(json.dumps({"password_hash": weak.hash("a long enough password")}))
    assert auth.verify_password("a long enough password")
    assert "m=65536" in json.loads(settings.auth_file.read_text())["password_hash"]


def test_throttling(auth, monkeypatch):
    for _ in range(a.FREE_ATTEMPTS):
        assert auth.wait_seconds("1.2.3.4") == 0
        auth.record_failure("1.2.3.4")
    assert auth.wait_seconds("1.2.3.4") > 0
    assert auth.wait_seconds("5.6.7.8") == 0
    auth.clear_failures("1.2.3.4")
    assert auth.wait_seconds("1.2.3.4") == 0
    for _ in range(40):
        auth.record_failure("9.9.9.9")
    assert auth.wait_seconds("9.9.9.9") <= a.MAX_DELAY


def test_sessions(auth, monkeypatch):
    anon = auth.new_session()
    assert auth.get_session(anon.sid) is anon and not anon.authenticated
    signed = auth.log_in(anon)
    assert signed.authenticated and signed.sid != anon.sid and signed.csrf != anon.csrf
    assert auth.get_session(anon.sid) is None
    assert auth.get_session(None) is None and auth.get_session("nope") is None
    other = auth.log_in(None)
    auth.end_all_except(signed)
    assert auth.get_session(other.sid) is None and auth.get_session(signed.sid) is signed
    auth.end(signed)
    assert auth.get_session(signed.sid) is None


def test_sessions_expire(auth, monkeypatch):
    session = auth.new_session()
    now = time.time()
    monkeypatch.setattr(a.time, "time", lambda: now + a.IDLE_SECONDS + 1)
    assert auth.get_session(session.sid) is None
    fresh = auth.new_session()
    monkeypatch.setattr(a.time, "time", lambda: now + a.IDLE_SECONDS + 2 + auth.settings.session_hours * 3600)
    fresh.last_seen = time.time()
    assert auth.get_session(fresh.sid) is None


def test_csrf_tokens(auth):
    session = auth.new_session()
    assert csrf_ok(session, session.csrf)
    # Change the first character to a different one (a random token may itself start with any character).
    forged = ("A" if session.csrf[0] != "A" else "B") + session.csrf[1:]
    assert not csrf_ok(session, forged)
    assert not csrf_ok(session, session.csrf[:-1]) and not csrf_ok(session, session.csrf + "x")
    assert not csrf_ok(session, "")
    assert not csrf_ok(session, None) and not csrf_ok(None, session.csrf)
