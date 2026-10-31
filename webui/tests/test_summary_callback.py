"""The summary callback (callback_plugins/usas_summary.py) in a real ansible-playbook run on this machine."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLAYBOOK = """\
- hosts: all
  gather_facts: false
  tasks:
    - name: Nothing to change
      ansible.builtin.command: "true"
      changed_when: false
    - name: A change
      ansible.builtin.command: "true"
    - name: Skipped
      ansible.builtin.debug:
      when: false
    - name: Ignored failure
      ansible.builtin.command: "false"
      ignore_errors: true
    - name: Secret failure
      ansible.builtin.command: "false"
      no_log: true
      ignore_errors: "{{ inventory_hostname == 'two' }}"
    - name: "Fails with \\u202eodd text"
      ansible.builtin.fail:
        msg: "bad \\x1b[31m thing"
"""


def test_per_host_results(tmp_path):
    ansible = Path(sys.executable).with_name("ansible-playbook")
    if not ansible.exists():
        pytest.skip("ansible-playbook isn't installed next to this Python")
    (tmp_path / "play.yml").write_text(PLAYBOOK)
    out = tmp_path / "hosts.json"
    env = {**os.environ, "ANSIBLE_CALLBACK_PLUGINS": str(REPO / "callback_plugins"),
           "ANSIBLE_CALLBACKS_ENABLED": "usas_summary", "USAS_SUMMARY_FILE": str(out), "ANSIBLE_NOCOLOR": "1",
           "ANSIBLE_LOCALHOST_WARNING": "False", "ANSIBLE_PYTHON_INTERPRETER": sys.executable}
    result = subprocess.run([str(ansible), "-i", "one,two,", "-c", "local", str(tmp_path / "play.yml")],  # noqa: S603
                            env=env, capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 2, result.stdout[-2000:]
    data = json.loads(out.read_text())
    assert data["finished"] is True and set(data["hosts"]) == {"one", "two"}
    one = data["hosts"]["one"]
    # As the play recap counts them: ok includes the changed results, and the ignored failure.
    assert (one["ok"], one["changed"], one["skipped"], one["ignored"], one["failed"]) == (3, 2, 1, 1, 1)
    assert one["changed_tasks"] == ["A change"]
    assert one["failed_tasks"] == [{"task": "Secret failure", "message": "(hidden: the task's output is secret)"}]
    two = data["hosts"]["two"]
    assert two["ignored"] == 2 and two["failed"] == 1
    failed = two["failed_tasks"][0]
    assert failed["task"] == "Fails with odd text" and failed["message"] == "bad [31m thing"
    assert out.stat().st_mode & 0o777 == 0o600


def test_updates_results_are_cleaned():
    sys.path.insert(0, str(REPO / "callback_plugins"))
    try:
        import usas_summary as cb
    finally:
        sys.path.pop(0)
    hostile = {"host": "vm-101", "status": "rm -rf", "reason": "bad\x1b[31m", "security_count": True,
               "pending": [{"name": "openssl", "current": "1", "candidate": "2", "security": True},
                           {"name": "$(id)", "current": "1"}, "nope"],
               "upgraded": [{"name": "tool", "from": "1.0", "to": "1.1‮"}],
               "held": ["usas-test-held", "bad name"], "reboot": {"required": 1, "why": "x" * 500},
               "wave": {"number": 2, "of": 3, "groups": ["web"], "ha": "yes"}, "snapshot": "none",
               "deferred": ["p"] * (cb.MAX_PACKAGES + 10)}
    result = cb.update_result(hostile)
    assert result["status"] == "failed" and result["reason"] == "bad [31m" and result["security_count"] == 0
    assert result["pending"] == [{"name": "openssl", "current": "1", "candidate": "2", "security": True}]
    assert result["upgraded"] == [{"name": "tool", "from": "1.0", "to": "1.1"}]
    assert result["held"] == ["usas-test-held"] and result["reboot"]["required"] is True
    assert len(result["reboot"]["why"]) == 200 and result["snapshot"]["status"] == ""
    assert result["wave"] == {"number": 2, "of": 3, "groups": ["web"], "ha": True}
    assert len(result["deferred"]) == cb.MAX_PACKAGES


UPDATES_PLAYBOOK = """\
- hosts: all
  gather_facts: false
  tasks:
    - name: Collect the results
      ansible.builtin.set_fact:
        usas_updates_results:
          - {host: one, status: updated, upgraded: [{name: tool, from: "1.0", to: "1.1"}]}
          - {host: two, status: nothing}
        usas_updates_summary: {counts: {updated: 1, nothing: 1}}
      run_once: true
"""


def test_updates_results_reach_the_file(tmp_path):
    ansible = Path(sys.executable).with_name("ansible-playbook")
    if not ansible.exists():
        pytest.skip("ansible-playbook isn't installed next to this Python")
    (tmp_path / "play.yml").write_text(UPDATES_PLAYBOOK)
    out = tmp_path / "hosts.json"
    env = {**os.environ, "ANSIBLE_CALLBACK_PLUGINS": str(REPO / "callback_plugins"),
           "ANSIBLE_CALLBACKS_ENABLED": "usas_summary", "USAS_SUMMARY_FILE": str(out), "ANSIBLE_NOCOLOR": "1",
           "ANSIBLE_LOCALHOST_WARNING": "False", "ANSIBLE_PYTHON_INTERPRETER": sys.executable}
    result = subprocess.run([str(ansible), "-i", "one,two,", "-c", "local", str(tmp_path / "play.yml")],  # noqa: S603
                            env=env, capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout[-2000:]
    updates = json.loads(out.read_text())["updates"]
    assert set(updates["hosts"]) == {"one", "two"} and updates["hosts"]["one"]["upgraded"][0]["to"] == "1.1"
    assert updates["summary"]["updated"] == 1 and updates["summary"]["failed"] == 0
