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
| The web UI (the USAS app and its JSON API on port 8080) | the operator, in a browser | [below](#the-web-ui); [web-ui.md](web-ui.md) |
| The container image and its settings (environment variables, the `/data` volume) | the operator, with Docker | [below](#the-web-ui); [web-ui.md](web-ui.md#settings) |
| `start.ps1` | the operator, on Windows | [web-ui.md](web-ui.md#starting-it); `Get-Help .\start.ps1` |

### Inventory groups

| Group | Members | Connection |
| --- | --- | --- |
| `proxmox_nodes` | each Proxmox VE node the servers use; the inventory host name must equal the Proxmox node name | SSH as `root` (`ansible_user: root`) |
| `pve_iso_build` | made by `build-installer.yml` while it runs: the nodes that build the installer ISO | as in `proxmox_nodes` |
| `ubuntu_servers` | each server, new or existing | SSH as `admin_user` with sudo (`ansible_user: "{{ admin_user }}"`) |

### Inventory variables

The variables below are the project's interface: names and meanings change only in a minor release before
1.0 (with an "Upgrading" note in the [changelog](../CHANGELOG.md)) and in a major release after it. The
full list, with every default and a comment for each, lives in each role's `defaults/main.yml`:
`roles/ubuntu_release`, `roles/installer_iso`, `roles/proxmox_vm`, `roles/common`, `roles/firewall`,
`roles/ssh_hardening`, `roles/fail2ban` and `roles/hardening`. Where a role's default and the example inventory differ, the
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
| `pve_iso_dir` | `""` | The storage's folder on the node. Empty means it is asked from Proxmox (`pvesm path`), such as `/var/lib/vz/template/iso` for `local` or `/mnt/pve/<id>/template/iso` for NFS; set it only to override that. |
| `installer_work_dir` | `/var/tmp/ubuntu-server-auto-setup` | Scratch space on the node (ISO build, seed files). |
| `installer_grub_timeout` | `3` | Seconds the installer's GRUB menu waits. |
| `installer_iso_rebuild` | `false` | Rebuild the remastered ISO even if it exists. |
| `installer_iso_storages` | `[]` | Set by `build-installer.yml` on each node it chooses: the ISO storages that node builds the ISO in. Empty means `pve_iso_storage`; don't set it yourself. |
| `installer_signing_keyring` | `ubuntu-cdimage-keyring.gpg` | The binary OpenPGP keyring `SHA256SUMS.gpg` is checked against, on the control node: a file in `roles/installer_iso/files/` or an absolute path. The default holds only Ubuntu's CD image signing key. |
| `installer_signing_fingerprint` | `843938DF228D22F7B3742BC0D94AA3F0EFE21092` | The full fingerprint of the key that must have signed `SHA256SUMS` (Ubuntu CD Image Automatic Signing Key (2012), as published at [ubuntu.com](https://ubuntu.com/tutorials/how-to-verify-ubuntu)). Change it, with the keyring, only for a mirror that signs its own `SHA256SUMS`. |

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
| `pve_storage`, `pve_efidisk_format` | `local-lvm`, `raw` | Disk storage and the EFI disk format (`qcow2` on directory storage). |
| `pve_bridge`, `pve_vlan` | `vmbr0`, `""` | Network bridge and optional VLAN tag. |
| `pve_tags` | `[ubuntu, ansible]` | Proxmox tags on the VM. |
| `vm_cores`, `vm_sockets`, `vm_cpu_type` | `2`, `1`, `host` | CPU. |
| `vm_memory_mb`, `vm_disk_gb` | `2048`, `32` | Memory and disk size. |
| `vm_secure_boot` | `false` | Pre-enroll Secure Boot keys. |
| `vm_onboot` | `true` | Start the VM when the node boots. |
| `vm_install_timeout` | `3600` | Seconds to wait for the installer to power off. |
| `vm_install_poll_interval` | `20` | Seconds between checks of the VM's power state during the install. |
| `ansible_host` | (required) | The static IPv4 address the installer configures. |
| `net_prefix`, `net_gateway` | `24`, `""` | Prefix length and gateway (gateway required). |
| `net_dns`, `net_search` | `[]`, `[]` | DNS servers and search domains. |
| `net_interface_match` | `en*` | Which interface gets the static address. |
| `admin_realname` | `Administrator` | The admin account's full name. |
| `autoinstall_locale`, `autoinstall_keyboard_layout` | `en_US.UTF-8`, `us` | Locale and keyboard. |
| `autoinstall_source_id` | `ubuntu-server` | `ubuntu-server` or `ubuntu-server-minimal`. |
| `autoinstall_storage_layout` | `lvm` | `lvm`, `direct` or `zfs`. |
| `autoinstall_updates` | `security` | Updates applied during the install: `security` or `all`. |
| `autoinstall_packages` | `[qemu-guest-agent]` | Extra packages installed by the installer. |
| `provision_manage_known_hosts` | `true` | Replace the control node's `known_hosts` entry for the new VM's IP with its fresh host key. |
| `provision_known_hosts_file` | `~/.ssh/known_hosts` | The control node's `known_hosts` file that gets that entry. |

**Baseline: system and users** (`common`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `admin_shell` | `/bin/bash` | The admin account's shell. |
| `admin_sudo_nopasswd` | `false` | Passwordless sudo for the admin account. False removes the installer's passwordless sudo on the first baseline run; later runs need `ansible_become_password` (the example inventory and the web UI set it to the admin password). On Ubuntu 26.04 the playbooks become root with the classic sudo, `/usr/bin/sudo.ws`, since Ansible can't answer sudo-rs's password prompt; set `ansible_become_exe` to override. |
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

### Vault variables

| Variable | Used by | Meaning |
| --- | --- | --- |
| `vault_admin_password` | `admin_password` (and `ansible_become_password` if passwordless sudo is off) | The admin account's password. |
| `vault_pve_api_token_id` | `pve_api_token_id` | The token's name, without `user@realm!`. |
| `vault_pve_api_token_secret` | `pve_api_token_secret` | The token's secret. |
| `vault_mail_relay_password` (optional) | `mail_relay_password` | The mail relay's password. |
| `vault_fail2ban_webhook_url` (optional) | `fail2ban_webhook_url` | The ban webhook's URL, which usually holds a token. |

### Commands

| Command | Playbook | Hosts | Effect |
| --- | --- | --- | --- |
| `make build-installer` | `playbooks/build-installer.yml` | `ubuntu_servers` and `proxmox_nodes` (to plan, through the API), then `pve_iso_build` | Download, verify and remaster the Ubuntu ISO on the nodes the servers use: once per shared ISO storage, on each node for a node-local one (with no server in the run, on each node in it). Changes only the nodes' ISO storage and `xorriso`. |
| `make provision` | `playbooks/provision-vms.yml` | `ubuntu_servers` | Create and install each VM whose `pve_vmid` doesn't exist. Erases the new VM's disk only. |
| `make baseline` | `playbooks/baseline.yml` | `ubuntu_servers` | Apply the baseline. Changes users, SSH, the firewall, Fail2Ban, packages and update settings, and the optional hardening and notifications you turn on. |
| `make keys` | `playbooks/baseline.yml --tags ssh_keys` | `ubuntu_servers` | Sync the admin account's authorized keys only. |
| `make site` | `playbooks/site.yml` | all | The three playbooks in order. |
| `make deps`, `make lint`, `make syntax` | none | none | Development and setup (see [CONTRIBUTING.md](../CONTRIBUTING.md)). |

`LIMIT=` becomes `--limit` (for `build-installer`, a node builds only if it is in the limit too), `ARGS=` is passed to `ansible-playbook` unchanged,
and `INVENTORY=` selects the inventory file. The baseline's roles are tagged `common`, `firewall`, `ssh`,
`fail2ban` and `hardening`, so `ARGS="--tags firewall"` runs one of them. Exit codes are `ansible-playbook`'s: 0 on success,
non-zero on any failed or unreachable host. `--check --diff` is supported by the baseline (provisioning
needs to really create the VM, so it isn't meaningful there).

### The web UI

The image `honeybeartech/ubuntu-server-auto-setup` on Docker Hub, mirrored to `ghcr.io/honeybeartech/ubuntu-server-auto-setup` (tags `X.Y.Z`, `X.Y` and `latest`, for
`linux/amd64` and `linux/arm64`) runs the web UI on port 8080 as UID 10001, with its data in the volume
mounted at `/data`. Publish the port on `127.0.0.1` only.

- **Pages**: overview, Proxmox, SSH key, defaults, servers, hardening, VMs (a read-only list of the
  cluster's VMs), run, history, account. Each form
  field sets one of the [inventory variables](#inventory-variables) (or a [Vault variable](#vault-variables))
  and names it; defaults, help and accepted formats come from the roles. The HTTP paths are listed in
  [web-ui.md](web-ui.md#http-endpoints): page URLs, and the JSON API under `/api/` that the UI's own app
  uses. That API isn't a stable interface for other programs; use the inventory and `ansible-playbook`.
- **Look**: a dark theme by default, light or the system's choice from the theme switch (kept in the
  `usas_theme` cookie).
- **Runs**: `build-installer`, `provision`, `baseline`, `site` and `keys`, with check mode, differences and
  a limit, the same as the [commands](#commands). Exit status and logs are kept in the run history.
- **Settings**: the environment variables `USAS_DATA_DIR`, `USAS_VAULT_PASSWORD_FILE`, `USAS_ALLOWED_HOSTS`,
  `USAS_SECURE_COOKIES`, `USAS_TRUSTED_PROXIES`, `USAS_SESSION_HOURS` and `USAS_PORT`
  ([web-ui.md](web-ui.md#settings)).
- **Files in `/data`**: `inventory/hosts.yml` and `inventory/group_vars/all/vault.yml` are an ordinary
  inventory and Vault (the CLI can use them with `--vault-password-file /data/.vault-pass`); the UI also
  keeps `ssh/`, `pve-ca.pem`, `ca-bundle.pem`, `auth.json` and `runs/` there
  ([web-ui.md](web-ui.md#what-is-stored-where)). Variables only the UI uses start with `webui_`
  (`webui_ufw_allow`, `webui_ufw_allow_host`, `webui_authorize_ui_key`).
- **Health**: `GET /healthz` answers `ok` without a sign-in; the image's `HEALTHCHECK` uses it.

## Interfaces the project uses

| System | From | Protocol and authentication | What for |
| --- | --- | --- | --- |
| Proxmox VE API (`https://<pve_api_host>:8006/api2/json`) | control node | HTTPS, API token (`PVEAPIToken=` header), certificate verified | List the cluster's nodes and each node's ISO storages (`/nodes`, `/nodes/<node>/storage`), list VMs, create, configure, start and watch them on their nodes. |
| Proxmox VE API, read-only (the web UI's VMs page) | the web UI | HTTPS, the same API token, certificate verified as for the playbooks; 10-second timeout, 5 seconds per guest-agent call | `GET /version`, `/cluster/resources?type=vm`, `/nodes/<node>/qemu/<vmid>/config`, and the guest agent's `network-get-interfaces`, `get-osinfo` and `get-host-name` (`VM.GuestAgent.Audit` on Proxmox VE 9). The answers are cached in memory only; nothing is written to `/data`. |
| Proxmox VE nodes (each node the servers use) | control node | SSH as root, key, host key checked (strictly, in a check before anything is built or created) | Install `xorriso` and `gpgv`, build ISOs and seed ISOs, delete seed files. |
| `releases.ubuntu.com` (or `ubuntu_iso_base_url`) | Proxmox node | HTTPS, no authentication; `SHA256SUMS` is checked against Ubuntu's signature | The Ubuntu ISO, `SHA256SUMS` and `SHA256SUMS.gpg`. |
| `github.com/<github_user>.keys` | control node | HTTPS, no authentication | The public SSH keys to authorize. |
| Servers | control node | SSH as `admin_user`, key; sudo | The baseline. |
| Ubuntu and Debian archives | servers, Proxmox nodes | APT over HTTP(S) with Ubuntu's/Debian's signed repository metadata | Packages and updates. |
| PyPI, Ansible Galaxy | control node (`make deps`), image build | HTTPS; the image installs hash-pinned packages | Ansible, the Python packages and the collections. |
| Docker Hub (`docker.io`), or its mirror GitHub Container Registry (`ghcr.io`) | Docker on the operator's machine | HTTPS; the image is signed (cosign) in both | The container image. |
| Docker Hub API | the release workflow | HTTPS, an access token held in the `release` environment (version tags only) | Pushing the image and its description. |
| The browser | the operator | HTTP on `127.0.0.1` (or HTTPS through a proxy), session cookie | The web UI. |
