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
| Control-node Python packages (ansible-core, proxmoxer, requests, passlib) | `requirements.txt` | version ranges (for example `ansible-core>=2.19,<2.21`) | `pip install -r requirements.txt` (`make deps`) |
| Lint tools (ansible-lint, yamllint) | `requirements-dev.txt` | minimum versions | `pip install -r requirements-dev.txt` (`make deps`) |
| Ansible collections (`community.proxmox`, `community.general`, `ansible.posix`) | `requirements.yml` | version ranges | `ansible-galaxy collection install -r requirements.yml` (`make deps`) |
| CI actions | `.github/workflows/*.yml` | full commit SHAs | GitHub Actions |
| Python packages in CI (all of the above, resolved) | `requirements-ci.txt`, compiled from `requirements-ci.in` | exact versions with SHA-256 hashes (`pip install --require-hashes`) | CI and `make test` |
| CI container images (gitleaks, actionlint) | `.github/workflows/checks.yml` | image digests (`@sha256:…`) | Docker |
| Integration-test base images (Debian, Ubuntu 26.04) | `tests/images/*/Dockerfile` | image digests (`@sha256:…`) | Docker, in `make test` |

On the systems they manage, the playbooks install packages only from Ubuntu's and Debian's own archives
(`apt`): `xorriso` on the Proxmox nodes; UFW, Fail2Ban, unattended-upgrades, qemu-guest-agent and a few
tools on the servers. The Ubuntu ISO is verified against Ubuntu's published SHA-256 checksum.

Version ranges, not exact pins, are used for the control node on purpose: the project is a set of
playbooks you run, not an application image, and you should get Ansible's and the collections' bug and
security fixes without waiting for a release. The ranges stop at the next major version, which may break
the playbooks. Hash-pinned lock files for CI are on the [roadmap](roadmap.md).

## Tracking dependencies

- **Dependabot** proposes updates weekly for `requirements*.txt` and the GitHub Actions, and security
  updates as soon as an advisory appears. Each update is merged by the maintainer after the required checks
  pass and, for runtime packages, a run against a disposable VM.
- **Ansible collections** have no Dependabot support. The maintainer checks the collections' changelogs
  and security advisories at least monthly and before every release, and widens the ranges in
  `requirements.yml` when a new major version has been tested.
- **Every pull request** runs **dependency review**, which fails when the change adds or updates a
  dependency with a known vulnerability of moderate or higher severity, or with a license outside the
  allowlist.
- **GitHub's dependency graph** and Dependabot security alerts watch the default branch between changes.
- Every release carries an SBOM (SPDX) of the source tree, see [verifying-releases.md](verifying-releases.md).

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
only as a dependency of the lint tools.

Any other license, a missing license, or a new GPL dependency other than those is a violation: the
dependency review check fails, and the dependency isn't added until the maintainer has decided, in the
pull request, to replace it or to extend this list (with the reason).

## Before a release

A release is only published when, at the time of the release:

- `main` has no open dependency finding past its remediation threshold;
- the required CI checks passed on the release commit (the release workflow runs them again on the tag);
- the latest collections allowed by `requirements.yml` and the latest ansible-core in range have been
  used for a run against a disposable VM.

## Policy for findings from static analysis (SAST)

The project's own code is analysed on every pull request by ansible-lint (the `production` profile,
including rules such as `no-log-password`, `risky-file-permissions`, `risky-shell-pipe` and
`command-instead-of-shell`), yamllint, actionlint, and CodeQL for the workflows, as part of the required
checks.

| Finding | Threshold |
| --- | --- |
| ansible-lint, yamllint or actionlint finding | blocks the merge (CI fails) |
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
