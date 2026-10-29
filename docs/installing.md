# Installing

"Installing" ubuntu-server-auto-setup means setting up a control node to run the playbooks, and giving it
access to your Proxmox VE nodes. Nothing is installed on the Proxmox nodes except the `xorriso` and `gpgv`
packages and the ISO files the playbooks create (and, if you choose the [optional hardening
step](#6-optional-a-node-user-other-than-root), a user of its own for the ISO builds). For a shorter
walk-through, see the [quick start](quick-start.md).

## What you need

- **A control node**: Linux or macOS, Python 3.12 or later, `git`, `make` and an SSH client
  (`ssh-keyscan` included). This is where your inventory, your Vault and your SSH private key live, so
  use a machine you trust.
- **Proxmox VE 8 or 9**, with SSH access as `root` using a key (`ssh root@<node>` must work without a
  password prompt; after the [optional step 6](#6-optional-a-node-user-other-than-root) the playbooks log in
  as a user without root's privileges instead), and its API reachable over HTTPS (port 8006) from the
  control node.
- **Storage**: about 6 GB in the node's ISO storage (`local`, `/var/lib/vz/template/iso`) for the
  Ubuntu ISO and its remastered copy, and room on the VM storage (`local-lvm` by default) for each disk.
  Every new VM also gets a 4 MB EFI disk on that storage (its Secure Boot keys and boot entries); a VM with
  disk encryption gets a 4 MB TPM state volume as well, on `pve_tpmstate_storage` (the same storage unless
  you set another). Both must be storages that allow disk images; on directory storage set
  `pve_efidisk_format: qcow2`. If the TPM state goes to another storage, the token's permissions must cover
  it too (the `pveum acl modify /` line below does).
- **Network**: a static IP address, gateway and DNS servers for every new VM, or a DHCP server on its
  network (`net_mode: dhcp`), on a bridge (`vmbr0`) the control node can reach over SSH.
- **A GitHub account** with your SSH public keys (`https://github.com/<user>.keys`), protected with
  two-factor authentication.

## 1. Get a release

Download a release from the [releases page](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases),
check it as described in [verifying-releases.md](verifying-releases.md), and unpack it; or clone the
repository and check out a release tag:

```sh
git clone https://github.com/HoneyBearTech/ubuntu-server-auto-setup.git
cd ubuntu-server-auto-setup
git -c gpg.ssh.allowedSignersFile=.github/allowed_signers tag -v v0.6.0   # the tag is signed
git checkout v0.6.0
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
Both files allow a range of versions; the release notes of each release list the exact ansible-core and
collection versions its checks ran with, and `ansible --version` and `ansible-galaxy collection list` show
the ones you have.

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
| `VM.GuestAgent.Audit` (Proxmox VE 9 and later) | read-only questions to the VMs' guest agents: the address DHCP gave a server with `net_mode: dhcp` (provisioning and the baseline), and the web UI's VMs page (addresses, OS and host name). Servers with static addresses don't need it. |

On a Proxmox node, as root:

```sh
pveum role add UbuntuAutoSetup --privs "VM.Allocate VM.Config.Disk VM.Config.CDROM VM.Config.CPU VM.Config.Memory VM.Config.Network VM.Config.Options VM.Config.HWType VM.Config.Cloudinit VM.PowerMgmt VM.Audit Datastore.AllocateSpace Datastore.Audit SDN.Use VM.GuestAgent.Audit"
pveum user add ansible@pve --comment "ubuntu-server-auto-setup"
pveum acl modify / --users ansible@pve --roles UbuntuAutoSetup
pveum user token add ansible@pve automation --privsep 1
pveum acl modify / --tokens 'ansible@pve!automation' --roles UbuntuAutoSetup
```

On **Proxmox VE 8**, leave `VM.GuestAgent.Audit` out: that version doesn't have it. To add it to a role
you created with an earlier release, on Proxmox VE 9:
`pveum role modify UbuntuAutoSetup --privs VM.GuestAgent.Audit --append 1`.

`VM.GuestAgent.Audit` allows only the guest agent's informational commands, such as listing the network
interfaces and reading the OS release; it doesn't allow running commands in the VM or reading its files
(those need `VM.GuestAgent.Unrestricted` or `VM.GuestAgent.FileRead`, which the token must not have).
Proxmox VE 8 has no such privilege: asking a guest agent needs **`VM.Monitor`**, which also allows running
commands as root in every VM with an agent and using the QEMU monitor. Adding `VM.Monitor` on Proxmox VE 8
is optional and not recommended. Without it, the VMs page shows each VM's address from the inventory (the
Servers page) or from its cloud-init settings, and says why the agents' data is missing. If you add it
anyway, anyone with the Vault or the web UI's password can run commands in those VMs.

Servers with `net_mode: dhcp` and no `ansible_host` are found the same way: once the installed system boots,
the playbooks ask its guest agent which address DHCP gave the first network card. On Proxmox VE 9 that is
covered by `VM.GuestAgent.Audit`. On Proxmox VE 8 it needs `VM.Monitor`, with the risk above; to avoid it,
reserve an address for the server on your DHCP server (set `net_mac` so the MAC address is known before the
VM exists) and put that address in `ansible_host`: then the agent isn't asked. Without either, provisioning
stops with a message naming the missing privilege.

The token is created with **privilege separation** (`--privsep 1`): it gets only the permissions given to
the token itself, and never more than its user has. `ansible@pve` has no password, so nobody can sign in to
the web UI as it. The `token add` command prints the secret once: put it in the Vault (step 4) right away.
You can narrow the ACLs from `/` to `/vms`, `/storage/<id>` and `/sdn/zones/<zone>` if your cluster has other
tenants.

The playbooks also log in to each node over SSH, as `root` by default, to download and build the ISOs and
write the seed files (the API can't run commands). [Step 6](#6-optional-a-node-user-other-than-root) replaces
root with a user that can only write to the ISO storage.

If the node uses Proxmox's self-signed certificate, add the cluster CA to the control node's trust store so
the API's certificate can be verified (keep `pve_validate_certs: true`). Copy `/etc/pve/pve-root-ca.pem`
from the node, then on Debian or Ubuntu
`sudo cp pve-root-ca.pem /usr/local/share/ca-certificates/pve-root-ca.crt && sudo update-ca-certificates`,
or on macOS add it to the System keychain and point Python at it (`export SSL_CERT_FILE=...`,
`export REQUESTS_CA_BUNDLE=...` with a bundle that includes it). In a virtual environment the Proxmox
modules don't read the system's trust store, so set `REQUESTS_CA_BUNDLE` on Linux too (for example to
`/etc/ssl/certs/ca-certificates.crt`). [host-keys.md](host-keys.md) shows how to check the CA's and the
node's SSH host key's fingerprints against the node's console before you trust them.

## 3. Write the inventory

```sh
cp inventory/hosts.example.yml inventory/hosts.yml
```

`inventory/hosts.yml` is gitignored. It has two groups:

- `proxmox_nodes`: one host per Proxmox node, named exactly like the node in Proxmox, with `ansible_host`;
  the group's `ansible_user` is `"{{ pve_ssh_user }}"`, and `pve_ssh_user` is `root` (step 6 changes it);
- `servers`: one host per server. For a new VM: `ansible_host` (its static IP), `pve_vmid`,
  `pve_node`, and the network and size variables. Group-wide values such as `net_gateway`, `net_dns`
  and `fail2ban_ignoreip` go under the group's `vars`. A server that gets its address from DHCP has
  `net_mode: dhcp` and no `ansible_host`; more network cards and VLANs are set with `pve_vlan` and
  `vm_extra_nics` ([interfaces.md](interfaces.md#networks)).

### Several nodes of one cluster

One inventory can place VMs on any node of one Proxmox VE cluster. Set `pve_node` for the group (the default)
and override it on the servers that go elsewhere, and list every node they use in `proxmox_nodes`:

```yaml
    proxmox_nodes:
      hosts:
        hephaestus: { ansible_host: 192.0.2.10, ansible_user: root }
        hyve: { ansible_host: 192.0.2.11, ansible_user: root }
    servers:
      vars:
        pve_node: hephaestus
      hosts:
        web-01: { ansible_host: 10.10.10.61, pve_vmid: 4061 }
        db-01: { ansible_host: 10.10.10.62, pve_vmid: 4062, pve_node: hyve }
```

- One API endpoint serves the whole cluster: `pve_api_host` can be any member. Nodes of a different cluster
  need their own inventory (and token).
- Before anything is built or created, the playbooks check every server's node against the cluster's node
  list (one error names every wrong one; names are case-sensitive), that its ISO storage allows ISO images on
  that node, and that every node they need answers over SSH with a host key already in `known_hosts`. Pin each
  node's key first ([host-keys.md](host-keys.md#clustered-nodes)).
- `build-installer` builds the installer ISO where the servers need it: once for a storage the cluster
  shares (`shared` in Proxmox, such as NFS, CIFS or CephFS), on one node that uses it; on every node that
  uses a node-local storage such as `local`. A node that already has the current ISO is skipped. With
  `LIMIT=`, only the servers in the limit count, and a node builds only if it is in the limit too
  (`LIMIT=db-01,hyve`); a node left out is reported as skipped.
- Each VM's seed ISO is written on its own node, in its ISO storage.

Every variable is described in [interfaces.md](interfaces.md) and the README's
[configuration section](../README.md#configuration); each role's `defaults/main.yml` has the full list.

New VMs boot with Secure Boot (`vm_secure_boot: true`): the firmware runs only Ubuntu's signed boot chain,
and the kernel loads only signed modules. Set `vm_secure_boot: false` for a VM that needs an unsigned
kernel or out-of-tree modules (some DKMS drivers). To encrypt a VM's disk, unlocked at boot by a virtual
TPM, set `vm_disk_encryption: true` and `vm_disk_passphrase: "{{ vault_disk_passphrase }}"` for it (or
for the group), after reading [what that protects](security.md#secure-boot-and-disk-encryption).

## 4. Create the Vault

```sh
cp inventory/vault.example.yml inventory/group_vars/all/vault.yml
$EDITOR inventory/group_vars/all/vault.yml
ansible-vault encrypt inventory/group_vars/all/vault.yml
```

It holds `vault_admin_password`, `vault_pve_api_token_id` (the token's name only, `automation`) and
`vault_pve_api_token_secret`, and `vault_disk_passphrase` if you encrypt VM disks (a long random one, for
example from `openssl rand -base64 24`: it is the recovery key you may have to type at a VM's console, so
keep a copy in your password manager too). The file is gitignored even when encrypted. Keep the Vault password in your
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

## 6. Optional: a node user other than root

By default the playbooks log in to each Proxmox node as root. Root isn't needed for the work they do there,
so you can give them a user of their own (`usas-build` below) that has no password, no sudo and no groups,
and can write to the ISO storage's folder and its own work directory only:

| Step on the node | What the user needs | How it gets it |
| --- | --- | --- |
| Download the ISO (and, for a point release the OS catalog doesn't pin, `SHA256SUMS`, whose signature `gpgv` checks) | a work directory | `/var/tmp/ubuntu-server-auto-setup`, its own, mode `0700` |
| Remaster the ISO (`xorriso`), build the seed ISOs | the same work directory | as above |
| Put the ISOs in the ISO storage, delete the seed afterwards | write access to that folder only | an ACL entry (`user:usas-build:rwx`) on `<storage>/template/iso`; Proxmox keeps owning the folder |
| Find the ISO storage's folder | `pvesm` runs for root only (it reads `/etc/pve`) | `prepare-node.yml` asks it once, as root, and writes the answer to `/etc/ubuntu-server-auto-setup/iso-dirs.json` (root's, readable by all) |
| Install `xorriso` and `gpgv` | root | `prepare-node.yml` installs them; later runs only check they are there |

No sudo rule is needed. Run `playbooks/prepare-node.yml` once per node; it logs in as root (`ssh root@<node>`
must still work for this one run). Give it the user's name, its public keys and the ISO storages it may
use, for example in `inventory/hosts.yml`:

```yaml
all:
  vars:
    pve_ssh_user: usas-build
    # The key the playbooks (or the web UI) log in with: the user's authorized_keys gets exactly these.
    proxmox_node_user_ssh_keys:
      - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... you@laptop
    # Every ISO storage the user may write to (default: pve_iso_storage only).
    proxmox_node_user_iso_storages: [local]
```

```sh
make prepare-node ARGS=--ask-vault-pass
```

The playbook is idempotent, so run it again after adding a storage or changing the keys. With the group's
`ansible_user: "{{ pve_ssh_user }}"` (as in `inventory/hosts.example.yml`), every later run logs in as
`usas-build`; the roles stop if the user they find on the node isn't `pve_ssh_user`. If root can only log
in with sudo from another account, run `make prepare-node ARGS="-e prepare_node_login_user=<you> --become"`.
Once it works, the control node no longer needs root's key on the node: remove it from root's
`authorized_keys` (rerunning `prepare-node.yml` then needs the `--become` form). Leave sshd's
`PermitRootLogin` alone on a cluster, whose nodes reach each other as root over SSH.

**Network storage.** On an NFS or CIFS storage the file server decides who may write, whatever ACL the node
sets: an NFS export that squashes or doesn't map other users (Synology's defaults, for example) accepts the ACL
entry and still refuses the user. The playbook writes a test file as the user in each ISO folder and stops,
naming the folder, if it can't. Either let the user's UID (`id -u usas-build` on the node; the same on every
node that shares the storage) write on the file server, or keep that storage out of
`proxmox_node_user_iso_storages` and keep `pve_ssh_user` at `root` for an inventory that builds there: root is
still the way for such a share.

On a Synology share whose permissions are DSM ACLs (a folder can show mode `777` on the node and still refuse
the user), with the NFS rule's **Squash** at **No mapping**, the share treats the client's UID as the DSM user with
that UID. DSM picks its users' UIDs, so match the node user to it:

1. In DSM, create a user (such as `usas-build`) with no application permissions and **Read only** on the share, and
   give it **Read & Write** on the share's `template/iso` folder only (File Station: **Properties → Permission**).
   Its UID: `grep '^usas-build:' /etc/passwd | cut -d: -f3` on the Synology.
2. On every node, give `usas-build` that UID (`usermod -u <uid> usas-build`, after checking with
   `getent passwd <uid>` that it is free) and hand it its work directory again:
   `chown -R usas-build: /var/tmp/ubuntu-server-auto-setup`.
3. Run `prepare-node.yml` again: its write test now passes.
4. Hand the user the ISOs that earlier root builds left in that folder (`chown usas-build <folder>/ubuntu-*.iso`
   on one node): the build sets their mode, which only their owner may do there, and stops with "Operation not
   permitted" otherwise.

**By hand.** The same, as root on the node, for the `local` storage:

```sh
apt install xorriso gpgv acl
useradd --system --home-dir /var/lib/usas-build --create-home --shell /bin/sh --password '*' usas-build
install -d -m 700 -o usas-build -g usas-build /var/lib/usas-build/.ssh
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... you@laptop' > /var/lib/usas-build/.ssh/authorized_keys
chown usas-build: /var/lib/usas-build/.ssh/authorized_keys
chmod 600 /var/lib/usas-build/.ssh/authorized_keys
install -d -m 700 -o usas-build -g usas-build /var/tmp/ubuntu-server-auto-setup
chown -R usas-build: /var/tmp/ubuntu-server-auto-setup   # what earlier root builds left
dir="$(dirname "$(pvesm path local:iso/x.iso)")"
setfacl -m u:usas-build:rwx "$dir"
install -d -m 755 /etc/ubuntu-server-auto-setup
printf '{"local": "%s"}\n' "$dir" > /etc/ubuntu-server-auto-setup/iso-dirs.json
```

Then set `pve_ssh_user: usas-build` in the inventory. Before `chown -R`, check that
`/var/tmp/ubuntu-server-auto-setup` is a folder (not a link) that root owns.

To go back to root, set `pve_ssh_user: root` again. To remove the user: `userdel -r usas-build`,
`setfacl -x u:usas-build <folder>` on each ISO folder, and `rm -r /etc/ubuntu-server-auto-setup`.

## Troubleshooting

- **"Couldn't download .../ubuntu-26.04.1-live-server-amd64.iso with the checksum the OS catalog pins"**:
  either the file is gone (HTTP 404: Ubuntu has published a newer point release and moved the old one to
  `old-releases.ubuntu.com`), or what was downloaded isn't the ISO Ubuntu signed. For a missing file, update
  ubuntu-server-auto-setup (a newer release's catalog pins the newer point release), or point
  `ubuntu_iso_base_url` at `https://old-releases.ubuntu.com/releases/26.04`. For a different checksum, use
  another mirror; don't work around the check. A copy already in the ISO storage is kept only if it has the
  pinned checksum.
- **"... is not listed in SHA256SUMS"** (with `ubuntu_point_release` set to a point release the catalog doesn't
  pin): Ubuntu has moved that point release. Set `ubuntu_point_release` to the current one, or clear it to use
  the catalog's, and rerun `make build-installer`.
- **"The signature on .../SHA256SUMS is bad or isn't by the key ..."** or **"Couldn't fetch
  .../SHA256SUMS.gpg"** (same case): the checksum file couldn't be authenticated, so nothing was downloaded.
  Check `ubuntu_iso_base_url`: a mirror must publish `SHA256SUMS.gpg` next to `SHA256SUMS`. If it's
  `releases.ubuntu.com` itself, a tampered file or a proxy rewriting it is possible; don't work around the
  check. If Ubuntu has moved to a new signing key, update to a release that ships it.
- **The installer waits for input or never powers off**: the remastered ISO didn't get the `autoinstall`
  argument, or the seed ISO wasn't found. Open the VM's console. Rebuild the ISO with
  `ARGS="-e installer_iso_rebuild=true"`.
- **"pve_node isn't a node in this cluster for ... host(s)"**: the error lists each server with the node it
  names, and the cluster's real node names. Fix the inventory; nothing was created.
- **"Node ... its SSH host key isn't in known_hosts, or has changed"**: the playbooks reach that node with
  strict host-key checking. Compare its key with the node's console and pin it
  ([host-keys.md](host-keys.md)); don't switch the check off.
- **"... skipped, --limit leaves its node out"** from `build-installer`: add the node to the limit, or run
  without one.
- **A provisioning run failed half-way**: the run deletes the seed ISO and names the failed task. The VM
  exists now, so a rerun would skip it: delete it in Proxmox (`qm destroy <vmid>`) and run again. If the
  run was killed outright (Ctrl-C), also delete `seed-<host>.iso` from the ISO storage and
  `/var/tmp/ubuntu-server-auto-setup/seed-<host>` on the node yourself (they hold the password hash).
- **"Ansible logs in to <node> as root, but pve_ssh_user is usas-build"**: the inventory still logs in as
  someone else. Set the `proxmox_nodes` group's `ansible_user` to `"{{ pve_ssh_user }}"` (or to the same
  name), and remove any `ansible_user: root` on the node's own entry.
- **"... iso-dirs.json on <node> doesn't name a folder for storage ..."** or **"... isn't writable by
  usas-build"**: run `make prepare-node` with that storage in `proxmox_node_user_iso_storages`; for an NFS
  or CIFS storage, see [step 6](#6-optional-a-node-user-other-than-root).
- **"... can't install packages, and xorriso or gpgv isn't installed"**: run `make prepare-node`, or
  `apt install xorriso gpgv` as root on the node.
- **"Timeout (32s) waiting for privilege escalation prompt"** or **"Connection timed out"** right after a
  reboot or a restart of SSH: the server was still starting. `ansible.cfg` waits 30 seconds (`timeout`
  under `[defaults]`); run it again (the playbooks are idempotent), or raise the timeout for a
  slow server with `ARGS="-T 60"`.
- **Locked out after the baseline**: log in on the Proxmox console as the admin user with the Vault
  password, fix `/etc/ssh/sshd_config.d/00-hardening.conf` or `ufw`, then correct the inventory.

## Uninstalling

Nothing runs permanently: to stop using the project, delete the checkout. To remove what it created:

- on each Proxmox node, the ISOs in `/var/lib/vz/template/iso` (`ubuntu-26.04.1-live-server-amd64.iso`
  and `...-autoinstall.iso`) and the work directory `/var/tmp/ubuntu-server-auto-setup`; `xorriso` can be
  removed with `apt remove xorriso`; the node user from step 6, if you made one (see the end of that step);
- the API token and user: `pveum user token remove ansible@pve automation`, `pveum user delete ansible@pve`,
  `pveum role delete UbuntuAutoSetup`;
- the VMs, if you want them gone (`qm destroy <vmid>`). Servers keep their baseline settings; they are
  ordinary Ubuntu configuration (`/etc/ssh/sshd_config.d/00-hardening.conf`, UFW, `/etc/fail2ban/jail.local`,
  `/etc/apt/apt.conf.d/52unattended-upgrades-local`, `/etc/sudoers.d/90-ansible-<admin_user>`).
