#!/usr/bin/env bash
# Prints what went wrong in an ansible-playbook log: each task that failed, from its TASK line to the next
# task, with the error. A log without a failed task (an error before the first task, or a play that was
# killed) gets its last lines instead. Usage: tests/show-failures.sh LOG [LINES], LINES 200 by default.
# Used by tests/real-iso.sh, whose CI job shows only this and keeps the full logs as an artifact.
set -euo pipefail

log="$1"
lines="${2:-200}"
failed="$(awk '
  function flush() { if (bad && !ignored) printf "%s", block; block = ""; bad = 0; ignored = 0 }
  /^(TASK|RUNNING HANDLER) \[/ || /^PLAY RECAP/ { flush() }
  { block = block $0 "\n" }
  /^(fatal|failed): \[/ || /^ERROR!/ { bad = 1 }
  /^\.\.\.ignoring$/ { ignored = 1 }
  END { flush() }
' "$log")"
if [ -n "$failed" ]; then
  printf '%s\n' "$failed"
else
  echo "(no failed task in $log; its last $lines lines:)"
  tail -n "$lines" "$log"
fi
