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
| Web UI packages (FastAPI, uvicorn, python-multipart, argon2-cffi, webauthn) | `requirements-webui.in` | minimum versions | `make deps`; the image installs `requirements-webui.txt` |
| Python packages in the container image (the control-node and web UI packages, resolved) | `requirements-webui.txt`, compiled from `requirements-webui.in` | exact versions with SHA-256 hashes, wheels only | `pip install --require-hashes` in the `Dockerfile` |
| The web UI app's packages: React and the Atkinson Hyperlegible fonts in the app; Vite, TypeScript, Vitest, Testing Library, jsdom, axe-core, Playwright and `@axe-core/playwright` to build and test it | `webui/frontend/package.json` | exact versions, and every package (also indirect ones) with its version and SHA-512 integrity hash in `webui/frontend/package-lock.json` | `npm ci --ignore-scripts` (`make app`, CI, the image's build stage) |
| Container image bases (`node:24-slim` for the app's build stage, `python:3.14-slim`) and the Debian packages (`openssh-client`, `tzdata`) | `Dockerfile` | image digests; Debian packages at the security archive's current version | `docker build`, release workflow |
| Ansible collections (`community.proxmox`, `community.general`, `ansible.posix`) | `requirements.yml` | version ranges | `ansible-galaxy collection install -r requirements.yml` (`make deps`) |
| CI actions | `.github/workflows/*.yml` | full commit SHAs | GitHub Actions |
| Python packages in CI (all of the above, resolved) | `requirements-ci.txt`, compiled from `requirements-ci.in` | exact versions with SHA-256 hashes (`pip install --require-hashes`) | CI and `make test` |
| CI container images (gitleaks, actionlint, hadolint, Trivy) | `.github/workflows/checks.yml`, `release.yml` | image digests (`@sha256:…`) | Docker |
| Fuzzing engine (atheris) | `requirements-fuzz.in`, pinned in `requirements-fuzz.txt` | exact version with SHA-256 hashes | the fuzzing workflow and `make fuzz` |
| Chromium for the browser tests (Playwright's headless build) | the version of `@playwright/test` in `package-lock.json` | that Playwright release's browser build, downloaded over HTTPS from Playwright's servers (not hash-pinned by this repository); CI caches it by Playwright version | `npx playwright install --with-deps --only-shell chromium` (CI); `npx playwright install chromium` locally |
| Integration-test base images (Debian, Ubuntu 26.04) | `tests/images/*/Dockerfile` | image digests (`@sha256:…`) | Docker, in `make test`; the Kubernetes tests' Git server is a stage of the Ubuntu target image (`git-server`), with Git and Python from Ubuntu's archive |
| Operating system images (Ubuntu installer ISOs and cloud images, Debian, CentOS Stream, Rocky Linux and AlmaLinux cloud images) | `roles/os_catalog/vars/catalog.yml` (the OS catalog), built by `tools/update_os_catalog.py` | exact URL and checksum per image; the vendors' signatures checked against pinned fingerprints when the catalog is built (`tools/os-catalog-keys/`) | downloaded by the Proxmox node during a run; the weekly OS catalog workflow reports new builds and gone pins |
| k3s, kube-vip and kube-vip's cloud provider, on Kubernetes members | `roles/k3s/vars/catalog.yml` (the k3s catalog), built by `tools/update_k3s_catalog.py` | k3s' binary by exact URL and SHA-256 per architecture, from k3s' own `sha256sum-<arch>.txt`; the two images by digest (`@sha256:…`). Neither project signs what it publishes, so the pins rest on HTTPS and review; the weekly OS catalog workflow checks them again and opens an issue when a pin changes upstream or a newer release is out (Dependabot can't read the catalog) | the `k3s` role (`get_url` with the checksum; containerd, from k3s' own manifests directory) |
| Helm and Argo CD's chart (`argo-cd`), on a cluster's first server with `k8s_bootstrap_argocd` | `roles/k3s/vars/catalog.yml` (`k3s_helm`, `k3s_argocd_chart`), built by `tools/update_k3s_catalog.py` | Helm's archive per architecture and the chart by exact URL and SHA-256, each hash taken from a file signed upstream (Helm's maintainers' keys, Argo's chart repository key, in `tools/k3s-catalog-keys/`, fingerprints in the tool) and checked again weekly; Argo CD's images by the tags the chart names | the `k3s` role (`get_url` with the checksum; Helm installs the chart from the checked file) |
| The images k3s and Argo CD pull themselves: k3s' system components (CoreDNS, Traefik, metrics-server, local-path-provisioner, the pause image; `docker.io/rancher/...`) and Argo CD's (`quay.io`, `ghcr.io`, `ecr-public.aws.com`) | the pinned k3s release (its `k3s-images.txt`) and the pinned Argo CD chart | the tags that release and chart name, not digests: the release and the chart are pinned by SHA-256, the images they name by tag only, and none of them is signed upstream | containerd on the members, from k3s' own manifests and the chart |
| k3s' SELinux policy (`k3s-selinux`), on RHEL-family Kubernetes members | Rancher's stable repository (`rpm.rancher.io`), set up by the `kubernetes_node` role | Rancher's signing key, pinned by fingerprint; `gpgcheck=1` (the repository's metadata isn't signed) | dnf |
| EPEL (Extra Packages for Enterprise Linux), on RHEL-family servers | `epel-release` from the distribution's own repositories | EPEL's signing key, which `epel-release` installs; `gpgcheck` checked to stay on | dnf, for Fail2Ban, `fail2ban-firewalld` and msmtp (only when Fail2Ban or the mail relay is on) |
| Vendor signing keys (Ubuntu CD image and cloud image keys, Rocky Linux 9 and 10, AlmaLinux 9 and 10) | `roles/installer_iso/files/ubuntu-cdimage-keyring.gpg`, `tools/os-catalog-keys/*.asc` | full fingerprints in `tools/update_os_catalog.py` and `installer_signing_fingerprint` | `gpgv`, when the catalog is built (and at run time for an unpinned Ubuntu point release) |
| Helm's and Argo's chart repository's signing keys (Helm's `KEYS` file, `argoproj.github.io/argo-helm/pgp_keys.asc`) | `tools/k3s-catalog-keys/*.asc` | full fingerprints in `tools/update_k3s_catalog.py` | `gpgv`, when the k3s catalog is built and checked |

On the systems they manage, the playbooks install packages only from the distributions' own repositories
(`apt` or `dnf`, and EPEL on the RHEL family where the table says): `xorriso` and `gpgv` on the Proxmox nodes;
UFW or firewalld, Fail2Ban, unattended-upgrades or dnf-automatic, qemu-guest-agent and a few tools on the
servers; on Kubernetes members also Longhorn's prerequisites and, on the RHEL family, `k3s-selinux` from
Rancher. Besides packages, `playbooks/kubernetes.yml` installs only what the k3s catalog pins (above): the k3s
binary on the members, and Helm and Argo CD's chart on a cluster's first server with the handoff on. The Ubuntu ISO is verified against the checksum the OS catalog pins, which was taken
from Ubuntu's checksum file after `gpgv` checked Ubuntu's signature on it with the CD image signing key (pinned
by fingerprint; never fetched at run time). A point release the catalog doesn't pin is checked the same way at
run time, with the key that ships in `roles/installer_iso/files/`. These are runtime inputs on your own
infrastructure rather than dependencies of the project's code: the vendors' mirrors and signing keys are
trusted as [security.md](security.md) describes.

Version ranges, not exact pins, are used for a control node you set up yourself on purpose: there the
project is a set of playbooks you run, and you should get Ansible's and the collections' bug and security
fixes without waiting for a release. The ranges stop at the next major version, which may break the
playbooks. CI and the container image, which the project builds itself, install exact, hash-pinned sets
(`requirements-ci.txt`, `requirements-webui.txt`). The collections in the image are the newest in range at
build time; the image lists them in `/app/collections/VERSIONS.txt`. Each release's notes list the exact
ansible-core and collection versions the release's checks ran with (`scripts/tested-versions.sh`, run by
the release workflow).

The web UI's app is built from npm packages. `npm ci` installs exactly what `package-lock.json` lists and
refuses a package whose contents don't match its recorded hash; no package runs an install script
(`webui/frontend/.npmrc` sets `ignore-scripts`, and nothing in the tree needs one). Versions were chosen
at least two weeks after their release. Only the build output reaches the container image: one script
with React in it, one stylesheet and the font files, with the licences of React, its scheduler and the
fonts in `/usr/share/licenses/ubuntu-server-auto-setup/third-party/`. Node, npm and the development tools
stay in the image's build stage.

## Tracking dependencies

- **Dependabot** proposes updates weekly for `requirements*.txt`, the web UI app's npm packages (the
  development tools' minor and patch updates grouped in one pull request), the GitHub Actions and the
  Docker base images (the `Dockerfile`, including the Node build stage, and the test images),
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
- **osv-scanner** checks every requirements file and the app's `package-lock.json` on every pull request
  and push, including the lower bounds in `requirements.txt` and what they pull in (the
  `Vulnerability scan (OSV)` check), and
  `tests/check_floors.py` checks every lower bound itself against OSV in the same check. The lower bounds are
  kept above versions with known vulnerabilities, so `make deps` can't keep an affected copy. The npm
  packages have no ranges to check: every version is exact.
- **The k3s catalog** (k3s, kube-vip, its cloud provider, Helm and Argo CD's chart) has no Dependabot support
  either. The weekly "OS catalog" workflow runs `tools/update_k3s_catalog.py --check` and opens or updates one
  issue when a pin no longer matches upstream, a download is gone, or newer releases are out (k3s' newest
  patch of its two newest minor lines among them); the maintainer adds it with `make k3s-catalog` in a pull request. A new pin
  never upgrades a cluster: its `k8s_version` does, and Argo CD, once handed over, is upgraded from its GitOps
  repository. The maintainer reads k3s', kube-vip's, Helm's and Argo CD's security advisories before every
  release, and re-checks before each one whether Rancher has a stable `k3s-selinux` for EL10.
- Every release carries an SBOM (SPDX) of the source tree, and the container image an SBOM of its own
  contents, see [verifying-releases.md](verifying-releases.md).
- **Every release image** is scanned with [Trivy](https://trivy.dev/) before it is pushed; a fixable
  vulnerability of high or critical severity in its operating system or Python packages stops the release.
  The image holds no npm packages, only the app's built files; the npm packages are checked by OSV and
  dependency review instead.
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
- A vulnerability in k3s, kube-vip, its cloud provider, Helm or Argo CD's chart is fixed by adding the fixed
  release to the k3s catalog within the same thresholds and saying so in the release notes. Clusters move to
  it when their operator sets `k8s_version` and runs `make kubernetes-upgrade` (USAS never upgrades a cluster on
  its own); a pin with a known vulnerability stays in the catalog only while clusters may still run it, since
  removing it would refuse them, and the release notes name it. Argo CD after the handoff is the GitOps
  repository's to upgrade.

## Policy for licenses

The project's own code is MIT. Python and npm dependencies must use one of these licenses (SPDX
identifiers):

- Permissive: `MIT`, `MIT-0`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `ISC`, `0BSD`, `Zlib`,
  `Unlicense`, `CC0-1.0`, `BlueOak-1.0.0`, `PSF-2.0`, `Python-2.0`;
- weak copyleft: `MPL-2.0`, `LGPL-2.1-only`, `LGPL-2.1-or-later`, `LGPL-3.0-only`, `LGPL-3.0-or-later`;
- `OFL-1.1` **for fonts only** (the web UI's Atkinson Hyperlegible Next and Mono);
- `GPL-3.0-only` and `GPL-3.0-or-later` **for Ansible itself and the Ansible project's own tools
  (`ansible-lint`, `yamllint`) and collections only**: the project exists to be run by Ansible, which loads
  the playbooks as data; nothing from those packages is copied into this repository or its releases.

One package is listed by name because its license is misdetected: `typing-extensions` is under
`PSF-2.0`, but GitHub's license data reads the license history in its `LICENSE` file as GPL. It reaches CI
through the lint tools, and the image through FastAPI's dependencies (pydantic).

What the playbooks download onto Kubernetes members is under its upstreams' own licenses and isn't part of
the repository or its releases: k3s, kube-vip and its cloud provider, Helm, Argo CD and its chart, and
`k3s-selinux` are all `Apache-2.0`.

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
- the release image passes the Trivy scan and the smoke test (the release workflow stops otherwise);
- `make catalog-check` and `make k3s-catalog-check` pass: every pinned image, binary, chart and signature still
  matches upstream.

## Policy for findings from static analysis (SAST)

The project's own code is analysed on every pull request by ansible-lint (the `production` profile,
including rules such as `no-log-password`, `risky-file-permissions`, `risky-shell-pipe` and
`command-instead-of-shell`), yamllint, actionlint, ruff (with bandit's security rules) for the web UI's
server, TypeScript's strict type check for its app, hadolint for the Dockerfile, and CodeQL for the
workflows, the Python code and the app's TypeScript, as part of the required checks.

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
