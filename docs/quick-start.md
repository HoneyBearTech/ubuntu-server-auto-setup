# Quick start

This takes one new VM from nothing to a hardened Ubuntu Server 26.04 install, in about half an hour (most of
it is the installer running). It assumes you already have the Proxmox API token described in
[installing.md](installing.md#2-create-the-proxmox-api-token); that page also explains every step in more
detail.

You need: a Linux or macOS machine with Python 3.12+ and `git`; root SSH access with a key to a Proxmox VE
8 or 9 node; a free VM ID and a free static IP address; and your SSH public keys on your GitHub account
(check `https://github.com/<you>.keys`).

> [!TIP]
> On Windows, or if you'd rather fill in forms than YAML, use the container image and its web UI instead:
> the same steps in a browser, with only Docker to install. See [web-ui.md](web-ui.md).

## 1. Get the code

```sh
git clone https://github.com/HoneyBearTech/ubuntu-server-auto-setup.git
cd ubuntu-server-auto-setup
python3 -m venv .venv && . .venv/bin/activate
make deps
```

## 2. Describe your node and your VM

```sh
cp inventory/hosts.example.yml inventory/hosts.yml
cp inventory/vault.example.yml inventory/group_vars/all/vault.yml
```

In `inventory/hosts.yml`:

- under `all.vars`, set `github_user` to your GitHub user name, `pve_api_host` to your Proxmox node's
  name or address, and `timezone`;
- under `proxmox_nodes`, rename `pve-1` to your node's Proxmox name and set its `ansible_host`;
- under `ubuntu_servers`, set `pve_node`, `pve_storage`, `pve_bridge`, `net_prefix`, `net_gateway`,
  `net_dns` and `fail2ban_ignoreip` for your network, then keep one host (say `web-01`) with your free
  `ansible_host` IP and `pve_vmid`, and delete the other.

In `inventory/group_vars/all/vault.yml`, set the admin password and the API token's name and secret, then
encrypt it and remember the password you choose:

```sh
ansible-vault encrypt inventory/group_vars/all/vault.yml
```

## 3. Build the installer

```sh
make build-installer ARGS=--ask-vault-pass
```

This downloads the Ubuntu ISO to the node (about 3 GB, checked against Ubuntu's checksums) and builds the
hands-off copy next to it. It only does that once; later runs find both ISOs and finish quickly.

## 4. Create the VM

```sh
make provision LIMIT=web-01 ARGS=--ask-vault-pass
```

Watch the VM's console in the Proxmox web UI if you like: the installer starts by itself, partitions the
disk, installs, and powers the VM off. The playbook then removes the installer media, starts the VM, and
waits until it can log in as `sysadmin` with your key.

## 5. Harden it

Preview the changes first, then apply them:

```sh
make baseline LIMIT=web-01 ARGS="--ask-vault-pass --check --diff"
make baseline LIMIT=web-01 ARGS=--ask-vault-pass
```

Then check from your machine that you can still log in, and that passwords and root are refused:

```sh
ssh sysadmin@<ip>
ssh -o PubkeyAuthentication=no sysadmin@<ip>   # should fail: "Permission denied (publickey)"
ssh root@<ip>                                  # should fail
```

On the server, `sudo ufw status verbose` and `sudo fail2ban-client status sshd` show the firewall and the
SSH jail.

## What next

- Add more hosts to `ubuntu_servers` and run `make site ARGS=--ask-vault-pass`: existing VMs are skipped,
  new ones are installed, and the baseline is applied (again) everywhere.
- Point the baseline at servers you already have (Ubuntu 24.04 or 26.04): add them to `ubuntu_servers`
  with their `ansible_host` and `ansible_user`, and run `make baseline LIMIT=<host>`. `make provision` and
  `make site` expect every host in that group to have a `pve_vmid`: give a Proxmox VM its existing ID (it
  is then skipped), and keep servers elsewhere out of those runs with `LIMIT`.
- Added or removed a key on GitHub? `make keys ARGS=--ask-vault-pass`.
- Read [security.md](security.md) for what this does and doesn't protect, and [upgrading.md](upgrading.md)
  for keeping it current.
