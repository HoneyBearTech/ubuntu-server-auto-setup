# External interfaces

Every way people and other software interact with ubuntu-server-auto-setup, what each one expects and
produces, and where it's specified. Its interface is the inventory you write and the commands you run;
the container image adds a web UI that writes the same inventory and runs the same commands. There is no
API for other programs. What should be reachable from where is described in the
[assurance case](assurance-case.md#attack-surface).

## Interfaces the project offers

| Interface | Who uses it | Specification |
| --- | --- | --- |
| Inventory variables | the operator, in `inventory/hosts.yml` | [below](#inventory-variables); every role's `roles/<role>/defaults/main.yml` |
| Vault variables | the operator, in `inventory/group_vars/all/vault.yml` | [below](#vault-variables); `inventory/vault.example.yml` |
| Inventory groups | the operator | [below](#inventory-groups) |
| Make targets | the operator | [below](#commands); `make help` |
| Playbooks | the operator, or other automation calling `ansible-playbook` | [below](#commands) |
| Tags | the operator (`ARGS="--tags ..."`) | [below](#commands) |
| The updates report (`<run id>.json` in `updates_report_dir`) | the operator, or other automation reading it | [below](#the-updates-report); [updates.md](updates.md#the-report) |
| The web UI (the USAS app and its JSON API on port 8080) | the operator, in a browser | [below](#the-web-ui); [web-ui.md](web-ui.md) |
| The status API (`GET /api/status`, off by default) | dashboards such as Heimdall, with the status token | [below](#the-status-api); [web-ui.md](web-ui.md#dashboards-heimdall) |
| The container image and its settings (environment variables, the `/data` volume) | the operator, with Docker | [below](#the-web-ui); [web-ui.md](web-ui.md#settings) |
| `start.ps1` | the operator, on Windows | [web-ui.md](web-ui.md#starting-it); `Get-Help .\start.ps1` |

### Inventory groups

| Group | Members | Connection |
| --- | --- | --- |
| `proxmox_nodes` | each Proxmox VE node the servers use; the inventory host name must equal the Proxmox node name | SSH as `pve_ssh_user` (`ansible_user: "{{ pve_ssh_user }}"`), `root` unless `prepare-node.yml` set up another user; `prepare-node.yml` itself logs in as `root` |
| `pve_iso_build` | made by `build-installer.yml` while it runs: the nodes that build the installer ISO | as in `proxmox_nodes` |
| `ubuntu_servers` | each server, new or existing | SSH as `admin_user` with sudo (`ansible_user: "{{ admin_user }}"`) |
| `other_servers` | servers this project didn't install, for the updates playbook only | SSH as their own `ansible_user`, with passwordless sudo or a sudo password of their own (never the Vault admin password unless `updates_allow_shared_become_password`) |
| `usas_updates_targets`, `usas_updates_ready` | made by `updates.yml` while it runs: the servers in the run, and those with something to install | as in `ubuntu_servers` and `other_servers` |

### Inventory variables

The variables below are the project's interface: names and meanings change only in a minor release before
1.0 (with an "Upgrading" note in the [changelog](../CHANGELOG.md)) and in a major release after it. The
full list, with every default and a comment for each, lives in each role's `defaults/main.yml`:
`roles/ubuntu_release`, `roles/installer_iso`, `roles/proxmox_vm`, `roles/proxmox_node_user`, `roles/common`, `roles/firewall`,
`roles/ssh_hardening`, `roles/fail2ban`, `roles/hardening` and `roles/updates`. Where a role's default and the example inventory differ, the
example shows a typical value.

**Shared by provisioning and the baseline**

| Variable | Default | Meaning |
| --- | --- | --- |
| `admin_user` | `sysadmin` | The admin account. |
| `admin_password` | `""` | Its password, normally `"{{ vault_admin_password }}"`. Required to provision; the baseline uses it only when it creates the user. |
| `github_user` | `""` | Whose keys from `https://github.com/<user>.keys` are authorized. Required (or `admin_extra_ssh_keys`). |
| `github_keys_url` | `https://github.com/<github_user>.keys` | Where the public keys are fetched from (the tests point it at a local file). |
| `admin_extra_ssh_keys` | `[]` | More public keys, one per list item. |
| `ssh_port` | `22` | The SSH port the baseline configures, opens in UFW and watches with Fail2Ban. |

**Ubuntu release and ISOs** (`ubuntu_release`, `installer_iso`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `ubuntu_release` | `"26.04"` | The release directory on the mirror. |
| `ubuntu_point_release` | `"26.04.1"` | The point release whose ISO is downloaded. |
| `ubuntu_iso_base_url` | `https://releases.ubuntu.com/{{ ubuntu_release }}` | Where the ISO, `SHA256SUMS` and its signature `SHA256SUMS.gpg` come from. A mirror must publish all three. |
| `pve_iso_storage` | `local` | The Proxmox storage for the installer and seed ISOs. It must allow ISO images (Datacenter → Storage → Content: ISO image) on each node that uses it. The installer ISO is built once in a storage the cluster shares (such as NFS), and on every node that uses a node-local one. |
| `pve_iso_dir` | `""` | The storage's folder on the node. Empty means it is asked from Proxmox (`pvesm path`, or for a user other than root the answer `prepare-node.yml` recorded), such as `/var/lib/vz/template/iso` for `local` or `/mnt/pve/<id>/template/iso` for NFS; set it only to override that. |
| `installer_work_dir` | `/var/tmp/ubuntu-server-auto-setup` | Scratch space on the node (ISO build, seed files). It must be a folder of `pve_ssh_user`'s, mode `0700`. |
| `pve_ssh_user` | `root` | The user the playbooks log in to the Proxmox nodes as (a lowercase Linux user name); the `proxmox_nodes`' `ansible_user` must match, which the roles check. Any other user than `root` needs `prepare-node.yml` first, on every node the servers use; the roles then read the ISO storage's folder from `/etc/ubuntu-server-auto-setup/iso-dirs.json` (`pvesm` runs for root only), check they can write to it, and check that `xorriso` and `gpgv` are installed instead of installing them. |
| `installer_grub_timeout` | `3` | Seconds the installer's GRUB menu waits. |
| `installer_iso_rebuild` | `false` | Rebuild the remastered ISO even if it exists. |
| `installer_iso_storages` | `[]` | Set by `build-installer.yml` on each node it chooses: the ISO storages that node builds the ISO in. Empty means `pve_iso_storage`; don't set it yourself. |
| `installer_signing_keyring` | `ubuntu-cdimage-keyring.gpg` | The binary OpenPGP keyring `SHA256SUMS.gpg` is checked against, on the control node: a file in `roles/installer_iso/files/` or an absolute path. The default holds only Ubuntu's CD image signing key. |
| `installer_signing_fingerprint` | `843938DF228D22F7B3742BC0D94AA3F0EFE21092` | The full fingerprint of the key that must have signed `SHA256SUMS` (Ubuntu CD Image Automatic Signing Key (2012), as published at [ubuntu.com](https://ubuntu.com/tutorials/how-to-verify-ubuntu)). Change it, with the keyring, only for a mirror that signs its own `SHA256SUMS`. |

**The node user** (`proxmox_node_user`, used by `playbooks/prepare-node.yml` only)

| Variable | Default | Meaning |
| --- | --- | --- |
| `proxmox_node_user_ssh_keys` | `[]` | Public keys that may log in as `pve_ssh_user`, one `authorized_keys` line each; they replace whatever was there. Required. |
| `proxmox_node_user_iso_storages` | `["{{ pve_iso_storage }}"]` | The ISO storages the user may write to; each gets an ACL entry on its ISO folder, and is recorded in `/etc/ubuntu-server-auto-setup/iso-dirs.json`. |
| `proxmox_node_user_home` | `/var/lib/<pve_ssh_user>` | The user's home directory. |
| `prepare_node_login_user` | `root` | Who `prepare-node.yml` logs in as (with `--become` if not root). |

**Proxmox API and VMs** (`proxmox_vm`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `pve_api_host`, `pve_api_port` | `""`, `8006` | The Proxmox API endpoint: any member of the cluster, which answers for all its nodes. One inventory uses one cluster. |
| `pve_api_user` | `ansible@pve` | The token's user. |
| `pve_api_token_id`, `pve_api_token_secret` | `""` | The token's name and secret, from the Vault. |
| `pve_validate_certs` | `true` | Verify the API's TLS certificate. |
| `pve_hide_secrets` | `true` | Hide the token and the password hash from task output; turn off only while debugging. |
| `pve_node` | `""` | The node the VM is created on (required): set it for the group as the default, and on a server to place that VM on another node of the same cluster. Checked against the cluster's nodes (case-sensitive) before anything is created. |
| `pve_api_timeout` | `60` | Seconds one Proxmox API request may take before it fails (the Proxmox modules' own default is 5, too short while disks are allocated). |
| `pve_ssh_host` | `"{{ pve_node }}"` | The inventory host, in `proxmox_nodes`, used to reach that node over SSH. |
| `pve_vmid` | `""` | The VM ID (required, fixed per host). |
| `pve_storage`, `pve_efidisk_format` | `local-lvm`, `raw` | Storage for the VM's disk and its 4 MB EFI disk, and the EFI disk's format (`qcow2` on directory storage). |
| `pve_tpmstate_storage` | `"{{ pve_storage }}"` | With `vm_disk_encryption`, the storage for the 4 MB vTPM state volume. On another storage than the disk, a copy of the disks' storage alone doesn't hold what unlocks them ([security.md](security.md#secure-boot-and-disk-encryption)). |
| `pve_bridge`, `pve_vlan` | `vmbr0`, `""` | Bridge and optional VLAN tag (1-4094) of the first network card, `net0`. |
| `pve_tags` | `[ubuntu, ansible]` | Proxmox tags on the VM. |
| `vm_cores`, `vm_sockets`, `vm_cpu_type` | `2`, `1`, `host` | CPU. |
| `vm_memory_mb`, `vm_disk_gb` | `2048`, `32` | Memory and disk size. |
| `vm_secure_boot` | `true` | Secure Boot: the EFI disk gets Microsoft's keys pre-enrolled (`pre-enrolled-keys=1`), so the VM boots only signed code (Ubuntu's shim, GRUB and kernel; the kernel then refuses unsigned modules). New VMs only; `false` gives an EFI disk without keys. |
| `vm_disk_encryption` | `false` | Install the root file system on LUKS2 (the `lvm` layout's volume group), with a vTPM (`tpmstate0`, TPM 2.0) that unlocks it at boot while the Secure Boot state (PCR 7) is the one measured at the VM's first boot; the passphrase stays enrolled as the recovery key. Needs `vm_secure_boot: true` and `autoinstall_storage_layout: lvm`. New VMs only. See [security.md](security.md#secure-boot-and-disk-encryption) for what it protects and what it doesn't. |
| `vm_disk_passphrase` | `""` | The LUKS passphrase and recovery key, normally `"{{ vault_disk_passphrase }}"`: 12 to 512 printable ASCII characters, no braces. Required with `vm_disk_encryption`; hidden from all output. |
| `vm_onboot` | `true` | Start the VM when the node boots. |
| `vm_install_timeout` | `3600` | Seconds to wait for the installer to power off. |
| `vm_install_poll_interval` | `20` | Seconds between checks of the VM's power state during the install. |
| `net_mode` | `static` | How `net0` gets its IPv4 address: `static` or `dhcp` ([networks](#networks)). |
| `ansible_host` | (required for `static`) | The static IPv4 address the installer configures. With `dhcp`, leave it out to have the address read from the guest agent, or set it to the address reserved for the server on the DHCP server. |
| `net_prefix`, `net_gateway` | `24`, `""` | Prefix length and gateway of a static `net0`. Exactly one card has the default route: `net_gateway`, a DHCP `net0`, or one card in `vm_extra_nics`. |
| `net_dns`, `net_search` | `[]`, `[]` | DNS servers and search domains (with DHCP, in addition to the ones DHCP gives). |
| `net_mac` | `""` | MAC address of `net0`, for example for a DHCP reservation. Empty uses one derived from the host name and VM ID. |
| `vm_nic_model` | `virtio` | Model of every card that doesn't set its own: `virtio`, `e1000`, `e1000e`, `vmxnet3` or `rtl8139`. |
| `vm_extra_nics` | `[]` | More network cards, `net1` and up ([networks](#networks)). |
| `net_dhcp_timeout` | `300` | Seconds to wait for the guest agent to report the address DHCP gave `net0`. |
| `admin_realname` | `Administrator` | The admin account's full name. |
| `autoinstall_locale`, `autoinstall_keyboard_layout` | `en_US.UTF-8`, `us` | Locale and keyboard. |
| `autoinstall_source_id` | `ubuntu-server` | `ubuntu-server` or `ubuntu-server-minimal`. |
| `autoinstall_storage_layout` | `lvm` | `lvm`, `direct` or `zfs`. |
| `autoinstall_updates` | `security` | Updates applied during the install: `security` or `all`. |
| `autoinstall_packages` | `[qemu-guest-agent]` | Extra packages installed by the installer (`qemu-guest-agent` is always added with `net_mode: dhcp`). |
| `provision_manage_known_hosts` | `true` | Replace the control node's `known_hosts` entry for the new VM's IP with its fresh host key. |
| `provision_known_hosts_file` | `~/.ssh/known_hosts` | The control node's `known_hosts` file that gets that entry. |

**Baseline: system and users** (`common`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `admin_shell` | `/bin/bash` | The admin account's shell. |
| `admin_sudo_nopasswd` | `false` | Passwordless sudo for the admin account. False removes the installer's passwordless sudo on the first baseline run; later runs need `ansible_become_password` (the example inventory and the web UI set it to the admin password). On Ubuntu 26.04 the playbooks become root with the classic sudo, `/usr/bin/sudo.ws`, since Ansible can't answer sudo-rs's password prompt; set `ansible_become_exe` to override. |
| `classic_sudo_needs_password` | `not admin_sudo_nopasswd` | Whether becoming root needs a password on the host. If it does and the host has sudo-rs but not the classic sudo, the playbook stops before changing anything. The baseline leaves it unset; the updates playbook sets it to whether `ansible_become_password` is set. |
| `ssh_keys_exclusive` | `true` | Remove authorized keys that aren't on GitHub or in `admin_extra_ssh_keys`. |
| `manage_hostname` | `true` | Set the hostname and `/etc/hosts` entry. Turn off where the platform owns the hostname. |
| `server_hostname`, `server_domain` | short inventory name, `""` | The hostname, and an optional domain for `/etc/hosts`. |
| `timezone` | `Etc/UTC` | The timezone. |
| `apt_upgrade` | `dist` | `dist`, `safe`, `full` or `"no"`. |
| `reboot_if_required` | `false` | Reboot when upgrades ask for it. |
| `common_packages` | `ca-certificates`, `curl`, `htop`, `unattended-upgrades`, `vim` | Packages installed on every server. |
| `unattended_upgrades_origins` | release, security and ESM security pockets | What unattended-upgrades installs. |
| `unattended_upgrades_auto_reboot`, `unattended_upgrades_reboot_time` | `false`, `"04:00"` | Automatic reboots after unattended upgrades. |
| `unattended_upgrades_remove_unused` | `true` | Remove unused dependencies. |

**Baseline: firewall** (`firewall`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `ufw_default_incoming`, `ufw_default_outgoing` | `deny`, `allow` | Default policies. |
| `ufw_logging` | `low` | UFW log level. |
| `ufw_ssh_limit` | `true` | Rate-limit SSH (`limit`) instead of a plain allow. |
| `ufw_ssh_sources` | `[any]` | Where SSH is allowed from. |
| `ufw_allow` | `[]` | Extra rules: `{port, proto (default tcp), from (default any), comment}`. |

**Baseline: SSH** (`ssh_hardening`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `ssh_permit_root_login` | `"no"` | `PermitRootLogin`. |
| `ssh_password_authentication` | `false` | `PasswordAuthentication` (and `AuthenticationMethods publickey` when off). |
| `ssh_max_auth_tries`, `ssh_login_grace_time` | `4`, `30` | Authentication limits (CIS: at most 4 tries). |
| `ssh_client_alive_interval`, `ssh_client_alive_count_max` | `300`, `2` | Dropping unresponsive sessions. |
| `ssh_x11_forwarding`, `ssh_allow_agent_forwarding`, `ssh_allow_tcp_forwarding` | `false`, `false`, `true` | Forwarding. |
| `ssh_allow_users` | `[]` | `AllowUsers`, if set (must include `admin_user`). |
| `ssh_known_hosts_file` | `~/.ssh/known_hosts` | The control node's `known_hosts` file that gets a `[host]:port` entry when `ssh_port` changes. |

**Baseline: Fail2Ban** (`fail2ban`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `fail2ban_bantime`, `fail2ban_findtime`, `fail2ban_maxretry` | `1h`, `10m`, `5` | Ban length, window and failures. |
| `fail2ban_bantime_increment`, `fail2ban_bantime_maxtime` | `true`, `1w` | Longer bans for repeat offenders, up to a maximum. |
| `fail2ban_ignoreip` | `[]` | Never banned (besides localhost). |
| `fail2ban_banaction` | `""` | Ban action; empty keeps Ubuntu's default (nftables). |
| `fail2ban_sshd_mode` | `normal` | The `sshd` filter mode: `normal`, `ddos`, `extra` or `aggressive`. |

**Baseline: extra hardening** (`hardening`; each part off by default, see [security.md](security.md#optional-hardening-and-notifications))

| Variable | Default | Meaning |
| --- | --- | --- |
| `hardening_sysctl_enabled` | `false` | Write `hardening_sysctl` and `hardening_sysctl_extra` to `/etc/sysctl.d/90-usas-hardening.conf` and apply them. |
| `hardening_sysctl` | kernel and network settings (see `roles/hardening/defaults/main.yml`) | The vetted set, as a mapping of sysctl names to values. |
| `hardening_sysctl_extra` | `{}` | Merged over `hardening_sysctl`; a key set to `null` is left out. |
| `hardening_auditd_enabled` | `false` | Install auditd with rules in `/etc/audit/rules.d/60-usas-hardening.rules`. |
| `hardening_auditd_immutable` | `false` | Lock the audit rules until the next reboot (`-e 2`). |
| `hardening_apparmor_enabled` | `false` | Keep AppArmor installed, enabled and running; report profiles in complain mode. |

**Baseline: notifications** (`common`, `fail2ban`; all off by default)

| Variable | Default | Meaning |
| --- | --- | --- |
| `unattended_upgrades_mail` | `""` | Mail unattended-upgrades' report here (needs a mail transfer agent). |
| `unattended_upgrades_mail_report` | `on-change` | `always`, `only-on-error` or `on-change`. |
| `mail_relay_enabled` | `false` | Install `msmtp-mta` as the server's `sendmail`, relaying through `mail_relay_host`. Refuses to replace another mail server. |
| `mail_relay_host`, `mail_relay_port`, `mail_relay_tls` | `""`, `587`, `starttls` | The smarthost, its port and `starttls`, `tls` (implicit, usually port 465) or `"off"`. |
| `mail_relay_from` | `""` | The sender address of everything the server mails. |
| `mail_relay_user`, `mail_relay_password` | `""` | Sign in to the relay; the password from the Vault (`"{{ vault_mail_relay_password }}"`). |
| `fail2ban_destemail`, `fail2ban_sender` | `""` | Mail every ban, with a whois report, to this address (Fail2Ban's `action_mw`); optional sender. |
| `fail2ban_webhook_url` | `""` | POST a message for every ban to this http(s) URL, normally from the Vault (`"{{ vault_fail2ban_webhook_url }}"`). |
| `fail2ban_webhook_format` | `text` | `text` (the message is the body, for ntfy) or `json` (`title`, `text`, `message` and `content`, for Slack, Mattermost, Gotify and Discord). |

**Updates** (`updates`, used by `playbooks/updates.yml` only; [updates.md](updates.md))

| Variable | Default | Meaning |
| --- | --- | --- |
| `updates_enabled` | `true` | `false` skips the server in every run. |
| `updates_scope` | `all` | `all` or `security` (only updates from a `-security` suite or ESM). |
| `updates_apt_upgrade` | `safe` | `safe` (upgrade and new dependencies, never removes) or `dist` (may remove; refused with `security`, excludes or `updates_kernel: false`). |
| `updates_exclude_packages` | `[]` | Package-name globs left alone in this run; never written to the server. |
| `updates_kernel`, `updates_kernel_packages` | `true`, the family's kernel globs | `false` adds the kernel packages to the excludes. |
| `updates_reboot` | `never` | `never` (report "reboot pending") or `if_required`. |
| `updates_reboot_window`, `updates_window` | `{}` (any time) | `{days: [sun], start: "02:00", duration: "3h", timezone: "Europe/Berlin"}`: when a reboot, or the update, may happen. |
| `updates_ignore_window` | `false` | Update and reboot outside the windows. |
| `updates_reboot_timeout`, `updates_post_reboot_delay` | `900`, `0` | Seconds to wait for a rebooted server, and after it answers. |
| `updates_boot_time_command` | `""` | The command whose output changes at each boot (default: the kernel's boot ID). |
| `updates_pcr7_packages` | `shim-signed`, the signed GRUB, `secureboot-db` | Updates that hold the reboot on a VM whose disk unlocks with the TPM. |
| `updates_restart_services` | `host` | `host` (the server's own needrestart setting), `list` or `auto`. |
| `updates_refresh_max_age` | `3600` | Seconds the package lists may be old before they are refreshed. |
| `updates_lock_wait` | `600` | Seconds to wait for a busy package manager before the server is `busy`. |
| `updates_task_timeout` | `3600` | Seconds an upgrade may take (it runs in the background on the server). |
| `updates_health_services`, `updates_health_ignore_services` | `[]` | Services that must run after the update, and globs of services whose stopping isn't a problem. |
| `updates_health_urls` | `[]` | `[{url, status}]`, called from the control node with the certificate checked. |
| `updates_health_timeout` | `300` | Seconds to wait for the server to finish starting. |
| `updates_allow_unsupported` | `false` | Try a release outside `updates_supported_releases`, within a known family. |
| `updates_allow_shared_become_password` | `false` | Allow the Vault admin password as the sudo password of a server in `other_servers`. |
| `updates_max_fail_percentage` | `0` | Share of started servers that may fail before no further one starts. |
| `updates_serial` | `25%` | The batch size (with `-e` only). |
| `updates_notify` | `on-change` | When to notify: `on-change`, `always` or `only-on-error`. |
| `updates_notify_mail` | `unattended_upgrades_mail` | Mail the run's summary here, through `mail_relay_host` (with `mail_relay_port`, `mail_relay_tls`, `mail_relay_from`, `mail_relay_user`, `mail_relay_password`) from the control node, with the relay's certificate checked. |
| `updates_webhook_url`, `updates_webhook_format` | `""`, `text` | POST the summary to this http(s) URL (normally from the Vault), as `text` or `json`, as for Fail2Ban. |
| `updates_report_dir` | `reports/updates` in the checkout | Where `<run id>.json` and `<run id>.txt` are written on the control node (`0600`, in a `0700` folder). |

### Networks

Every VM has a first network card, `net0`, and up to 31 more from `vm_extra_nics`. Each card gets a fixed
MAC address (`net_mac` or a card's `mac`, otherwise one derived from the host name, the VM ID and the card
number under Proxmox's prefix `BC:24:11`, so a VM created again keeps it), and the installed system matches
each card by that MAC address rather than by interface name. In the installed system's netplan
configuration, `net0` is `primary` and the others are named like their Proxmox cards (`net1`, `net2`, ...).

`net0` is static (`ansible_host`, `net_prefix`, `net_gateway`, `net_dns`, `net_search`) or, with
`net_mode: dhcp`, configured by DHCP. DHCP identifies the card by its MAC address (`dhcp-identifier: mac`), so
an address reserved for that MAC address on the DHCP server is the one both the installer and the installed
system get. Unless `ansible_host` is set, provisioning waits for the installed system's guest agent (the
`qemu-guest-agent` package, enabled on the VM) to report the address of the card with `net0`'s MAC address,
up to `net_dhcp_timeout` seconds, prints it, and uses it as `ansible_host` for the rest of the run. The
baseline playbook does the same when it runs on its own. The API token needs `VM.GuestAgent.Audit` on
Proxmox VE 9 or `VM.Monitor` on Proxmox VE 8 for that ([installing.md](installing.md#2-create-the-proxmox-api-token));
provisioning checks it before it creates such a VM, because a VM whose address can't be read after the install
never has its host key trusted and must be deleted and created again.
Only an IPv4 address that matches `provision_ipv4_pattern` and isn't link-local is used: the guest reports it,
so it is treated as untrusted. The new VM's host key is trusted for that address right after the install;
if DHCP later gives the server another address, SSH refuses it until its key is in `known_hosts` for the new
address, so reserving the address is the easier way.

Each item of `vm_extra_nics` is a mapping with these keys:

| Key | Default | Meaning |
| --- | --- | --- |
| `mode` | `static` | `static`, `dhcp`, or `none` (the card is attached and up, without an address). |
| `bridge` | `pve_bridge` | The Proxmox bridge (letters, digits, `.`, `_` or `-`, at most 15). |
| `vlan` | none | VLAN tag, 1-4094. |
| `model` | `vm_nic_model` | The card's model. |
| `mac` | derived | The card's MAC address (unicast). |
| `mtu` | the bridge's | MTU, 576-9216, set on the Proxmox card and in the guest; `virtio` cards only. |
| `address`, `prefix` | (required for `static`) | The card's IPv4 address and prefix length, 1-32. |
| `gateway` | none | A default route through this card (`static` only). Then set `net_gateway: ""` for the host. |

A `dhcp` card in `vm_extra_nics` takes neither the default route nor DNS servers from DHCP, and extra cards
are `optional`, so booting doesn't wait for them. There must be exactly one default route: a check stops
provisioning otherwise. IPv6 isn't configured yet (`dhcp6` is off on every card).

The firewall's rules aren't bound to an interface: SSH and every `ufw_allow` rule are open on every card,
VLANs included. To allow SSH from one network only, set `ufw_ssh_sources`, and use `from` in `ufw_allow`.

### Vault variables

| Variable | Used by | Meaning |
| --- | --- | --- |
| `vault_admin_password` | `admin_password` (and `ansible_become_password` if passwordless sudo is off) | The admin account's password. |
| `vault_pve_api_token_id` | `pve_api_token_id` | The token's name, without `user@realm!`. |
| `vault_pve_api_token_secret` | `pve_api_token_secret` | The token's secret. |
| `vault_mail_relay_password` (optional) | `mail_relay_password` | The mail relay's password. |
| `vault_fail2ban_webhook_url` (optional) | `fail2ban_webhook_url` | The ban webhook's URL, which usually holds a token. |
| `vault_disk_passphrase` (optional) | `vm_disk_passphrase` | The disk encryption passphrase, also the recovery key at the console. |
| `vault_become_password_<group>` (optional, any name you choose) | `ansible_become_password` of a group in `other_servers` | That group's own sudo password, for the updates playbook. |

### Commands

| Command | Playbook | Hosts | Effect |
| --- | --- | --- | --- |
| `make prepare-node` | `playbooks/prepare-node.yml` | `proxmox_nodes` | Optional, once, as root: create `pve_ssh_user` (no password, no sudo, its `authorized_keys`), install `xorriso`, `gpgv` and `acl`, hand it the work directory, add an ACL entry for it on each ISO storage's folder, and record those folders in `/etc/ubuntu-server-auto-setup/iso-dirs.json`. |
| `make build-installer` | `playbooks/build-installer.yml` | `ubuntu_servers` and `proxmox_nodes` (to plan, through the API), then `pve_iso_build` | Download, verify and remaster the Ubuntu ISO on the nodes the servers use: once per shared ISO storage, on each node for a node-local one (with no server in the run, on each node in it). Changes only the nodes' ISO storage and `xorriso`. |
| `make provision` | `playbooks/provision-vms.yml` | `ubuntu_servers` | Create and install each VM whose `pve_vmid` doesn't exist. Erases the new VM's disk only. |
| `make baseline` | `playbooks/baseline.yml` | `ubuntu_servers` | Apply the baseline. Changes users, SSH, the firewall, Fail2Ban, packages and update settings, and the optional hardening and notifications you turn on. |
| `make keys` | `playbooks/baseline.yml --tags ssh_keys` | `ubuntu_servers` | Sync the admin account's authorized keys only. |
| `make site` | `playbooks/site.yml` | all | The three playbooks in order. |
| `make updates-check` | `playbooks/updates.yml --check` | `ubuntu_servers` and `other_servers` | Report the pending updates, held, phased and excluded packages, the automatic-update setup and pending reboots of each server, to the screen and `updates_report_dir`. Changes no server (it refreshes the package lists). |
| `make updates` | `playbooks/updates.yml` | `ubuntu_servers` and `other_servers` | Install the updates and, with `updates_reboot: if_required`, reboot the servers that need it, then check their health; a batch at a time. Never changes a server's configuration. Fails if a server failed or couldn't be reached. |
| `make test-updates` | none | none | The updates playbook's integration tests (`OS=ubuntu-24.04` picks the release). |
| `make deps`, `make lint`, `make syntax` | none | none | Development and setup (see [CONTRIBUTING.md](../CONTRIBUTING.md)). |

`LIMIT=` becomes `--limit` (for `build-installer`, a node builds only if it is in the limit too), `ARGS=` is passed to `ansible-playbook` unchanged,
and `INVENTORY=` selects the inventory file. The baseline's roles are tagged `common`, `firewall`, `ssh`,
`fail2ban` and `hardening`, so `ARGS="--tags firewall"` runs one of them. Exit codes are `ansible-playbook`'s: 0 on success,
non-zero on any failed or unreachable host (for `updates`, busy, skipped and unsupported servers don't count). `--check --diff` is supported by the baseline and by `updates.yml` (where `--check` is the report-only mode) (provisioning
needs to really create the VM, so it isn't meaningful there).

### The updates report

`updates.yml` writes `<run id>.json` (and a text summary, `<run id>.txt`) to `updates_report_dir` on the
control node, mode `0600`. The run ID is the start time and four random hex digits (`20261026T020000Z-3fa9`).
The JSON object has `run` (`id`, `mode`: `check` or `apply`, `started`, `finished`, in UTC), `summary` (a count
per status) and `hosts`, one object per server with: `host`, `status` (`updated`, `nothing`, `pending`,
`busy`, `skipped`, `unsupported`, `unreachable` or `failed`), `reason`, `os` (`facts`, `hint`, `warning`),
`installed_by_usas`, `scope`, `apt_upgrade`, `window`, `pending` (each `name`, `current`, `candidate`,
`suites`, `security`, `new`), `security_count`, `planned`, `upgraded` (each `name`, `from`, `to`), `held`,
`excluded`, `deferred`, `removals`, `reboot` (`expected`, `required`, `packages`, `done`, `held`, `why`),
`restart_services`, `auto_updates`, `health` (`status`, `problems`), `snapshot`, `started` and `finished`.
Names and text in it come from the servers, after the checks described in [updates.md](updates.md#the-report);
new keys may be added in a minor release, and existing ones keep their meaning.

### The web UI

The image `honeybeartech/ubuntu-server-auto-setup` on Docker Hub, mirrored to `ghcr.io/honeybeartech/ubuntu-server-auto-setup` (tags `X.Y.Z`, `X.Y` and `latest`, for
`linux/amd64` and `linux/arm64`) runs the web UI on port 8080 as UID 10001, with its data in the volume
mounted at `/data`. Publish the port on `127.0.0.1` only.

- **Pages**: overview, Proxmox, SSH key, defaults, servers, hardening, VMs (a read-only list of the
  cluster's VMs), run, history, account. Each form
  field sets one of the [inventory variables](#inventory-variables) (or a [Vault variable](#vault-variables))
  and names it; defaults, help and accepted formats come from the roles. The HTTP paths are listed in
  [web-ui.md](web-ui.md#http-endpoints): page URLs, and the JSON API under `/api/` that the UI's own app
  uses. That API isn't a stable interface for other programs (use the inventory and `ansible-playbook`),
  except [the status API](#the-status-api).
- **Look**: a dark theme by default, light or the system's choice from the theme switch (kept in the
  `usas_theme` cookie).
- **Runs**: `build-installer`, `provision`, `baseline`, `site` and `keys`, with check mode, differences and
  a limit, the same as the [commands](#commands). Exit status and logs are kept in the run history.
- **Settings**: the environment variables `USAS_DATA_DIR`, `USAS_VAULT_PASSWORD_FILE`, `USAS_ALLOWED_HOSTS`,
  `USAS_SECURE_COOKIES`, `USAS_TRUSTED_PROXIES`, `USAS_SESSION_HOURS`, `USAS_PUBLIC_URL` (the address
  passkeys belong to) and `USAS_PORT` ([web-ui.md](web-ui.md#settings)).
- **Sign-in**: the password, or passkeys (WebAuthn) added on the account page, with "Require a passkey" to
  turn the password off ([web-ui.md](web-ui.md#passkeys)).
- **Files in `/data`**: `inventory/hosts.yml` and `inventory/group_vars/all/vault.yml` are an ordinary
  inventory and Vault (the CLI can use them with `--vault-password-file /data/.vault-pass`); the UI also
  keeps `ssh/`, `pve-ca.pem`, `ca-bundle.pem`, `auth.json`, `passkeys.json`, `status-token.json`, `webui.json`
  and `runs/` there
  ([web-ui.md](web-ui.md#what-is-stored-where)). Variables only the UI uses start with `webui_`
  (`webui_ufw_allow`, `webui_ufw_allow_host`, `webui_authorize_ui_key`).
- **Health**: `GET /healthz` answers `ok` without a sign-in; the image's `HEALTHCHECK` uses it.

### The status API

`GET /api/status` gives dashboards a short, read-only summary. It is off (404) until a token is made on
the account page, and takes the token only as `Authorization: Bearer usas_st_<43 characters>`. It is the
one stable interface of the web UI for other programs: `schema` goes up when a key changes meaning or goes
away; new keys can appear in any release, so ignore keys you don't know.

```json
{
  "schema": 1,
  "version": "0.6.0",
  "setup": {"done": 5, "total": 5},
  "running": {"playbook": "site", "check": false, "started": 1792750000},
  "last_run": {"playbook": "baseline", "check": false, "started": 1792740000, "status": "succeeded",
               "finished": 1792740600},
  "servers": {"total": 5, "pending": 1},
  "vms": {"running": 9, "total": 12},
  "fetched": 1792750030
}
```

| Key | Meaning |
| --- | --- |
| `schema` | The answer's version, `1`. |
| `version` | The USAS version (`dev` for images built from a checkout). |
| `setup` | The overview page's set-up steps: how many are done, of how many. |
| `running` | The run going now, or `null`: `playbook` (`build-installer`, `provision`, `baseline`, `site` or `keys`), `check` (check mode), `started` (Unix time). |
| `last_run` | The newest finished run, or `null`: the same keys plus `status` (`succeeded`, `failed`, `stopped` or `interrupted`) and `finished`. |
| `servers` | `total`: the servers in the inventory; `pending`: those whose VM ID isn't on the cluster yet (a provisioning run would create them), or `null` when the cluster can't be asked. |
| `vms` | The cluster's QEMU VMs (templates left out): `running` and `total`, or `null` until the Proxmox page has the API host and token, or when the cluster can't be listed. Counted at most once a minute. |
| `fetched` | When the answer was made (Unix time). |

It never holds host, node, cluster or VM names, addresses, VM IDs, run limits or logs, or any secret.
Other answers: `401` with `WWW-Authenticate: Bearer` (no, a malformed, a wrong, a replaced or a revoked
token), `404` (off), `429` with `Retry-After` (too many wrong tokens from that address, or more than 60 calls
a minute), `405` (any method but `GET`); each with `{"error": "..."}`. The host check and the HTTPS rule of
the pages apply. Cookies, sessions and CSRF tokens play no part in it.

## Interfaces the project uses

| System | From | Protocol and authentication | What for |
| --- | --- | --- | --- |
| Proxmox VE API (`https://<pve_api_host>:8006/api2/json`) | control node | HTTPS, API token (`PVEAPIToken=` header), certificate verified | List the cluster's nodes and each node's ISO storages (`/nodes`, `/nodes/<node>/storage`), list VMs, create, configure, start and watch them on their nodes; for a new server with `net_mode: dhcp`, check first that the token may ask its guest agent (`/access/permissions`); for servers with `net_mode: dhcp`, read `net0`'s MAC address from the VM's configuration and ask the guest agent's `network-get-interfaces` for its address (`VM.GuestAgent.Audit` on Proxmox VE 9, `VM.Monitor` on 8). A read whose connection drops (pveproxy restarting a worker) is retried up to 3 times, 5 seconds apart; any answer, an error included, is final. A VM creation whose connection drops is retried the same way, and the run carries on only with a VM of that ID that has the host's name and the description "Installed by ubuntu-server-auto-setup". |
| Proxmox VE API, read-only (the web UI's VMs page) | the web UI | HTTPS, the same API token, certificate verified as for the playbooks; 10-second timeout, 5 seconds per guest-agent call | `GET /version`, `/cluster/resources?type=vm`, `/nodes/<node>/qemu/<vmid>/config`, and the guest agent's `network-get-interfaces`, `get-osinfo` and `get-host-name` (`VM.GuestAgent.Audit` on Proxmox VE 9). The answers are cached in memory only; nothing is written to `/data`. |
| Proxmox VE nodes (each node the servers use) | control node | SSH as `pve_ssh_user` (root by default), key, host key checked (strictly, in a check before anything is built or created) | Install `xorriso` and `gpgv` (root only), build ISOs and seed ISOs, delete seed files. As root once, `prepare-node.yml` sets up another user. |
| `releases.ubuntu.com` (or `ubuntu_iso_base_url`) | Proxmox node | HTTPS, no authentication; `SHA256SUMS` is checked against Ubuntu's signature. `SHA256SUMS` and `SHA256SUMS.gpg` are fetched with a 30-second timeout and up to 3 retries when the connection fails; any answer from the server, an error included, is final | The Ubuntu ISO, `SHA256SUMS` and `SHA256SUMS.gpg`. |
| `github.com/<github_user>.keys` | control node | HTTPS, no authentication | The public SSH keys to authorize. |
| Servers | control node | SSH as `admin_user`, key; sudo | The baseline. |
| Ubuntu and Debian archives | servers, Proxmox nodes | APT over HTTP(S) with Ubuntu's/Debian's signed repository metadata | Packages and updates. |
| PyPI, Ansible Galaxy | control node (`make deps`), image build | HTTPS; the image installs hash-pinned packages | Ansible, the Python packages and the collections. |
| Docker Hub (`docker.io`), or its mirror GitHub Container Registry (`ghcr.io`) | Docker on the operator's machine | HTTPS; the image is signed (cosign) in both | The container image. |
| Docker Hub API | the release workflow | HTTPS, an access token held in the `release` environment (version tags only) | Pushing the image and its description. |
| The browser | the operator | HTTP on `127.0.0.1` (or HTTPS through a proxy), the password or a passkey (WebAuthn), session cookie | The web UI. |
