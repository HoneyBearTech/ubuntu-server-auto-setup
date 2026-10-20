# Installing

"Installing" ubuntu-server-auto-setup means setting up a control node to run the playbooks, and giving it
access to your Proxmox VE nodes. Nothing is installed on the Proxmox nodes except the `xorriso` package
and the ISO files the playbooks create. For a shorter walk-through, see the [quick start](quick-start.md).

## What you need

- **A control node**: Linux or macOS, Python 3.11 or later, `git`, `make` and an SSH client
  (`ssh-keyscan` included). This is where your inventory, your Vault and your SSH private key live, so
  use a machine you trust.
- **Proxmox VE 8 or 9**, with SSH access as `root` using a key (`ssh root@<node>` must work without a
  password prompt), and its API reachable over HTTPS (port 8006) from the control node.
- **Storage**: about 6 GB in the node's ISO storage (`local`, `/var/lib/vz/template/iso`) for the
  Ubuntu ISO and its remastered copy, and room on the VM storage (`local-lvm` by default) for each disk.
- **Network**: a static IP address, gateway and DNS servers for every new VM, on a bridge (`vmbr0`) the
  control node can reach over SSH.
- **A GitHub account** with your SSH public keys (`https://github.com/<user>.keys`), protected with
  two-factor authentication.

## 1. Get a release

Download a release from the [releases page](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases),
check it as described in [verifying-releases.md](verifying-releases.md), and unpack it; or clone the
repository and check out a release tag:

```sh
git clone https://github.com/HoneyBearTech/ubuntu-server-auto-setup.git
cd ubuntu-server-auto-setup
git -c gpg.ssh.allowedSignersFile=.github/allowed_signers tag -v v0.1.0   # the tag is signed
git checkout v0.1.0
```

The latest release is listed on the [releases page](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases);
`main` has the newest changes, tested but not yet released.

Then install the dependencies in a virtual environment:

```sh
python3 -m venv .venv && . .venv/bin/activate
make deps
```

`make deps` installs `requirements.txt` (ansible-core, proxmoxer, requests, passlib) and
`requirements-dev.txt` (ansible-lint, yamllint) with pip, and the collections in `requirements.yml`
(`community.proxmox`, `community.general`, `ansible.posix`) with `ansible-galaxy` into `collections/`.

## 2. Create the Proxmox API token

The playbooks create and start VMs through the Proxmox API with a token. Give it its own user and a role
with only the privileges it needs:

| Privilege | Why |
| --- | --- |
| `VM.Allocate` | create the VM |
| `VM.Config.Disk`, `VM.Config.CDROM`, `VM.Config.CPU`, `VM.Config.Memory`, `VM.Config.Network`, `VM.Config.Options`, `VM.Config.HWType`, `VM.Config.Cloudinit` | set up its disks, the two ISOs, CPU, memory, network, boot order, machine type and options |
| `VM.PowerMgmt` | start the VM |
| `VM.Audit` | list VMs (to skip existing ones) and watch the installer finish |
| `Datastore.AllocateSpace`, `Datastore.Audit` | allocate the disks and use the ISOs in the ISO storage |
| `SDN.Use` | attach the VM to a bridge (Proxmox VE 8 and later check this) |

On a Proxmox node, as root:

```sh
pveum role add UbuntuAutoSetup --privs "VM.Allocate VM.Config.Disk VM.Config.CDROM VM.Config.CPU VM.Config.Memory VM.Config.Network VM.Config.Options VM.Config.HWType VM.Config.Cloudinit VM.PowerMgmt VM.Audit Datastore.AllocateSpace Datastore.Audit SDN.Use"
pveum user add ansible@pve --comment "ubuntu-server-auto-setup"
pveum acl modify / --users ansible@pve --roles UbuntuAutoSetup
pveum user token add ansible@pve automation --privsep 1
pveum acl modify / --tokens 'ansible@pve!automation' --roles UbuntuAutoSetup
```

The token is created with **privilege separation** (`--privsep 1`): it gets only the permissions given to
the token itself, and never more than its user has. `ansible@pve` has no password, so nobody can sign in to
the web UI as it. The `token add` command prints the secret once: put it in the Vault (step 4) right away.
You can narrow the ACLs from `/` to `/vms`, `/storage/<id>` and `/sdn/zones/<zone>` if your cluster has other
tenants.

The playbooks also log in to each node over SSH as `root` to download and build the ISOs (the API can't
run commands). If you'd rather not use root, that is on the [roadmap](roadmap.md).

If the node uses Proxmox's self-signed certificate, add the cluster CA to the control node's trust store so
the API's certificate can be verified (keep `pve_validate_certs: true`). Copy `/etc/pve/pve-root-ca.pem`
from the node, then on Debian or Ubuntu
`sudo cp pve-root-ca.pem /usr/local/share/ca-certificates/pve-root-ca.crt && sudo update-ca-certificates`,
or on macOS add it to the System keychain and point Python at it (`export SSL_CERT_FILE=...`,
`export REQUESTS_CA_BUNDLE=...` with a bundle that includes it).

## 3. Write the inventory

```sh
cp inventory/hosts.example.yml inventory/hosts.yml
```

`inventory/hosts.yml` is gitignored. It has two groups:

- `proxmox_nodes`: one host per Proxmox node, named exactly like the node in Proxmox, with `ansible_host`
  and `ansible_user: root`;
- `ubuntu_servers`: one host per server. For a new VM: `ansible_host` (its static IP), `pve_vmid`,
  `pve_node`, and the network and size variables. Group-wide values such as `net_gateway`, `net_dns`
  and `fail2ban_ignoreip` go under the group's `vars`.

Every variable is described in [interfaces.md](interfaces.md) and the README's
[configuration section](../README.md#configuration); each role's `defaults/main.yml` has the full list.

## 4. Create the Vault

```sh
cp inventory/vault.example.yml inventory/group_vars/all/vault.yml
$EDITOR inventory/group_vars/all/vault.yml
ansible-vault encrypt inventory/group_vars/all/vault.yml
```

It holds `vault_admin_password`, `vault_pve_api_token_id` (the token's name only, `automation`) and
`vault_pve_api_token_secret`. The file is gitignored even when encrypted. Keep the Vault password in your
password manager; pass it with `ARGS=--ask-vault-pass`, or keep it in a file outside the checkout (mode
`0600`) and use `ARGS="--vault-password-file <file>"`. Change things later with
`ansible-vault edit inventory/group_vars/all/vault.yml`.

## 5. Run it on one host

```sh
make build-installer ARGS=--ask-vault-pass
make provision LIMIT=<host> ARGS=--ask-vault-pass
make baseline LIMIT=<host> ARGS="--ask-vault-pass --check --diff"
make baseline LIMIT=<host> ARGS=--ask-vault-pass
```

Check that you can still log in, and that password and root logins are refused (see the
[quick start](quick-start.md#5-harden-it)). Then `make site ARGS=--ask-vault-pass` handles every host.

## Troubleshooting

- **"... is not listed in SHA256SUMS"**: Ubuntu has published a newer point release and moved the old one.
  Set `ubuntu_point_release` to the new one (and rerun `make build-installer`), or point
  `ubuntu_iso_base_url` at `https://old-releases.ubuntu.com/releases/26.04`.
- **The installer waits for input or never powers off**: the remastered ISO didn't get the `autoinstall`
  argument, or the seed ISO wasn't found. Open the VM's console. Rebuild the ISO with
  `ARGS="-e installer_iso_rebuild=true"`.
- **A provisioning run failed half-way**: the run deletes the seed ISO and names the failed task. The VM
  exists now, so a rerun would skip it: delete it in Proxmox (`qm destroy <vmid>`) and run again. If the
  run was killed outright (Ctrl-C), also delete `seed-<host>.iso` from the ISO storage and
  `/var/tmp/ubuntu-server-auto-setup/seed-<host>` on the node yourself (they hold the password hash).
- **Locked out after the baseline**: log in on the Proxmox console as the admin user with the Vault
  password, fix `/etc/ssh/sshd_config.d/00-hardening.conf` or `ufw`, then correct the inventory.

## Uninstalling

Nothing runs permanently: to stop using the project, delete the checkout. To remove what it created:

- on each Proxmox node, the ISOs in `/var/lib/vz/template/iso` (`ubuntu-26.04.1-live-server-amd64.iso`
  and `...-autoinstall.iso`) and the work directory `/var/tmp/ubuntu-server-auto-setup`; `xorriso` can be
  removed with `apt remove xorriso`;
- the API token and user: `pveum user token remove ansible@pve automation`, `pveum user delete ansible@pve`,
  `pveum role delete UbuntuAutoSetup`;
- the VMs, if you want them gone (`qm destroy <vmid>`). Servers keep their baseline settings; they are
  ordinary Ubuntu configuration (`/etc/ssh/sshd_config.d/00-hardening.conf`, UFW, `/etc/fail2ban/jail.local`,
  `/etc/apt/apt.conf.d/52unattended-upgrades-local`, `/etc/sudoers.d/90-ansible-<admin_user>`).
