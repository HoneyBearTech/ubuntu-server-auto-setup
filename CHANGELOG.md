# Changelog

All notable changes to ubuntu-server-auto-setup are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Before 1.0.0, a minor version may include changes that need
steps when upgrading (for example a renamed variable); those are listed under "Upgrading" and in
[docs/upgrading.md](docs/upgrading.md).

## [Unreleased]

### Added

- **A status API for dashboards such as Heimdall** (`GET /api/status`, off by default): a short, read-only
  summary (whether a run is going and which playbook, how the last one ended, the cluster's running and total
  VMs, the servers still to install, the version and the set-up progress) with no names, addresses or
  secrets. It takes a token of its own as `Authorization: Bearer`, made on the Account page with the
  current password, shown once and kept only as an HMAC; it can be replaced or revoked there, wrong tokens
  are slowed down and calls are limited to 60 a minute. This adds attack surface: see
  [docs/security.md](docs/security.md) and [docs/web-ui.md](docs/web-ui.md#dashboards-heimdall).

### Fixed

- The web UI test "streams a running run's log" sometimes failed in CI: it looked for the log's event
  stream before React had opened it. It now waits for the stream, as does the log view's follow test.
- Runs right after a server rebooted or restarted SSH sometimes stopped with "Timeout waiting for privilege
  escalation prompt". `ansible.cfg` (the image uses the same file) now waits 30 seconds for a connection and
  for sudo's prompt instead of Ansible's 10.

## [0.5.0] - 2026-10-23

New VMs boot with Secure Boot by default, and can have their root disk encrypted with LUKS2 that their
virtual TPM unlocks at boot (off by default). New VMs can take their address from DHCP, read back through
the QEMU guest agent, and get more network cards and VLANs, each with a fixed MAC address. One inventory can
place VMs on several nodes of a Proxmox cluster, and the installer ISO is built once per shared storage or
on each node for local storage. The ISO builds can run as a node user other than root, set up once by the
new `prepare-node.yml`. The web UI adds passkey sign-in, a node per server and a network-card editor on the
Servers page.

### Upgrading

- **New VMs now boot with Secure Boot** (`vm_secure_boot` defaults to `true`): their EFI disk gets
  Microsoft's keys pre-enrolled (`pre-enrolled-keys=1`), so they run only Ubuntu's signed boot chain and the
  kernel loads only signed modules. Existing VMs are untouched: provisioning skips every VM that exists.
  To keep the old behaviour for new VMs (an EFI disk without keys, for example for an unsigned kernel or
  out-of-tree DKMS modules), set `vm_secure_boot: false` in the inventory, for the group or the host. In the
  web UI it is "Secure Boot keys" under the advanced options.
- **`make build-installer` now uses the Proxmox API** (read-only: the cluster's nodes and each node's ISO
  storages), so it needs `pve_api_host` and the token like provisioning does. It builds on the nodes the
  servers use rather than on every host in `proxmox_nodes`: a node no server uses gets no ISO unless the run
  has no server in it. Single-node inventories work unchanged. `make build-installer` now passes `LIMIT=`.
- **Add `VM.GuestAgent.Audit` to the API token's role** on Proxmox VE 9 if you use DHCP (`net_mode: dhcp`)
  or the web UI's VMs page: both ask the VMs' guest agents for their addresses. Without it, a run with a
  DHCP server stops and names the privilege, and the VMs page shows only the addresses from the inventory or
  cloud-init. Servers with static addresses don't need it. `pveum role modify UbuntuAutoSetup --privs VM.GuestAgent.Audit --append 1` adds it; Proxmox VE 8 has
  no such privilege and needs `VM.Monitor` instead, with the risks
  [docs/installing.md](docs/installing.md#2-create-the-proxmox-api-token) describes.
- **`net_interface_match` is no longer used**: the installer finds the first network card by its MAC
  address. Remove it from your inventory; it has no effect. New VMs get a MAC address derived from their host
  name and VM ID instead of a random one, so a DHCP reservation for a VM you create again keeps working; set
  `net_mac` to choose it yourself. Existing VMs are untouched.

### Added

- **Optional disk encryption with TPM2 unlocking** (`vm_disk_encryption`, off by default): the installer
  puts the root file system on LUKS2 (the `lvm` layout with a passphrase), the VM gets a virtual TPM 2.0
  (`tpmstate0`, on `pve_tpmstate_storage`, the VM storage by default). At the end of the install
  `systemd-cryptenroll` adds a TPM2 key slot, with `tpm2-device=auto` in crypttab and the initramfs rebuilt,
  so the VM boots unattended from its first boot; right after that boot, provisioning binds the slot to
  PCR 7 (the Secure Boot state) as the installed system measures it. The passphrase comes from the Vault
  (`vm_disk_passphrase: "{{ vault_disk_passphrase }}"`), stays enrolled as the recovery key, is written only
  to the short-lived seed and the installer's RAM, and is hidden from all output; the installer's copy of its
  configuration is deleted at the VM's first boot. It needs Secure Boot and the `lvm` layout (the run stops
  otherwise). It protects a copy of the VM's disk taken without its TPM state, not a compromised Proxmox
  host or a backup that includes the TPM state:
  [docs/security.md](docs/security.md#secure-boot-and-disk-encryption) explains the threat model, why PCR 7,
  and how to recover at the console.
- CI installs from the real remastered ISO in a VM with Secure Boot and a TPM 2.0 (QEMU, OVMF with
  Microsoft's keys, swtpm, set up as Proxmox does), with an encrypted disk, and checks Secure Boot, LUKS2
  with a TPM2 key slot bound to PCR 7, unattended boots and reboots, that no file on the disk holds the
  passphrase, and the recovery path (`tests/secure-boot-install.sh`, in the "Installer ISO" job).
- A "Secure Boot and disk encryption" section in the pre-release checklist for the real-Proxmox checks
  (`mokutil --sb-state`, `cryptsetup status`, an unattended reboot, the recovery passphrase).
- A passkey check in the pre-release checklist's web UI section, with a real authenticator.
- **Several Proxmox nodes per inventory.** A server can set its own `pve_node` (and `pve_ssh_host`); the
  group's value stays the default. One API endpoint, any member of the cluster, serves every node; nodes of
  another cluster need another inventory.
  - Before anything is built or created, every server's node is checked against the cluster's nodes, and its
    ISO storage against that node's storages, with one error naming every wrong host (node names are
    case-sensitive). Every node needed must answer over SSH with a host key already in `known_hosts`
    (checked with `StrictHostKeyChecking=yes`); one error names each node that doesn't, and why.
  - `build-installer` works out where the installer ISO is needed: once for a storage the cluster shares
    (NFS, CIFS, CephFS), built by one node that uses it, and on every node that uses a node-local storage.
    A node that already has the current ISO is skipped, and with `--limit` a node outside the limit is
    reported as skipped. Each VM's seed ISO goes to its own node.
  - Web UI: a node dropdown per server (the cluster's nodes from the connection test, the Proxmox page's node
    as the default); the SSH key page lists every node in use with its own address, host-key scan, trust and
    login test; the Run page shows which node builds the ISO for each node.
  - Tests: the mock Proxmox API answers for two nodes with per-node storage and routes VMs by node; the
    integration tests run a second node container that shares only the NFS storage's folder with the first,
    and check per-node builds for local storage, one build for shared storage, a refused node list, the
    host-key check and second runs that change nothing. A new fuzz target checks the ISO plan and the node
    checks.
- **A Proxmox node user other than root (optional).** `playbooks/prepare-node.yml` (`make prepare-node`),
  run once as root, creates `pve_ssh_user` (for example `usas-build`) on each node: no password, no groups,
  no sudo, only the keys in `proxmox_node_user_ssh_keys`, its own work directory, and write access to the ISO
  folders of `proxmox_node_user_iso_storages` through an ACL entry each. Since `pvesm` runs for root only, it
  records those folders in `/etc/ubuntu-server-auto-setup/iso-dirs.json`. With `pve_ssh_user` set, the ISO
  builds and the seed files need no root: the roles check that they log in as that user, that the work
  directory is its own and the ISO folder writable, and that `xorriso` and `gpgv` are installed. The default
  stays `root`, so nothing changes until you opt in ([docs/installing.md](docs/installing.md#6-optional-a-node-user-other-than-root)).
- The web UI's **Node SSH user** (Proxmox page, More settings) sets that user for the inventory, the SSH
  page's `authorized_keys` command and **Test the SSH login**.
- The example inventory logs in to the nodes as `"{{ pve_ssh_user }}"` instead of `root`; an inventory
  that sets `ansible_user: root` keeps working as before.
- **DHCP** for new VMs: `net_mode: dhcp` (per host or for a group; the default stays `static`). The installer
  configures the first network card with DHCP, and once the installed system boots, provisioning asks its
  QEMU guest agent for the address and uses it as `ansible_host` for the rest of the run, printing it; the
  baseline playbook does the same when run on its own. It waits up to `net_dhcp_timeout` seconds (300) and
  says what to check if no address comes. The API token needs `VM.GuestAgent.Audit` on Proxmox VE 9 or
  `VM.Monitor` on Proxmox VE 8 for this; with an address reserved on the DHCP server for `net_mac` and set
  as `ansible_host`, the agent isn't asked at all.
- **More network cards and VLANs**: `vm_extra_nics` adds `net1` and up, each with its bridge, VLAN tag,
  model, MAC address, MTU and a static address, DHCP or no address; `pve_vlan` already tags `net0`. Each
  card gets a fixed MAC address (`net_mac`, a card's `mac`, or one derived from the host name and VM ID, so a
  VM created again keeps it) and the installed system matches cards by MAC address, not by interface name.
  Exactly one card may have the default route; every network value is checked before anything is created.
  The firewall's rules apply to every card (docs/interfaces.md, Networks).
- The web UI's **Servers** page edits both: an **Address** column (`static` or `dhcp`; with `dhcp` the IP
  address may stay empty), a **MAC address** column, and **Extra network cards** written like
  `vmbr1 vlan 20 10.20.0.5/24 mtu 9000, vmbr1 vlan 30 dhcp`, checked against the provisioning role's formats
  and its one-default-route rule. The Defaults page's gateway is needed only for servers with a static
  address, and the overview counts it as set when every server has its default route.
- **Passkeys** in the web UI. Once signed in with the password, add passkeys on the account page (a name
  and the current password), see when each was added and last used, and remove them; the sign-in page
  offers **Sign in with a passkey** next to the password. **Require a passkey** turns password sign-in off;
  deleting `passkeys.json` from the data volume turns it on again. Passkeys work at `http://localhost`
  on any port, or over HTTPS under the host name set in the new `USAS_PUBLIC_URL`; elsewhere the page
  explains why it doesn't offer them. WebAuthn through py_webauthn (`webauthn` 3.0.1, hash-pinned in the
  image): user verification required, single-use challenges tied to the session that expire after
  5 minutes, origin and RP ID checks, signature counters, and the same throttling as the password
  ([docs/web-ui.md](docs/web-ui.md#passkeys), [docs/security.md](docs/security.md)).
- The container log records every sign-in and failed sign-in, with the client's address, and passkeys
  being added, removed or required.

### Changed

- `vm_secure_boot` defaults to `true` (see Upgrading).
- The mock Proxmox API stores `efidisk0` and `tpmstate0` the way Proxmox does and refuses what Proxmox
  would; the integration tests check the installer configuration the VM actually got.
- The installer matches the first network card by its MAC address instead of the interface name pattern
  `net_interface_match`, which is no longer used. New VMs get a MAC address derived from their host name and
  VM ID instead of a random one; existing VMs are untouched.
- `pve_bridge` and `pve_vlan` are now checked (a bridge name, a VLAN tag 1-4094) before a VM is created.

### Fixed

- `make build-installer` retries fetching `SHA256SUMS` and its signature when the connection fails (a TLS
  handshake timing out at releases.ubuntu.com stopped the run), with a 30-second timeout instead of 10.
- The release-file, provenance and tag examples in docs/verifying-releases.md use v0.4.0, like the
  container image examples.
- Provisioning checks that the API token may ask a new DHCP server's guest agent (`VM.GuestAgent.Audit` on
  Proxmox VE 9, `VM.Monitor` on 8) before it creates the VM. Without it, the VM was installed, the run stopped
  when it asked for the address, and later runs couldn't reach the VM because its new host key was never
  trusted. A new DHCP server whose address can't be read (no lease) now says how to recover: delete the VM
  and run again, or pin its key ([docs/host-keys.md](docs/host-keys.md#when-a-key-or-certificate-changes)).
- `prepare-node.yml` writes a test file as the node user in each ISO folder and stops, naming the folder and
  the user's UID, if it can't: an NFS export that doesn't let that UID write (Synology's defaults, for example)
  accepted the ACL entry, so the run reported success and the build failed later. That build now says the
  folder can't be reached by the node user instead of a bare "Permission denied". docs/installing.md says
  what the export needs, and that root stays the way for a share that won't let the user in.
- A new VM with `net_mode: dhcp` could fail right after its install with "that VM is stopped: start it and run
  again": the address lookup took the VM's status from the cluster's list, which Proxmox refreshes every few
  seconds and still said "stopped" just after the start. It now asks the VM's node, and waits up to 30
  seconds for a VM that is starting.
- The pre-release checklist's recovery-passphrase check passes `--disable-external-tokens` to `cryptsetup`:
  without it, cryptsetup unlocked with the TPM token and accepted any passphrase.
- CI's Secure Boot test answers the recovery passphrase prompt reliably: it waits for the whole prompt and
  answers the console's cursor queries (systemd 259 asks them on a serial console), falls back to typing on
  the screen, and its passphrase check passes `--disable-external-tokens` to `cryptsetup`, without which the
  TPM token unlocked the disk and any passphrase was accepted; it also checks that a wrong one is refused.

### Security

- The development requirements set a lower bound of 26.10.1 on black (installed through ansible-lint), which fixes
  GHSA-cg8m-r9f2-5wm2. Only the lint tooling is affected; the playbooks, the image and the web UI don't use black.

## [0.4.0] - 2026-10-21

The first baseline run on Ubuntu 26.04 completes again (sudo-rs), and provisioning no longer detaches the
installer before a slow-starting VM is running; Ubuntu's GPG signature on `SHA256SUMS` is verified before
the ISO checksum is trusted; extra SSH keys, the web UI's own included, are installed again, each once;
`--check --diff` works on a fresh server. New: optional extra hardening and update and ban notifications
(all off by default), a read-only VMs page in the web UI, browser and screen-reader checks of the web UI,
and a pre-release checklist on real Proxmox VE.

### Upgrading

- **From 0.3.0, on Ubuntu 26.04: run the baseline again.** A first baseline run there stopped right after it
  removed passwordless sudo, so SSH hardening, the firewall and Fail2Ban were never applied; the next run
  applies them. Key logins and `sudo` with the admin password worked all along. Your own playbooks that
  become root on 26.04 with a password need `ansible_become_exe: /usr/bin/sudo.ws` as well.
- **A mirror in `ubuntu_iso_base_url` must publish `SHA256SUMS.gpg`** next to `SHA256SUMS`, signed by
  Ubuntu's CD image key: `make build-installer` now stops before the download if the signature is missing
  or wrong. releases.ubuntu.com and old-releases.ubuntu.com publish it. The Proxmox node gets `gpgv` if it
  doesn't have it.

### Added

- A **VMs** page in the web UI (first under Playbooks): a read-only list of every VM and container on the
  cluster with its node, status, CPU and memory use, the address the playbooks would use and where it came
  from (the QEMU guest agent, the Servers page or cloud-init), and whether the playbooks support it
  (Ubuntu 26.04 and 24.04; other Ubuntu releases, an unknown OS, other systems, templates and containers
  are labelled with the reason). It refreshes every 30 seconds while visible. The browser tests check it by
  keyboard and with axe-core at phone width in both themes. Selecting VMs and running
  the baseline against them comes in a later release. **New attack surface**: what guest agents report
  reaches the UI; it is treated as untrusted (see docs/security.md and the assurance case's boundary B12).
  The guest agents need the read-only `VM.GuestAgent.Audit` privilege on Proxmox VE 9; on an existing role,
  add it with `pveum role modify UbuntuAutoSetup --privs VM.GuestAgent.Audit --append 1`. On Proxmox VE 8
  the page works without the agents ([docs/installing.md](docs/installing.md#2-create-the-proxmox-api-token)).

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
- Browser tests of the web UI in CI (Playwright, Chromium; `tests/webui-browser.sh`, `make webui-browser`),
  in the "Web UI tests" check: the whole setup and a provisioning run by keyboard only, against the server
  and the mock Proxmox API; Tab order, a visible and uncovered focus outline and no keyboard trap on every
  page; where focus goes after each action; the run's status in a polite live region while the output
  streams without moving focus; and axe-core's WCAG 2.1 A and AA rules with real layout, so with contrast,
  on every page in the dark, light and system themes. See [docs/testing.md](docs/testing.md#the-browser-tests).
- A screen-reader walk-through with VoiceOver and NVDA, with what each step should announce and a table for
  each release's results ([docs/accessibility.md](docs/accessibility.md#screen-reader-walk-through)), now part
  of the pre-release checklist. It hasn't been run yet.

- Optional extra hardening in a new `hardening` role, each part off by default with its own switch:
  `hardening_sysctl_enabled` (kernel and network settings in `/etc/sysctl.d/90-usas-hardening.conf`,
  applied and checked live on every run (keys the kernel lacks are reported, not applied), overridable with `hardening_sysctl` and `hardening_sysctl_extra`;
  IP forwarding and router advertisements left alone for Docker and Proxmox guests),
  `hardening_auditd_enabled` (auditd with rules for identity files, sudoers, the sshd configuration, time
  changes, kernel modules and the audit configuration and logs; `hardening_auditd_immutable` locks them
  until a reboot) and `hardening_apparmor_enabled` (AppArmor kept installed, enabled and running, with
  complain-mode profiles reported, never forced). The role is tagged `hardening`.
- Optional notifications: `unattended_upgrades_mail` (with `unattended_upgrades_mail_report`) for
  unattended-upgrades' report; `fail2ban_destemail` and `fail2ban_sender` to mail every ban (`action_mw`);
  `fail2ban_webhook_url` and `fail2ban_webhook_format` to post every ban to an ntfy, Gotify, Slack, Discord
  or Mattermost webhook; and `mail_relay_enabled` to install `msmtp-mta` as a send-only `sendmail` through
  your smarthost (`mail_relay_host`, `mail_relay_port`, `mail_relay_tls`, `mail_relay_from`,
  `mail_relay_user`, and `mail_relay_password` from the Vault). The relay password and webhook URL are
  stored on the server in root-only `0600` files, and every value is checked against `\Z`-anchored
  allowlist patterns. With the defaults, a rerun changes nothing on existing servers.
- The web UI's Hardening page has the new switches and notification settings, in groups of their own. The
  relay password and webhook URL are kept in the Vault, and a saved optional secret can now be removed
  ("Remove the saved value").
- Tests: an integration scenario that turns every option on (`tests/hardening-vars.yml`,
  `tests/verify-hardening.yml`, including a real ban reaching a webhook listener), injection cases, and the
  fuzz target `fuzz_baseline_extras.py`; `fuzz_configs.py` now covers the Fail2Ban notifications.

### Fixed

- A key both published on GitHub and listed in `admin_extra_ssh_keys` (for example a personal key uploaded
  as the web UI's key) was installed twice by the installer, and every baseline run then reported
  "Install the authorized keys" as changed. Each key is now installed once, by its type and key material,
  keeping the extra key's comment.
- The first baseline run stopped on Ubuntu 26.04 (since 0.3.0) right after it removed passwordless sudo:
  26.04's `sudo` is sudo-rs, which wraps the password prompt Ansible waits for, so the next task timed out
  "waiting for privilege escalation prompt" before SSH, the firewall and Fail2Ban were set up. The baseline
  now becomes root with the classic sudo (`/usr/bin/sudo.ws`) where it is installed, unless
  `ansible_become_exe` is set; where only sudo-rs is installed, it stops before changing anything and says
  what to set. The test server image now uses sudo-rs as its `sudo`, like Ubuntu Server 26.04.
- Provisioning could leave a new VM booting from an empty disk (since 0.1.0). Proxmox answers a start
  request before the VM is running, and when the start took a moment the first status check still read
  "stopped": the run took the install as finished and detached the installer and seed ISOs before the VM
  had even started. It now waits for Proxmox's start task to finish, and stops if the start failed, before
  it waits for the installer to power the VM off.
- Keys in `admin_extra_ssh_keys`, including the web UI's own key, were left out of `authorized_keys` and the
  installer's configuration whenever `github_user` was set (since 0.2.0), and the baseline's exclusive key
  sync removed them. They are installed again.
- `--check --diff` works on a server the baseline hasn't run on yet. It stopped at the first package or
  service the run would install: the package lists are now refreshed in check mode too, and the UFW rules,
  Fail2Ban and other services that the run would install are skipped in check mode until they exist. A check
  run with a new `ssh_port` no longer tries to reconnect on a port sshd isn't using yet, and one with an
  `admin_user` that doesn't exist yet no longer fails on its `authorized_keys`.
- Web UI: on narrow screens, Escape now closes the open menu and puts focus back on the **Menu** button,
  and moving focus out of the menu closes it, so it no longer covers the part of the page that has focus.

- [docs/installing.md](docs/installing.md) now says to set `REQUESTS_CA_BUNDLE` on Linux as well: in a
  virtual environment the Proxmox modules don't read the system's trust store.

### Security

- The installer role verifies Ubuntu's GPG signature on `SHA256SUMS` before it trusts the ISO checksum in
  it: `gpgv` (installed on the Proxmox node if missing) checks `SHA256SUMS.gpg` against the Ubuntu CD image
  signing key that now ships with the role (`roles/installer_iso/files/ubuntu-cdimage-keyring.gpg`), pinned
  by its full fingerprint `843938DF228D22F7B3742BC0D94AA3F0EFE21092` (`installer_signing_fingerprint`) and
  never fetched from a keyserver. The checksum is read from the verified file. A missing or bad signature,
  or one by any other key, stops the run before the ISO is downloaded. A mirror set in
  `ubuntu_iso_base_url` must now publish `SHA256SUMS.gpg`. The integration tests sign their stand-in
  `SHA256SUMS` with a key made at image build time and check that tampered, unsigned and wrongly signed
  files are refused; the weekly real-ISO job checks Ubuntu's real signature with the shipped key.

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

[Unreleased]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.5.0
[0.4.0]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.4.0
[0.3.0]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.3.0
[0.2.3]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.2.3
[0.2.2]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.2.2
[0.2.1]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.2.1
[0.2.0]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.2.0
[0.1.0]: https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.1.0
