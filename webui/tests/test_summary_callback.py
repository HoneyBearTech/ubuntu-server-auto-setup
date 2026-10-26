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
