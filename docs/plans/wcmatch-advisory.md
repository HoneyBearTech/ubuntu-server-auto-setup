# Plan: fix the wcmatch advisory that fails the vulnerability scan

**What the owner asked for:** "yes fix the wcmatch advisory"

## What we found

- GHSA-53pm-wgxf-c6qf (published 2026-11-20, medium): wcmatch before 11.2 can take polynomial time when it matches
  untrusted names against patterns with several `*`. 11.2 (released 2026-10-22) fixes it.
- wcmatch comes in only through ansible-lint, a lint tool for development and CI; nothing on a managed server or in
  the web UI uses it.
- The `Vulnerability scan (OSV)` check now fails on every pull request (seen on #216) and will fail on `main`'s next
  run. It reports two places:
  - `requirements-ci.txt` pins `wcmatch==11.1`;
  - `requirements-dev.txt` has no lower bound for wcmatch, so `ansible-lint>=25.9` can resolve to an affected copy.
- Renovate never edits `requirements-dev.txt` (its lower bounds are security floors set by hand, see
  `docs/dependencies.md`), so this needs a hand change.

## What we'll change

1. **`requirements-dev.txt`:** add `wcmatch>=11.2  # through ansible-lint; 11.2 fixes GHSA-53pm-wgxf-c6qf`, in the
   same style as the existing `black` and `pygments` floors.
   *Check:* `tests/check_floors.py` on the requirements files passes.
2. **`requirements-ci.txt`:** regenerate it with the repository's documented pip-compile command (Python 3.12
   image) and `--upgrade-package wcmatch==11.2`, so only wcmatch and bracex move (wcmatch 11.2 needs bracex 3.2 or
   later; the lock has 3.1) and every other pin and hash stays as it is.
   *Check:* the diff of the lock file touches only those packages; the same osv-scanner image and command as CI
   reports nothing; `pip install --require-hashes -r requirements-ci.txt` succeeds in a clean Python 3.12
   container; ansible-lint still runs on the repository.
3. **`CHANGELOG.md`:** a "Security" line under "Unreleased".
4. **Job notes:** `docs/pass-map.md` and `docs/decisions-log.md`.

Nothing in `.github/workflows` changes.

## Who's on it

Engineer, QA, Security Officer, Auditor. No Researcher (the scan output and the advisory are the research).

## Risks

- wcmatch 11.2 is a minor release; ansible-lint accepts it (its requirement has no upper bound). QA runs the lint to
  be sure.

## Out of scope

- Other dependency updates (Renovate's job).
- #216 itself: once this is merged, its scan passes after the branch is updated or the check re-run.
