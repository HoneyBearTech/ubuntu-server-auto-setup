# Decisions log

Why maintenance changes were made the way they were, newest first. What shipped is in the
[pass map](pass-map.md).

## 2026-11-20: renovate-migrate ([#213](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/pull/213))

**Decision:** Renovate proposes and, where Dependabot's auto-merge workflow did, merges dependency updates;
Dependabot keeps only its alerts, which Renovate reads.

- **Starting point:** the hand-written configuration prepared in #188 (closed), checked against today's files.
- **Automatic merging stays as it was:** patch and minor updates of Python and npm packages and Actions, and the
  CentOS Stream test-image digests (quay.io deletes a tag's old digest within days). Renovate merges them itself
  (`platformAutomerge`), which works because `main` now requires no approving review; every required check still
  gates the merge. New: a 3-day wait after a release (not for the CentOS digests or security fixes).
- **Python lock files:** Renovate moves only the pins in `requirements-ci.txt` and `requirements-webui.txt`
  (`rangeStrategy: update-lockfile`), passing pip-compile the exact version it checked. Raising the lower bounds
  ("bump") was rejected: pip-compile could then lock a newer, younger or major release than the one checked, and
  the bounds in `requirements.txt` and `requirements-dev.txt` are hand-set security floors
  (`tests/check_floors.py`) that an edit would move without regenerating the lock files. Packages those two files
  bring into the lock files are updated too (they count as indirect, which Renovate otherwise skips).
- **Known limit:** when a lock file is regenerated for an automatic update, other pins its new version needs move
  with it, sometimes to a new major, with no wait of their own. Dependabot's auto-merge had the same property;
  the required checks gate it. Documented in `docs/dependencies.md`.
- **Lock-file maintenance is off** for every manager: Renovate's pip-compile manager turns it on by default, and
  its whole-file refresh would skip the 3-day wait.
- **`requirements-fuzz.txt`** (hash-pinned atheris) is proposed but never merged automatically: rewriting its
  hashes would add wheels the pin leaves out on purpose.
- **Signed commits:** Renovate commits through the GitHub API (`platformCommit`), which signs them, with a
  sign-off line for the DCO check.
- **Left alone:** the workflow files. `dependabot-auto-merge.yml` stays and is idle; removing it, and the
  Dependabot exemption in `dco.yml`, is the maintainer's call.

**Rejected review findings:**
- Hold back security fixes or Actions updates in the release workflow from merging on their own: both merged
  automatically under Dependabot, the maintainer asked for the same behaviour, and the 3-day wait (not for
  security fixes) is stricter than before.
- Turn off automatic merging for `requirements.txt`: no longer applies, since Renovate no longer edits that file.
