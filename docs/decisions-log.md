# Decisions log

Why things in ubuntu-server-auto-setup are the way they are: each entry gives the decision, the reason, what was
considered and not done, and review findings that were declined, with why. Newest first. What shipped when is in the
[pass map](pass-map.md).

## 2026-11-21: fix the wcmatch advisory in the lint tools

Job `wcmatch-advisory`, [#217](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/pull/217).

**Decision:** GHSA-53pm-wgxf-c6qf (published 2026-11-20) is fixed by hand: a `wcmatch>=11.2` floor in
`requirements-dev.txt`, like the `black` and `pygments` floors, and a lock update of only wcmatch and bracex in
`requirements-ci.txt` (`pip-compile --upgrade-package`). **Why:** the advisory failed the OSV check on every pull
request, and Renovate never edits `requirements-dev.txt`, whose lower bounds are set by hand.

- **bracex 3.2.3, not the newest 3.2.4:** wcmatch 11.2 needs bracex 3.2 or later; 3.2.4 was less than a day old,
  and dependency updates here wait 3 days after a release. 3.2.3 is a week old and has no known advisory.
- **Nothing shipped changes:** wcmatch is a lint dependency; the runtime lock, the image and the managed servers
  don't use it.
- **Seen, not changed:** ansible-lint logs that it can't load `build-installer.yml`, `provision-vms.yml` and
  `baseline.yml` for its syntax check, on `main` as on this branch, and still passes; a separate look if wanted.

## 2026-11-20: move dependency updates from Dependabot to Renovate

Job `renovate-migrate`, [#213](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/pull/213).

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

## 2026-11-20: refresh the OS catalog, including Rocky Linux and the point releases

Job `os-catalog-refresh`.

**Decision.** Keep everything `make catalog` wrote: ten cloud-image pins, not the eight the weekly issue (#170)
listed. Rocky Linux 9 and 10 also had newer builds, and Rocky Linux and AlmaLinux move to point releases 9.9 and
10.3.

**Why.** The catalog tool pins the newest build of each release; both vendors update only their newest point
release, so 9.8 and 10.2 stop getting fixes. Nothing in the roles, playbooks or web UI depends on a point release:
they accept any minor version of a supported major, and image file names come from the catalog. Servers already
installed are not changed. CI's real-image boot test checks each new image with the baseline.

**Not done.** The k3s catalog half of #170 (new k3s patch releases, Helm, Argo CD chart): a separate change with its
own tests, in its own pull request. The plan file still says "eight releases"; it records what was approved, and
this entry records the difference.

**Review findings.** Fixed: the changelog said every image was checked against signed checksums, but Debian and
CentOS Stream publish no signatures (the catalog marks them `signed: false`). Noted, no change: the plan said the
installer ISO is checked "if it changed"; CI checks it on any catalog change.
