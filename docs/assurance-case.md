# Assurance case

This document argues that ubuntu-server-auto-setup meets its [security requirements](security.md). It
describes the threat model, identifies the trust boundaries, shows how secure design principles were
applied, and lists how common weaknesses are countered, with pointers to the code that backs each claim.
The component overview is in [architecture.md](architecture.md).

The top-level claim is:

> **Run as documented, the playbooks install only the VMs the inventory asks for, without touching
> existing ones; they never disclose the Vault's secrets beyond the systems that need them (the Proxmox API
> for the token, the new server's installer for the password hash); and every server they harden ends up
> reachable only with the authorized SSH keys, behind a default-deny firewall, with automatic security
> updates, as stated in [security.md](security.md).**

It rests on four arguments: the threat model is understood (1), every trust boundary is mediated (2),
the design follows secure design principles (3), and the implementation counters common weaknesses and
is continuously checked (4).

## 1. Threat model

**Assets**, most valuable first: root access to the servers and the Proxmox nodes (through the control
node's SSH keys and the admin user's sudo); the Proxmox API token; the admin password (and its hash); the
Vault password; the integrity of the installed system (the ISO, the installer configuration, the
authorized keys); the availability of the servers (a lockout or a wiped disk); the project's release
artifacts.

**Actors and what they may try:**

| Actor | Trusted with | Threats considered |
| --- | --- | --- |
| The operator (you) | Everything the inventory reaches | Mistakes: a wrong VM ID, a wrong SSH port or firewall rule, a forgotten management network. Out of scope as an attacker. |
| Network attacker (between the control node or Proxmox node and the internet) | Nothing | Tamper with the Ubuntu ISO or the GitHub keys in transit, intercept the API token. |
| Attacker on the VMs' network | Nothing | Impersonate a new VM to get a host key trusted; brute-force SSH on the servers; read installer data from the network. |
| Internet attacker reaching a server | Nothing | Guess passwords, brute-force SSH, exploit exposed services, log in as root. |
| Someone controlling the GitHub account `github_user` | The account | Add their own key to every server at the next sync. (Treated as trusted: see "Out of scope".) |
| Another Proxmox user (non-root) | Their own Proxmox permissions | Read the seed ISO with the password hash from ISO storage. |
| Upstream supply chain | What we install | A tampered ISO, a malicious Ansible collection or Python package, a compromised GitHub Action. |
| Malicious contributor | Opening pull requests | Smuggle a backdoor into a role (an extra key, a weakened setting), exfiltrate CI credentials. |

**Out of scope** (see [security.md](security.md#what-it-does-not-protect-against)): a compromised
control node; root on the Proxmox nodes; the GitHub account behind `github_user` being taken over;
an operator using the playbooks to do harm; vulnerabilities in Ubuntu, Proxmox VE, OpenSSH or Ansible
themselves; anything on the servers beyond the baseline.

## 2. Trust boundaries

| # | Boundary | What crosses it | How it is mediated |
| --- | --- | --- | --- |
| B1 | Control node → Proxmox API | VM create/start/config/status calls, the API token | HTTPS with certificate verification (`pve_validate_certs`, default `true`); a token with a narrow role and privilege separation ([installing.md](installing.md#2-create-the-proxmox-api-token)); token handling hidden from output (`pve_hide_secrets`, `roles/proxmox_vm/tasks/`). |
| B2 | Control node → Proxmox node (SSH as root) | ISO builds, seed files with the password hash | SSH with host key checking (`ansible.cfg`: `host_key_checking = true`); seed directory `0700`, files `0600`; seed ISO and directory deleted after the install (`roles/proxmox_vm/tasks/provision.yml`). |
| B3 | Proxmox node → `releases.ubuntu.com` | The Ubuntu ISO and `SHA256SUMS` | HTTPS; the ISO is verified against the published SHA-256 before use, and the run stops if the release isn't listed (`roles/installer_iso/tasks/main.yml`). |
| B4 | Control node → `github.com` | Public SSH keys | HTTPS; lines filtered to public-key formats; an empty result stops the run; JSON-encoded into the installer configuration (`roles/proxmox_vm/templates/user-data.j2`, `roles/common/tasks/ssh_keys.yml`). |
| B5 | Seed ISO → installer on the new VM | Hostname, network, admin user, password hash, keys | A local CD-ROM image attached only to that VM, only during the install; no network datasource. Values from the inventory and GitHub are JSON-encoded where they are free text. |
| B6 | Control node → new VM (first SSH contact) | The VM's host key | Trust on first use: the old `known_hosts` entry for the IP is replaced with the scanned ed25519 key (`provision_manage_known_hosts`). Later port changes use the key read over the verified connection (`roles/ssh_hardening/tasks/main.yml`). |
| B7 | Internet / LAN → servers | SSH and whatever `ufw_allow` opens | UFW default deny with rate-limited SSH; sshd public-key only, no root, `AllowUsers` optional; Fail2Ban (`roles/firewall/`, `roles/ssh_hardening/`, `roles/fail2ban/`). |
| B8 | Contributors → `main` → releases | Code changes, release artifacts | Protected `main` (pull request, required checks), DCO, lint and secret scanning, read-only workflow tokens, pinned actions, keyless signing and provenance (`.github/workflows/`). |

### Attack surface

| Entry point | Reachable by | Main protections | Critical code |
| --- | --- | --- | --- |
| SSH on each server | anyone who can reach `ssh_port` | keys only, no root, 3 tries, UFW rate limit, Fail2Ban, optional source restriction (`ufw_ssh_sources`) | `roles/ssh_hardening/`, `roles/firewall/`, `roles/fail2ban/` |
| Ports opened by `ufw_allow` | as configured | default deny; each rule explicit, optionally limited to a source network | `roles/firewall/tasks/main.yml` |
| GitHub keys endpoint | whoever controls `github_user` | 2FA on the account (operator), key-format filter, empty-result guard | `roles/common/tasks/ssh_keys.yml`, `roles/proxmox_vm/tasks/provision.yml` |
| Ubuntu download | network path to `releases.ubuntu.com` | HTTPS, SHA-256 verification | `roles/installer_iso/tasks/main.yml` |
| Seed ISO in ISO storage | root on the node, Proxmox users with access to that storage | `0600`, short-lived, deleted after install | `roles/proxmox_vm/tasks/provision.yml` |
| Proxmox API token | anything that can read the Vault or intercept the API | Vault encryption, TLS verification, least-privilege role | `inventory/vault.example.yml`, `roles/proxmox_vm/` |
| The release pipeline | the maintainer, GitHub Actions | protected `main`, pinned actions, keyless signing, provenance | `.github/workflows/release.yml` |

The threats, boundaries and countermeasures here are reviewed whenever a change adds an entry point or
touches one of these paths (the pull request says so), and at each release.

## 3. Secure design principles

The principles below are Saltzer and Schroeder's, plus the additional ones the OpenSSF badge lists.

- **Economy of mechanism.** Ubuntu's own installer, OpenSSH, UFW, Fail2Ban and unattended-upgrades, configured
  with small files; no agent, daemon or custom code on the servers. One role per concern.
- **Fail-safe defaults.** Provisioning skips existing VMs rather than reconciling them; the ISO step stops
  if the checksum or the release is missing; the key sync stops rather than writing an empty list;
  `ssh_hardening` stops if `ssh_allow_users` would exclude the admin user; sshd configuration is validated
  before it is installed; certificate verification is on by default; secrets are hidden from output by
  default.
- **Complete mediation.** Every API call carries the token and is checked by Proxmox against the token's
  role; every SSH connection is authenticated by key and host key.
- **Open design.** The playbooks, this case and the threat model are public. Security relies on keys and
  the Vault, not on the secrecy of the design.
- **Separation of privilege.** VM management goes through the API with a token; ISO handling uses SSH as
  root on the node; server changes use the admin user with sudo. The token can't run commands on the node.
- **Least privilege.** The API token's role lists only the privileges provisioning uses, with privilege
  separation; the token has no password-based login; workflows get read-only tokens and raise permissions
  per job only where needed.
- **Least common mechanism.** Each VM gets its own seed ISO and seed directory, removed after its install.
- **Psychological acceptability.** One command per task (`make provision`, `make baseline`, `make keys`),
  `--check --diff` works for the baseline, and failures say what to fix.
- **Limited attack surface.** Servers expose only SSH (and what you add); the installer needs no network
  service; nothing listens on the control node.
- **Input validation with allowlists.** Required variables are asserted before provisioning starts (a
  numeric `pve_vmid`, a static IP, a gateway, keys or a password); keys are filtered to known public-key
  prefixes; free-text values are JSON-encoded into the installer configuration.

## 4. Countering common weaknesses

Mapped to the [CWE Top 25](https://cwe.mitre.org/top25/) and the weaknesses that matter for automation of
this kind:

| Weakness | Countermeasures |
| --- | --- |
| **Hard-coded or committed credentials** (CWE-798, CWE-540) | Secrets only in a gitignored, encrypted Vault; examples hold placeholders; gitleaks scans every change and the full history; GitHub secret scanning and push protection. |
| **Cleartext storage of sensitive information** (CWE-312, CWE-532) | The password reaches the node only as a SHA-512 crypt hash, in root-only files that are deleted after the install; `no_log` on tasks that handle the token or the hash; ansible-lint's `no-log-password` rule. |
| **Cleartext transmission** (CWE-319) and **improper certificate validation** (CWE-295) | HTTPS for the API, GitHub and Ubuntu downloads, with certificate verification on by default. |
| **Download of code without integrity check** (CWE-494) | The ISO is verified against Ubuntu's SHA-256 before use; collections and packages come from Galaxy and PyPI over HTTPS (see [dependencies.md](dependencies.md)). |
| **OS command injection** (CWE-78) and **improper neutralization in templates** (CWE-1336) | Commands run with argument lists (`ansible.builtin.command` with `argv`), never through a shell; inventory and GitHub values are JSON-encoded into the installer's YAML; key lines are filtered to public-key formats. |
| **Incorrect default permissions** (CWE-276) | Explicit file modes everywhere (`0600` seed files, `0440` sudoers validated with `visudo`, `0644` configuration); ansible-lint's `risky-file-permissions` rule. |
| **Improper restriction of authentication attempts** (CWE-307) | SSH key-only, `MaxAuthTries 4`, UFW rate limit and Fail2Ban with incremental bans. |
| **Improper privilege management** (CWE-269) | Least-privilege API token with privilege separation; no root SSH on servers. Passwordless sudo for the admin user is a documented default that can be turned off. |
| **Use of components with known vulnerabilities** (CWE-1104) | Dependabot for the Python packages and Actions; dependency review on pull requests; the baseline upgrades packages and turns on unattended security upgrades on every server. |
| **Weak password hashing** (CWE-916) | SHA-512 crypt with a random salt (key-stretched) for the admin password; SSH doesn't accept passwords at all. |
| **Destructive operations without confirmation** (lockouts, wiped disks) | Existing VMs are skipped; the firewall opens SSH before enabling; a new SSH port is verified before the old one closes; the warning in the README and docs; `LIMIT=` and `--check --diff` for trying changes on one host. |
| **CI/CD weaknesses** (untrusted input in workflows, excessive token permissions) | `pull_request` only (no `pull_request_target`), read-only top-level permissions, actions pinned by commit SHA, untrusted event data only through `env:`; CodeQL analyses the workflows and actionlint lints them. |
| **Memory-safety weaknesses** (CWE-787, CWE-125, CWE-416) | Not applicable: the project is YAML and Jinja run by Ansible (Python), a memory-safe runtime. |

## 5. Verification evidence

- **Static analysis on every pull request**: yamllint, ansible-lint with the `production` profile (which
  includes its security-relevant rules), a syntax check of every playbook, actionlint, and CodeQL for the
  workflows. Any finding fails the check (`.github/workflows/checks.yml`, `.github/workflows/codeql.yml`).
- **Secret scanning**: gitleaks over the whole history on every change, plus GitHub secret scanning with
  push protection.
- **Dependency checks**: dependency review on pull requests and Dependabot alerts on `main`
  ([dependencies.md](dependencies.md)).
- **Integration tests on every pull request**: every playbook runs against containers and a mock Proxmox
  API, and the results are checked, including what must be refused (password and root logins, an empty key
  list) and the cleanup of a failed install; every playbook must change nothing on a second run, and at
  least 80% of role tasks must run ([testing.md](testing.md)). A weekly job remasters the real Ubuntu ISO.
- **Manual end-to-end runs** for what containers can't show (a real install, reboots), as the
  [test policy](../CONTRIBUTING.md#test-policy) requires.
- **Runtime checks inside the playbooks**: assertions on required variables, on the ISO checksum, on the
  remastered GRUB configuration, on a non-empty key list and on `ssh_allow_users`; `sshd -t` and `visudo`
  validation before files are installed.
- **OpenSSF Scorecard** weekly, and the [OpenSSF Best Practices entry](https://www.bestpractices.dev/projects/15740).
