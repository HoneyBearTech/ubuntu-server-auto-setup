# Changelog

All notable changes to ubuntu-server-auto-setup are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Before 1.0.0, a minor version may include changes that need
steps when upgrading (for example a renamed variable); those are listed under "Upgrading" and in
[docs/upgrading.md](docs/upgrading.md).

## [Unreleased]

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

[Unreleased]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.2.0
[0.1.0]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.1.0
