# Security requirements

This page says what you can and cannot expect from ubuntu-server-auto-setup in terms of security. The
reasoning behind it (threat model, trust boundaries, design principles and how common weaknesses are
countered) is in the [assurance case](assurance-case.md). To report a vulnerability, see
[SECURITY.md](../SECURITY.md).

## Intended use

The playbooks are meant to be run by an administrator, from a control node they trust, against Proxmox VE
nodes and Ubuntu servers they own, on a private network. The control node holds the inventory, the Vault
and the SSH keys that reach every server and the Proxmox nodes as root, so it is as sensitive as those
systems. With the container image, the control node is the container: its web UI is meant for one
administrator on the same machine (`127.0.0.1`), and its data volume holds the same secrets. The rest of
this page assumes that use, with the defaults and the advice in the README's "Running it securely" section
and in [web-ui.md](web-ui.md#security).

## What the project is designed to guarantee

**Secrets**

- The admin password and the Proxmox API token are read from an Ansible Vault file that is gitignored, and
  never written to the repository. Tasks that handle the token or the password hash hide their arguments
  and results from the output (`no_log`).
- The admin password leaves the control node only as a salted SHA-512 crypt hash. On the Proxmox node the
  hash exists only in the per-host seed files (directory `0700`, files `0600`, root-owned) for the duration
  of provisioning; the seed ISO and its directory are deleted once the installer has finished.
- The API token is sent only to the Proxmox API, over HTTPS with certificate verification on by default
  (`pve_validate_certs: true`).

**Downloads**

- The Ubuntu ISO is downloaded over HTTPS and checked against the SHA-256 checksum Ubuntu publishes for it
  (also fetched over HTTPS); a mismatch stops the run.
- SSH public keys come from `https://github.com/<github_user>.keys` over HTTPS. Only lines that look like
  public keys are used, and they reach the installer configuration as data written with `to_nice_yaml`, so they can't add other
  configuration or commands.

**Provisioning**

- An existing VM is never modified, reinstalled or wiped: provisioning skips every `pve_vmid` that already
  exists anywhere in the cluster. Only the disk of the VM being created is erased.
- The installer reads its configuration from a local CD-ROM image; nothing is served on the network, and
  the new server doesn't fetch its configuration from anywhere.
- The installed server has password authentication over SSH off and only your keys authorized from its
  first boot.
- The Proxmox API token needs only the privileges listed in [installing.md](installing.md#2-create-the-proxmox-api-token)
  (no `Sys.*`, no user or permission management, no `VM.Console`), on a dedicated user with privilege
  separation.

**Baseline** (on every server in `ubuntu_servers`, whether this project installed it or not)

- SSH: no root login, no password or keyboard-interactive authentication (public key only), at most 3
  authentication attempts, a 30-second login grace time, unresponsive sessions dropped, X11 and agent forwarding
  off, optionally `AllowUsers` and a non-standard port. The configuration is validated with `sshd -t`
  before it is installed.
- The admin user's `authorized_keys` matches the keys on GitHub (plus `admin_extra_ssh_keys`) after every
  baseline run or `make keys`; a key removed from GitHub is removed from the servers. An empty or failed
  response from GitHub never empties the file: the run stops instead.
- UFW denies all incoming connections except SSH (rate-limited: more than 6 connections in 30 seconds from
  one address are refused) and the rules you list in `ufw_allow`.
- Fail2Ban bans addresses that fail SSH authentication 5 times in 10 minutes for an hour, longer for
  repeat offenders (up to a week), except those in `fail2ban_ignoreip`.
- Security updates (including Ubuntu Pro ESM security updates where enabled) are installed automatically
  every day by unattended-upgrades.
- The changes are made in an order that keeps the running SSH session working: the firewall allows the SSH
  port before it is enabled, and a new SSH port is opened and verified before port 22 is closed.

**The web UI** (the container image)

- Every page and action except the health check needs a sign-in. The password is stored only as an
  argon2id hash; the first one can only be set with a one-time code printed in the container's log; failed
  attempts are slowed down exponentially (up to 15 minutes between tries).
- Sessions are random, kept in memory, carried in an `HttpOnly`, `SameSite=Strict` cookie (`Secure` over
  HTTPS), renewed at sign-in and ended by sign-out, inactivity (2 hours), age (12 hours), a password
  change (for other sessions) and a restart.
- Every post needs the session's CSRF token in an `X-CSRF-Token` header, and requests that say they come
  from another site (`Origin`, `Sec-Fetch-Site`) are refused; the API answers no cross-origin requests.
  Only the configured host names are served, against DNS rebinding, and names other than `localhost` only
  over HTTPS. Pages can't be framed and load only the UI's own script, stylesheet and fonts, nothing inline
  (Content Security Policy). The app in the browser only shows what the server answers: every value is
  checked on the server.
- The API token and the admin password are stored only in the Ansible Vault file; once saved they are never
  sent back to the browser; run logs mask them. The SSH private key never leaves the data volume.
- Every value the UI writes into the inventory is validated against the roles' own allowlist patterns (or
  a stricter format of the UI's) and tagged `!unsafe`, so no value can become an Ansible template; commands
  run with argument lists, never a shell; `--limit` takes only the inventory's own host and group names.
- The Proxmox node's host key is trusted only after the user has compared its fingerprint, and checked on
  every connection; certificate verification for the API stays on, with the cluster's CA added to the
  trust store instead.
- The container runs as an unprivileged user and writes only to `/data` (and `/tmp`); the documented
  command publishes the port on `127.0.0.1` only.

**The project itself**

- Releases are signed keylessly with Sigstore cosign, with SHA-256 checksums, an SPDX SBOM and SLSA build
  provenance, and version tags are signed by the maintainer ([verifying-releases.md](verifying-releases.md)).
  The container image is scanned for fixable high and critical vulnerabilities and smoke-tested before it
  is pushed, and is signed with cosign, with an SBOM and SLSA provenance attached.
- Every change is linted (yamllint, ansible-lint `production` profile), syntax-checked and scanned for
  secrets (gitleaks over the full history) before it can merge; workflows run with read-only tokens and
  pinned actions. The only CI secret, the Docker Hub token that publishes the image, is available only to
  release runs for a `v*` tag (the `release` environment).

## What it does not protect against

- **A compromised control node.** It holds the Vault password (when you type it), the API token, your SSH
  private keys and the inventory. Anyone who controls it controls your servers. With the container image,
  that includes the Docker host and anyone who can read the data volume or run `docker exec`: the volume
  holds the SSH key and, unless you move it out with `USAS_VAULT_PASSWORD_FILE`, the Vault password.
- **The web UI's password, and the browser it is used from.** The UI has one password, no accounts and no
  second factor. Whoever knows it, or controls the browser or a browser extension while a session is
  open, can do everything the UI can. Published beyond `127.0.0.1`, the UI is exposed to password guessing
  (slowed down, not stopped) from wherever it can be reached; over plain HTTP its traffic, password
  included, could be read on the network, which is why it refuses other host names without HTTPS.
- **A compromised GitHub account.** Whoever controls `github_user` decides which keys can log in to your
  servers at the next provisioning, baseline run or `make keys`. Protect the account with two-factor
  authentication, or use `admin_extra_ssh_keys` and `ssh_keys_exclusive` to depend on it less.
- **Proxmox administrators and root on the Proxmox nodes.** They can read the seed ISO while a VM is being
  installed, read and change VM disks, and run commands inside the VMs through `qemu-guest-agent`.
- **Trust on first use of a new VM's host key.** The control node accepts the host key it scans when a new
  VM first boots. An attacker who can impersonate the VM's IP address on your network at that moment could
  get their key trusted. Provision on a network you control.
- **Leftovers of a killed run.** A failed provisioning run deletes the seed ISO in a `rescue` step. A run
  killed outright (Ctrl-C, control node lost) can still leave the seed ISO (with the password hash, root-only)
  in the node's ISO storage until you delete it ([installing.md](installing.md#troubleshooting)).
- **The installer's own log.** Ubuntu's installer keeps a root-only copy of its configuration, including
  the password hash, in `/var/log/installer/autoinstall-user-data`. The baseline's `common` role deletes it;
  until the baseline has run, it is on the server.
- **Passwordless sudo during provisioning.** The installer gives the admin user passwordless `sudo` so the
  first baseline run can work unattended; the baseline then removes it (`admin_sudo_nopasswd: false`, the
  default), so an SSH key alone doesn't give root afterwards. Until that first baseline run finishes, anyone
  with one of the authorized keys has root. Setting `admin_sudo_nopasswd: true` keeps passwordless `sudo`.
- **Your inventory.** The project does what the inventory says. A wrong `pve_vmid`, `ssh_port`,
  `ufw_allow`, `ufw_ssh_sources` or `fail2ban_ignoreip` can create the wrong VM, open a port you didn't
  mean to, or lock you out.
- **The servers' applications and wider hardening.** The baseline covers access (SSH, firewall, brute
  force) and updates. It isn't a CIS or STIG benchmark: it doesn't configure auditd, AppArmor profiles,
  disk encryption, kernel parameters, or anything your applications need.
- **Between install and baseline.** A freshly installed server has key-only SSH but no firewall or
  Fail2Ban until the baseline has run; `make site` runs it straight away.
- **Upstream components.** Vulnerabilities in Ubuntu, Proxmox VE, OpenSSH, Ansible or its collections
  are fixed upstream; how the project tracks them is in [dependencies.md](dependencies.md).
- **The checksum file's own authenticity beyond HTTPS.** The ISO is checked against `SHA256SUMS`, but the
  GPG signature on `SHA256SUMS` isn't verified yet (it's on the [roadmap](roadmap.md)); the check relies on
  the TLS connection to `releases.ubuntu.com`.
