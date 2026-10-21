#!/usr/bin/env bash
# Prints a Markdown section with the exact ansible-core and Ansible collection versions installed here, for
# the release notes: the release workflow runs it in the job that installs requirements-ci.txt and
# requirements.yml and runs the checks. The collections are the ones requirements.yml names, each at the
# version Ansible loads (the first one on its collections path). Needs ansible-core, the collections and jq.
# Usage: scripts/tested-versions.sh   (from anywhere; reads the repository's ansible.cfg and requirements.yml)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# "ansible [core 2.20.10]" on the first line.
core="$(ansible --version | sed -n '1s/^ansible \[core \([^]]*\)\].*/\1/p')"
python="$(ansible --version | sed -n 's/^ *python version = \([^ ]*\).*/\1/p')"
: "${core:?ansible --version printed no ansible-core version}" "${python:?ansible --version printed no Python version}"

names="$(sed -n 's/^ *- name: *\([a-z0-9_]*\.[a-z0-9_]*\).*/\1/p' requirements.yml)"
: "${names:?requirements.yml lists no collections}"
# Exits non-zero when it finds no collections at all; the loop below then names the missing ones.
installed="$(ansible-galaxy collection list --format json 2>/dev/null || echo "{}")"

cat <<EOF
## Tested with

The release's checks (lint, playbook syntax check, the web UI's unit tests) ran with these versions:
ansible-core as pinned in \`requirements-ci.txt\`, and the newest collections \`requirements.yml\` allowed
at the time. Other versions in those ranges are expected to work, but these are the ones checked.

| Component | Version |
| --- | --- |
| ansible-core | ${core} |
EOF
for name in $names; do
  version="$(jq -r --arg n "$name" '[.[] | .[$n].version // empty] | first // empty' <<<"$installed")"
  if [[ -z "$version" ]]; then
    echo "error: collection $name is not installed (ansible-galaxy collection install -r requirements.yml)" >&2
    exit 1
  fi
  echo "| ${name} | ${version} |"
done
echo "| Python (control node in CI) | ${python} |"
