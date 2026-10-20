# Changelog

All notable changes to ubuntu-server-auto-setup are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Before 1.0.0, a minor version may include changes that need
steps when upgrading (for example a renamed variable); those are listed under "Upgrading" and in
[docs/upgrading.md](docs/upgrading.md).

## [Unreleased]

### Upgrading

- **ansible-core 2.20.7 or later is now required** (was 2.19 or 2.20). Older versions are affected by
  CVE-2026-11332, an argument injection in `ansible-galaxy role install`. Run `make deps` (or
  `pip install -r requirements.txt`) to upgrade.

### Security

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

- Fuzzing with Atheris (`make fuzz`, `.github/workflows/fuzz.yml`) of the user-data, authorized keys,
  ISO checksum, and sshd and Fail2Ban templates, on every pull request and weekly. See
  [docs/testing.md](docs/testing.md#fuzzing).

### Changed

- Dependabot's patch and minor updates are approved and merged automatically once every required check
  passes (`.github/workflows/dependabot-auto-merge.yml`); `main` now requires one approval and up-to-date
  branches.

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

[Unreleased]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.1.0
