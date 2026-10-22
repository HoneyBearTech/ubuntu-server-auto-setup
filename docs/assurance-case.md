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
> updates, as stated in [security.md](security.md). Run from the container image, the web UI lets only the
> person who set its password (or holds one of the passkeys they added) configure and start those runs, and
> never hands the secrets it holds to the browser or to another site.**

It rests on four arguments: the threat model is understood (1), every trust boundary is mediated (2),
the design follows secure design principles (3), and the implementation counters common weaknesses and
is continuously checked (4).

## 1. Threat model

**Assets**, most valuable first: root access to the servers and the Proxmox nodes (through the control
node's SSH keys and the admin user's sudo; with the optional node user, write access to the ISO storage
instead of root on the nodes); the Proxmox API token; the admin password (and its hash); the Vault password;
with the container image, the web UI's password, its passkeys (their public keys and counters, whose
integrity matters: a key added by someone else is a way in) and sessions, and the data volume (which holds
the key, the Vault and by default the Vault password); the integrity of the installed system (the
ISO, the installer configuration, the authorized keys); the availability of the servers (a lockout or a
wiped disk); the project's release artifacts, including the container image.

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
| A web page open in the operator's browser | Nothing | Drive the web UI through the browser: cross-site form posts (CSRF), DNS rebinding to reach `127.0.0.1`, framing the UI (clickjacking), injecting script through stored values (XSS). |
| A malicious guest (whoever controls a VM on the cluster) | Their own VM | Feed the web UI false or hostile data through the VM's guest agent: another VM's address or a made-up OS to mislead the operator, markup, control or text-direction characters to inject script or disguise text, huge or slow answers to stall the UI. |
| Someone who can reach the web UI's port | Nothing | Guess the password, set the first password before the operator does, ride or fix a session, replay or forge a passkey response, use a passkey response from another site (phishing), add a passkey of their own through an open session, inject Ansible templates or extra command-line options through form values, read files through crafted run IDs. |
| Another user or process on the Docker host | Their own account | Read the data volume or the container's memory. (Treated as trusted: see "Out of scope".) |

**Out of scope** (see [security.md](security.md#what-it-does-not-protect-against)): a compromised
control node (for the image: the Docker host, its administrators and anyone who can read the data
volume); someone who knows the web UI's password (while passkeys aren't required) or holds an unlocked device
with one of its passkeys, or controls the browser it is used from; root on the
Proxmox nodes; the GitHub account behind `github_user` being taken over;
an operator using the playbooks to do harm; vulnerabilities in Ubuntu, Proxmox VE, OpenSSH or Ansible
themselves; anything on the servers beyond the baseline.

## 2. Trust boundaries

| # | Boundary | What crosses it | How it is mediated |
| --- | --- | --- | --- |
| B1 | Control node → Proxmox API | VM create/start/config/status calls, the API token | HTTPS with certificate verification (`pve_validate_certs`, default `true`); a token with a narrow role and privilege separation ([installing.md](installing.md#2-create-the-proxmox-api-token)); token handling hidden from output (`pve_hide_secrets`, `roles/proxmox_vm/tasks/`). |
| B2 | Control node → Proxmox node (SSH as `pve_ssh_user`: root, or a node user set up by `prepare-node.yml`) | ISO builds, seed files with the password hash | SSH with host key checking (`ansible.cfg`: `host_key_checking = true`); seed directory `0700`, files `0600`; seed ISO and directory deleted after the install (`roles/proxmox_vm/tasks/provision.yml`). With a node user: no password, no groups, no sudo; write access through one ACL entry per ISO folder and its own `0700` work directory only; the folders recorded by root in a file the user can't change, since `pvesm` is root-only; every run checks the login user, the work directory's owner and type, and that the ISO folder is writable before it downloads anything (`roles/proxmox_node_user/`, `roles/ubuntu_release/tasks/iso_dir.yml`, `roles/installer_iso/tasks/main.yml`). |
| B3 | Proxmox node → `releases.ubuntu.com` | The Ubuntu ISO, `SHA256SUMS` and its signature `SHA256SUMS.gpg` | HTTPS; `gpgv` checks the signature on `SHA256SUMS` against Ubuntu's CD image signing key, shipped with the role and pinned by its full fingerprint (never fetched at run time); the checksum is then read from those verified bytes, the ISO is verified against it before use, and the run stops if the signature is missing or bad, is by another key, or the release isn't listed (`roles/installer_iso/tasks/main.yml`). |
| B4 | Control node → `github.com` | Public SSH keys | HTTPS; lines filtered to public-key formats; an empty result stops the run; written into the installer configuration (`roles/proxmox_vm/templates/user-data.j2`, `roles/common/tasks/ssh_keys.yml`). |
| B5 | Seed ISO → installer on the new VM | Hostname, network, admin user, password hash, keys | A local CD-ROM image attached only to that VM, only during the install; no network datasource. Values from the inventory and GitHub are written as data with `to_nice_yaml` where they are free text. |
| B6 | Control node → new VM (first SSH contact) | The VM's host key | Trust on first use: the old `known_hosts` entry for the IP is replaced with the scanned ed25519 key (`provision_manage_known_hosts`). Later port changes use the key read over the verified connection (`roles/ssh_hardening/tasks/main.yml`). |
| B7 | Internet / LAN → servers | SSH and whatever `ufw_allow` opens | UFW default deny with rate-limited SSH; sshd public-key only, no root, `AllowUsers` optional; Fail2Ban (`roles/firewall/`, `roles/ssh_hardening/`, `roles/fail2ban/`). |
| B8 | Contributors → `main` → releases | Code changes, release artifacts, the Docker Hub token | Protected `main` (pull request, required checks), DCO, lint and secret scanning, read-only workflow tokens, pinned actions, keyless signing and provenance (`.github/workflows/`). The Docker Hub token is a secret of the `release` environment, which only `v*` tags can deploy to, so pull requests and branches can't read it; release tags are protected from being moved or deleted. |
| B9 | Browser → web UI | Sign-in (a password or a passkey response), form values, the API token and admin password once, run requests | Published on `127.0.0.1` only; allowed host names (DNS rebinding) and HTTPS for any other name; argon2id password with a first-run setup code and throttling; optional passkeys (WebAuthn, py_webauthn): added only with the password again, a random challenge per session that is used once and expires in 5 minutes, the origin and RP ID checked against `USAS_PUBLIC_URL` (or `localhost`), never the `Host` header, user verification required, the signature counter checked, failures throttled with the password's, and "Require a passkey" refusing the password unchecked; an unreadable passkeys file fails closed; server-side sessions in an `HttpOnly`, `SameSite=Strict` cookie, renewed at sign-in; a CSRF token on every form plus `Origin`/`Sec-Fetch-Site` checks; CSP, no framing; secrets never sent back; sign-ins logged (`webui/usas_web/main.py`, `auth.py`, `passkeys.py`). |
| B10 | Web UI → inventory, Vault and `ansible-playbook` | Validated values, the run's options | Values checked against the roles' own allowlist patterns (`schema.py`, read from `roles/*/vars/main.yml`) and written with `!unsafe`; secrets only in the Vault; files `0600`; `ansible-playbook`, `ssh` and `ssh-keyscan` started with argument lists; `--limit` only from inventory names; run IDs from a fixed format (`store.py`, `runner.py`, `sshkeys.py`). |
| B12 | Guest agents (through the Proxmox API) → web UI | Each VM's addresses, OS release and host name, as the guest reports them | Read-only: `GET` calls with `VM.GuestAgent.Audit` (no command execution or file access; `VM.Monitor` on Proxmox VE 8 stays opt-in), over the verified API connection. VM IDs and node names are checked before they go into an API path. Answers are parsed by `webui/usas_web/vms.py`: IPv4 only, special-use ranges dropped, at most 16 addresses, strings stripped of control and direction characters and cut to 120 characters, host names checked against the roles' pattern. The data is display-only, labelled with its source, and shown as text by React. At most 4 agent calls at a time, 5 seconds each, 15 seconds per refresh, with caches shared by every session and negative caching of silent agents, all in a worker thread (`webui/usas_web/remote.py`). |
| B13 | Guest agent (through the Proxmox API) → provisioning and the baseline (`net_mode: dhcp`) | The address DHCP gave `net0`, as the guest reports it | Only for servers with `net_mode: dhcp` and no `ansible_host`. Read-only `GET` calls (`VM.GuestAgent.Audit`) over the verified API connection; only the interface with `net0`'s MAC address (read from the VM's configuration) counts, and only an address matching `provision_ipv4_pattern` that isn't link-local is used, as the SSH target only. SSH still checks the host key: right after the install, the VM's fresh key is trusted as in B6 (nothing but the installer has run in it yet); on later runs the address must answer with a key already in `known_hosts`, so a guest that reports another address can't make Ansible talk to an unknown host (`roles/proxmox_vm/tasks/dhcp_address.yml`). |
| B11 | Registry (Docker Hub, or the GHCR mirror) → the operator's Docker | The container image | Built by the release workflow from a digest-pinned base and hash-pinned packages, scanned and smoke-tested, signed keylessly with cosign, with SBOM and SLSA provenance; `start.ps1 -Verify` and [verifying-releases.md](verifying-releases.md) check the signature. |
| B12 | Servers → mail relay and webhook (optional, off by default) | Update reports and ban notices (host name, banned addresses), the relay password, the webhook URL's token | Only when the operator sets a relay or a URL. The relay password and URL come from the Vault and live on the server in root-only `0600` files; msmtp verifies the relay's certificate against the system's CAs (`tls_trust_file system`) unless `mail_relay_tls: "off"`; addresses, host, user and URL pass `\Z`-anchored allowlist patterns, fuzzed for injection (`roles/common/`, `roles/fail2ban/`, `tests/fuzz/`). |

### Attack surface

| Entry point | Reachable by | Main protections | Critical code |
| --- | --- | --- | --- |
| SSH on each server | anyone who can reach `ssh_port` | keys only, no root, 3 tries, UFW rate limit, Fail2Ban, optional source restriction (`ufw_ssh_sources`) | `roles/ssh_hardening/`, `roles/firewall/`, `roles/fail2ban/` |
| Ports opened by `ufw_allow` | as configured | default deny; each rule explicit, optionally limited to a source network | `roles/firewall/tasks/main.yml` |
| GitHub keys endpoint | whoever controls `github_user` | 2FA on the account (operator), key-format filter, empty-result guard | `roles/common/tasks/ssh_keys.yml`, `roles/proxmox_vm/tasks/provision.yml` |
| Ubuntu download | network path to `releases.ubuntu.com` | HTTPS, SHA-256 verification | `roles/installer_iso/tasks/main.yml` |
| Seed ISO in ISO storage | root on the node, the node user (if one is set up), Proxmox users with access to that storage | `0600`, short-lived, deleted after install | `roles/proxmox_vm/tasks/provision.yml` |
| The node user's SSH key (optional) | anything that can read the control node's or the web UI's key | no root, no sudo, no `/etc/pve`; write access to the listed ISO folders only, so a stolen key can replace installer ISOs but not take the node | `roles/proxmox_node_user/`, `playbooks/prepare-node.yml` |
| Proxmox API token | anything that can read the Vault or intercept the API | Vault encryption, TLS verification, least-privilege role | `inventory/vault.example.yml`, `roles/proxmox_vm/` |
| The release pipeline | the maintainer, GitHub Actions | protected `main`, pinned actions, keyless signing, provenance | `.github/workflows/release.yml` |
| The web UI's HTTP port | the local machine (documented); a network if the operator publishes it | sign-in (password or passkey), sessions, CSRF and Origin checks, host check, CSP, input validation | `webui/usas_web/main.py`, `auth.py`, `passkeys.py`, `schema.py` |
| Guest agents, through provisioning (`net_mode: dhcp`) | whoever controls that VM | read-only privilege, MAC and IPv4-pattern filter, host key checking | `roles/proxmox_vm/tasks/dhcp_address.yml` |
| Guest agents, through the VMs page | whoever controls a VM on the cluster | read-only privilege, parsing and limits in `vms.py`, display-only, bounded fan-out and timeouts | `webui/usas_web/vms.py`, `webui/usas_web/remote.py` |
| The data volume | the Docker host's administrators | file modes `0600`/`0700`, Vault encryption (optionally with the password outside the volume), non-root container user | `webui/usas_web/store.py`, `Dockerfile` |
| The container image | anyone pulling it | signed, SBOM, provenance, vulnerability scan before release | `Dockerfile`, `.github/workflows/release.yml` |

The threats, boundaries and countermeasures here are reviewed whenever a change adds an entry point or
touches one of these paths (the pull request says so), and at each release.

## 3. Secure design principles

The principles below are Saltzer and Schroeder's, plus the additional ones the OpenSSF badge lists.

- **Economy of mechanism.** Ubuntu's own installer, OpenSSH, UFW, Fail2Ban and unattended-upgrades, configured
  with small files; no agent, daemon or custom code on the servers. One role per concern.
- **Fail-safe defaults.** Provisioning skips existing VMs rather than reconciling them; the ISO step stops
  if the signature, the checksum or the release is missing; the key sync stops rather than writing an empty list;
  an unreadable passkeys file turns password sign-in off and accepts no passkey;
  `ssh_hardening` stops if `ssh_allow_users` would exclude the admin user; sshd configuration is validated
  before it is installed; certificate verification is on by default; secrets are hidden from output by
  default.
- **Complete mediation.** Every API call carries the token and is checked by Proxmox against the token's
  role; every SSH connection is authenticated by key and host key.
- **Open design.** The playbooks, this case and the threat model are public. Security relies on keys and
  the Vault, not on the secrecy of the design.
- **Separation of privilege.** VM management goes through the API with a token; ISO handling uses SSH on the
  node, as root or as a node user that can only write ISOs; server changes use the admin user with sudo.
  The token can't run commands on the node, and the node user can't change Proxmox's configuration.
- **Least privilege.** The API token's role lists only the privileges provisioning uses, with privilege
  separation; on the nodes, the optional node user (`playbooks/prepare-node.yml`) replaces root for every
  run after one setup run, with write access to the ISO folders through ACL entries and no sudo rule at all
  (root's privileges remain the default, so existing setups keep working); the token has no password-based
  login; workflows get read-only tokens and raise permissions per job only where needed.
- **Least common mechanism.** Each VM gets its own seed ISO and seed directory, removed after its install.
- **Psychological acceptability.** One command per task (`make provision`, `make baseline`, `make keys`),
  `--check --diff` works for the baseline, and failures say what to fix. The web UI lays out every option
  with its default and help text, shows errors next to the fields, and offers check mode and a limit on
  every run.
- **Limited attack surface.** Servers expose only SSH (and what you add); the installer needs no network
  service; nothing listens on the control node. The web UI, the one listener the project adds, is
  published on `127.0.0.1` only, has no API for other programs, and serves only its own script and
  stylesheet.
- **Input validation with allowlists.** Required variables are asserted before provisioning starts (a
  numeric `pve_vmid`, a static IP, a gateway, keys or a password). Values that end up in configuration
  files or shell commands are checked against allowlist patterns in each role's `vars/main.yml` (the admin
  user name and the node's SSH user, host names, IPv4 addresses, `ssh_allow_users`, `fail2ban_ignoreip`, Fail2Ban times, sysctl names
  and values, notification addresses, the mail relay's host and user, the webhook URL), anchored
  with `\Z` so a trailing line break can't slip through. Keys are split on every line break and filtered to
  known public-key prefixes. The installer configuration is built as data and written with `to_nice_yaml`.
  The [fuzz targets](testing.md#fuzzing) check that no accepted value can change a file's structure. The
  web UI applies the same patterns, read from the same files, before it writes anything.

## 4. Countering common weaknesses

Mapped to the [CWE Top 25](https://cwe.mitre.org/top25/) and the weaknesses that matter for automation of
this kind:

| Weakness | Countermeasures |
| --- | --- |
| **Hard-coded or committed credentials** (CWE-798, CWE-540) | Secrets only in a gitignored, encrypted Vault; examples hold placeholders; gitleaks scans every change and the full history; GitHub secret scanning and push protection. |
| **Cleartext storage of sensitive information** (CWE-312, CWE-532) | The password reaches the node only as a SHA-512 crypt hash, in root-only files that are deleted after the install; `no_log` on tasks that handle the token or the hash; ansible-lint's `no-log-password` rule. |
| **Cleartext transmission** (CWE-319) and **improper certificate validation** (CWE-295) | HTTPS for the API, GitHub and Ubuntu downloads, with certificate verification on by default. |
| **Download of code without integrity check** (CWE-494) | The ISO is verified against Ubuntu's SHA-256 before use, taken from a `SHA256SUMS` whose signature by Ubuntu's pinned CD image signing key has been checked; collections and packages come from Galaxy and PyPI over HTTPS (see [dependencies.md](dependencies.md)). |
| **OS command injection** (CWE-78) and **improper neutralization in templates** (CWE-1336) | Commands run with argument lists (`ansible.builtin.command` with `argv`), never through a shell; the installer's YAML is built as data and written with `to_nice_yaml`; key lines are filtered to public-key formats. Values that reach a configuration file or a shell command are validated against allowlist patterns, and Atheris fuzz targets check the generated user-data, sshd and Fail2Ban files on every change. |
| **Incorrect default permissions** (CWE-276) | Explicit file modes everywhere (`0600` seed files, `0440` sudoers validated with `visudo`, `0644` configuration); ansible-lint's `risky-file-permissions` rule. |
| **Improper restriction of authentication attempts** (CWE-307) | SSH key-only, `MaxAuthTries 4`, UFW rate limit and Fail2Ban with incremental bans. |
| **Improper privilege management** (CWE-269) | Least-privilege API token with privilege separation; no root SSH on servers. Passwordless sudo for the admin user is a documented default that can be turned off. |
| **Use of components with known vulnerabilities** (CWE-1104) | Dependabot for the Python and npm packages and Actions; dependency review and osv-scanner on pull requests; the baseline upgrades packages and turns on unattended security upgrades on every server. |
| **Weak password hashing** (CWE-916) | SHA-512 crypt with a random salt (key-stretched) for the admin password; SSH doesn't accept passwords at all. |
| **Destructive operations without confirmation** (lockouts, wiped disks) | Existing VMs are skipped; the firewall opens SSH before enabling; a new SSH port is verified before the old one closes; the warning in the README and docs; `LIMIT=` and `--check --diff` for trying changes on one host. |
| **CI/CD weaknesses** (untrusted input in workflows, excessive token permissions) | `pull_request` only (no `pull_request_target`), read-only top-level permissions, actions pinned by commit SHA, untrusted event data only through `env:`; CodeQL analyses the workflows and actionlint lints them. |
| **Cross-site request forgery** (CWE-352) and **DNS rebinding** (CWE-350) | A per-session CSRF token in the `X-CSRF-Token` header of every post (a header another site's page can't set without a CORS permission the server never gives); requests whose `Origin` or `Sec-Fetch-Site` names another site are refused; `SameSite=Strict` cookies; only the configured host names are served (`webui/usas_web/main.py`). |
| **Cross-site scripting** (CWE-79) and **clickjacking** (CWE-1021) | React escapes every value it shows (the app sets no raw HTML) and the page shell is a Jinja template with auto-escaping; a Content Security Policy with `script-src 'self'`, `style-src 'self'` and `font-src 'self'`, no inline script or style (tests check the shell and the built files); `frame-ancestors 'none'` and `X-Frame-Options: DENY`. |
| **Template injection into Ansible** (CWE-1336) | Every value the UI writes into the inventory or the Vault is tagged `!unsafe`, so Ansible never evaluates it (`webui/usas_web/store.py`; a unit test runs Ansible on such values). |
| **Argument injection** (CWE-88) and **path traversal** (CWE-22) | `--limit=` takes only inventory host and group names; host names for `ssh` and `ssh-keyscan` are checked and passed after `--`; run IDs must match a fixed format before any file is opened. |
| **Authentication weaknesses** (CWE-307, CWE-384, CWE-1390) | Failed sign-ins slowed down exponentially, with passkey failures counted alongside wrong passwords; a new session ID at sign-in; the first password only with the setup code from the container's log; at least 12 characters; argon2id hashing. Optional passkeys can replace the password entirely ("Require a passkey"), which removes guessing. |
| **Authentication bypass by capture-replay** (CWE-294) and **by spoofing** (CWE-290, CWE-346) | Passkey challenges are 32 random bytes, kept with the session that asked for them, removed on first use whatever the outcome, and valid for 5 minutes; a response's origin must be the configured one exactly and its RP ID hash must match, so a response made on another site can't be used here; the signature is checked against the public key saved at registration, the signature counter must go up when the authenticator keeps one, the user handle must match, and user verification is required (`webui/usas_web/passkeys.py`; `webui/tests/test_passkeys.py` tests each with a software authenticator). |
| **Improper input validation** (CWE-20) and **insufficient verification of data authenticity** (CWE-345) | What guest agents report is parsed defensively (`webui/usas_web/vms.py`): only usable IPv4 addresses, length and count limits, control and direction characters removed, VM IDs and node names checked before they reach an API path. The guest's claims are never trusted to decide anything: the OS classification is labelled as reported by the guest, every address shows its source (agent, inventory or cloud-init), and nothing on the VMs page starts a run. Unit tests and the image smoke test feed it hostile strings. |
| **Allocation of resources without limits** (CWE-770) | The VMs page's API calls run in a pool of 4 worker threads, never on the event loop that streams the run logs, with a 10-second timeout per API call, 5 seconds per agent call and a 15-second budget per refresh; answers are cached for every session, silent agents for 2 minutes; the app polls every 30 seconds only while its tab is visible and backs off to 2 minutes after an error. |
| **Open redirect** (CWE-601) | The sign-in page returns only to the UI's own pages. |
| **Sensitive data in responses or logs** (CWE-200, CWE-532) | Saved secrets are never sent back (the API says only whether one is saved); run logs mask the Vault's values; pages and API answers are `Cache-Control: no-store`. |
| **Memory-safety weaknesses** (CWE-787, CWE-125, CWE-416) | Not applicable: the project is YAML and Jinja run by Ansible (Python), the web UI's server is Python and its app TypeScript run by the browser, all memory-safe runtimes. |
| **Supply chain of the app's npm packages** (CWE-1357, CWE-506) | Exact versions with integrity hashes in `package-lock.json`, `npm ci` with install scripts off, versions at least two weeks old when added; only the built files reach the image; OSV, dependency review and Dependabot watch the lock file ([dependencies.md](dependencies.md)). |

## 5. Verification evidence

- **Static analysis on every pull request**: yamllint, ansible-lint with the `production` profile (which
  includes its security-relevant rules), a syntax check of every playbook, actionlint, ruff (with its
  bandit security rules) for the web UI's server, TypeScript's strict check for its app, hadolint for the
  Dockerfile, and CodeQL for the workflows, the Python code and the app's TypeScript. Any finding fails the check (`.github/workflows/checks.yml`, `.github/workflows/codeql.yml`).
- **Web UI tests on every pull request**: unit tests (at least 80% statement coverage) of sign-in,
  passkeys (registration, sign-in and every refusal: another origin or RP ID, a replayed or expired
  challenge, a counter going back, no user verification) with a software authenticator, sessions, the CSRF
  header and Origin checks, the host check, every form's API and its validation against
  the roles' patterns, the inventory and Vault files Ansible reads back, runs and their logs; the app's
  tests of every page against a stand-in API, with axe-core, structural accessibility checks and the
  contrast of every colour pair; browser tests in Chromium of the whole setup by keyboard only, focus on
  every page and axe-core with real layout in every theme, and adding a passkey and signing in with it
through Chromium's virtual authenticator; and a smoke test that builds the image and drives
  the UI against the mock Proxmox API through a real `ansible-playbook` run and the VMs page with fixture
  guests (a slow agent, a forbidden one, hostile strings) ([testing.md](testing.md#the-web-ui)). Release images
  are also scanned with Trivy and fail on fixable high or critical vulnerabilities.
- **Secret scanning**: gitleaks over the whole history on every change, plus GitHub secret scanning with
  push protection.
- **Dependency checks**: dependency review on pull requests and Dependabot alerts on `main`
  ([dependencies.md](dependencies.md)).
- **Fuzzing on every pull request** (and ten minutes per target weekly): Atheris targets render the real
  templates and Jinja expressions from the roles with arbitrary inputs and check that nothing an accepted
  value contains can add or change a setting ([testing.md](testing.md#fuzzing)). They found two bugs
  before release, both fixed with regression tests: validation patterns anchored with `$` accepted a
  trailing line break, and characters outside Unicode's basic plane didn't survive JSON-encoding into the
  installer configuration.
- **Integration tests on every pull request**: every playbook runs against containers and a mock Proxmox
  API, and the results are checked, including what must be refused (password and root logins, an empty key
  list) and the cleanup of a failed install; every playbook must change nothing on a second run, and at
  least 80% of role tasks must run ([testing.md](testing.md)). A weekly job remasters the real Ubuntu ISO.
- **Manual end-to-end runs** for what containers can't show (a real install, reboots), as the
  [test policy](../CONTRIBUTING.md#test-policy) requires.
- **Runtime checks inside the playbooks**: assertions on required variables, on the signature on Ubuntu's
  `SHA256SUMS` and the ISO checksum, on the
  remastered GRUB configuration, on a non-empty key list and on `ssh_allow_users`; `sshd -t` and `visudo`
  validation before files are installed.
- **OpenSSF Scorecard** weekly, and the [OpenSSF Best Practices entry](https://www.bestpractices.dev/projects/15740).
