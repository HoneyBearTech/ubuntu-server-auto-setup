# Records which role tasks and handlers actually ran (ok, changed or failed, not skipped) during the
# integration tests, one "path:line" per line in $TASK_COVERAGE_FILE. tests/coverage.py turns that into the
# task coverage figure. Enabled only by tests/run.sh (ANSIBLE_CALLBACKS_ENABLED=task_coverage).
from __future__ import annotations

import os

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
name: task_coverage
type: aggregate
short_description: Record which role tasks ran, for the task coverage report
description:
  - Appends the file and line of every task that ran to the file named by TASK_COVERAGE_FILE.
requirements:
  - TASK_COVERAGE_FILE set in the environment
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "task_coverage"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self):
        super().__init__()
        self._out = os.environ.get("TASK_COVERAGE_FILE")

    def _record(self, task):
        path = task.get_path() if task else None
        if not self._out or not path or "/roles/" not in path:
            return
        with open(self._out, "a", encoding="utf-8") as fh:
            fh.write(path + "\n")

    def v2_runner_on_ok(self, result):
        self._record(result._task)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._record(result._task)

    def v2_playbook_on_include(self, included_file):
        self._record(included_file._task)
