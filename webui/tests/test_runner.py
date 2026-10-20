"""Running ansible-playbook: the command line, the environment, redaction, the history."""

import json
import time
from dataclasses import replace

import pytest
from usas_web import runner as r
from usas_web.runner import Run, RunError, Runner, parse_limit

from .conftest import TOKEN_SECRET


@pytest.fixture
def runner(settings, fake_ansible):
    s = replace(settings, ansible_playbook=str(fake_ansible))
    s.inventory_file.parent.mkdir(parents=True)
    s.inventory_file.write_text("---\n")
    return Runner(s)


NAMES = {"web-01", "db-01", "ubuntu_servers", "all"}

HOSTILE_LIMITS = [
    "-e @/etc/passwd", "--become", "web-01 --become", "web-01;id", "web-01\nid", "web-01\n", "\nweb-01",
    "-web-01", "--limit=all", "web-01 -e x=1", "*", "web-0?", "../web-01", "web-01|id", "$(id)", "`id`",
]


def wait(runner, run_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = runner.get(run_id)
        if run.done:
            return run
        time.sleep(0.05)
    raise AssertionError("the run didn't finish")


def test_parse_limit():
    assert parse_limit("", NAMES) == ""
    assert parse_limit(" web-01, db-01 ", NAMES) == "web-01,db-01"
    assert parse_limit("ubuntu_servers:!db-01,&web-01", NAMES) == "ubuntu_servers,!db-01,&web-01"
    with pytest.raises(RunError):
        parse_limit("web-02", NAMES)


@pytest.mark.parametrize("hostile", HOSTILE_LIMITS)
def test_hostile_limits_are_refused(hostile):
    with pytest.raises(RunError, match="isn't a"):
        parse_limit(hostile, NAMES)


def test_inventory_names_that_could_be_options_are_never_used():
    # Even a name in the inventory can't reach the command line if it looks like an option.
    with pytest.raises(RunError):
        parse_limit("-e", NAMES | {"-e"})


@pytest.mark.parametrize("hostile", HOSTILE_LIMITS)
def test_hostile_values_never_reach_the_command(runner, hostile):
    with pytest.raises(RunError):
        runner.start("baseline", False, False, hostile, NAMES, [])
    for action in (hostile, "baseline --become", "-e @/etc/passwd", "baseline;id", "baseline\n"):
        with pytest.raises(RunError, match="unknown action"):
            runner.start(action, False, False, "", NAMES, [])
        with pytest.raises(RunError, match="unknown action"):
            runner.command(action, False, False, "")
    assert runner.current() is None and runner.history() == []


def test_command_is_an_argument_list(runner):
    argv = runner.command("keys", True, True, "web-01")
    assert argv[1:] == ["-i", str(runner.settings.inventory_file), "--vault-password-file",
                        str(runner.settings.vault_password_file), "--tags", "ssh_keys", "--check", "--diff",
                        "--limit=web-01", "playbooks/baseline.yml"]
    assert runner.command("site", False, False, "")[-1] == "playbooks/site.yml"


def test_environment(runner):
    env = runner.environment()
    assert env["ANSIBLE_CONFIG"].endswith("ansible.cfg") and env["ANSIBLE_NOCOLOR"] == "1"
    assert "SSL_CERT_FILE" not in env
    runner.settings.ca_bundle.write_text("x")
    assert runner.environment()["SSL_CERT_FILE"] == str(runner.settings.ca_bundle)


def test_a_run_is_logged_with_secrets_redacted(runner):
    run = runner.start("provision", False, False, "", NAMES, [TOKEN_SECRET, "", "abc"])
    with pytest.raises(RunError, match="Another run"):
        runner.start("baseline", False, False, "", NAMES, [])
    done = wait(runner, run.id)
    assert done.status == "succeeded" and done.returncode == 0 and done.finished >= done.started
    log = runner.log_path(run.id).read_text()
    assert log.startswith("$ ansible-playbook -i ")
    assert "ARGS -i" in log and "playbooks/provision-vms.yml" in log
    assert TOKEN_SECRET not in log and f"SECRET {r.REDACTED}" in log
    assert runner.current() is None
    assert [x.id for x in runner.history()] == [run.id]


def test_a_failed_run(runner, monkeypatch):
    runner.settings.run_env["FAKE_RC"] = "2"
    run = wait(runner, runner.start("baseline", True, False, "", NAMES, []).id)
    assert run.status == "failed" and run.returncode == 2


def test_stop(runner):
    runner.settings.run_env["FAKE_DELAY"] = "30"
    run = runner.start("site", False, False, "", NAMES, [])
    assert not runner.stop("19990101-000000-abcdef")
    assert runner.stop(run.id)
    assert wait(runner, run.id).status == "stopped"


def test_unknown_or_unready(runner, settings):
    with pytest.raises(RunError, match="unknown"):
        runner.start("rm", False, False, "", NAMES, [])
    runner.settings.inventory_file.unlink()
    with pytest.raises(RunError, match="no inventory"):
        runner.start("site", False, False, "", NAMES, [])
    assert runner.get("../../etc/passwd") is None and runner.get("20250101-000000-abcdef") is None
    with pytest.raises(RunError):
        runner.log_path("../x")


def test_interrupted_runs_and_pruning(runner, monkeypatch):
    stale = Run(id="20250101-000000-aaaaaa", action="site", check=False, diff=False, limit="", status="running",
                started=1.0)
    runner._save(stale)
    (runner.dir / "20250101-000000-bbbbbb.json").write_text("not json")
    assert Runner(runner.settings).get(stale.id).status == "interrupted"
    monkeypatch.setattr(r, "KEEP_RUNS", 0)
    runner._prune()
    assert runner.history() == []


def test_history_is_newest_first(runner):
    for i in range(3):
        runner._save(Run(id=f"2025010{i + 1}-000000-aaaaaa", action="site", check=False, diff=False, limit="",
                         status="succeeded", started=float(i)))
    assert [x.id[:8] for x in runner.history()] == ["20250103", "20250102", "20250101"]
    assert json.loads((runner.dir / "20250101-000000-aaaaaa.json").read_text())["action"] == "site"
