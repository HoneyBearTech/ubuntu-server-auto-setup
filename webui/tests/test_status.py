"""The status API for dashboards (GET /api/status): off until a token exists, the token (shown once, stored as
an HMAC, replaced, revoked), every way a call must be refused, the throttles, what the answer holds and what
it must never hold, and that it leaves sessions, cookies and the other routes alone."""

import json
import logging
import stat
from dataclasses import replace
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient
from usas_web import remote, status
from usas_web.vms import Vm

from .conftest import PASSWORD, PROXMOX, TOKEN_SECRET, configure, csrf, make_client, post, sign_up
from .test_app import fake_pve, wait_done


def create(c, password=PASSWORD):
    return post(c, "/status-token/create", {"status_current": password})


def new_token(c) -> str:
    r = create(c)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def call(c, token=None, header=None, **kw):
    """A dashboard's call: no cookie (a client of its own), the token as a Bearer header."""
    dashboard = TestClient(c.app, base_url=kw.pop("base_url", "http://localhost"))
    headers = {"Authorization": header if header is not None else f"Bearer {token}"} if token or header else {}
    return dashboard.get("/api/status", headers=headers)


@pytest.fixture
def token(ui):
    return new_token(ui)


# --- parse_bearer and the token's format --------------------------------------------------------------------

def test_a_new_token_has_the_prefix_and_256_bits():
    token = status.new_token()
    assert token.startswith("usas_st_") and len(token) == 8 + 43
    assert status.parse_bearer(f"Bearer {token}") == token
    assert status.new_token() != token


@pytest.mark.parametrize("header", [
    None, "", "Bearer", "Bearer ", "Basic dXNhczp4", "Token usas_st_" + "a" * 43, "Bearer usas_st_" + "a" * 42,
    "Bearer usas_st_" + "a" * 44, "Bearer usas_st_" + "a" * 42 + "=", "Bearer usas_st_" + "a" * 42 + "\n",
    "Bearer usas_pt_" + "a" * 43, "Bearer  " + "x" * 300, "Bearer usas_st_" + "a" * 42 + "é",
    "Bearer usas_st_" + "a" * 43 + " extra",
])
def test_parse_bearer_refuses_anything_else(header):
    assert status.parse_bearer(header) is None


def test_parse_bearer_takes_any_case_and_surrounding_blanks():
    token = "usas_st_" + "A-_9" * 10 + "abc"
    assert status.parse_bearer(f"bearer {token}") == token
    assert status.parse_bearer(f"BEARER \t{token}\t ") == token


# --- Off by default -------------------------------------------------------------------------------------------

def test_the_api_is_off_until_a_token_exists(ui):
    r = call(ui, header="Bearer " + status.new_token())
    assert r.status_code == 404 and "is off" in r.json()["error"]
    info = ui.get("/api/account").json()["status_token"]
    assert info == {"exists": False, "broken": False, "hint": "", "created": None, "last_used": None,
                    "last_from": ""}
    assert not ui.app_state.settings.status_token_file.exists()


# --- Creating, replacing, revoking ------------------------------------------------------------------------------

def test_creating_needs_a_sign_in_csrf_and_the_password(client, caplog):
    assert post(client, "/status-token/create", {"status_current": PASSWORD}).status_code == 401
    sign_up(client)
    r = client.post("/api/status-token/create", data={"status_current": PASSWORD})
    assert r.status_code == 403  # no CSRF token
    r = client.post("/api/status-token/create", data={"status_current": PASSWORD},
                    headers={"X-CSRF-Token": csrf(client), "Origin": "https://evil.example"})
    assert r.status_code == 403
    r = create(client, "wrong password!")
    assert r.status_code == 422 and r.json()["errors"] == {"status_current": "The current password is wrong."}
    assert not client.app_state.settings.status_token_file.exists()
    with caplog.at_level(logging.INFO, logger="usas_web"):
        r = create(client)
    assert r.status_code == 200 and "isn't shown again" in r.json()["message"]
    assert "Status API token created from testclient" in caplog.text
    assert r.json()["token"] not in caplog.text


def test_wrong_passwords_for_a_token_are_slowed_down(ui):
    for _ in range(5):
        assert create(ui, "wrong password!").status_code == 422
    r = create(ui)
    assert r.status_code == 429 and "Try again in" in r.json()["error"]


def test_the_token_is_shown_once_and_stored_as_an_hmac(ui, token):
    path = ui.app_state.settings.status_token_file
    record = json.loads(path.read_text())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert token not in path.read_text() and token[8:] not in path.read_text()
    assert set(record) == {"hash", "hint", "created", "last_used", "last_from"}
    assert len(record["hash"]) == 64 and record["hint"] == token[8:12]
    info = ui.get("/api/account").json()["status_token"]
    assert info["exists"] and info["hint"] == token[8:12] and info["created"] > 0 and info["last_used"] is None
    assert token not in ui.get("/api/account").text


def test_replacing_makes_the_old_token_stop_working(ui, token, caplog):
    assert call(ui, token).status_code == 200
    with caplog.at_level(logging.INFO, logger="usas_web"):
        r = create(ui)
    assert "the old one no longer works" in r.json()["message"] and "token replaced" in caplog.text
    second = r.json()["token"]
    assert second != token
    assert call(ui, token).status_code == 401
    assert call(ui, second).status_code == 200


def test_revoking_turns_the_api_off(ui, token, caplog):
    with caplog.at_level(logging.INFO, logger="usas_web"):
        r = post(ui, "/status-token/revoke")
    assert r.status_code == 200 and "Revoked" in r.json()["message"] and "token revoked" in caplog.text
    assert call(ui, token).status_code == 404
    assert not ui.app_state.settings.status_token_file.exists()
    assert "There is no token" in post(ui, "/status-token/revoke").json()["message"]


def test_revoking_needs_a_sign_in_and_csrf(ui, token):
    assert ui.post("/api/status-token/revoke").status_code == 403
    post(ui, "/logout")
    assert post(ui, "/status-token/revoke").status_code == 401
    assert call(ui, token).status_code == 200


def test_a_new_vault_password_invalidates_the_token(ui, token, settings):
    assert call(ui, token).status_code == 200
    settings.vault_password_file.write_text("another vault password\n")
    assert call(ui, token).status_code == 401


def test_an_unreadable_token_file_accepts_no_token(ui, token):
    path = ui.app_state.settings.status_token_file
    path.write_text("{not json")
    assert call(ui, token).status_code == 401
    assert ui.get("/api/account").json()["status_token"]["broken"] is True
    path.write_text('{"hash": 5}')
    assert call(ui, token).status_code == 401
    assert post(ui, "/status-token/revoke").status_code == 200 and not path.exists()


# --- Refused calls ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("header,reason", [
    (None, "no token"), ("Basic dXNhczpwYXNz", "not a status token"), ("Bearer nope", "not a status token"),
    ("Bearer " + "usas_st_" + "x" * 43, "wrong token"),
])
def test_calls_without_the_right_token_are_refused(ui, token, header, reason, caplog):
    with caplog.at_level(logging.WARNING, logger="usas_web"):
        r = call(ui, header=header) if header else TestClient(ui.app, base_url="http://localhost").get("/api/status")
    assert r.status_code == 401 and r.headers["www-authenticate"] == 'Bearer realm="usas"'
    assert r.json() == {"error": "The token is missing or wrong. Create one on the Account page."}
    assert f"Status API call refused from testclient: {reason}" in caplog.text
    assert "set-cookie" not in r.headers


def test_the_token_is_not_taken_from_the_query_string_or_other_headers(ui, token):
    dashboard = TestClient(ui.app, base_url="http://localhost")
    assert dashboard.get(f"/api/status?token={token}").status_code == 401
    assert dashboard.get(f"/api/status?access_token={token}").status_code == 401
    assert dashboard.get("/api/status", headers={"X-Api-Key": token}).status_code == 401
    dashboard.cookies.set("usas_session", token)
    assert dashboard.get("/api/status").status_code == 401


def test_only_get(ui, token):
    dashboard = TestClient(ui.app, base_url="http://localhost")
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        r = dashboard.request(method, "/api/status", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 405, method


def test_wrong_tokens_are_slowed_down_per_client_apart_from_the_sign_in(ui, token):
    wrong = "Bearer usas_st_" + "x" * 43
    for _ in range(5):
        assert call(ui, header=wrong).status_code == 401
    r = call(ui, token)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0  # even the right token waits
    assert "wrong token" in r.json()["error"]
    # The sign-in's own throttle is untouched: a dashboard with an old token can't lock the owner out.
    assert ui.app_state.auth.wait_seconds("testclient") == 0
    post(ui, "/logout")
    assert post(ui, "/login", {"password": PASSWORD}).status_code == 200


def test_a_right_token_clears_the_failures(ui, token):
    throttle = ui.app_state.status_throttle
    for _ in range(4):
        assert call(ui, header="Bearer nope").status_code == 401
    assert call(ui, token).status_code == 200
    assert throttle.wait_seconds("testclient") == 0 and "testclient" not in throttle._failures


def test_right_tokens_are_limited_per_minute(ui, token, monkeypatch):
    monkeypatch.setattr(status, "PER_MINUTE", 3)
    assert [call(ui, token).status_code for _ in range(3)] == [200, 200, 200]
    r = call(ui, token)
    assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 60 and "3 calls a minute" in r.text


def test_the_throttle_waits_and_limits_by_the_clock():
    now = [1000.0]
    t = status.Throttle(clock=lambda: now[0])
    for _ in range(5):
        t.record_failure("a")
    assert t.wait_seconds("a") == 2 and t.wait_seconds("b") == 0
    for _ in range(20):
        t.record_failure("a")
    assert t.wait_seconds("a") == status.MAX_DELAY
    now[0] += status.MAX_DELAY
    assert t.wait_seconds("a") == 0
    assert all(t.over_limit() == 0 for _ in range(status.PER_MINUTE))
    assert t.over_limit() == 60
    now[0] += 30
    assert t.over_limit() == 30
    now[0] += 30
    assert t.over_limit() == 0
    assert t.should_log("a") and not t.should_log("a") and t.should_log("b")
    now[0] += status.LOG_EVERY
    assert t.should_log("a")


# --- No session, the guard still applies ---------------------------------------------------------------------

def test_calls_make_no_session_and_set_no_cookie(ui, token):
    sessions = len(ui.app_state.auth._sessions)
    for _ in range(5):
        r = call(ui, token)
        assert r.status_code == 200 and "set-cookie" not in r.headers
    assert call(ui, header="Bearer nope").status_code == 401
    assert len(ui.app_state.auth._sessions) == sessions


def test_the_answer_has_the_strict_headers(ui, token):
    r = call(ui, token)
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["content-type"] == "application/json"
    assert "access-control-allow-origin" not in r.headers


def test_the_host_check_and_https_rule_still_apply(settings, fake_ansible):
    s = replace(settings, allowed_hosts=("usas.example.lan",) + settings.allowed_hosts)
    c, app = make_client(s)
    c.app_state = app.state
    sign_up(c)
    token = new_token(c)
    assert call(c, token, base_url="http://evil.example").status_code == 400
    r = call(c, token, base_url="http://usas.example.lan")
    assert r.status_code == 400 and "HTTPS" in r.text
    assert call(c, token, base_url="https://usas.example.lan").status_code == 200


def test_a_signed_in_session_doesnt_open_it(ui, token):
    r = ui.get("/api/status")  # the session's cookie, no token
    assert r.status_code == 401


def test_the_token_opens_nothing_else(ui, token):
    post(ui, "/logout")
    dashboard = TestClient(ui.app, base_url="http://localhost")
    headers = {"Authorization": f"Bearer {token}"}
    for path in ("/api/overview", "/api/runs", "/api/vms", "/api/account", "/api/proxmox"):
        assert dashboard.get(path, headers=headers).status_code == 401, path
    assert dashboard.get("/runs", headers=headers, follow_redirects=False).status_code == 303
    r = dashboard.post("/api/run", data={"playbook": "site"}, headers=headers)
    assert r.status_code in (401, 403)


# --- The answer -----------------------------------------------------------------------------------------------

def test_the_answer_before_anything_is_set_up(ui, token):
    data = call(ui, token).json()
    assert data == {"schema": 1, "version": data["version"], "setup": {"done": 0, "total": 5}, "running": None,
                    "last_run": None, "servers": {"total": 0, "pending": None}, "vms": None,
                    "fetched": data["fetched"]}
    assert isinstance(data["version"], str) and data["fetched"] > 0


def test_the_answer_with_servers_runs_and_the_cluster(ui, token, monkeypatch):
    configure(ui, servers=3)  # VMs 4000-4002; the cluster has 4000, so two are still to install
    fake_pve(monkeypatch, resources=[
        {"vmid": 4000, "name": "web-00", "node": "pve-1", "type": "qemu", "status": "running"},
        {"vmid": 101, "name": "app", "node": "pve-1", "type": "qemu", "status": "stopped"},
        {"vmid": 900, "name": "tmpl", "node": "pve-1", "type": "qemu", "status": "stopped", "template": 1},
        {"vmid": 200, "name": "ct", "node": "pve-1", "type": "lxc", "status": "running"},
    ])
    run_id = post(ui, "/run", {"playbook": "baseline", "check": "on", "limit": "web-00"}).json()["run"]["id"]
    run = wait_done(ui, run_id)
    data = call(ui, token).json()
    assert data["setup"] == {"done": 3, "total": 5}  # no SSH key or node address yet
    assert data["running"] is None
    assert data["last_run"] == {"playbook": "baseline", "check": True, "started": int(run.started),
                                "status": "succeeded", "finished": int(run.finished)}
    assert data["servers"] == {"total": 3, "pending": 2}
    assert data["vms"] == {"running": 1, "total": 2}  # QEMU VMs, not the template or the container


def test_the_answer_while_a_run_is_going(ui, token, monkeypatch):
    configure(ui)
    monkeypatch.setitem(ui.app_state.settings.run_env, "FAKE_DELAY", "30")  # the run's own environment
    run_id = post(ui, "/run", {"playbook": "site"}).json()["run"]["id"]
    try:
        data = call(ui, token).json()
        assert data["running"]["playbook"] == "site" and data["running"]["check"] is False
        assert set(data["running"]) == {"playbook", "check", "started"}
        assert data["last_run"] is None
    finally:
        ui.app_state.runner.stop(run_id)
        wait_done(ui, run_id, timeout=30)
    assert call(ui, token).json()["last_run"]["status"] == "stopped"


def test_the_answer_never_holds_names_addresses_or_secrets(ui, token, monkeypatch):
    configure(ui, servers=2)
    ui.app_state.store.save_cluster("olympus-cluster", {"pve-1": "online"})
    fake_pve(monkeypatch)
    run_id = post(ui, "/run", {"playbook": "keys", "limit": "web-01"}).json()["run"]["id"]
    wait_done(ui, run_id)
    body = call(ui, token).text
    for secret in (TOKEN_SECRET, PASSWORD, token, token[8:], "admin pass", "automation", "ansible@pve",
                   "pve.example.lan", "pve-1", "olympus", "web-0", "10.0.0.", "app", run_id, "octocat",
                   "example.lan", "vault", "hash"):
        assert secret not in body, secret

    def numbers(value):
        if isinstance(value, dict):
            return [n for v in value.values() for n in numbers(v)]
        return [value] if isinstance(value, int) and not isinstance(value, bool) else []
    assert not {4000, 4001, 101} & set(numbers(json.loads(body)))  # no VM IDs
    assert set(json.loads(body)) == {"schema", "version", "setup", "running", "last_run", "servers", "vms",
                                     "fetched"}


def test_the_cluster_is_asked_at_most_once_a_minute(ui, token, monkeypatch):
    configure(ui)
    calls = []
    fake_pve(monkeypatch, calls=calls)
    for _ in range(5):
        assert call(ui, token).json()["vms"] == {"running": 2, "total": 2}
    assert [url for url, _ in calls if "cluster/resources" in url] != []
    assert len([url for url, _ in calls if "cluster/resources" in url]) == 1
    assert all("/agent/" not in url and "/config" not in url for url, _ in calls)  # no guest agents


def test_an_unreachable_cluster_means_unknown_counts(ui, token, monkeypatch):
    configure(ui)
    tries = []

    def down(url, **kw):
        tries.append(url)
        raise requests.exceptions.ConnectionError()
    monkeypatch.setattr(remote.requests, "get", down)
    for _ in range(3):
        data = call(ui, token).json()
        assert data["vms"] is None and data["servers"] == {"total": 1, "pending": None}
    assert len(tries) == 1  # the failure is kept for a minute too
    assert "Couldn't connect" not in call(ui, token).text


def test_new_api_settings_ask_the_cluster_again(ui, token, monkeypatch):
    configure(ui)
    calls = []
    fake_pve(monkeypatch, calls=calls)
    call(ui, token)
    post(ui, "/proxmox", {"action": "save", **{**PROXMOX, "pve_api_host": "pve2.example.lan"}})
    call(ui, token)
    assert len([url for url, _ in calls if "cluster/resources" in url]) == 2


def test_cluster_counts_expire():
    now = [0.0]
    counts = status.ClusterCounts(clock=lambda: now[0])
    fetched = []

    def fetch():
        fetched.append(1)
        return ["vm"]
    assert counts.get("a", fetch) == ["vm"] and counts.get("a", fetch) == ["vm"] and len(fetched) == 1
    now[0] += status.CLUSTER_TTL
    assert counts.get("a", fetch) == ["vm"] and len(fetched) == 2
    assert counts.get("b", fetch) == ["vm"] and len(fetched) == 3

    def broken():
        raise RuntimeError("down")
    now[0] += status.CLUSTER_TTL
    assert counts.get("b", broken) is None and counts.get("b", fetch) is None and len(fetched) == 3


def test_summary_maps_runs_and_vms():
    def run(status_, started, finished=None, action="site"):
        return SimpleNamespace(action=action, check=False, started=started, finished=finished, status=status_,
                               done=status_ != "running", limit="secret-host", id="20261023-000000-abcdef")
    history = [run("running", 30), run("weird", 20, 25, "provision"), run("succeeded", 10, 15)]
    vms = [Vm(vmid=4000, name="x", node="n", type="qemu", status="running"),
           Vm(vmid=4001, name="y", node="n", type="qemu", status="paused")]
    data = status.summary("1.2.3", history[0], history, [{"done": True}, {"done": False}],
                          [{"pve_vmid": 4000}, {"pve_vmid": 4002}, {"name": "no-vmid"}], vms, 99.5)
    assert data["running"] == {"playbook": "site", "check": False, "started": 30}
    assert data["last_run"] == {"playbook": "provision", "check": False, "started": 20, "status": "failed",
                                "finished": 25}
    assert data["setup"] == {"done": 1, "total": 2} and data["fetched"] == 99
    assert data["servers"] == {"total": 3, "pending": 1} and data["vms"] == {"running": 1, "total": 2}
    assert "secret-host" not in json.dumps(data) and "abcdef" not in json.dumps(data)
    assert status.summary("v", None, [], [], [], None, 0)["last_run"] is None


# --- Last use -------------------------------------------------------------------------------------------------

def test_last_use_is_shown_and_written_at_most_every_five_minutes(tmp_path, caplog):
    now = [1000.0]
    tokens = status.StatusTokens(tmp_path / "t.json", lambda: b"vault", clock=lambda: now[0])
    token = tokens.create()
    assert tokens.verify(token) and not tokens.verify(token[:-1] + ("A" if token[-1] != "A" else "B"))
    tokens.used("10.0.0.5")
    assert json.loads((tmp_path / "t.json").read_text())["last_used"] == 1000
    now[0] += 60
    tokens.used("10.0.0.6")
    assert json.loads((tmp_path / "t.json").read_text())["last_from"] == "10.0.0.5"  # not written yet
    assert tokens.info()["last_used"] == 1060 and tokens.info()["last_from"] == "10.0.0.6"
    now[0] += status.SAVE_EVERY
    tokens.used("10.0.0.6")
    assert json.loads((tmp_path / "t.json").read_text())["last_from"] == "10.0.0.6"
    tokens.revoke()
    tokens.used("10.0.0.7")  # a call that was checked just before the revoke writes nothing back
    assert not (tmp_path / "t.json").exists() and not tokens.exists()


def test_a_use_is_logged_once_an_hour_per_address(ui, token, caplog):
    with caplog.at_level(logging.INFO, logger="usas_web"):
        for _ in range(3):
            call(ui, token)
    assert caplog.text.count("Status API used from testclient") == 1
    assert ui.get("/api/account").json()["status_token"]["last_from"] == "testclient"
