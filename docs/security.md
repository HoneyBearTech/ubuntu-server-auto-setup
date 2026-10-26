# Security requirements

This page says what you can and cannot expect from ubuntu-server-auto-setup in terms of security. The
reasoning behind it (threat model, trust boundaries, design principles and how common weaknesses are
countered) is in the [assurance case](assurance-case.md). To report a vulnerability, see
[SECURITY.md](../SECURITY.md).

## Intended use

The playbooks are meant to be run by an administrator, from a control node they trust, against Proxmox VE
nodes and Ubuntu servers they own, on a private network. The control node holds the inventory, the Vault
and the SSH keys that reach every server and the Proxmox nodes (as root, unless you set up a [node user of
its own](#the-proxmox-node-user-optional)), so it is as sensitive as those systems. With the container
image, the control node is the container: its web UI is meant for one administrator on the same machine
(`127.0.0.1`), and its data volume holds the same secrets. The rest of this page assumes that use, with the
defaults and the advice in the README's "Running it securely" section and in [web-ui.md](web-ui.md#security).

## What the project is designed to guarantee

**Secrets**

- The admin password and the Proxmox API token are read from an Ansible Vault file that is gitignored, and
  never written to the repository. Tasks that handle the token or the password hash hide their arguments
  and results from the output (`no_log`).
- The admin password leaves the control node only as a salted SHA-512 crypt hash. On the Proxmox node the
  hash exists only in the per-host seed files (directory `0700`, files `0600`, owned by the user the
  playbooks log in as: root, or the node user) for the duration of provisioning; the seed ISO and its
  directory are deleted once the installer has finished.
- The API token is sent only to the Proxmox API, over HTTPS with certificate verification on by default
  (`pve_validate_certs: true`).
- The disk encryption passphrase (`vault_disk_passphrase`, only with `vm_disk_encryption`) is written only
  into the same per-host seed files as the password hash, which are deleted after the install. It is
  checked against its pattern into a plain true/false, so no task's output can hold it, and the task that
  renders the seed stays hidden even with `pve_hide_secrets: false`. The installer reads it into its own
  RAM; the copy Ubuntu's installer keeps of its configuration on the new, encrypted disk is deleted as soon
  as the VM first boots.

**Downloads**

- The Ubuntu ISO is downloaded over HTTPS and checked against the SHA-256 checksum Ubuntu publishes for it
  in `SHA256SUMS`. Before that checksum is used, `gpgv` checks Ubuntu's signature on `SHA256SUMS`
  (`SHA256SUMS.gpg`) against the Ubuntu CD image signing key that ships with the role
  (`roles/installer_iso/files/ubuntu-cdimage-keyring.gpg`), pinned by its full fingerprint
  `8439 38DF 228D 22F7 B374 2BC0 D94A A3F0 EFE2 1092` (`installer_signing_fingerprint`). The key is never
  fetched from a keyserver at run time. A missing or bad signature, a signature by any other key, or a
  checksum mismatch stops the run before the ISO is used. To inspect the shipped key:
  `gpg --show-keys roles/installer_iso/files/ubuntu-cdimage-keyring.gpg`.
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
- New VMs boot with UEFI Secure Boot on (`vm_secure_boot: true`, the default): their EFI disk has
  Microsoft's keys pre-enrolled, so the firmware runs only Ubuntu's signed shim, GRUB and kernel, and the
  kernel (in lockdown) loads only signed modules. The remastered installer ISO keeps Ubuntu's signed EFI boot
  image unchanged (only `grub.cfg`, a configuration file GRUB doesn't verify, is replaced), and CI installs
  from it under Secure Boot.
- The Proxmox API token needs only the privileges listed in [installing.md](installing.md#2-create-the-proxmox-api-token)
  (no `Sys.*`, no user or permission management, no `VM.Console`), on a dedicated user with privilege
  separation.
- For a server with `net_mode: dhcp` and no `ansible_host`, the address Ansible connects to is the one the
  VM's guest agent reports for the card with `net0`'s MAC address. It is used only if it is a plain IPv4
  address (not link-local), and SSH host key checking still applies: a fresh VM's key is trusted once, right
  after its install, and later runs need the key already in `known_hosts`. To leave the guest out of it,
  reserve the address on the DHCP server and set it as `ansible_host`.
- Every network value (bridges, VLAN tags, MAC addresses, MTUs, addresses, prefixes, gateways and the keys
  of `vm_extra_nics`) is checked against `\Z`-anchored patterns before a VM is created, so none can add an
  option to a Proxmox network card or a key to the installer's network configuration (fuzzed in
  `tests/fuzz/fuzz_user_data.py`).

<a id="the-proxmox-node-user-optional"></a>
**The Proxmox node user (optional)**

With `pve_ssh_user` set to a user that `playbooks/prepare-node.yml` created, the playbooks never log in to a
Proxmox node as root after that one setup run, and nothing they do there runs as root:

- The user has no password (`*`, so only its authorized keys log in), no groups (so no sudo, and no
  `www-data` access to `/etc/pve`), and no sudo rule. Its `authorized_keys` holds exactly
  `proxmox_node_user_ssh_keys`.
- It can write to its own work directory (`0700`) and to the ISO folder of each storage listed in
  `proxmox_node_user_iso_storages`, through one ACL entry per folder; it can't change the folders' owner or
  anything else of Proxmox's. Proxmox's `pvesm` refuses to run for it, so the folders are looked up once,
  as root, and recorded in a root-owned file it can read but not change.
- Every run checks that the user it logs in as is `pve_ssh_user`, that the work directory is that user's
  own folder (not a link, mode `0700`) and that it can write to the ISO folder, before it downloads
  anything. It doesn't install packages; it checks that `xorriso` and `gpgv` are there.

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

- Every page and action except the health check and the status API needs a sign-in. The password is stored only as an
  argon2id hash; the first one can only be set with a one-time code printed in the container's log; failed
  attempts are slowed down exponentially (up to 15 minutes between tries). Every sign-in and failed
  sign-in is logged with the client's address.
- **Passkeys** (optional) sign in instead of the password: WebAuthn through py_webauthn, added only by a
  signed-in session that enters the password again, and kept as public keys in a `0600` file. Each
  challenge belongs to one session, is used once and expires after 5 minutes; a response counts only from
  the UI's own origin and host name (`USAS_PUBLIC_URL`, or `localhost`; never taken from the request), with
  user verification, and with a signature counter that must go up if the authenticator keeps one. Failures
  count towards the same slow-down as wrong passwords. **Require a passkey** turns password sign-in off
  (the password isn't even checked) and signs out other sessions that used it; removing a passkey signs
  out the other sessions that used it. If the passkeys file can't be read, no passkey works and the
  password is refused, until the file is deleted ([web-ui.md](web-ui.md#passkeys)).
- Sessions are random, kept in memory, carried in an `HttpOnly`, `SameSite=Strict` cookie (`Secure` over
  HTTPS), renewed at sign-in and ended by sign-out, inactivity (2 hours), age (12 hours), a password
  change (for other sessions), removing the passkey or requiring a passkey (for the sessions that used it
  or the password), and a restart.
- Every post needs the session's CSRF token in an `X-CSRF-Token` header, and requests that say they come
  from another site (`Origin`, `Sec-Fetch-Site`) are refused; the API answers no cross-origin requests.
  Only the configured host names are served, against DNS rebinding, and names other than `localhost` only
  over HTTPS. Pages can't be framed and load only the UI's own script, stylesheet and fonts, nothing inline
  (Content Security Policy). The app in the browser only shows what the server answers: every value is
  checked on the server.
- The API token, the admin password and the other secrets (the mail relay password, the webhook URL and the
  disk passphrase) are stored only in the Ansible Vault file; once saved they are never sent back to the
  browser; run logs mask them. The Defaults page refuses disk encryption without Secure Boot, the `lvm`
  layout and a passphrase that matches the role's pattern, as the role would. The SSH private key never leaves the data volume.
- Every value the UI writes into the inventory is validated against the roles' own allowlist patterns (or
  a stricter format of the UI's) and tagged `!unsafe`, so no value can become an Ansible template (text that
  would load as a number, boolean or null, which can't hold a template, is written as a plain quoted string, so
  it stays text); commands
  run with argument lists, never a shell; `--limit` takes only the inventory's own host and group names.
- The VMs page only reads: it lists the cluster's VMs and asks their guest agents, through the Proxmox API,
  for addresses, the OS and the host name with the read-only `VM.GuestAgent.Audit` privilege (on Proxmox
  VE 8, the broader `VM.Monitor` stays optional). What a guest reports is treated as untrusted: only
  usable IPv4 addresses are kept, other strings are stripped of control characters and shortened, every
  address shows its source, nothing is shown as HTML, and no guest-reported value decides anything. Agent
  calls are few, short and bounded (4 at a time, 5 seconds each, 15 seconds per refresh, cached), so a
  slow or hostile guest can't stall the UI.
- **The status API for dashboards** (`GET /api/status`, for Heimdall and the like) is off until a signed-in
  session that enters the password again makes its token. The token (32 random bytes after `usas_st_`) is
  shown once; the data volume keeps only an HMAC-SHA256 of it, keyed from the Vault password through scrypt. It is accepted
  only as `Authorization: Bearer`, compared in constant time, and opens only this route: it makes no
  session, can't post and can't start a run. Replacing it stops the old one at once; revoking it turns the
  API off; an unreadable token file accepts no token. Wrong tokens are slowed down per client address
  (5 free, then the wait doubles up to 15 minutes) in a bucket of their own, so a dashboard with an old token
  can't lock the sign-in out; right ones are limited to 60 calls a minute. The answer holds only counts, the
  playbook and result of the running or last run, the version and the setup progress: no host, node, cluster
  or VM names, no addresses or VM IDs, no run limits or logs, no secrets. The cluster is asked for the counts
  at most once a minute. Making, replacing and revoking the token and refused calls are logged; uses once an
  hour per address. The host check and the HTTPS rule apply to it as to the pages
  ([web-ui.md](web-ui.md#dashboards-heimdall)).
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

**Updates** (`playbooks/updates.yml`, [updates.md](updates.md))

- The updates playbook never configures a server: it reads, installs the updates you chose, reboots only
  with `updates_reboot: if_required`, and checks health. It doesn't stop, mask or change automatic updates,
  write holds, or install anything that isn't an update of an installed package or its new dependency.
- It never turns off package signature checks: apt checks every package against the server's own keyrings
  (`tests/updates.sh` fails if the role names an option that would turn that off).
- A server outside `ubuntu_servers` with a `pve_vmid` (in `other_servers`) never gets the Vault admin
  password as its sudo password unless `updates_allow_shared_become_password` says so for it, since root
  there could read it. Host keys are checked strictly on every connection, as for every playbook.
- What servers report (package names, versions, services) is checked against the role's patterns, stripped
  of control characters and capped at 5,000 entries per list before it reaches the report, which is
  readable only by the control node's user.
- With `safe` (the default) apt never removes a package; an update that would need a package you excluded
  skips the server instead of upgrading that package. One failed server, or a failed health check, stops
  the servers not yet started.

## Optional hardening and notifications

These are off by default and each has its own switch, because each can get in the way of something a
server legitimately does, and a baseline that breaks a working server on its next run is worse than one that
does a little less. Turn on what fits your servers, then run in check mode first. Turning one off again
removes its files; settings already loaded in the kernel stay until the next reboot.

- **Kernel and network settings** (`hardening_sysctl_enabled`): `/etc/sysctl.d/90-usas-hardening.conf`
  hides kernel addresses and the kernel log from unprivileged users, turns off unprivileged BPF and
  hardens its JIT, limits `ptrace` to a process's descendants, protects hard links, symbolic links, FIFOs
  and regular files in shared directories, turns off core dumps of setuid programs, uses strict reverse-path
  filtering, refuses ICMP redirects and source-routed packets (and sends no redirects), and keeps TCP SYN
  cookies on. Live values are checked and corrected on every run; a key the kernel doesn't have (IPv6
  turned off at boot, or a container) stays in the file but is reported instead of applied. IP forwarding and IPv6 router
  advertisements are left alone, so Docker and a Proxmox guest's networking keep working. Off by default
  because strict reverse-path filtering drops traffic on hosts with asymmetric routing (several interfaces,
  VPN gateways such as a Tailscale subnet router), and because debugging and profiling tools (`perf`, `strace`
  on unrelated processes) stop working. Change single keys with `hardening_sysctl_extra` (a key set to
  `null` is left out), or replace `hardening_sysctl` entirely.
- **auditd** (`hardening_auditd_enabled`): the kernel records changes to users, groups and passwords, to
  sudoers, to the sshd configuration, to the clock and time zone, to the audit configuration and logs,
  and every kernel module loaded or removed (`ausearch -k identity`, `-k sudoers`, `-k sshd`,
  `-k time-change`, `-k modules`, `-k audit-config`, `-k audit-log`). Off by default because it costs disk
  space and some performance, and its logs are only useful if someone reads them or ships them elsewhere.
  `hardening_auditd_immutable` adds `-e 2`: nobody, root included, can change or stop auditing until the
  next reboot, so new rules from a later run wait for a reboot too. auditd can't run in a container (the
  rules are installed, the service isn't started).
- **AppArmor** (`hardening_apparmor_enabled`): installs AppArmor, keeps the service enabled and running so
  the profiles Ubuntu and your packages ship are loaded in the mode they ship with, fails if the kernel has
  AppArmor turned off, and reports profiles in complain mode. It never changes a profile's mode: some
  packages ship complain-mode profiles on purpose, and forcing them to enforce can break the service. Ubuntu
  has AppArmor on already; the switch makes sure it stays that way.
- **Notifications**: `unattended_upgrades_mail` mails unattended-upgrades' report (`MailReport`
  `on-change` by default); `fail2ban_destemail` mails every ban with a whois report (Fail2Ban's
  `action_mw`); `fail2ban_webhook_url` posts a message for every ban to an ntfy, Gotify, Slack, Discord or
  Mattermost URL (`fail2ban_webhook_format`: `text` or `json`). Mail needs a mail transfer agent on the
  server. `mail_relay_enabled` installs `msmtp-mta` as a send-only `sendmail` that hands every message to
  your smarthost (`mail_relay_host`, `mail_relay_port`, `mail_relay_tls`: `starttls`, `tls` or `off`), from
  `mail_relay_from`; it refuses to replace an existing mail server (Postfix, Exim and the like). Off by
  default because they need an outside service and send information about the server (its name, the
  addresses it bans, its package updates) to it.
- **Secrets for notifications**: the relay password (`vault_mail_relay_password`) and the webhook URL, which
  usually holds a token (`vault_fail2ban_webhook_url`), belong in the Vault like the other secrets. On the
  server they are in root-owned `0600` files (`/etc/msmtp-password`, read by msmtp's `passwordeval`, and
  `/etc/fail2ban/action.d/usas-webhook.conf`), so only root can send mail through the relay; the tasks that
  write or check them hide their values from the output. Addresses, the relay's host and user, and the URL are
  checked against `\Z`-anchored allowlist patterns (no spaces, quotes, line breaks, `%`, `<` or `>`), so a
  value can't add settings to apt, msmtp or Fail2Ban, or arguments to the webhook's `curl` command.

## Secure Boot and disk encryption

**Secure Boot** is on for new VMs. Ubuntu 26.04.1's shim is signed with the Microsoft UEFI CA 2011, which
every Proxmox EFI disk with pre-enrolled keys trusts (UEFI firmware doesn't check certificate expiry, so
the CA's expiry in 2026 doesn't stop it booting). Shims signed only with Microsoft's 2023 CA will need the
2023 certificates in the EFI disk: `qm config <vmid>` shows `ms-cert=2023k` on the `efidisk0` line when
they are there, and recent Proxmox VE releases can add them to a VM that is shut down
(`qm enroll-efi-keys <vmid>` on 9.2 and later). Existing VMs are never changed by this project; turning
Secure Boot on for one is a Proxmox setting (a new EFI disk with pre-enrolled keys). Opt a new VM out with
`vm_secure_boot: false` if it must run an unsigned kernel or out-of-tree modules.

**Disk encryption** (`vm_disk_encryption: true`, off by default) installs the root file system on LUKS2:
the installer's `lvm` layout with a passphrase puts the volume group on an encrypted partition (`/boot`
and the EFI system partition stay unencrypted, as Ubuntu does). The VM gets a virtual TPM 2.0 (Proxmox
`tpmstate0`). Enrolling the TPM happens in two steps, both during provisioning:

1. At the end of the install, the TPM is reachable from the installer, which runs `systemd-cryptenroll
   --tpm2-device=auto --tpm2-pcrs=` in the new system: a TPM2 key slot not yet bound to any PCR. It also adds
   `tpm2-device=auto,tries=5` to `/etc/crypttab` and rebuilds the initramfs (dracut, with its TPM2 modules),
   so the first boot unlocks without anyone typing a passphrase. That can't wait for the baseline: until the TPM
   holds a key, nobody could answer the passphrase prompt.
2. As soon as the VM has booted from its disk, provisioning runs `systemd-cryptenroll --unlock-tpm2-device=auto
   --tpm2-device=auto --tpm2-pcrs=7 --wipe-slot=tpm2`: the TPM itself unlocks the disk for a new slot bound to
   PCR 7 as this boot measured it, and the unbound slot is wiped. Binding to PCR 7 in the installer doesn't
   work: the install changes Secure Boot variables (Ubuntu's forbidden-signatures (dbx) updates, shim's SBAT
   level), so the installed system's first boot measures a different PCR 7 than the installer did, and the
   VM would stop at the passphrase prompt. CI showed exactly that.

The window between the two steps lasts the first boot (a minute or two, with the VM's TPM state on your
Proxmox storage only). If provisioning stops in between, run step 2 by hand on the VM. The Vault passphrase
keeps its own key slot all along: it is the recovery key.

*Why PCR 7.* The TPM releases the disk key only while PCR 7 has the value it had at the first boot. PCR 7
records the Secure Boot state: whether it is on, the enrolled keys (PK, KEK, db, dbx) and the certificates
that verified the boot chain. It doesn't change when the kernel, the initramfs or GRUB's configuration is
updated, so routine updates and reboots stay unattended. It does change, and the VM then stops at the
passphrase prompt on its console, when Secure Boot is turned off, the EFI disk's keys change (for example
`qm enroll-efi-keys`, a forbidden-signatures (dbx) update from the OS, or a new EFI disk), or the VM boots
code signed by a different authority. Binding to more PCRs (the boot loader and kernel, 4 and 9) would also
catch a swapped kernel, but would ask for the passphrase after every kernel update.

*What it protects.* A copy of the VM's disk volume that doesn't come with the VM's TPM state: the disk
image copied out of shared storage (NFS, Ceph) or a storage export, a storage-level snapshot, replication
or backup of the disk volume alone, a decommissioned disk that held only the VM's disk. Without the TPM
state and the passphrase, the data is unreadable. Keep the TPM state elsewhere than the disks with
`pve_tpmstate_storage` (for example on the node's local storage while the disks are on shared storage) to
make that hold for the disks' storage as a whole.

*What it doesn't protect.*

- **A compromised Proxmox host, or root on it.** The vTPM is software (swtpm) running on the node; its
  state is a file the node can read. Root on the node can also read a running VM's memory, where the disk
  key is, change the VM's disks, and run commands in it through `qemu-guest-agent`.
- **Backups and copies that include the TPM state.** Proxmox backups (`vzdump`, Proxmox Backup Server)
  include `tpmstate0` and `efidisk0` along with the disk, so whoever restores one gets a VM that unlocks
  itself. Encrypt those backups (Proxmox Backup Server's client-side encryption) if they leave your
  control. The same goes for a stolen node disk that held both the VM's disk and its TPM state.
- **Someone who can start the VM from other media.** PCR 7 records which certificates verified the boot
  chain, not which program they verified: booting other media signed by the same authorities (an Ubuntu
  live ISO, for example) on the same VM gives the same PCR 7, and the TPM would release the key to it. Who
  can attach an ISO to the VM and start it (`VM.Config.CDROM` and `VM.PowerMgmt`, which the project's API
  token has) can therefore read the disk. Protect the token like a root credential.
- **The running VM.** Once unlocked, the disk is readable by anything that gets into the VM.

*Recovery.* When the TPM doesn't release the key, the VM waits at "Please enter passphrase for disk" on
its console (Proxmox's noVNC or xterm.js console). Type the Vault passphrase; the keyboard layout there is
`autoinstall_keyboard_layout`. It asks up to three times. After a third wrong passphrase,
systemd-cryptsetup gives up and the boot waits there for good: reset the VM (Proxmox's Reset, or `qm reset
<vmid>` on the node) for three more tries. (The crypttab line's `tries=5` makes it three: Ubuntu 26.04's
systemd 259 counts the TPM's attempt and a search for a key file as tries, so its default of 3 left one
passphrase. VMs installed with 0.5.0 have that default; see below to change it.) Once it is up, enroll the
TPM again for the new Secure Boot state (it asks for the passphrase):

```sh
sudo systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 "$(sudo blkid -t TYPE=crypto_LUKS -o device)"
```

On a VM installed with 0.5.0, whose crypttab line has no `tries=`, add it and rebuild the initramfs:

```sh
sudo sed -i -E '/tpm2-device=auto/{/tries=/!s/tpm2-device=auto/tpm2-device=auto,tries=5/}' /etc/crypttab
sudo dracut --regenerate-all --force
```

To change the passphrase, run `sudo cryptsetup luksChangeKey "$(sudo blkid -t TYPE=crypto_LUKS -o device)"`
on each VM and update `vault_disk_passphrase` for the VMs you install later. CI installs an encrypted VM
under Secure Boot with a TPM, as Proxmox sets them up, and checks the unattended boots, that no file on the
disk holds the passphrase after provisioning, and this recovery path, with two wrong passphrases first
(see [testing.md](testing.md)).

## Pinning the Proxmox nodes' keys

The checks above stop a run when a Proxmox node's SSH host key or the API's certificate isn't the one the
control node trusts, but only after something has been trusted the first time. [host-keys.md](host-keys.md)
shows how to read the node's host key fingerprints and the API's certificate on the node's console, compare
them with what the control node sees, and pin them: `known_hosts` entries checked with
`StrictHostKeyChecking=yes` (for every node of a cluster), `pve_validate_certs: true` with the cluster's CA
in the trust store, and how to do the same in the web UI's data volume.

## What it does not protect against

- **A compromised control node.** It holds the Vault password (when you type it), the API token, your SSH
  private keys and the inventory. Anyone who controls it controls your servers. With the container image,
  that includes the Docker host and anyone who can read the data volume or run `docker exec`: the volume
  holds the SSH key and, unless you move it out with `USAS_VAULT_PASSWORD_FILE`, the Vault password.
- **The web UI's password, its passkeys, and the browser it is used from.** The UI has one user and no
  accounts; a passkey replaces the password rather than adding a second factor to it. Whoever knows the
  password (unless a passkey is required), or holds an unlocked device or password manager with one of its
  passkeys, or controls the browser or a browser extension while a session is open, can do everything the
  UI can, including adding a passkey of their own if they know the password. Published beyond `127.0.0.1`
  without "Require a passkey", the UI is exposed to password guessing (slowed down, not stopped) from
  wherever it can be reached; over plain HTTP its traffic, password included, could be read on the
  network, which is why it refuses other host names without HTTPS. The UI asks for no attestation, so it
  can't tell which kind of authenticator made a passkey: a passkey kept in a synced password manager is
  as safe as that account. Anyone who can `docker exec` into the container or write the data volume can
  delete `passkeys.json` and so turn the requirement off; that access is out of scope anyway (above).
- **Whoever holds the status token.** A dashboard needs the token in its own settings: Heimdall keeps it in
  plain text in its database and fills it into the tile's edit form, so anyone who can edit the dashboard's
  tiles or read its data can read it. With it they learn what the summary says (whether a run is going, how
  the last one ended, how many VMs run and how many servers wait to be installed, the version), nothing
  more, and they can't change anything. Over plain HTTP beyond `localhost` the token could be read on the
  network, which is why the UI refuses that. Revoke the token when a dashboard no longer needs it, and on any
  doubt replace it.
- **A compromised GitHub account.** Whoever controls `github_user` decides which keys can log in to your
  servers at the next provisioning, baseline run or `make keys`. Protect the account with two-factor
  authentication, or use `admin_extra_ssh_keys` and `ssh_keys_exclusive` to depend on it less.
- **What a guest says about itself.** A VM's guest agent reports whatever the VM's owner wants: another
  VM's address, Ubuntu when it isn't. The VMs page labels this data as reported by the guest and shows
  where each address came from; check them before you act on them.
- **What a server says about its packages.** The updates report shows what each server claims: a server
  whose root is hostile can report anything, or report nothing. Updating a server also means trusting its
  package sources (its mirrors and keys), which the playbook doesn't change.
- **Reboots and updates that go wrong.** An update can break an application in ways the health checks
  don't see, and a server may not come back from a reboot. Snapshots (a later release) aren't backups,
  and rolling back to one also undoes the security updates. On a VM whose disk unlocks with the TPM, a
  Secure Boot update means typing the passphrase at the console at the next boot; the playbook holds that
  reboot and says so.
- **Proxmox administrators and root on the Proxmox nodes.** They can read the seed ISO while a VM is being
  installed (with the disk passphrase, if the VM is encrypted), read and change VM disks, read a VM's vTPM
  state and memory (so disk encryption doesn't keep them out), and run commands inside the VMs through
  `qemu-guest-agent`.
- **Snapshots of the ISO storage.** The seed ISO exists in the ISO storage only during the install, but a
  storage that takes snapshots or backups on its own (a NAS behind NFS, for example) can keep a copy, with
  the password hash and any disk passphrase. Exclude the ISO folder from them, or keep `pve_iso_storage`
  on storage that doesn't.
- **A stolen key of the node user.** A node user takes root on the node out of the SSH key's reach, but not
  the ISO storage: whoever has its key can replace the installer ISO that new VMs boot (on a storage shared
  by the cluster, for every node), read a seed ISO during an install (with any disk passphrase in it), and
  fill the storage. Guard that key like the API token, and keep the user's storages to the ones it needs.
- **Trust on first use of a new VM's host key.** The control node accepts the host key it scans when a new
  VM first boots. An attacker who can impersonate the VM's IP address on your network at that moment could
  get their key trusted. Provision on a network you control.
- **Leftovers of a killed run.** A failed provisioning run deletes the seed ISO in a `rescue` step. A run
  killed outright (Ctrl-C, control node lost) can still leave the seed ISO (with the password hash, readable
  only by root and the user the playbooks log in as)
  in the node's ISO storage until you delete it ([installing.md](installing.md#troubleshooting)).
- **The installer's own log.** Ubuntu's installer keeps a root-only copy of its configuration, including
  the password hash, in `/var/log/installer/autoinstall-user-data`. The baseline's `common` role deletes it;
  until the baseline has run, it is on the server. On an encrypted VM the copy also holds the disk
  passphrase, so provisioning deletes it as soon as the VM first boots (it was only ever inside the
  encrypted volume).
- **Passwordless sudo during provisioning.** The installer gives the admin user passwordless `sudo` so the
  first baseline run can work unattended; the baseline then removes it (`admin_sudo_nopasswd: false`, the
  default), so an SSH key alone doesn't give root afterwards. Until that first baseline run finishes, anyone
  with one of the authorized keys has root. Setting `admin_sudo_nopasswd: true` keeps passwordless `sudo`.
- **Your inventory.** The project does what the inventory says. A wrong `pve_vmid`, `ssh_port`,
  `ufw_allow`, `ufw_ssh_sources` or `fail2ban_ignoreip` can create the wrong VM, open a port you didn't
  mean to, or lock you out.
- **The servers' applications and wider hardening.** The baseline covers access (SSH, firewall, brute
  force) and updates. It isn't a CIS or STIG benchmark, and it doesn't configure anything your applications
  need. Disk encryption, auditd, AppArmor reporting and kernel parameters are opt-in (above).
- **Between install and baseline.** A freshly installed server has key-only SSH but no firewall or
  Fail2Ban until the baseline has run; `make site` runs it straight away.
- **Upstream components.** Vulnerabilities in Ubuntu, Proxmox VE, OpenSSH, Ansible or its collections
  are fixed upstream; how the project tracks them is in [dependencies.md](dependencies.md).
- **Ubuntu's signing key itself.** The ISO is trusted because Ubuntu's CD image signing key signed its
  checksum. If Ubuntu replaces that key, the role stops until a release ships the new key; if the key were
  compromised, the check would not catch it.
