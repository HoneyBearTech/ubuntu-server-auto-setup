# Plan: move dependency updates from Dependabot to Renovate

**What the owner asked for:** "remove Dependabot workflows, turn off Dependabot security updates, and turn on Renovate"
and, for this repository, "ubuntu-server-auto-setup will use Renovate and only use Dependabot for alerts". The owner
set the required approvals on `main` to 0 (2026-11-20), so Renovate's own automatic merging can replace the
auto-merge workflow.

## What we found

- `.github/dependabot.yml` covers pip (control node, web UI, lint, test and fuzz tools), npm (`webui/frontend`), the
  GitHub Actions and the Docker base images (the web UI image, the Proxmox test container and the integration-test
  target, the last one daily), with groups and holds: react together, development tools together, no `@types/node`
  majors, Python below 3.15, Node.js LTS only, the test target's stages by digest only.
- `.github/workflows/dependabot-auto-merge.yml` merges Dependabot's patch and minor updates (not Docker) once every
  required check passes, and also the CentOS Stream digests of the test target image, because quay.io deletes the
  digest a tag left within days and the integration tests break if that update waits.
- A hand-written Renovate config that carries all of this over was prepared on 14 November (#188, closed, automerge
  off); it is the starting point, checked again against today's `main`.
- `main` requires eleven checks (lint, integration tests, gitleaks, DCO, dependency review, three CodeQL analyses,
  OSV scan, web UI tests, image smoke test), signed commits and squash merges; `dco.yml` already accepts Renovate's
  commits.

## What we'll change

1. **Add `renovate.json`** from #188's config, updated for today's files: every ecosystem, group and hold above,
   plus the pins Dependabot never saw (`requirements.yml`'s collections, the `docker run` tool images in workflows,
   `python-version` and `node-version` in workflows); weekly, early on Monday (America/Chicago), assigned to the
   owner; signed commits with a sign-off line.
   **What merges by itself, as today:** patch and minor updates of the Python and npm packages and the GitHub
   Actions, once every required check passes, after a 3-day wait; and the CentOS Stream digests of the test target
   image, with no wait. **Everything else waits for the owner:** major updates, every other image update, and
   anything Renovate can't classify. Security fixes skip the wait. Renovate's periodic whole-lock-file refresh is
   off, so nothing skips the wait that way.
   *Check:* Renovate's config validator in strict mode, and a dry run that finds every file Dependabot covered.
2. **Delete `.github/dependabot.yml`.** Dependabot alerts stay on (Renovate reads them).
3. **Update the docs that name Dependabot as the update tool** (`docs/dependencies.md`, `docs/updates.md` and the
   others that mention it, `GOVERNANCE.md`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md` where relevant), and a
   `CHANGELOG.md` entry.
4. **Job notes:** `docs/roadmap.md`, `docs/pass-map.md` and `docs/decisions-log.md`.

Nothing in `.github/workflows` changes. `dependabot-auto-merge.yml` stays; with no Dependabot update pull requests,
nothing triggers it.

## Who's on it

Engineer, QA, Security Officer, Auditor. No Researcher: #188 already mapped every file; the Engineer checks it against
today's `main`.

## Risks

- Renovate's pull requests are branches in this repository, so their CI runs with its secrets, including pull
  requests that update the Actions in workflow files; the required checks still gate every merge.
- If the CentOS Stream rule is wrong, a digest waits for the owner and the integration tests on CentOS Stream break
  until it is merged; the Auditor checks the rule in Renovate itself.

## Out of scope

- Deleting `dependabot-auto-merge.yml` and workflow comments that name Dependabot (workflow files).
- Repo settings and the branch rules.
