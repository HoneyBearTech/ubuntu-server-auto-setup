# Changelog

All notable changes to ubuntu-server-auto-setup are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Before 1.0.0, a minor version may include changes that need
steps when upgrading (for example a renamed variable); those are listed under "Upgrading" and in
[docs/upgrading.md](docs/upgrading.md).

## [Unreleased]

### Added

- A pre-release checklist on real Proxmox VE (`docs/release-checklist.md`): what the maintainer checks on
  a real cluster before tagging a release, beyond what CI covers (the ISO in shared storage, a real install
  and power-off, idempotent reruns, key-only SSH, the firewall, Fail2Ban, sudo, the web UI and the
  published image's signatures), and what to record in the release pull request. The test policy and
  docs/testing.md link to it.
- The release notes list the exact ansible-core and collection versions (`community.proxmox`,
  `community.general`, `ansible.posix`) the release's checks ran with, made by `scripts/tested-versions.sh`
  in the release workflow's checks job.
- [docs/host-keys.md](docs/host-keys.md): how to pin the Proxmox node's SSH host key and the API's TLS
  certificate instead of trusting them on first use: comparing fingerprints with the node's console,
  `StrictHostKeyChecking=yes`, clustered nodes, the cluster CA with `pve_validate_certs: true`, and giving
  the web UI's container a `known_hosts` entry.

### Fixed

- [docs/installing.md](docs/installing.md) now says to set `REQUESTS_CA_BUNDLE` on Linux as well: in a
  virtual environment the Proxmox modules don't read the system's trust store.

### Fixed

- Keys in `admin_extra_ssh_keys`, including the web UI's own key, were left out of `authorized_keys` and the
  installer's configuration whenever `github_user` was set (since 0.2.0), and the baseline's exclusive key
  sync removed them. They are installed again.
- `--check --diff` works on a server the baseline hasn't run on yet. It stopped at the first package or
  service the run would install: the package lists are now refreshed in check mode too, and the UFW rules,
  Fail2Ban and other services that the run would install are skipped in check mode until they exist. A check
  run with a new `ssh_port` no longer tries to reconnect on a port sshd isn't using yet, and one with an
  `admin_user` that doesn't exist yet no longer fails on its `authorized_keys`.

## [0.3.0] - 2026-10-21

The web UI becomes USAS, a React app with a dark theme; passwordless sudo is removed after the first baseline
run; the UI's files are versioned by content and release, with a favicon; the README and the Docker Hub page
are branded; Dependabot groups the app's updates.

### Upgrading

- **Passwordless sudo is now removed by default.** `admin_sudo_nopasswd` defaults to `false`: the next
  baseline run removes the admin user's passwordless `sudo` (the installer grants it only so the first run
  can work unattended), and later runs use `ansible_become_password`. Keep that set to the admin password,
  as the example inventory and the web UI do, or set `admin_sudo_nopasswd: true` to keep the old behaviour.
  The baseline refuses to remove passwordless `sudo` if no password is set, so nobody is locked out of it.
- **The web UI needs JavaScript now.** Its page addresses, its port (8080) and its `/data` volume are the
  same, so upgrade as before (`start.ps1 -Update`, or `docker pull` and recreate the container with the same
  volume): the password, settings, inventory, Vault, SSH key and run history carry over. Anything that
  posted to the old pages' forms directly must post to `/api/<page>` with the CSRF token in an
  `X-CSRF-Token` header ([docs/web-ui.md](docs/web-ui.md#http-endpoints)).
- **Running the web UI from a checkout needs Node.js 24**: `make ui` builds the app first (`make app`). The
  container image needs nothing new.

### Added

- The web UI has an icon (a small shield with a server), at `/static/favicon.svg` and `/favicon.ico`.
- A new look for the web UI, under the name **USAS**: a sidebar with the pages in the order you need them
  (a top bar with a menu on phones, down to 375 pixels wide), the overview's steps as cards marked done or
  to do, status badges on runs, errors next to their fields and in a summary that links to them, and a run
  log with Ansible's status lines coloured, line numbers, a header that stays in view, and switches to
  follow the output and wrap long lines. It opens in a **dark theme**; a theme switch offers light and the
  system's choice, remembered for the browser (in the `usas_theme` cookie) and applied before the page is
  drawn. The fonts are Atkinson Hyperlegible Next and Mono (SIL Open Font License 1.1), served from the
  image, so the UI needs no internet access. The icon is redrawn as the USAS mark.
- The README opens with the USAS banner (dark and light versions), an overview diagram, screenshots of the
  web UI in both themes and a table of supported versions. Docker Hub gets its own shorter page,
  `docs/dockerhub.md`, with absolute image links it can show (the README's HTML images and its length don't
  suit Docker Hub); `tests/check_dockerhub.py` checks it in CI, and the release workflow publishes it with
  the next release.
- The web UI's app has its own tests in CI (part of the required "Web UI tests" check): every page with
  axe-core's WCAG 2.1 AA rules and structural accessibility checks, the WCAG contrast of every colour pair
  in both themes, and what each page shows and posts. CodeQL now also analyses its TypeScript.

### Changed

- The web UI is now a React app (TypeScript, built with Vite into static files in the image's build
  stage) on top of a JSON API in the same FastAPI server. The page addresses, the form field names, the
  checks of every value and the downloads and log stream stay the same; the forms post to `/api/<page>` and
  carry the CSRF token in an `X-CSRF-Token` header ([docs/web-ui.md](docs/web-ui.md#http-endpoints)). The
  UI now needs JavaScript. The app's npm packages are locked with integrity hashes, installed without
  install scripts and watched by Dependabot, OSV and dependency review; the runtime image holds only the
  built files (no Node or npm). The Content Security Policy allows the app's own fonts (`font-src 'self'`)
  and still nothing inline.
- Dependabot keeps the app's npm packages current: development tools come as one grouped pull request,
  `react` and `react-dom` (with their type packages) always move together, and the image's Node.js build
  stage follows long-term-support releases only.

### Fixed

- The control node needs Python 3.12 or later, as ansible-core 2.20 does; the README and the docs said 3.11.
  The README no longer says the ISOs always go to `/var/lib/vz/template/iso` (the folder is asked from
  Proxmox since 0.2.3).
- The web UI's stylesheet and script are versioned with a hash of their contents, so browsers no longer keep
  stale files after an upgrade (the URLs said `?v=0.2.0.dev0` in every release). The page footer shows the
  image's release version, which the release workflow sets from the tag.

## [0.2.3] - 2026-10-21

Fixes from the first run on a real Proxmox VE cluster: node names, API timeouts and shared ISO storage.

### Added

- The web UI's connection test lists every node in the cluster with its status (and the cluster's name),
  and the Proxmox node becomes a dropdown of those nodes. A node name that isn't in the cluster is refused,
  with a hint when only the capitalisation differs.
- Provisioning checks `pve_node` against the cluster's nodes before anything is created, and names the real
  nodes when it doesn't match (node names are case-sensitive), instead of failing later inside the API call
  that creates the VM.

### Fixed

- Creating a VM no longer fails with "Read timed out (read timeout=5)" while Proxmox allocates its disks:
  the Proxmox API calls now wait up to `pve_api_timeout` seconds (default 60; the Proxmox modules' own
  default is 5). This needs community.proxmox 1.6.0 or later (`requirements.yml`).
- An ISO storage other than `local` (such as an NFS share for the cluster) works: the ISOs used to be
  written to `local`'s folder while the VM's CD-ROMs pointed at the chosen storage. The storage's folder is
  now asked from Proxmox (`pvesm path`), after checking that the storage exists, is active and allows ISO
  images; `pve_iso_dir` defaults to empty for that and remains an override. The web UI's connection test
  lists the storages that allow ISO images as a dropdown.

## [0.2.2] - 2026-10-21

Publishes the changes listed under 0.2.1, with the image fix below.

### Security

- The container image no longer contains pip. Nothing installs packages at run time, and pip's vendored
  libraries in the Python 3.14 base image (certifi, msgpack, setuptools, urllib3) carried fixable advisories,
  including GHSA-6q48-6c7w-9gw2 (critical); the release's Trivy gate stopped 0.2.1 on them. The web UI's own
  copies of those libraries were already fixed versions.
- Pull requests now run the release's Trivy scan on the image too (in the Image smoke test check), so a
  vulnerable base image is caught before a release is cut.

## [0.2.1] - 2026-10-21

**Not published:** the release run stopped at the image vulnerability scan before pushing anything, and
the tag can't be moved, so these changes ship in 0.2.2. The container image moves to Python 3.14, and
dependency lower bounds skip more vulnerable versions.

### Security

- Lower bounds raised past versions with known vulnerabilities: `python-multipart>=0.0.31` (the web UI's
  form parser; GHSA-wp53-j4wj-2cfg, GHSA-mj87-hwqh-73pj, GHSA-pp6c-gr5w-3c5g, GHSA-5rvq-cxj2-64vf,
  GHSA-6jv3-5f52-599m, GHSA-vffw-93wf-4j4q, GHSA-v9pg-7xvm-68hf) and `pytest>=9.0.3` (CVE-2025-71176). The
  image and CI already used fixed versions (0.0.32 and 9.1.1).
- CI now also checks every lower bound in the requirements files against OSV (`tests/check_floors.py`, in
  the Vulnerability scan (OSV) check), not only the versions they resolve to.

### Changed

- The container image runs Python 3.14 (was 3.12), the newest Python that ansible-core 2.20 supports.
  Dependabot no longer proposes Python 3.15 or later for the image until ansible-core supports it.

### Fixed

- A failure to update the Docker Hub page no longer stops a release: the v0.2.0 run pushed and signed the
  image, then stopped before creating the GitHub Release because the Docker Hub token lacked Delete scope.

## [0.2.0] - 2026-10-20

The container image with a web UI, published to Docker Hub, plus fuzzing and stricter input validation.

### Upgrading

- **ansible-core 2.20.7 or later is now required** (was 2.19 or 2.20). Older versions are affected by
  CVE-2026-11332, an argument injection in `ansible-galaxy role install`. Run `make deps` (or
  `pip install -r requirements.txt`) to upgrade.

### Security

- `requirements.txt` requires certifi 2026.10.19 or later, which removes the emSign root certificates
  whose intermediate CA keys were compromised (GHSA-6q48-6c7w-9gw2, critical). CI, the release and the
  image already used that version.
- The minimum versions in `requirements.txt` now exclude releases with known vulnerabilities:
  ansible-core 2.20.7 (CVE-2026-11332), requests 2.34.2 (CVE-2024-47081, CVE-2026-25645) and idna 3.15
  (PYSEC-2026-215). CI already used fixed versions; this protects `make deps` installs into an environment
  that has older copies. CI now scans the requirement files with osv-scanner on every change.
- Values that reach a configuration file or a shell command are now validated against allowlist patterns
  before anything changes: the admin user name and host names (used in the installer's root shell
  commands and the sudoers file name), IPv4 addresses, `ssh_allow_users`, `ssh_permit_root_login`,
  `fail2ban_ignoreip`, and the Fail2Ban times and mode. Before, a line break in some of these could add
  directives to the sshd or Fail2Ban configuration, and a shell metacharacter in `admin_user` could run in
  the installer. Inventory values come from the operator, so this is defence in depth.
- The installer configuration is built as data and written with `to_nice_yaml`; characters outside
  Unicode's basic plane (such as an emoji in `admin_realname`) used to be mangled by JSON-encoding.
- `ubuntu_iso_name` is checked before use: it becomes a path on the Proxmox node, so slashes, spaces and
  line breaks are refused.
- An entry of `admin_extra_ssh_keys` that contains a line break is split into separate lines instead of
  being written as one entry.

### Added

- The image is published to Docker Hub as `honeybeartech/ubuntu-server-auto-setup` (the default in the
  README, `docker-compose.yml` and `start.ps1`) and mirrored to GHCR, signed and with provenance in both.
  The release workflow's Docker Hub token is held in a `release` environment that only version tags can
  use, and the README is synced to the Docker Hub page. README badges show the Docker Hub version, image
  size and pulls.
- A container image, `honeybeartech/ubuntu-server-auto-setup` on Docker Hub, mirrored to `ghcr.io/honeybeartech/ubuntu-server-auto-setup` (linux/amd64 and linux/arm64), with
  ansible-core, the collections, the playbooks and roles, and a **web UI**, so the project runs anywhere
  Docker does, including Windows with Docker Desktop: `docker run -d -p 127.0.0.1:8080:8080 -v
  usas-data:/data honeybeartech/ubuntu-server-auto-setup`, `docker-compose.yml`, or `start.ps1` on
  Windows. See [docs/web-ui.md](docs/web-ui.md).
- The web UI walks through the Proxmox API (with a connection test and the cluster's CA certificate), the
  UI's SSH key and the Proxmox node's host key (scanned, compared, then trusted), the defaults (with a
  preview of the GitHub keys), a servers table and the hardening options, and runs `build-installer`,
  `provision`, `baseline`, `site` and the key sync, with check mode, differences and a limit, a live log and
  a run history. Its fields, defaults and validation come from the roles' `defaults/main.yml` and
  `vars/main.yml`. It writes an ordinary inventory and an Ansible Vault file to the `/data` volume.
- The web UI needs a sign-in (one password, argon2id, set on first start with a setup code from the
  container's log), and has CSRF protection, `Origin` and host-name checks, a strict Content Security
  Policy, and secure session cookies over HTTPS. See [docs/security.md](docs/security.md) and the updated
  [assurance case](docs/assurance-case.md).
- Releases publish the image, scanned with Trivy and smoke-tested first, signed with cosign, with an SBOM
  and SLSA provenance; `images.txt` with its digest is in `SHA256SUMS`
  ([docs/verifying-releases.md](docs/verifying-releases.md)).
- CI: the web UI's unit tests (at least 80% statement coverage), a smoke test of the image against the mock
  Proxmox API with a real playbook run, ruff for the Python code and hadolint for the Dockerfile.
  `make webui-test`, `make image`, `make smoke` and `make ui`.
- [docs/accessibility.md](docs/accessibility.md): how the web UI is built and checked for WCAG 2.1 AA.
- Fuzzing with Atheris (`make fuzz`, `.github/workflows/fuzz.yml`) of the user-data, authorized keys,
  ISO checksum, and sshd and Fail2Ban templates, on every pull request and weekly. See
  [docs/testing.md](docs/testing.md#fuzzing).

### Changed

- Dependabot's Docker base-image updates are no longer approved automatically; they wait for the
  maintainer, like major updates.
- Dependabot's patch and minor updates are approved and merged automatically once every required check
  passes (`.github/workflows/dependabot-auto-merge.yml`); `main` now requires one approval and up-to-date
  branches.
- `tests/mock_pve.py` takes `--bind` so the smoke test can run it in a container.
- Dependabot also updates the `Dockerfile`'s base image.

## [0.1.0] - 2026-10-20

The first release of the Ansible version: the project is rewritten from an interactive bash script into
Ansible playbooks.

### Upgrading

- The bash script `setup.sh` (and `setupLibrary.sh`) for Ubuntu 14.04 to 20.04 has been **replaced** and is
  no longer maintained. It is still in the git history: the last commit that has it is
  [`f2e802d`](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/tree/f2e802d). Servers set up with
  it can be brought under the new baseline with `playbooks/baseline.yml` once they run Ubuntu 24.04 or
  26.04.

### Added

- `playbooks/build-installer.yml`: downloads the Ubuntu Server 26.04.1 ISO on the Proxmox node(s), verifies
  it against Ubuntu's `SHA256SUMS`, and remasters a copy that boots straight into a hands-off install.
- `playbooks/provision-vms.yml`: creates each missing VM through the Proxmox API (UEFI, q35, VirtIO SCSI)
  with a per-host seed ISO (static IP, admin user, SHA-512 password hash from Ansible Vault, GitHub SSH
  keys, password SSH off, `qemu-guest-agent`), waits for the install, removes the seed ISO and waits for
  SSH. Existing VMs are skipped.
- `playbooks/baseline.yml` for any Ubuntu 24.04 or 26.04 server: full upgrade, hostname and timezone, the
  admin user with `authorized_keys` kept in sync with GitHub, SSH hardening (no root login, no passwords,
  optional port change), UFW, Fail2Ban, unattended security upgrades and `qemu-guest-agent`.
- `playbooks/site.yml` to run all three, a `Makefile` (`deps`, `lint`, `syntax`, `site`,
  `build-installer`, `provision`, `baseline`, `keys`), and example inventory and Vault files.
- Documentation in `docs/`: quick start, installing (with the Proxmox API token's privileges), upgrading,
  architecture, security requirements, assurance case, interfaces, dependencies, roadmap and verifying
  releases; `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `GOVERNANCE.md` and `SUPPORT.md`.
- CI on GitHub Actions: yamllint, ansible-lint, a playbook syntax check and gitleaks on every change;
  CodeQL, OpenSSF Scorecard, dependency review and a DCO sign-off check; Dependabot for the Python packages
  and the Actions; a release workflow that publishes a source archive with SHA256 checksums, an SPDX SBOM,
  a cosign keyless signature and SLSA build provenance.
- Integration tests (`make test`, required on every pull request): every playbook runs against containers
  and a mock Proxmox API, the results are checked (including refused logins, key sync, a port change and
  the cleanup of a failed install), every playbook must change nothing on a second run, and at least 80%
  of role tasks must run. A weekly job remasters the real Ubuntu ISO and compares its boot records.
  See [docs/testing.md](docs/testing.md).
- CI installs an exact, hash-pinned set of Python packages (`requirements-ci.txt`).
- Variables `github_keys_url`, `vm_install_poll_interval`, `provision_known_hosts_file` and
  `ssh_known_hosts_file`; provisioning also works when the new VM is reached on an SSH port other than 22.

### Removed

- `setup.sh`, `setupLibrary.sh`, the bash test-library submodule (`.gitmodules`), and the CircleCI and old
  GitHub Actions configurations that tested them.

[Unreleased]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.3.0
[0.2.3]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.2.3
[0.2.2]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.2.2
[0.2.1]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.2.1
[0.2.0]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.2.0
[0.1.0]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.1.0
