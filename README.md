# ubuntu-server-auto-setup

[![CI](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/actions/workflows/checks.yml/badge.svg)](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/actions/workflows/checks.yml)
[![CodeQL](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/actions/workflows/codeql.yml/badge.svg)](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/HoneyBearTech/ubuntu-server-auto-setup/badge)](https://scorecard.dev/viewer/?uri=github.com/HoneyBearTech/ubuntu-server-auto-setup)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15740/badge)](https://www.bestpractices.dev/projects/15740)
[![OpenSSF Baseline](https://www.bestpractices.dev/projects/15740/baseline)](https://www.bestpractices.dev/projects/15740)
[![Fuzzing](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/actions/workflows/fuzz.yml/badge.svg)](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/actions/workflows/fuzz.yml)
[![Installer ISO](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/actions/workflows/installer-iso.yml/badge.svg)](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/actions/workflows/installer-iso.yml)
[![Release](https://img.shields.io/github/v/release/HoneyBearTech/ubuntu-server-auto-setup?sort=semver)](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/latest)
[![Docker Hub](https://img.shields.io/docker/v/honeybeartech/ubuntu-server-auto-setup?sort=semver&logo=docker&logoColor=white&label=Docker%20Hub)](https://hub.docker.com/r/honeybeartech/ubuntu-server-auto-setup)
[![Image size](https://img.shields.io/docker/image-size/honeybeartech/ubuntu-server-auto-setup?sort=semver&logo=docker&logoColor=white&label=image%20size)](https://hub.docker.com/r/honeybeartech/ubuntu-server-auto-setup/tags)
[![Docker pulls](https://img.shields.io/docker/pulls/honeybeartech/ubuntu-server-auto-setup?logo=docker&logoColor=white)](https://hub.docker.com/r/honeybeartech/ubuntu-server-auto-setup)
[![GHCR mirror](https://img.shields.io/badge/mirror-ghcr.io-2496ED?logo=github&logoColor=white)](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/pkgs/container/ubuntu-server-auto-setup)
[![Platforms](https://img.shields.io/badge/runs_on-Windows_%7C_Linux_%7C_macOS-2496ED?logo=docker&logoColor=white)](docs/web-ui.md)
[![License: MIT](https://img.shields.io/github/license/HoneyBearTech/ubuntu-server-auto-setup)](LICENSE)
[![Ubuntu 26.04 LTS](https://img.shields.io/badge/Ubuntu-26.04_LTS-E95420?logo=ubuntu&logoColor=white)](https://releases.ubuntu.com/26.04/)
[![Proxmox VE](https://img.shields.io/badge/Proxmox_VE-API-E57000?logo=proxmox&logoColor=white)](https://pve.proxmox.com/pve-docs/api-viewer/)
[![ansible-core 2.20](https://img.shields.io/badge/ansible--core-2.20.7%2B-1A1918?logo=ansible&logoColor=white)](https://docs.ansible.com/)

Ansible playbooks that install Ubuntu Server 26.04 on fresh Proxmox VE VMs with no hands on the keyboard,
load your GitHub SSH keys, and apply a hardened baseline (UFW, Fail2Ban, SSH hardening, unattended upgrades).
Run them from a checkout with `make`, or from a container image with a small web UI that lays out every
option and runs the playbooks for you, on Windows, Linux or macOS.

> [!WARNING]
> **Provisioning creates VMs, and the installer wipes the target VM's disk.** `make provision` only
> touches VM IDs (`pve_vmid`) that don't exist yet, but a wrong ID or storage in your inventory still means
> a new VM and a freshly erased disk.
>
> **The baseline changes SSH and firewall settings, and can lock you out** if your inventory variables are
> wrong (the SSH port, the admin user, the GitHub user whose keys are installed, the firewall rules).
>
> Run against one host first with `LIMIT=<host>` (in the web UI: Limit), keep a console (the Proxmox web
> console) open, and try `ARGS="--check --diff"` (check mode) before a real run.

## Documentation

- [Quick start](docs/quick-start.md), [installing](docs/installing.md) (including the Proxmox API token
  and its privileges), [upgrading and backups](docs/upgrading.md) and
  [verifying releases](docs/verifying-releases.md). The [documentation index](docs/README.md) lists
  everything.
- [The web UI and the container image](docs/web-ui.md): starting it on Windows, Linux or macOS, what it
  stores, and how it is secured; its [accessibility](docs/accessibility.md).
- [Architecture](docs/architecture.md): the playbooks and roles, and how a VM goes from nothing to a
  hardened server.
- [Security requirements](docs/security.md): what this project does and doesn't protect against, and the
  [assurance case](docs/assurance-case.md) behind it.
- [External interfaces](docs/interfaces.md): the inventory variables, the commands, and every system
  the playbooks talk to; [dependencies and vulnerability management](docs/dependencies.md).
- [Testing](docs/testing.md): the integration tests that run every playbook on every pull request, and how
  to run them yourself.
- [Roadmap](docs/roadmap.md): where the project is going, and what it won't do.
- [Support](SUPPORT.md): which versions get fixes, and for how long. [Changelog](CHANGELOG.md).
- [Contributing](CONTRIBUTING.md), [Code of Conduct](CODE_OF_CONDUCT.md), [Governance](GOVERNANCE.md) and
  [reporting a vulnerability](SECURITY.md).

## What it does

Three playbooks, run from your own machine (the "control node"), plus `playbooks/site.yml` that runs all
three in order:

1. **`playbooks/build-installer.yml`** runs on your Proxmox node(s) (inventory group `proxmox_nodes`) over
   SSH as root. It downloads the official `ubuntu-26.04.1-live-server-amd64.iso`, checks it against
   Ubuntu's published `SHA256SUMS`, and uses `xorriso` to build a copy whose GRUB menu adds the
   `autoinstall` kernel argument and a short timeout (without that argument the Ubuntu installer stops and
   asks before wiping disks). Both ISOs go into the node's ISO storage (`/var/lib/vz/template/iso`).
2. **`playbooks/provision-vms.yml`** handles every host in the group `ubuntu_servers` whose VM (by
   `pve_vmid`) doesn't exist yet. It fetches your public keys from `https://github.com/<github_user>.keys`,
   renders the installer's answers (hostname, static IP, the admin user with a SHA-512 hashed password from
   Ansible Vault, your SSH keys, password SSH off, `qemu-guest-agent`), builds a small per-host seed ISO
   on the node, creates the VM through the Proxmox API (UEFI, q35, VirtIO SCSI) with both ISOs attached,
   and starts it. When the installer has finished and powered the VM off, it detaches both ISOs, deletes
   the seed ISO (it holds the password hash), starts the VM and waits for SSH. VMs that already exist are
   skipped, so reruns are safe.
3. **`playbooks/baseline.yml`** runs on the servers. It works on any existing Ubuntu 24.04 or 26.04
   server, not only ones this project installed:
   - `apt` full upgrade, hostname and timezone;
   - the admin user, with its `authorized_keys` kept exactly in sync with your GitHub keys;
   - an SSH hardening drop-in: no root login, no passwords, and optionally a different port (which also
     switches off Ubuntu's socket-activated `ssh.socket` so the new port takes effect);
   - UFW: deny incoming, rate-limited SSH, and any extra allow rules you list;
   - Fail2Ban: an `sshd` jail on the systemd journal, banning through nftables, with longer bans for
     repeat offenders and an `ignoreip` list for your own networks;
   - unattended security upgrades, and `qemu-guest-agent`.

## Getting started

### With Docker: the web UI (Windows, Linux, macOS)

You need [Docker](https://docs.docker.com/get-started/get-docker/) (on Windows, Docker Desktop with the WSL 2
backend), a Proxmox API token with the privileges in
[docs/installing.md](docs/installing.md#2-create-the-proxmox-api-token), root SSH access to the Proxmox
node (the UI makes a key you add there), and free static IP addresses.

On Windows, download [`start.ps1`](start.ps1) and run it in PowerShell; it starts the container, shows the
setup code for the first sign-in and opens the browser:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Anywhere else (or on Windows without the script):

```sh
docker run -d --name usas --restart unless-stopped -p 127.0.0.1:8080:8080 -v usas-data:/data \
  honeybeartech/ubuntu-server-auto-setup
docker logs usas    # the setup code for the first sign-in
```

Then open <http://localhost:8080>, set a password, and work through the pages: Proxmox, SSH key, defaults,
servers, hardening, run. [`docker-compose.yml`](docker-compose.yml) does the same with extra hardening.
**The UI holds your Proxmox API token and a root SSH key: keep it on `127.0.0.1`** and read
[exposing it](docs/web-ui.md#exposing-it-beyond-localhost) before you publish it anywhere else.
[docs/web-ui.md](docs/web-ui.md) explains every step, where the data lives and how to back it up.

### From a checkout: the command line

You need:

- a control node (Linux or macOS) with Python 3.11 or later and `git`;
- one or more Proxmox VE 8 or 9 nodes that you can reach over SSH as `root` with a key;
- a Proxmox API token with the privileges listed in [docs/installing.md](docs/installing.md#2-create-the-proxmox-api-token);
- free static IP addresses on the VMs' network, and a GitHub account with your SSH public keys on it.

1. Get the code and install the dependencies in a virtual environment:

   ```sh
   git clone https://github.com/HoneyBearTech/ubuntu-server-auto-setup.git
   cd ubuntu-server-auto-setup
   python3 -m venv .venv && . .venv/bin/activate
   make deps
   ```

2. Create your inventory and your secrets (both files are gitignored):

   ```sh
   cp inventory/hosts.example.yml inventory/hosts.yml
   cp inventory/vault.example.yml inventory/group_vars/all/vault.yml
   $EDITOR inventory/hosts.yml inventory/group_vars/all/vault.yml
   ansible-vault encrypt inventory/group_vars/all/vault.yml
   ```

3. Run it against one new VM first:

   ```sh
   make build-installer ARGS=--ask-vault-pass
   make provision LIMIT=web-01 ARGS=--ask-vault-pass
   make baseline LIMIT=web-01 ARGS="--ask-vault-pass --check --diff"
   make baseline LIMIT=web-01 ARGS=--ask-vault-pass
   ```

   Once that host looks right, `make site ARGS=--ask-vault-pass` does everything for every host. The
   [quick start](docs/quick-start.md) walks through the same steps with more explanation.

## Usage

| Command | What it does |
| --- | --- |
| `make deps` | Install the Python packages (`requirements.txt`, `requirements-dev.txt`, `requirements-webui.in`) and the Ansible collections (`requirements.yml`). |
| `make lint` | `yamllint .`, `ansible-lint` and `ruff check`. |
| `make syntax` | Syntax-check `playbooks/site.yml` against the example inventory. |
| `make test` | Run the integration tests against containers and a mock Proxmox API (needs Docker; see [docs/testing.md](docs/testing.md)). |
| `make webui-test` | Run the web UI's unit tests with coverage. |
| `make image`, `make smoke` | Build the container image; build it and smoke-test it against the mock Proxmox API. |
| `make ui` | Run the web UI from the checkout on <http://localhost:8080>, with its data in `.webui-data/`. |
| `make site` | Build the installer, provision missing VMs, apply the baseline. |
| `make build-installer` | Download and remaster the Ubuntu ISO on the Proxmox node(s). |
| `make provision` | Create and auto-install the VMs that don't exist yet. |
| `make baseline` | Apply the baseline to the servers. |
| `make keys` | Re-sync the admin user's GitHub keys only (after you add or remove a key on GitHub). |

Two variables apply to every target: `LIMIT=<host or group>` narrows the run (`--limit`), and
`ARGS="..."` passes anything else to `ansible-playbook`, for example `ARGS="--ask-vault-pass"`,
`ARGS="--vault-password-file ~/.config/usas-vault-pass"` or `ARGS="--check --diff"`. `INVENTORY=` points at
another inventory file (default `inventory/hosts.yml`).

## Configuration

Everything is configured through the inventory (`inventory/hosts.yml`, copied from
[`inventory/hosts.example.yml`](inventory/hosts.example.yml)) and the encrypted Vault file
(`inventory/group_vars/all/vault.yml`, copied from [`inventory/vault.example.yml`](inventory/vault.example.yml)).
The variables you are most likely to set are below. [docs/interfaces.md](docs/interfaces.md) describes them
in more detail, and every role lists all of its variables, with defaults and comments, in
`roles/<role>/defaults/main.yml` (`installer_iso`, `ubuntu_release`, `proxmox_vm`, `common`, `firewall`,
`ssh_hardening`, `fail2ban`).

**Secrets (Vault)**

| Variable | What it is |
| --- | --- |
| `vault_admin_password` | The admin user's password, for the console (and `sudo` if you turn passwordless sudo off). SSH stays key-only. |
| `vault_pve_api_token_id` | The Proxmox API token's name only (`automation`, not `ansible@pve!automation`). |
| `vault_pve_api_token_secret` | The token's secret (a UUID). |

**All hosts**

| Variable | Default | What it is |
| --- | --- | --- |
| `github_user` | (required) | The GitHub account whose public keys (`https://github.com/<user>.keys`) are installed for the admin user. |
| `admin_extra_ssh_keys` | `[]` | Extra public keys to install besides the GitHub ones. |
| `ssh_keys_exclusive` | `true` | Remove keys from `authorized_keys` that are no longer on GitHub or in `admin_extra_ssh_keys`. The run refuses to touch the file if it finds no keys at all. |
| `admin_user` | `sysadmin` | The admin user created by the installer and managed by the baseline. |
| `admin_password` | `""` | Set it to `"{{ vault_admin_password }}"`. Hashed (SHA-512) before it reaches the installer; the baseline only uses it when it has to create the user. |
| `admin_sudo_nopasswd` | `false` | Passwordless `sudo` for the admin user. The default removes the installer's passwordless `sudo` on the first baseline run; later runs use `ansible_become_password` (set to the admin password in the example inventory). |
| `timezone` | `Etc/UTC` | Any [tz database](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) name. |
| `pve_api_host`, `pve_api_port` | `""`, `8006` | The Proxmox API. |
| `pve_api_user` | `ansible@pve` | The user that owns the API token. |
| `pve_api_token_id`, `pve_api_token_secret` | `""` | Set them to the Vault values. |
| `pve_validate_certs` | `true` | Verify the Proxmox API's TLS certificate. Leave it on; see [running it securely](#running-it-securely). |

**Proxmox nodes** (group `proxmox_nodes`): each inventory host name must match the Proxmox node name, with
`ansible_host` and `ansible_user: root`. The ISO location is set by `pve_iso_storage` (default `local`)
and `pve_iso_dir` (default `/var/lib/vz/template/iso`); the Ubuntu release by `ubuntu_release` (`26.04`)
and `ubuntu_point_release` (`26.04.1`); `installer_grub_timeout` (3 seconds) and `installer_iso_rebuild`
(`false`) control the remastered ISO.

**New VMs** (group `ubuntu_servers`, used by `provision-vms.yml`)

| Variable | Default | What it is |
| --- | --- | --- |
| `ansible_host` | (required) | The server's static IP address; the installer configures it. |
| `pve_vmid` | (required) | The Proxmox VM ID. Only IDs that don't exist anywhere in the cluster yet are created. |
| `pve_node` | (required) | The Proxmox node the VM is created on (and, through `pve_ssh_host`, the inventory host used to reach it over SSH). |
| `pve_storage` | `local-lvm` | The storage for the VM's disk and EFI disk (`pve_efidisk_format`: `raw`, or `qcow2` on directory storage). |
| `pve_bridge`, `pve_vlan` | `vmbr0`, `""` | The network bridge and an optional VLAN tag. |
| `vm_cores`, `vm_memory_mb`, `vm_disk_gb` | `2`, `2048`, `32` | The VM's size. |
| `vm_secure_boot` | `false` | Pre-enroll the Secure Boot keys in the EFI disk. |
| `net_prefix`, `net_gateway` | `24`, (required) | The network prefix length and the default gateway. |
| `net_dns`, `net_search` | `[]`, `[]` | DNS servers and search domains. |
| `autoinstall_storage_layout` | `lvm` | The installer's disk layout: `lvm`, `direct` or `zfs`. |
| `vm_install_timeout` | `3600` | Seconds to wait for the installer to finish. |

**Baseline** (group `ubuntu_servers`, used by `baseline.yml`)

| Variable | Default | What it is |
| --- | --- | --- |
| `ssh_port` | `22` | The SSH port. Anything other than 22 switches sshd from `ssh.socket` to `ssh.service`; the firewall rule moves with it. |
| `ssh_allow_users` | `[]` | If set, only these users may log in over SSH (it must include `admin_user`; the run refuses otherwise). |
| `ufw_ssh_sources` | `[any]` | Where SSH is allowed from: `any`, or a list of addresses and networks. |
| `ufw_allow` | `[]` | Extra firewall allow rules: `port`, `proto` (default `tcp`), optional `from` (a source address or network) and `comment`. |
| `fail2ban_ignoreip` | `[]` | Addresses and networks Fail2Ban never bans. Put your own management network here. |
| `fail2ban_bantime`, `fail2ban_maxretry` | `1h`, `5` | The first ban's length and the failures that trigger it; repeat offenders get longer bans, up to `fail2ban_bantime_maxtime` (`1w`). |
| `apt_upgrade` | `dist` | The upgrade the baseline runs: `dist`, `safe`, `full` or `"no"`. |
| `reboot_if_required` | `false` | Reboot after upgrades when the server asks for it (otherwise the run reports it). |
| `unattended_upgrades_auto_reboot` | `false` | Let unattended upgrades reboot the server, at `unattended_upgrades_reboot_time` (`04:00`). |

## Running it securely

- **Keep the Vault encrypted** and its password out of the repository: use `--ask-vault-pass`, or a
  password file outside the checkout (or `.vault-pass`, which is gitignored) with mode `0600`. Never commit
  `inventory/hosts.yml` or a plaintext `vault.yml`; both are gitignored.
- **Give the API token only the privileges in [docs/installing.md](docs/installing.md#2-create-the-proxmox-api-token)**,
  on a dedicated `ansible@pve` user with privilege separation, and rotate it if your control node may
  have been compromised.
- **Keep `pve_validate_certs: true`.** If your Proxmox node uses its self-signed certificate, add the
  cluster's CA (`/etc/pve/pve-root-ca.pem`) to your control node's trust store instead of turning
  verification off, or the API token can be intercepted.
- **Protect your GitHub account with two-factor authentication.** Whoever controls it controls which keys
  can log in to your servers: `make keys` and the baseline install exactly the keys GitHub lists.
- **Provision on a network you trust.** The control node learns a new VM's SSH host key by scanning it the
  first time the VM boots (trust on first use). Later port changes reuse the key read over the verified
  connection.
- **Sudo needs the admin password.** The installer grants passwordless `sudo` only so the first baseline
  run can work unattended; that run removes it, so a stolen SSH key alone isn't root. Keep
  `ansible_become_password` set to the admin password (as in the example inventory); set
  `admin_sudo_nopasswd: true` only if you accept that any authorized key is root.
- **Put your own management network in `fail2ban_ignoreip`**, and double-check `ssh_port`,
  `ufw_ssh_sources` and `ufw_allow` before the first baseline run on a server that's already in use.
- **Seed files are temporary.** The seed ISO holds the password hash. Provisioning deletes it once the
  install finishes, and also when a run fails part-way. Only a run killed outright (Ctrl-C, lost control
  node) can leave `seed-<host>.iso` in the node's ISO storage; delete it by hand then. The baseline removes
  the installer's own copy of the answers (`/var/log/installer/autoinstall-user-data`) from the server.
- **Keep the web UI on localhost.** It can create VMs and change SSH and firewall settings everywhere, and
  its data volume holds a root SSH key and the API token. The documented command publishes it on
  `127.0.0.1` only; anyone who can reach it can try to sign in. Use a strong password, back up the volume
  like a secret, and see [docs/web-ui.md](docs/web-ui.md#security) before exposing it.
- What this project does and doesn't protect against is in [docs/security.md](docs/security.md).

## Previous version

Up to 2021 this repository held an interactive bash script (`setup.sh`) for Ubuntu 14.04 to 20.04. It was
replaced by these Ansible playbooks and is no longer maintained, but it is still in the git history: the
last commit that has it is
[`f2e802d`](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/tree/f2e802d).

## License

[MIT](LICENSE).
