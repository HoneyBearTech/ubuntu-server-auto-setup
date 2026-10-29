# Assurance case

This document argues that ubuntu-server-auto-setup meets its [security requirements](security.md). It
describes the threat model, identifies the trust boundaries, shows how secure design principles were
applied, and lists how common weaknesses are countered, with pointers to the code that backs each claim.
The component overview is in [architecture.md](architecture.md).

The top-level claim is:

> **Run as documented, the playbooks install only the VMs the inventory asks for, without touching
> existing ones; they never disclose the Vault's secrets beyond the systems that need them (the Proxmox API
> for the token, the new server's installer for the password hash and any disk passphrase); and every server they harden ends up
> reachable only with the authorized SSH keys, behind a default-deny firewall, with automatic security
> updates, as stated in [security.md](security.md). Run from the container image, the web UI lets only the
> person who set its password (or holds one of the passkeys they added) configure and start those runs, gives
> a dashboard that holds the status token they made only a read-only summary without names, addresses or
> secrets, and never hands the secrets it holds to the browser or to another site.**

It rests on four arguments: the threat model is understood (1), every trust boundary is mediated (2),
the design follows secure design principles (3), and the implementation counters common weaknesses and
is continuously checked (4).

## 1. Threat model

**Assets**, most valuable first: root access to the servers and the Proxmox nodes (through the control
node's SSH keys and the admin user's sudo; with the optional node user, write access to the ISO storage
instead of root on the nodes); the Proxmox API token; the admin password (and its hash); the Vault password;
with the container image, the web UI's password, its passkeys (their public keys and counters, whose
integrity matters: a key added by someone else is a way in) and sessions, the status token for dashboards
(read-only, but a way in to the summary), and the data volume (which holds
the key, the Vault and by default the Vault password); the integrity of the installed system (the
ISO, the installer configuration, the authorized keys, the signed boot chain); with disk encryption, the
data on a VM's disk and the passphrase that is its recovery key; the availability of the servers (a lockout or a
wiped disk); the project's release artifacts, including the container image.

**Actors and what they may try:**

| Actor | Trusted with | Threats considered |
| --- | --- | --- |
| The operator (you) | Everything the inventory reaches | Mistakes: a wrong VM ID, a wrong SSH port or firewall rule, a forgotten management network. Out of scope as an attacker. |
| Network attacker (between the control node or Proxmox node and the internet) | Nothing | Tamper with the Ubuntu ISO or the GitHub keys in transit, intercept the API token. |
| Attacker on the VMs' network | Nothing | Impersonate a new VM to get a host key trusted; brute-force SSH on the servers; read installer data from the network. |
| Internet attacker reaching a server | Nothing | Guess passwords, brute-force SSH, exploit exposed services, log in as root. |
| Someone controlling the GitHub account `github_user` | The account | Add their own key to every server at the next sync. (Treated as trusted: see "Out of scope".) |
| Another Proxmox user (non-root) | Their own Proxmox permissions | Read the seed ISO with the password hash (and any disk passphrase) from ISO storage; with `VM.Config.CDROM` and `VM.PowerMgmt` on an encrypted VM, boot it from other signed media to make its vTPM release the disk key. |
| Someone with a copy of a VM's disk | A disk image, a storage snapshot or replica, a decommissioned disk | Read the server's data offline, or change its boot chain to run their code at the next boot. |
| Upstream supply chain | What we install | A tampered ISO, a malicious Ansible collection or Python package, a compromised GitHub Action. |
| Malicious contributor | Opening pull requests | Smuggle a backdoor into a role (an extra key, a weakened setting), exfiltrate CI credentials. |
| A web page open in the operator's browser | Nothing | Drive the web UI through the browser: cross-site form posts (CSRF), DNS rebinding to reach `127.0.0.1`, framing the UI (clickjacking), injecting script through stored values (XSS). |
| A malicious guest (whoever controls a VM on the cluster) | Their own VM | Feed the web UI false or hostile data through the VM's guest agent: another VM's address or a made-up OS to mislead the operator, markup, control or text-direction characters to inject script or disguise text, huge or slow answers to stall the UI. |
| A server the updates playbook reaches that may be hostile (root on a server in `other_servers`, or a compromised one) | Its own system | Read the sudo password Ansible sends it; report false or oversized package and service data to inject into the report or a log, or to make the operator act on it; pretend to be another server; stall a run. |
| Someone who can reach the web UI's port | Nothing | Guess the password, set the first password before the operator does, ride or fix a session, replay or forge a passkey response, use a passkey response from another site (phishing), add a passkey of their own through an open session, guess
a status token or make one through an open session, inject Ansible templates or extra command-line options through form values, read files through crafted run IDs. |
| A dashboard server (such as Heimdall) and whoever can read its settings | The status token: reading the summary at `/api/status` | Use the token for more than the summary (a post, another route, a session), keep using it after it was replaced or revoked, flood the endpoint to load the UI or the Proxmox API, learn host names, addresses or secrets from the answer. Heimdall keeps the token in plain text in its database and shows it to anyone who can edit the tile, so its holders are more than the operator. |
| Another user or process on the Docker host | Their own account | Read the data volume or the container's memory. (Treated as trusted: see "Out of scope".) |

**Out of scope** (see [security.md](security.md#what-it-does-not-protect-against)): a compromised
control node (for the image: the Docker host, its administrators and anyone who can read the data
volume); someone who knows the web UI's password (while passkeys aren't required) or holds an unlocked device
with one of its passkeys, or controls the browser it is used from; root on the
Proxmox nodes, who can read a VM's vTPM state and memory, so disk encryption doesn't keep them out; Proxmox
backups that include a VM's TPM state; the GitHub account behind `github_user` being taken over;
an operator using the playbooks to do harm; vulnerabilities in Ubuntu, Proxmox VE, OpenSSH or Ansible
themselves; anything on the servers beyond the baseline.

## 2. Trust boundaries

| # | Boundary | What crosses it | How it is mediated |
| --- | --- | --- | --- |
| B1 | Control node → Proxmox API | VM create/start/config/status calls, the API token | HTTPS with certificate verification (`pve_validate_certs`, default `true`); a token with a narrow role and privilege separation ([installing.md](installing.md#2-create-the-proxmox-api-token)); token handling hidden from output (`pve_hide_secrets`, `roles/proxmox_vm/tasks/`). |
| B2 | Control node → Proxmox node (SSH as `pve_ssh_user`: root, or a node user set up by `prepare-node.yml`) | ISO builds, seed files with the password hash | SSH with host key checking (`ansible.cfg`: `host_key_checking = true`); seed directory `0700`, files `0600`; seed ISO and directory deleted after the install (`roles/proxmox_vm/tasks/provision.yml`). With a node user: no password, no groups, no sudo; write access through one ACL entry per ISO folder and its own `0700` work directory only; the folders recorded by root in a file the user can't change, since `pvesm` is root-only; every run checks the login user, the work directory's owner and type, and that the ISO folder is writable before it downloads anything (`roles/proxmox_node_user/`, `roles/os_catalog/tasks/iso_dir.yml`, `roles/installer_iso/tasks/main.yml`). |
| B3 | Proxmox node → `releases.ubuntu.com` | The Ubuntu ISO (and, only for a point release the OS catalog doesn't pin, `SHA256SUMS` and its signature `SHA256SUMS.gpg`) | HTTPS; the ISO must have the checksum the reviewed OS catalog pins (B16), and a copy already in storage is kept only if it has it. For an unpinned point release, `gpgv` checks the signature on `SHA256SUMS` against Ubuntu's CD image signing key, shipped with the role and pinned by its full fingerprint (never fetched at run time); the checksum is then read from those verified bytes, and the run stops if the signature is missing or bad, is by another key, or the release isn't listed (`roles/installer_iso/tasks/main.yml`, `storage.yml`). |
| B4 | Control node → `github.com` | Public SSH keys | HTTPS; lines filtered to public-key formats; an empty result stops the run; written into the installer configuration (`roles/proxmox_vm/templates/user-data.j2`, `roles/common/tasks/ssh_keys.yml`). |
| B5 | Seed ISO → installer on the new VM | Hostname, network, admin user, password hash, keys, and with disk encryption the passphrase | A local CD-ROM image attached only to that VM, only during the install; no network datasource. Values from the inventory and GitHub are written as data with `to_nice_yaml` where they are free text. The passphrase goes only to the installer's RAM (`/run`) and the storage layout; its pattern is checked into a true/false so no output holds it; the installer's copy on the new disk is deleted at the first boot (`roles/proxmox_vm/templates/user-data.j2`, `tasks/provision.yml`). |
| B6 | Control node → new VM (first SSH contact) | The VM's host key | Trust on first use: the old `known_hosts` entry for the IP is replaced with the scanned ed25519 key (`provision_manage_known_hosts`). Later port changes use the key read over the verified connection (`roles/ssh_hardening/tasks/main.yml`). |
| B7 | Internet / LAN → servers | SSH and whatever `ufw_allow` opens | UFW default deny with rate-limited SSH; sshd public-key only, no root, `AllowUsers` optional; Fail2Ban (`roles/firewall/`, `roles/ssh_hardening/`, `roles/fail2ban/`). |
| B8 | Contributors → `main` → releases | Code changes, release artifacts, the Docker Hub token | Protected `main` (pull request, required checks), DCO, lint and secret scanning, read-only workflow tokens, pinned actions, keyless signing and provenance (`.github/workflows/`). The Docker Hub token is a secret of the `release` environment, which only `v*` tags can deploy to, so pull requests and branches can't read it; release tags are protected from being moved or deleted. |
| B9 | Browser → web UI | Sign-in (a password or a passkey response), form values, the API token and admin password once, run requests | Published on `127.0.0.1` only; allowed host names (DNS rebinding) and HTTPS for any other name; argon2id password with a first-run setup code and throttling; optional passkeys (WebAuthn, py_webauthn): added only with the password again, a random challenge per session that is used once and expires in 5 minutes, the origin and RP ID checked against `USAS_PUBLIC_URL` (or `localhost`), never the `Host` header, user verification required, the signature counter checked, failures throttled with the password's, and "Require a passkey" refusing the password unchecked; an unreadable passkeys file fails closed; server-side sessions in an `HttpOnly`, `SameSite=Strict` cookie, renewed at sign-in; a CSRF token on every form plus `Origin`/`Sec-Fetch-Site` checks; CSP, no framing; secrets never sent back; sign-ins logged (`webui/usas_web/main.py`, `auth.py`, `passkeys.py`). |
| B10 | Web UI → inventory, Vault and `ansible-playbook` | Validated values, the run's options | Values checked against the roles' own allowlist patterns (`schema.py`, read from `roles/*/vars/main.yml`) and written with `!unsafe`; secrets only in the Vault (the admin password, the API token, the relay password, the webhook URL and the disk passphrase), never sent back to the browser and masked in run logs; files `0600`; `ansible-playbook`, `ssh` and `ssh-keyscan` started with argument lists; `--limit` only from inventory names; run IDs from a fixed format (`store.py`, `runner.py`, `sshkeys.py`). |
| B11 | Registry (Docker Hub, or the GHCR mirror) → the operator's Docker | The container image | Built by the release workflow from a digest-pinned base and hash-pinned packages, scanned and smoke-tested, signed keylessly with cosign, with SBOM and SLSA provenance; `start.ps1 -Verify` and [verifying-releases.md](verifying-releases.md) check the signature. |
| B12 | Guest agents (through the Proxmox API) → web UI | Each VM's addresses, OS release and host name, as the guest reports them | Read-only: `GET` calls with `VM.GuestAgent.Audit` (no command execution or file access; `VM.Monitor` on Proxmox VE 8 stays opt-in), over the verified API connection. VM IDs and node names are checked before they go into an API path. Answers are parsed by `plugins/module_utils/usas_vms.py` (loaded by `webui/usas_web/vms.py`): IPv4 only, special-use ranges dropped, at most 16 addresses, strings stripped of control and direction characters and cut to 120 characters, host names checked against the roles' pattern. The data is display-only, labelled with its source, and shown as text by React. At most 4 agent calls at a time, 5 seconds each, 15 seconds per refresh, with caches shared by every session and negative caching of silent agents, all in a worker thread (`webui/usas_web/remote.py`). |
| B13 | Guest agent (through the Proxmox API) → provisioning and the baseline (`net_mode: dhcp`) | The address DHCP gave `net0`, as the guest reports it | Only for servers with `net_mode: dhcp` and no `ansible_host`. Before such a VM is created, the token's own privileges on its path are read (`GET /access/permissions`), so a token that can't ask the agent is refused before anything is installed. Read-only `GET` calls (`VM.GuestAgent.Audit`) over the verified API connection; only the interface with `net0`'s MAC address (read from the VM's configuration) counts, and only an address matching `provision_ipv4_pattern` that isn't link-local is used, as the SSH target only. SSH still checks the host key: right after the install, the VM's fresh key is trusted as in B6 (nothing but the installer has run in it yet); on later runs the address must answer with a key already in `known_hosts`, so a guest that reports another address can't make Ansible talk to an unknown host (`roles/proxmox_vm/tasks/dhcp_address.yml`). |
| B14 | Firmware → boot chain → disk (each VM) | The VM's boot code; with disk encryption, the disk key | UEFI Secure Boot with Microsoft's keys pre-enrolled (`vm_secure_boot`, on by default): only Ubuntu's signed shim, GRUB and kernel run, and the kernel loads only signed modules; the remastered ISO keeps the signed EFI image and CI installs from it under Secure Boot (`tests/secure-boot-install.sh`). With `vm_disk_encryption`, the root file system is LUKS2 and a vTPM releases its key only while PCR 7 (the Secure Boot state and the certificates that verified the boot) matches the VM's first boot (the installer's TPM slot, bound to no PCR, is replaced by one bound to PCR 7 right after that boot, `roles/proxmox_vm/tasks/first_boot.yml`); the TPM state can live on other storage than the disks (`pve_tpmstate_storage`); a changed state stops at the passphrase prompt instead of booting. |
| B15 | Dashboard server (Heimdall) → the web UI's status API | `GET /api/status` with the status token; back, the summary (schema version, USAS version, setup progress, the running or last run's playbook and result, server and VM counts) | Off until a signed-in session that enters the password again makes a token; one token, 32 random bytes with the prefix `usas_st_`, shown once and kept only as an HMAC-SHA256 keyed from the Vault password through scrypt (`status-token.json`, `0600`); taken only from `Authorization: Bearer` (never the query string), parsed by a `\Z`-anchored pattern and compared in constant time; replacing it stops the old one at once, revoking turns the API off; an unreadable token file accepts no token. Wrong tokens are slowed down per client address in their own bucket (5 free, then doubling up to 15 minutes), right ones limited to 60 calls a minute; the route makes no session and sets no cookie, the token opens no other route and no route takes it, and posts still need a session's CSRF token. The same host check and HTTPS rule as B9. The answer is built from fixed fields only (no run ID, limit or log, no host, node, cluster or VM names, addresses, VM IDs or secrets); the cluster is asked at most once a minute (failures too), in a worker thread, with the VMs page's timeout. Creation, replacement, revocation and refused calls are logged, uses once an hour per address (`webui/usas_web/status.py`, `main.py`). |
| B16 | Vendor image indexes → the OS catalog (`make catalog` on the maintainer's machine, the OS catalog workflow in CI) | Directory listings, checksum files and their signatures from Ubuntu, Debian, CentOS, Rocky Linux and AlmaLinux | HTTPS from the vendors' own hosts only, redirects included; `gpgv` checks every signed checksum file against a key pinned by full fingerprint in `tools/update_os_catalog.py` (Ubuntu, Rocky, Alma); CentOS Stream and Debian cloud images publish no signature and are marked `signed: false`; every parsed value must match a strict pattern (file names, hex checksums, build serials; fuzzed by `tests/fuzz/fuzz_os_catalog.py`) before it's written; the result is reviewed in a pull request, where CI checks it again. No playbook run reads a vendor index (`tools/update_os_catalog.py`, `.github/workflows/catalog.yml`). |
| B17 | Servers, and the control node for the updates playbook → mail relay and webhook (optional, off by default) | Update reports and ban notices (host name, banned addresses), the updates run's summary, the relay password, the webhook URL's token | Only when the operator sets a relay or a URL. The control node sends the updates summary with `roles/updates/library/usas_mail.py` (STARTTLS or TLS with `ssl.create_default_context()`, so the relay's certificate and name are checked; headers built by `email.message`, which refuses line breaks) and posts the webhook with `uri` and `validate_certs`, its output hidden; the same patterns check the addresses, relay and URL (read from `roles/common` and `roles/fail2ban`). The relay password and URL come from the Vault and live on the server in root-only `0600` files; msmtp verifies the relay's certificate against the system's CAs (`tls_trust_file system`) unless `mail_relay_tls: "off"`; addresses, host, user and URL pass `\Z`-anchored allowlist patterns, fuzzed for injection (`roles/common/`, `roles/fail2ban/`, `tests/fuzz/`). |
| B18 | Control node → existing servers, over SSH with sudo (`playbooks/updates.yml`) | Package installs and reboots; the sudo password, if one is set | Only hosts in the inventory (`ubuntu_servers`, `other_servers`), with strict host key checking on every connection (no new key is trusted on its own). A server outside `ubuntu_servers` with a `pve_vmid` is refused before any connection if its sudo password is the Vault admin password, unless `updates_allow_shared_become_password` is set for it. Settings are checked against `\Z`-anchored patterns and value lists before anything runs; package names reach apt only as module arguments after passing the package pattern; signature checks stay on (`tests/updates.sh` fails on options that would turn them off); `safe` upgrades can't remove packages (`--no-remove`); reboots only with `updates_reboot: if_required`, held on TPM-unlocked disks after a Secure Boot update; a failed server or health check stops the servers not yet started (`roles/updates/tasks/`). |
| B19 | Existing servers → the updates report and log on the control node | Pending, held and upgraded packages, versions, suites, services, needrestart and apt configuration output, as each server reports them | Parsed by `roles/updates/filter_plugins/usas_updates.py` in the C locale: names and versions must pass the role's patterns or are dropped, text loses control and direction characters and is cut to length, each list stops at 5,000 entries; the data only describes and never decides what another server gets (what is installed is the operator's selection applied to checked names); the report is written `0600` in a `0700` folder. Fuzzed by `tests/fuzz/fuzz_update_reports.py`. |
| B20 | Proxmox API and guest agents → the updates playbook's cluster inventory (`usas_pve`) | The cluster's VMs (IDs, names, nodes, tags, pools, HA state, status, locks), their configs, and each guest's addresses and OS, as the guest reports them | Read-only `GET` calls with `VM.Audit` and `VM.GuestAgent.Audit` over the verified API connection (the inventory's own `pve_api_*` settings and `pve_validate_certs`), 4 agent calls at a time within 15 seconds; parsed by `plugins/module_utils/usas_vms.py`, the VMs page's parser. A VM is a static server only by its `pve_vmid`; every other VM is named `vm-<vmid>` and grouped under fixed prefixes, so no guest or VM owner can choose a name or group that picks up variables. Guest strings are never templated (`wrap_var`). The guest's address is used only with its host key pinned as `usas-<cluster>-<vmid>` (`HostKeyAlias`, strict checking, its own control path); an unpinned or mismatching key is reported, never trusted. VMs that are stopped, locked, containers, templates, Windows or tagged `usas-no-updates` are reported and never contacted. A source that can't be read stops the run (`any_unparsed_is_failed`). Fuzzed by `tests/fuzz/fuzz_cluster_inventory.py`. |

### Attack surface

| Entry point | Reachable by | Main protections | Critical code |
| --- | --- | --- | --- |
| SSH on each server | anyone who can reach `ssh_port` | keys only, no root, 3 tries, UFW rate limit, Fail2Ban, optional source restriction (`ufw_ssh_sources`) | `roles/ssh_hardening/`, `roles/firewall/`, `roles/fail2ban/` |
| Ports opened by `ufw_allow` | as configured | default deny; each rule explicit, optionally limited to a source network | `roles/firewall/tasks/main.yml` |
| GitHub keys endpoint | whoever controls `github_user` | 2FA on the account (operator), key-format filter, empty-result guard | `roles/common/tasks/ssh_keys.yml`, `roles/proxmox_vm/tasks/provision.yml` |
| Ubuntu download | network path to `releases.ubuntu.com` | HTTPS, the checksum pinned in the reviewed OS catalog (or a signed `SHA256SUMS` for an unpinned point release) | `roles/installer_iso/tasks/main.yml`, `roles/os_catalog/vars/catalog.yml` |
| Vendor indexes, when the catalog is built | the network path to the vendors, the vendors' sites and signing keys | HTTPS from the vendors' hosts only, `gpgv` against pinned fingerprints, strict parsing, pull-request review | `tools/update_os_catalog.py`, `tools/os-catalog-keys/` |
| Seed ISO in ISO storage | root on the node, the node user (if one is set up), Proxmox users with access to that storage | `0600`, short-lived, deleted after install | `roles/proxmox_vm/tasks/provision.yml` |
| The node user's SSH key (optional) | anything that can read the control node's or the web UI's key | no root, no sudo, no `/etc/pve`; write access to the listed ISO folders only, so a stolen key can replace installer ISOs but not take the node | `roles/proxmox_node_user/`, `playbooks/prepare-node.yml` |
| An encrypted VM's disk volume and vTPM state | the storage's administrators, backups, anyone who can attach media to the VM and start it | LUKS2; the key sealed to the vTPM under PCR 7; the passphrase only in the Vault and the short-lived seed; TPM state optionally on separate storage | `roles/proxmox_vm/templates/user-data.j2`, `roles/proxmox_vm/tasks/` |
| Proxmox API token | anything that can read the Vault or intercept the API | Vault encryption, TLS verification, least-privilege role | `inventory/vault.example.yml`, `roles/proxmox_vm/` |
| The release pipeline | the maintainer, GitHub Actions | protected `main`, pinned actions, keyless signing, provenance | `.github/workflows/release.yml` |
| The web UI's HTTP port | the local machine (documented); a network if the operator publishes it | sign-in (password or passkey), sessions, CSRF and Origin checks, host check, CSP, input validation | `webui/usas_web/main.py`, `auth.py`, `passkeys.py`, `schema.py` |
| The status API (`GET /api/status`, off by default) | a dashboard holding the token, anyone who can reach the port (to guess) | token shown once, HMAC at rest, Bearer header only, constant-time check, throttled failures, rate limit, no session, read-only fixed-field answer | `webui/usas_web/status.py`, `webui/usas_web/main.py` |
| Guest agents, through provisioning (`net_mode: dhcp`) | whoever controls that VM | read-only privilege, MAC and IPv4-pattern filter, host key checking | `roles/proxmox_vm/tasks/dhcp_address.yml` |
| Guest agents, through the VMs page | whoever controls a VM on the cluster | read-only privilege, parsing and limits in `usas_vms.py`, display-only, bounded fan-out and timeouts | `plugins/module_utils/usas_vms.py`, `webui/usas_web/remote.py` |
| The cluster inventory, from the Proxmox API and guest agents | whoever controls a VM on the cluster (its name, tags, agent answers) | read-only privileges, fixed names and group prefixes, values never templated, host key pinned by VM ID | `plugins/inventory/usas_pve.py`, `plugins/module_utils/usas_vms.py` |
| SSH with sudo to existing servers, by the updates playbook | the servers it reaches, including ones this project didn't install | inventory-only targets, strict host keys, no admin password for `other_servers`, pattern checks, signature checks kept, no removals in `safe`, reboots opt-in, stop after a failure | `playbooks/updates.yml`, `roles/updates/` |
| What servers report to the updates playbook | root on each of those servers | pattern checks, stripping, caps, display-only, `0600` report | `roles/updates/filter_plugins/usas_updates.py` |
| The data volume | the Docker host's administrators | file modes `0600`/`0700`, Vault encryption (optionally with the password outside the volume), non-root container user | `webui/usas_web/store.py`, `Dockerfile` |
| The container image | anyone pulling it | signed, SBOM, provenance, vulnerability scan before release | `Dockerfile`, `.github/workflows/release.yml` |

The threats, boundaries and countermeasures here are reviewed whenever a change adds an entry point or
touches one of these paths (the pull request says so), and at each release.

## 3. Secure design principles

The principles below are Saltzer and Schroeder's, plus the additional ones the OpenSSF badge lists.

- **Economy of mechanism.** Ubuntu's own installer, OpenSSH, UFW, Fail2Ban and unattended-upgrades, configured
  with small files; no agent, daemon or custom code on the servers. One role per concern.
- **Fail-safe defaults.** New VMs boot with Secure Boot on; an encrypted VM whose Secure Boot state
  changed stops at the passphrase prompt rather than unlocking; disk encryption without Secure Boot is
  refused. Provisioning skips existing VMs rather than reconciling them; the ISO step stops
  if the signature, the checksum or the release is missing; the key sync stops rather than writing an empty list;
  an unreadable passkeys file turns password sign-in off and accepts no passkey; the status API is off until
  a token is made, and an unreadable token file accepts no token;
  `ssh_hardening` stops if `ssh_allow_users` would exclude the admin user; the updates playbook reports
  before it changes anything (`--check`), never reboots unless asked, skips a server whose update needs a
  package left out, and starts no further server after a failure; sshd configuration is validated
  before it is installed; certificate verification is on by default; secrets are hidden from output by
  default.
- **Complete mediation.** Every API call carries the token and is checked by Proxmox against the token's
  role; every SSH connection is authenticated by key and host key; every web UI request needs a session, and
  every status API call the token, checked each time. The updates playbook checks every server's host key
  on every connection, including servers this project didn't install.
- **Open design.** The playbooks, this case and the threat model are public. Security relies on keys and
  the Vault, not on the secrecy of the design.
- **Separation of privilege.** VM management goes through the API with a token; ISO handling uses SSH on the
  node, as root or as a node user that can only write ISOs; server changes use the admin user with sudo.
  The token can't run commands on the node, and the node user can't change Proxmox's configuration.
- **Least privilege.** The API token's role lists only the privileges provisioning uses, with privilege
  separation; on the nodes, the optional node user (`playbooks/prepare-node.yml`) replaces root for every
  run after one setup run, with write access to the ISO folders through ACL entries and no sudo rule at all
  (root's privileges remain the default, so existing setups keep working); the token has no password-based
  login; the updates playbook never sends the Vault admin password to a server this project didn't install
  (each has its own sudo or none); the web UI's status token reads one fixed summary and can't post, start a run or open another
  route; workflows get read-only tokens and raise permissions per job only where needed.
- **Least common mechanism.** Each VM gets its own seed ISO and seed directory, removed after its install.
- **Psychological acceptability.** One command per task (`make provision`, `make baseline`, `make keys`),
  `--check --diff` works for the baseline, and failures say what to fix. The web UI lays out every option
  with its default and help text, shows errors next to the fields, and offers check mode and a limit on
  every run.
- **Limited attack surface.** Servers expose only SSH (and what you add); the installer needs no network
  service; nothing listens on the control node. The web UI, the one listener the project adds, is
  published on `127.0.0.1` only, serves only its own script and stylesheet, and has one API for other
  programs: the read-only status API for dashboards, off until the operator makes a token.
- **Input validation with allowlists.** Required variables are asserted before provisioning starts (a
  numeric `pve_vmid`, a static IP, a gateway, keys or a password). Values that end up in configuration
  files or shell commands are checked against allowlist patterns in each role's `vars/main.yml` (the admin
  user name and the node's SSH user, host names, IPv4 addresses, `ssh_allow_users`, `fail2ban_ignoreip`, Fail2Ban times, sysctl names
  and values, notification addresses, the mail relay's host and user, the webhook URL, the disk passphrase
  and the TPM state's storage), anchored
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
| **Cleartext storage of sensitive information** (CWE-312, CWE-532) | The password reaches the node only as a SHA-512 crypt hash, in root-only files that are deleted after the install; the disk passphrase only in those same files, in the installer's RAM, and in the installer's copy of its configuration inside the encrypted volume until the first boot deletes it (CI checks that no file on the installed disk holds it); `no_log` on tasks that handle the token, the hash or the passphrase, with the passphrase hidden even while debugging (the integration tests grep verbose output for it); ansible-lint's `no-log-password` rule. With disk encryption, the data on the VM's disk is encrypted at rest (LUKS2). |
| **Cleartext transmission** (CWE-319) and **improper certificate validation** (CWE-295) | HTTPS for the API, GitHub and Ubuntu downloads, with certificate verification on by default. |
| **Download of code without integrity check** (CWE-494) | Every image the project installs is pinned by checksum in the reviewed OS catalog, whose checksums were taken from the vendor's signed checksum file after `gpgv` checked the signature against a pinned key (HTTPS only for CentOS Stream and Debian's cloud images, which publish no signature, marked `signed: false`); the ISO is verified against that checksum before use, and a point release the catalog doesn't pin against a `SHA256SUMS` whose signature by Ubuntu's pinned CD image signing key the run checks; collections and packages come from Galaxy and PyPI over HTTPS (see [dependencies.md](dependencies.md)). |
| **OS command injection** (CWE-78) and **improper neutralization in templates** (CWE-1336) | Commands run with argument lists (`ansible.builtin.command` with `argv`), never through a shell; the installer's YAML is built as data and written with `to_nice_yaml`; key lines are filtered to public-key formats. Values that reach a configuration file or a shell command are validated against allowlist patterns, and Atheris fuzz targets check the generated user-data, sshd and Fail2Ban files on every change. |
| **Incorrect default permissions** (CWE-276) | Explicit file modes everywhere (`0600` seed files, `0440` sudoers validated with `visudo`, `0644` configuration); ansible-lint's `risky-file-permissions` rule. |
| **Improper restriction of authentication attempts** (CWE-307) | SSH key-only, `MaxAuthTries 4`, UFW rate limit and Fail2Ban with incremental bans. An encrypted VM's console prompt allows three passphrases per boot (`tries=5` in its crypttab, `roles/proxmox_vm/templates/user-data.j2`), each slowed by LUKS2's Argon2id key derivation; more takes a reset. |
| **Improper privilege management** (CWE-269) | Least-privilege API token with privilege separation; no root SSH on servers. Passwordless sudo for the admin user is a documented default that can be turned off. |
| **Use of components with known vulnerabilities** (CWE-1104) | Dependabot for the Python and npm packages and Actions; dependency review and osv-scanner on pull requests; the baseline upgrades packages and turns on unattended security upgrades on every server. |
| **Weak password hashing** (CWE-916) | SHA-512 crypt with a random salt (key-stretched) for the admin password; SSH doesn't accept passwords at all. |
| **Destructive operations without confirmation** (lockouts, wiped disks) | Existing VMs are skipped; the firewall opens SSH before enabling; a new SSH port is verified before the old one closes; the warning in the README and docs; `LIMIT=` and `--check --diff` for trying changes on one host. |
| **CI/CD weaknesses** (untrusted input in workflows, excessive token permissions) | `pull_request` only (no `pull_request_target`), read-only top-level permissions, actions pinned by commit SHA, untrusted event data only through `env:`; CodeQL analyses the workflows and actionlint lints them. |
| **Cross-site request forgery** (CWE-352) and **DNS rebinding** (CWE-350) | A per-session CSRF token in the `X-CSRF-Token` header of every post (a header another site's page can't set without a CORS permission the server never gives); requests whose `Origin` or `Sec-Fetch-Site` names another site are refused; `SameSite=Strict` cookies; only the configured host names are served (`webui/usas_web/main.py`). |
| **Cross-site scripting** (CWE-79) and **clickjacking** (CWE-1021) | React escapes every value it shows (the app sets no raw HTML) and the page shell is a Jinja template with auto-escaping; a Content Security Policy with `script-src 'self'`, `style-src 'self'` and `font-src 'self'`, no inline script or style (tests check the shell and the built files); `frame-ancestors 'none'` and `X-Frame-Options: DENY`. |
| **Template injection into Ansible** (CWE-1336) | Every value the UI writes into the inventory or the Vault is tagged `!unsafe`, so Ansible never evaluates it. Text that would load as a number, boolean or null (Ansible types an `!unsafe` scalar as if it had no quotes) is written as a plain quoted string instead: it can't hold a template (`webui/usas_web/store.py`; unit tests run Ansible on both kinds of value). |
| **Argument injection** (CWE-88) and **path traversal** (CWE-22) | `--limit=` takes only inventory host and group names; host names for `ssh` and `ssh-keyscan` are checked and passed after `--`; run IDs must match a fixed format before any file is opened. |
| **Authentication weaknesses** (CWE-307, CWE-384, CWE-1390) | Failed sign-ins slowed down exponentially, with passkey failures counted alongside wrong passwords; a new session ID at sign-in; the first password only with the setup code from the container's log; at least 12 characters; argon2id hashing. Optional passkeys can replace the password entirely ("Require a passkey"), which removes guessing. |
| **Authentication bypass by capture-replay** (CWE-294) and **by spoofing** (CWE-290, CWE-346) | Passkey challenges are 32 random bytes, kept with the session that asked for them, removed on first use whatever the outcome, and valid for 5 minutes; a response's origin must be the configured one exactly and its RP ID hash must match, so a response made on another site can't be used here; the signature is checked against the public key saved at registration, the signature counter must go up when the authenticator keeps one, the user handle must match, and user verification is required (`webui/usas_web/passkeys.py`; `webui/tests/test_passkeys.py` tests each with a software authenticator). |
| **Improper input validation** (CWE-20) and **insufficient verification of data authenticity** (CWE-345) | What guest agents report is parsed defensively (`plugins/module_utils/usas_vms.py`): only usable IPv4 addresses, length and count limits, control and direction characters removed, VM IDs and node names checked before they reach an API path. The guest's claims are never trusted to decide anything: the OS classification is labelled as reported by the guest, every address shows its source (agent, inventory or cloud-init), and nothing on the VMs page starts a run. Unit tests and the image smoke test feed it hostile strings. |
| **Allocation of resources without limits** (CWE-770) | The VMs page's API calls run in a pool of 4 worker threads, never on the event loop that streams the run logs, with a 10-second timeout per API call, 5 seconds per agent call and a 15-second budget per refresh; answers are cached for every session, silent agents for 2 minutes; the app polls every 30 seconds only while its tab is visible and backs off to 2 minutes after an error. The status API makes no session, allows 60 calls a minute, checks a token with one HMAC (not argon2id, which would make every call cost 64 MiB), and asks the cluster at most once a minute, in a worker thread. |
| **Missing or weak authentication of the status API** (CWE-306, CWE-287, CWE-208, CWE-307) and **credentials in URLs** (CWE-598) | The route answers 404 until a token exists, and 401 to a missing, malformed, wrong, replaced or revoked token; the token has 256 random bits, is compared as an HMAC with `hmac.compare_digest`, and is read only from the `Authorization` header; wrong tokens are throttled per client address apart from the sign-in, so an old token in a dashboard can't lock the operator out; the token never makes a session, and no other route accepts it (`webui/usas_web/status.py`; `webui/tests/test_status.py`, and the `fuzz_status_token.py` fuzz target for the header parser). |
| **Insufficiently protected credentials** (CWE-522, CWE-256) for the status token | Shown once, never sent back; the data volume keeps an HMAC-SHA256 keyed from the Vault password through scrypt, so a copy of the volume without the Vault password can't even confirm a guess; making or replacing it needs the password again; one click revokes it. |
| **Insufficiently protected credentials** (CWE-522) sent to servers by the updates playbook | A sudo password reaches a server only over SSH with a checked host key (for a VM found on the cluster, the key pinned under its VM ID, so a guest can't redirect the connection by reporting another address: CWE-345), and root on that server can read it: so a server in `other_servers` gets its own sudo password or passwordless sudo, and the playbook refuses before connecting if its password is the Vault admin password (`roles/updates/tasks/start.yml`; `tests/updates.sh` checks the refusal). |
| **Improper output neutralization for logs** (CWE-117) and **allocation without limits** (CWE-770) for server reports | Package and service names and versions from a server must match the role's patterns, other text is stripped of control and direction characters and cut to length, every list is capped at 5,000 entries, apt runs in the C locale, and upgrades run with a time limit (`updates_task_timeout`) in the background (`roles/updates/filter_plugins/usas_updates.py`, fuzzed by `fuzz_update_reports.py`). |
| **Open redirect** (CWE-601) | The sign-in page returns only to the UI's own pages. |
| **Sensitive data in responses or logs** (CWE-200, CWE-532) | Saved secrets are never sent back (the API says only whether one is saved); run logs mask the Vault's values; pages and API answers are `Cache-Control: no-store`. The status API's answer is built from fixed fields (counts, the playbook's key from the fixed table, a result word, times), never from inventory text; a test seeds distinctive names, addresses and secrets and checks none is in it. The log records the status token's use, never the token. |
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
  header and Origin checks, the host check, the status API (every refused token, the throttles, the answer's
  fields and that it holds no name, address or secret), every form's API and its validation against
  the roles' patterns, the inventory and Vault files Ansible reads back, runs and their logs; the app's
  tests of every page against a stand-in API, with axe-core, structural accessibility checks and the
  contrast of every colour pair; browser tests in Chromium of the whole setup by keyboard only, focus on
  every page and axe-core with real layout in every theme, adding a passkey and signing in with it
  through Chromium's virtual authenticator, and making, using and revoking a status token by keyboard; and a
  smoke test that builds the image and drives
  the UI against the mock Proxmox API through a real `ansible-playbook` run and the VMs page with fixture
  guests (a slow agent, a forbidden one, hostile strings) ([testing.md](testing.md#the-web-ui)). Release images
  are also scanned with Trivy and fail on fixable high or critical vulnerabilities.
- **Secret scanning**: gitleaks over the whole history on every change, plus GitHub secret scanning with
  push protection.
- **Dependency checks**: dependency review on pull requests and Dependabot alerts on `main`
  ([dependencies.md](dependencies.md)).
- **Fuzzing on every pull request** (and ten minutes per target weekly): Atheris targets render the real
  templates and Jinja expressions from the roles with arbitrary inputs and check that nothing an accepted
  value contains can add or change a setting, that the status API's header parser accepts nothing but a
  well-formed token, and that whatever a server reports to the updates playbook comes out checked, capped
  and clean, with maintenance windows right across daylight-saving changes ([testing.md](testing.md#fuzzing)). They found two bugs
  before release, both fixed with regression tests: validation patterns anchored with `$` accepted a
  trailing line break, and characters outside Unicode's basic plane didn't survive JSON-encoding into the
  installer configuration.
- **Integration tests on every pull request**: every playbook runs against containers and a mock Proxmox
  API, and the results are checked, including what must be refused (password and root logins, an empty key
  list) and the cleanup of a failed install; every playbook must change nothing on a second run, and at
  least 80% of role tasks must run ([testing.md](testing.md)). A weekly job (and every change to the
  installer or its configuration) remasters the real Ubuntu ISO and installs from it in a VM with Secure
  Boot and a TPM 2.0 set up as Proxmox does, with an encrypted disk: it checks that Secure Boot is on, the
  disk is LUKS2 with a TPM2 key slot bound to PCR 7, the VM boots and reboots without a passphrase, no file
  on the disk holds the passphrase after provisioning, and the passphrase recovers the VM at the console.
  The updates playbook has its own suite on Ubuntu 22.04, 24.04 and 26.04 (`tests/updates.sh`): servers
  with updates waiting in a signed test repository are checked, updated (all, security only, with an
  exclude, `dist`), rebooted and health-checked, and a second run must change nothing; it checks the
  refusals (the shared admin password, `dist` with excludes, bad settings), busy and unreachable servers,
  a held reboot after a Secure Boot update on an encrypted VM, and that a broken service stops the rollout.
- **Manual end-to-end runs** for what containers can't show (a real install, reboots), as the
  [test policy](../CONTRIBUTING.md#test-policy) requires.
- **The OS catalog check**: `tools/update_os_catalog.py --check` verifies every pinned image against the
  vendors again (signature, checksum, still downloadable) on each pull request that changes the catalog and
  weekly; its unit tests (offline, with a throwaway signing key) refuse a signature by another key, a changed
  checksum file, a missing signature and unsafe values, and a fuzz target covers its parsers.
- **Runtime checks inside the playbooks**: assertions on required variables, on the ISO's checksum (pinned
  by the catalog, or from a `SHA256SUMS` whose signature is checked), on the
  remastered GRUB configuration, on a non-empty key list and on `ssh_allow_users`; `sshd -t` and `visudo`
  validation before files are installed.
- **OpenSSF Scorecard** weekly, and the [OpenSSF Best Practices entry](https://www.bestpractices.dev/projects/15740).
