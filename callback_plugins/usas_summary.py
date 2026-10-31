# Writes each host's result of a web UI run (ok, changed, failed, unreachable, skipped, and the names of the
# tasks that changed or failed) as JSON to the file named by USAS_SUMMARY_FILE, after every task result, so the
# run's page can show a table per host while the run goes on. The web UI's runner turns it on for its own runs
# only (ANSIBLE_CALLBACKS_ENABLED=usas_summary). What a host reports is untrusted: messages are cut short and
# stripped of control characters, and the page shows them as text.
#
# For the updates playbook it also keeps each server's result (roles/updates/tasks/report.yml sets
# usas_updates_results once all servers are done) under "updates": the status, the packages, the reboot, the
# snapshot and the wave. Package names that don't look like one are dropped, and each list stops at MAX_PACKAGES.
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
MAX_PACKAGES = 5000  # entries kept per list of an updates result
PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._:~-]{0,127}\Z")
UPDATE_STATUSES = ("updated", "nothing", "pending", "busy", "skipped", "unsupported", "unreachable", "failed")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
COUNTS = ("ok", "changed", "failed", "unreachable", "skipped", "rescued", "ignored")


def clean(value, limit=MAX_TEXT):
    text = " ".join(CONTROL.sub(" ", str(value or "")).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value[:MAX_PACKAGES] if isinstance(value, list) else []


def _names(value):
    """Package or service names: the ones that look like one."""
    found = []
    for item in _list(value):
        name = clean(item.get("name") if isinstance(item, dict) else item, 128)
        if PACKAGE.match(name):
            found.append(name)
    return found


def _packages(value, keys):
    """Package entries (dicts) with a valid name, and the given keys as short text (or booleans)."""
    found = []
    for item in _list(value):
        if not isinstance(item, dict) or not PACKAGE.match(clean(item.get("name"), 128)):
            continue
        entry = {"name": clean(item.get("name"), 128)}
        for key in keys:
            raw = item.get(key)
            entry[key] = raw if isinstance(raw, bool) else clean(raw, 64)
        found.append(entry)
    return found


def _number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10**6 else 0


def update_result(r):
    """What the run's page shows of one server's usas_updates_result."""
    reboot, health, snapshot, wave, os_ = (_dict(r.get(k)) for k in ("reboot", "health", "snapshot", "wave", "os"))
    status = r.get("status") if r.get("status") in UPDATE_STATUSES else "failed"
    return {
        "status": status, "reason": clean(r.get("reason")), "scope": clean(r.get("scope"), 20),
        "os": clean(os_.get("facts") or os_.get("hint"), 80),
        "pending": _packages(r.get("pending"), ("current", "candidate", "security")),
        "planned": _names(r.get("planned")),
        "upgraded": _packages(r.get("upgraded"), ("from", "to")),
        "security_count": _number(r.get("security_count")),
        "held": _names(r.get("held")), "excluded": _names(r.get("excluded")), "deferred": _names(r.get("deferred")),
        "removals": _names(r.get("removals")), "restart_services": _names(r.get("restart_services")),
        "reboot": {"required": bool(reboot.get("required")), "done": bool(reboot.get("done")),
                   "held": bool(reboot.get("held")), "why": clean(reboot.get("why"), 200)},
        "health": {"status": clean(health.get("status"), 20),
                   "problems": [clean(p, 200) for p in _list(health.get("problems"))[:50]]},
        "snapshot": {"status": clean(snapshot.get("status"), 20), "name": clean(snapshot.get("name"), 64),
                     "reason": clean(snapshot.get("reason"), 200), "warning": clean(snapshot.get("warning"), 200)},
        "wave": {"number": _number(wave.get("number")), "of": _number(wave.get("of")),
                 "groups": _names(wave.get("groups"))[:16], "ha": bool(wave.get("ha"))},
        "auto_updates": clean(r.get("auto_updates"), 200),
    }


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "usas_summary"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self):
        super().__init__()
        self._out = os.environ.get("USAS_SUMMARY_FILE")
        self._hosts = {}
        self._updates = None
        self._finished = False

    def _host(self, name):
        return self._hosts.setdefault(str(name), {**{k: 0 for k in COUNTS}, "changed_tasks": [], "failed_tasks": []})

    def _write(self):
        if not self._out:
            return
        data = {"finished": self._finished, "updated": int(time.time()), "hosts": self._hosts}
        if self._updates is not None:
            data["updates"] = self._updates
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

    def _updates_results(self, result):
        facts = _dict(_dict(result._result).get("ansible_facts"))
        results = facts.get("usas_updates_results")
        if not isinstance(results, list):
            return
        hosts = {}
        for r in results[:MAX_PACKAGES]:
            if isinstance(r, dict) and r.get("host"):
                hosts[clean(r["host"], 128)] = update_result(r)
        counts = _dict(_dict(facts.get("usas_updates_summary")).get("counts"))
        self._updates = {"hosts": hosts, "summary": {s: _number(counts.get(s)) for s in UPDATE_STATUSES}}

    def v2_runner_on_ok(self, result):
        self._updates_results(result)
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
