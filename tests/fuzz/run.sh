#!/usr/bin/env bash
# Runs every fuzz target for FUZZ_SECONDS (default 60) each. Needs Linux x86-64 and Python 3.12-3.14 with
# requirements-ci.txt and requirements-fuzz.txt installed (atheris has wheels only for that platform).
# A finding prints the failing input and saves it as crash-<hash> in the current directory.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
seconds="${FUZZ_SECONDS:-60}"
log="$(mktemp)"
trap 'rm -f "$log"' EXIT
for target in fuzz_*.py; do
  echo "=== $target (${seconds}s)"
  if ! python3 "$target" -max_total_time="$seconds" -rss_limit_mb=2048 -print_final_stats=1 >"$log" 2>&1; then
    cat "$log"
    echo "ERROR: $target found a failing input" >&2
    exit 1
  fi
  grep -E 'stat::number_of_executed_units|stat::new_units_added' "$log" || true
done
echo "=== All fuzz targets ran without a finding"
