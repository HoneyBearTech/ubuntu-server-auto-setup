# Architecture

ubuntu-server-auto-setup is a set of Ansible playbooks and roles run from one machine, the control node.
It has no server, daemon, database or agent of its own: each run reads the inventory and the Vault, talks
to Proxmox and the servers, and exits. The security reasoning behind this design is in the
[assurance case](assurance-case.md); the interfaces it uses are listed in [interfaces.md](interfaces.md).

## Components

```text
 control node (you)                      Proxmox VE node(s)                    new / existing servers
 ------------------                      ------------------                    ----------------------
 inventory/hosts.yml        SSH (root)   ISO storage                            Ubuntu 26.04 (or 24.04)
 Vault (encrypted)  ───────────────────▶ ubuntu-26.04.1-live-server-amd64.iso   admin user + GitHub keys
 ansible-playbook                        ...-autoinstall.iso (remastered)        sshd drop-in, UFW,
   │                                     seed-<host>.iso (temporary)             Fail2Ban, unattended
   │  HTTPS + API token                  VM <pve_vmid> (UEFI, q35, VirtIO)       upgrades, qemu-guest-agent
   ├────────────────────────────────────▶ Proxmox API                                 ▲
   │                                                                                  │ SSH (admin user, key)
   ├──────────────────────────────────────────────────────────────────────────────────┘
   │  HTTPS
   ├──▶ github.com/<github_user>.keys     (public SSH keys)
   └──▶ releases.ubuntu.com (from the Proxmox node: ISO + SHA256SUMS)
```

| Part | What it is | Where |
| --- | --- | --- |
| Inventory | Hosts, groups and variables: which Proxmox nodes, which servers, their IPs, VM IDs and sizes, and the baseline settings. | `inventory/hosts.yml` (gitignored; example in `inventory/hosts.example.yml`) |
| Vault | The admin password and the Proxmox API token, encrypted with Ansible Vault. | `inventory/group_vars/all/vault.yml` (gitignored; example in `inventory/vault.example.yml`) |
| Playbooks | `build-installer.yml`, `provision-vms.yml`, `baseline.yml`, and `site.yml` that imports all three. | `playbooks/` |
| `ubuntu_release` | Shared defaults only: the Ubuntu release, ISO names and URLs, and the ISO storage location. | `roles/ubuntu_release/` |
| `installer_iso` | Downloads and verifies the Ubuntu ISO on a Proxmox node and remasters it for hands-off installs. | `roles/installer_iso/` |
| `proxmox_vm` | Creates one VM per host through the Proxmox API, with a per-host seed ISO, and waits for the install. | `roles/proxmox_vm/` |
| `common` | Packages and upgrades, hostname, timezone, the admin user, sudo, GitHub keys, unattended upgrades, the guest agent. | `roles/common/` |
| `firewall` | UFW: default policies, SSH (rate-limited), extra allow rules. | `roles/firewall/` |
| `ssh_hardening` | The sshd drop-in, the optional port change and the switch from `ssh.socket`. | `roles/ssh_hardening/` |
| `fail2ban` | The `sshd` jail. | `roles/fail2ban/` |
| `Makefile` | Short commands for the playbooks (`make site`, `make provision`, ...), with `LIMIT=` and `ARGS=`. | `Makefile` |

## How a server is built

### 1. The installer ISO (`build-installer.yml`, on `proxmox_nodes`, as root over SSH)

1. Install `xorriso` on the node.
2. Fetch `SHA256SUMS` from `releases.ubuntu.com` over HTTPS and pick the line for
   `ubuntu-26.04.1-live-server-amd64.iso`; stop if it isn't there (a newer point release has replaced it).
3. Download the ISO into the node's ISO storage, verifying that checksum (`get_url` with `checksum`).
4. If the remastered ISO doesn't exist yet (or `installer_iso_rebuild` is set): extract `boot/grub/grub.cfg`,
   add the `autoinstall` kernel argument to the installer entries and shorten the GRUB timeout, check that
   the argument is really there, and write a new ISO with `xorriso`, replaying the original boot records
   (BIOS and UEFI). Without the `autoinstall` argument, Ubuntu's installer stops and asks before it wipes a
   disk, even when it has an autoinstall configuration.

### 2. The VM (`provision-vms.yml`, on `ubuntu_servers`, from the control node)

For each host, after checking that the required variables are set:

1. List the cluster's VMs through the API; if `pve_vmid` exists anywhere, report it and **skip the host**.
2. Fetch the public keys from `https://github.com/<github_user>.keys`, add `admin_extra_ssh_keys`, keep
   only lines that look like public keys, and stop if none are left.
3. Hash the admin password with SHA-512 crypt on the control node.
4. On the node, write the autoinstall `user-data` and `meta-data` into a `0700` directory under
   `/var/tmp/ubuntu-server-auto-setup/` and pack them into `seed-<host>.iso` (volume label `CIDATA`, the
   cloud-init NoCloud convention), mode `0600`. The `user-data` sets the hostname, the static network
   configuration, the disk layout, the admin user with the password hash, the authorized keys, password SSH
   off, the packages (`qemu-guest-agent`), security updates during install, passwordless sudo for the admin
   user, and `shutdown: poweroff`.
5. Create the VM through the API (`community.proxmox.proxmox_kvm`): OVMF (UEFI) with an EFI disk, q35,
   VirtIO SCSI single with one disk, a VirtIO NIC on `pve_bridge` (optionally tagged), the guest agent
   enabled, the installer ISO and the seed ISO as CD-ROMs, and the boot order disk then installer.
6. Start the VM. The installer finds the seed ISO, installs without asking, and powers the VM off. The
   playbook polls the VM's status until it is stopped (up to `vm_install_timeout`).
7. Remove both CD-ROMs, set the boot order to the disk only, delete the seed ISO and its directory on the
   node, and start the VM.
8. Wait for SSH on port 22, replace the control node's `known_hosts` entry for the IP with the new host
   key (scanned once, trust on first use), and wait until Ansible can log in as the admin user (cloud-init
   finishes creating the user and keys on first boot).

### 3. The baseline (`baseline.yml`, on `ubuntu_servers`, as the admin user with sudo)

1. If `ssh_port` isn't 22, probe it; connect on whichever of `ssh_port` and 22 answers, so the same
   inventory works before and after the port change.
2. `common`: wait for cloud-init, run the upgrade (`apt_upgrade`), install base packages, set the hostname,
   `/etc/hosts` and timezone, create or update the admin user (the password only when the user is
   created), set passwordless sudo (or remove it), install the GitHub keys (exclusive by default, refusing
   to write an empty list), configure unattended upgrades, and reboot or report a pending reboot.
3. `firewall`: allow (rate-limit) SSH on `ssh_port` from `ufw_ssh_sources` and add `ufw_allow` rules
   **before** enabling UFW, so the running session survives; then set the default policies and enable it.
4. `ssh_hardening`: refuse to continue if `ssh_allow_users` would lock out the admin user; install
   `/etc/ssh/sshd_config.d/00-hardening.conf` (validated with `sshd -t`), and for a custom port enable
   `ssh.service` and disable `ssh.socket`; restart sshd; if the port changed, add a `known_hosts` entry for
   the new port from the host key read over the existing connection, reconnect on the new port, and only
   then remove the port 22 firewall rule.
5. `fail2ban`: write `/etc/fail2ban/jail.local` with the `sshd` jail on the systemd journal, on `ssh_port`,
   with incremental bans and `fail2ban_ignoreip`.

`site.yml` runs the three playbooks in that order. `make keys` runs only the `ssh_keys` tasks of the
baseline.

## Key properties

- **Idempotent and safe to rerun.** Existing VMs are never touched by provisioning; the ISO is downloaded
  and built once; a second baseline run reports no changes.
- **Ordered to avoid lockouts.** The firewall opens the SSH port before it is enabled; the new SSH port is
  opened and verified before port 22 is closed; the key sync refuses to empty `authorized_keys`; the sshd
  drop-in is validated before it is installed.
- **Secrets stay on the control node and in Vault.** The password reaches the node only as a hash, in
  files readable by root only, deleted at the end of provisioning. Tasks that handle the token or the hash
  are hidden from the output (`pve_hide_secrets`, `no_log`).
- **No inbound network exposure during install.** The installer reads its configuration from a local
  CD-ROM image, not from a network datasource.
- **Works on servers it didn't install.** The baseline only assumes Ubuntu 24.04 or 26.04 and an account
  that can `sudo`.

## Technology

| Choice | Why |
| --- | --- |
| Ansible (ansible-core 2.20) | Agentless over SSH, idempotent modules, and Ansible Vault for the few secrets. No controller to run. |
| Ubuntu autoinstall (Subiquity) with a NoCloud seed ISO | Ubuntu's own unattended installer; a local seed needs no web server and exposes nothing on the network. |
| Remastering with `xorriso` | Adds one kernel argument while keeping Ubuntu's signed boot chain and boot records; no custom kernel or initrd. |
| `community.proxmox` and the Proxmox API with a token | A token with a narrow role instead of root credentials for VM management; the `uri` module covers what the collection doesn't (start, status, detach). |
| UFW, Fail2Ban, OpenSSH drop-ins, unattended-upgrades | The tools Ubuntu ships and documents, so the result is ordinary, inspectable Ubuntu configuration. |
| GitHub `.keys` endpoint | One place to manage the keys for every server; removing a key there removes it everywhere at the next sync. |
