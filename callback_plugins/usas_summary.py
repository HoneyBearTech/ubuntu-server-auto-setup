# Writes each host's result of a web UI run (ok, changed, failed, unreachable, skipped, and the names of the
# tasks that changed or failed) as JSON to the file named by USAS_SUMMARY_FILE, after every task result, so the
# run's page can show a table per host while the run goes on. The web UI's runner turns it on for its own runs
# only (ANSIBLE_CALLBACKS_ENABLED=usas_summary). What a host reports is untrusted: messages are cut short and
# stripped of control characters, and the page shows them as text.
from __future__ import annotations

import json
import os
import re
import tempfile
import time

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
name: usas_summary
type: aggregate
short_description: Per-host results of a run, for the web UI
description:
  - Writes the counts and the changed and failed task names of every host to USAS_SUMMARY_FILE as JSON.
requirements:
  - USAS_SUMMARY_FILE set in the environment
"""

MAX_TASKS = 50  # changed or failed task names kept per host
MAX_TEXT = 300
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
COUNTS = ("ok", "changed", "failed", "unreachable", "skipped", "rescued", "ignored")


def clean(value, limit=MAX_TEXT):
    text = " ".join(CONTROL.sub(" ", str(value or "")).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "usas_summary"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self):
        super().__init__()
        self._out = os.environ.get("USAS_SUMMARY_FILE")
        self._hosts = {}
        self._finished = False

    def _host(self, name):
        return self._hosts.setdefault(str(name), {**{k: 0 for k in COUNTS}, "changed_tasks": [], "failed_tasks": []})

    def _write(self):
        if not self._out:
            return
        data = {"finished": self._finished, "updated": int(time.time()), "hosts": self._hosts}
        directory = os.path.dirname(self._out) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".summary.")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self._out)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _count(self, result, kind, message=None):
        host = self._host(result._host.get_name())
        host[kind] += 1
        name = clean(result._task.get_name(), 120)
        if kind == "changed" and len(host["changed_tasks"]) < MAX_TASKS:
            host["changed_tasks"].append(name)
        if kind in ("failed", "unreachable") and len(host["failed_tasks"]) < MAX_TASKS:
            host["failed_tasks"].append({"task": name, "message": clean(message)})
        self._write()

    @staticmethod
    def _message(result):
        data = result._result if isinstance(result._result, dict) else {}
        if data.get("_ansible_no_log") or data.get("censored"):
            return "(hidden: the task's output is secret)"
        return data.get("msg") or data.get("stderr") or data.get("reason") or ""

    def v2_runner_on_ok(self, result):
        # As in the play recap, ok counts the changed results too.
        if isinstance(result._result, dict) and result._result.get("changed"):
            self._host(result._host.get_name())["ok"] += 1
            self._count(result, "changed")
        else:
            self._count(result, "ok")

    def v2_runner_on_failed(self, result, ignore_errors=False):
        if ignore_errors:
            self._count(result, "ignored")
        else:
            self._count(result, "failed", self._message(result))

    def v2_runner_on_unreachable(self, result):
        self._count(result, "unreachable", self._message(result))

    def v2_runner_on_skipped(self, result):
        self._count(result, "skipped")

    def v2_playbook_on_stats(self, stats):
        for name in sorted(stats.processed):
            summary = stats.summarize(name)
            host = self._host(name)
            for key in COUNTS:
                host[key] = int(summary.get("failures" if key == "failed" else key, host[key]) or 0)
        self._finished = True
        self._write()
