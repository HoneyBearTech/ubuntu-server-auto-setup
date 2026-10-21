# Dependencies and vulnerability management

This page says how ubuntu-server-auto-setup chooses, obtains and tracks the software it depends on, and how
findings from dependency scanning (software composition analysis, SCA) and from static analysis of its own
code (SAST) are handled. Reporting a vulnerability in the project itself is covered by
[SECURITY.md](../SECURITY.md).

## Choosing a dependency

A new dependency (a Python package, an Ansible collection, a GitHub Action) is added only when it clearly
beats a few tasks with `ansible.builtin` modules. Before adding one, the pull request says why, and the
dependency must:

- be actively maintained (recent releases, responsive to security reports) and widely used, or small
  enough to review;
- have a license on the allowlist below;
- come from the ecosystem's standard registry (PyPI, Ansible Galaxy, GitHub Actions from their authors, or
  container images from their publishers' registries);
- not duplicate something already in the tree.

Preferred order for Ansible content: `ansible.builtin`, then collections maintained by the Ansible
community (`community.*`, `ansible.posix`), then anything else.

## Obtaining dependencies

Every dependency is declared in a manifest and installed with the ecosystem's standard tool; nothing is
copied into the repository ("vendored").

| What | Declared in | Pinned by | Installed with |
| --- | --- | --- | --- |
| Control-node Python packages (ansible-core, proxmoxer, requests, passlib) | `requirements.txt` | version ranges (for example `ansible-core>=2.20.7,<2.21`) | `pip install -r requirements.txt` (`make deps`) |
| Lint and test tools (ansible-lint, yamllint, ruff, pytest, pytest-cov, httpx2) | `requirements-dev.txt` | minimum versions | `pip install -r requirements-dev.txt` (`make deps`) |
| Web UI packages (FastAPI, uvicorn, python-multipart, argon2-cffi) | `requirements-webui.in` | minimum versions | `make deps`; the image installs `requirements-webui.txt` |
| Python packages in the container image (the control-node and web UI packages, resolved) | `requirements-webui.txt`, compiled from `requirements-webui.in` | exact versions with SHA-256 hashes, wheels only | `pip install --require-hashes` in the `Dockerfile` |
| Container image base (`python:3.12-slim`) and its Debian packages (`openssh-client`, `tzdata`) | `Dockerfile` | image digest; Debian packages at the security archive's current version | `docker build`, release workflow |
| Ansible collections (`community.proxmox`, `community.general`, `ansible.posix`) | `requirements.yml` | version ranges | `ansible-galaxy collection install -r requirements.yml` (`make deps`) |
| CI actions | `.github/workflows/*.yml` | full commit SHAs | GitHub Actions |
| Python packages in CI (all of the above, resolved) | `requirements-ci.txt`, compiled from `requirements-ci.in` | exact versions with SHA-256 hashes (`pip install --require-hashes`) | CI and `make test` |
| CI container images (gitleaks, actionlint, hadolint, Trivy) | `.github/workflows/checks.yml`, `release.yml` | image digests (`@sha256:…`) | Docker |
| Fuzzing engine (atheris) | `requirements-fuzz.in`, pinned in `requirements-fuzz.txt` | exact version with SHA-256 hashes | the fuzzing workflow and `make fuzz` |
| Integration-test base images (Debian, Ubuntu 26.04) | `tests/images/*/Dockerfile` | image digests (`@sha256:…`) | Docker, in `make test` |

On the systems they manage, the playbooks install packages only from Ubuntu's and Debian's own archives
(`apt`): `xorriso` on the Proxmox nodes; UFW, Fail2Ban, unattended-upgrades, qemu-guest-agent and a few
tools on the servers. The Ubuntu ISO is verified against Ubuntu's published SHA-256 checksum.

Version ranges, not exact pins, are used for a control node you set up yourself on purpose: there the
project is a set of playbooks you run, and you should get Ansible's and the collections' bug and security
fixes without waiting for a release. The ranges stop at the next major version, which may break the
playbooks. CI and the container image, which the project builds itself, install exact, hash-pinned sets
(`requirements-ci.txt`, `requirements-webui.txt`). The collections in the image are the newest in range at
build time; the image lists them in `/app/collections/VERSIONS.txt`.

The web UI has no JavaScript dependencies: its pages are rendered on the server and its one small script is
the project's own, so there is no front-end build and nothing bundled from npm.

## Tracking dependencies

- **Dependabot** proposes updates weekly for `requirements*.txt`, the GitHub Actions and the Docker base
  images (the `Dockerfile` and the test images),
  and security updates as soon as an advisory appears. Patch and minor updates are approved by
  `.github/workflows/dependabot-auto-merge.yml` and merge automatically once every required check passes,
  including the integration tests that run every playbook. Major updates, updates without a version
  level, and every Docker base-image update wait for the maintainer, who also tries runtime-package majors against a
  disposable VM.
- **Ansible collections** have no Dependabot support. The maintainer checks the collections' changelogs
  and security advisories at least monthly and before every release, and widens the ranges in
  `requirements.yml` when a new major version has been tested.
- **Every pull request** runs **dependency review**, which fails when the change adds or updates a
  dependency with a known vulnerability of moderate or higher severity, or with a license outside the
  allowlist.
- **GitHub's dependency graph** and Dependabot security alerts watch the default branch between changes.
- **osv-scanner** checks every requirements file on every pull request and push, including the lower bounds
  in `requirements.txt` and what they pull in (the `Vulnerability scan (OSV)` check), and
  `tests/check_floors.py` checks every lower bound itself against OSV in the same check. The lower bounds are
  kept above versions with known vulnerabilities, so `make deps` can't keep an affected copy.
- Every release carries an SBOM (SPDX) of the source tree, and the container image an SBOM of its own
  contents, see [verifying-releases.md](verifying-releases.md).
- **Every release image** is scanned with [Trivy](https://trivy.dev/) before it is pushed; a fixable
  vulnerability of high or critical severity in its operating system or Python packages stops the release.
  Each release build also installs the Debian security updates published since the base image.

## Policy for vulnerabilities in dependencies

Severities are the advisory's CVSS rating. "Fixed" means the vulnerable version is excluded from the
ranges on `main` (or the playbooks no longer use the affected feature) and, if a release is affected, a
patched release is published.

| Severity | Remediation threshold |
| --- | --- |
| Critical | within 7 days of the finding |
| High | within 14 days |
| Moderate | within 30 days, or with the next release if sooner |
| Low | with the next regular dependency update |

- A finding can be closed without a change only if the vulnerable code can't be reached by these playbooks
  (for example a module the project doesn't use). That decision is recorded in the Dependabot alert's
  dismissal with the reason, and in the release notes.
- If no fixed version exists, the maintainer mitigates (configuration, avoiding the affected module, or
  replacing the dependency) within the same threshold, or records why it isn't exploitable.
- **Malicious packages** are removed at once; a release that depended on one is withdrawn and announced.
- A vulnerability in the container image (its base image, Debian packages or Python packages) is fixed by
  a new image release within the same thresholds; until then, [web-ui.md](web-ui.md) says how to limit
  exposure (keep it on `127.0.0.1`).
- Vulnerabilities in what the playbooks install on servers (OpenSSH, UFW, Fail2Ban, the kernel) are fixed
  by Ubuntu and installed by the baseline's upgrade and by unattended-upgrades; the project changes its
  defaults only if a setting is needed to mitigate one.

## Policy for licenses

The project's own code is MIT. Python dependencies must use one of these licenses (SPDX identifiers):

- Permissive: `MIT`, `MIT-0`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `ISC`, `0BSD`, `Zlib`,
  `Unlicense`, `CC0-1.0`, `BlueOak-1.0.0`, `PSF-2.0`, `Python-2.0`;
- weak copyleft: `MPL-2.0`, `LGPL-2.1-only`, `LGPL-2.1-or-later`, `LGPL-3.0-only`, `LGPL-3.0-or-later`;
- `GPL-3.0-only` and `GPL-3.0-or-later` **for Ansible itself and the Ansible project's own tools
  (`ansible-lint`, `yamllint`) and collections only**: the project exists to be run by Ansible, which loads
  the playbooks as data; nothing from those packages is copied into this repository or its releases.

One package is listed by name because its license is misdetected: `typing-extensions` is under
`PSF-2.0`, but GitHub's license data reads the license history in its `LICENSE` file as GPL. It reaches CI
through the lint tools, and the image through FastAPI's dependencies (pydantic).

The container image also contains the Debian packages of its base image and `openssh-client` and `tzdata`
under their own (mostly free software) licenses, as any Debian-based image does; its SBOM lists them.

Any other license, a missing license, or a new GPL dependency other than those is a violation: the
dependency review check fails, and the dependency isn't added until the maintainer has decided, in the
pull request, to replace it or to extend this list (with the reason).

## Before a release

A release is only published when, at the time of the release:

- `main` has no open dependency finding past its remediation threshold;
- the required CI checks passed on the release commit (the release workflow runs them again on the tag);
- the latest collections allowed by `requirements.yml` and the latest ansible-core in range have been
  used for a run against a disposable VM;
- the release image passes the Trivy scan and the smoke test (the release workflow stops otherwise).

## Policy for findings from static analysis (SAST)

The project's own code is analysed on every pull request by ansible-lint (the `production` profile,
including rules such as `no-log-password`, `risky-file-permissions`, `risky-shell-pipe` and
`command-instead-of-shell`), yamllint, actionlint, ruff (with bandit's security rules) for the web UI,
hadolint for the Dockerfile, and CodeQL for the workflows and the Python code, as part of the required
checks.

| Finding | Threshold |
| --- | --- |
| ansible-lint, yamllint, actionlint, ruff or hadolint finding | blocks the merge (CI fails) |
| CodeQL alert of high or critical severity | blocks the merge (code scanning protection) |
| Any other CodeQL alert on a pull request | fixed before merging, or dismissed with a written reason |
| gitleaks finding | blocks the merge; a real secret is revoked at once, whatever happens to the commit |

A finding is suppressed only when it's a false positive or can't be exploited, and only where it occurs:
a `# noqa: <rule>` with a reason on that line, or a CodeQL dismissal with its reason. Alerts found on
`main` by the weekly scans follow the dependency thresholds above.

## Published vulnerabilities

Vulnerabilities in the project itself are published as
[GitHub security advisories](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/security/advisories),
with the affected and fixed versions, how to tell whether you're affected, and how to fix or mitigate, as
described in [SECURITY.md](../SECURITY.md).
