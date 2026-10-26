# Upgrading, backups and rotating secrets

ubuntu-server-auto-setup keeps no state of its own: everything it knows is in your inventory and your
Vault, and everything it does is on your Proxmox nodes and servers. Upgrading it means updating the
checkout and its dependencies; backing it up means backing up those two files.

## Back up

Back up, outside the repository:

- `inventory/hosts.yml` and `inventory/group_vars/all/vault.yml` (encrypted), and any `host_vars/` you
  added. They are gitignored, so `git` won't keep them for you.
- The Vault password, in your password manager. Without it the Vault can't be decrypted; the API token can
  be recreated, but you'd also lose the admin password if it isn't stored anywhere else.

Back up your servers with Proxmox (`vzdump`, or Proxmox Backup Server) as you would anyway, and take a
snapshot (`qm snapshot <vmid> before-baseline`) before running a new version of the baseline against a
server that matters. A backup of a VM with disk encryption includes its TPM state, so it restores to a VM
that unlocks itself: encrypt backups that leave your control (Proxmox Backup Server's client-side
encryption), and keep the Vault passphrase, which unlocks a restored disk whose TPM state is lost.

To restore: copy the two files back into a fresh checkout of the same version, run `make deps`, and check
the Vault opens with `ansible-vault view inventory/group_vars/all/vault.yml`.

## Upgrade

1. Read the [changelog](../CHANGELOG.md) from your version to the new one, especially any "Upgrading"
   section: before 1.0, a minor release may rename variables or change defaults.
2. Get the new version, check its signature ([verifying-releases.md](verifying-releases.md)) and update
   the dependencies:

   ```sh
   git fetch --tags
   git -c gpg.ssh.allowedSignersFile=.github/allowed_signers tag -v vX.Y.Z
   git checkout vX.Y.Z
   . .venv/bin/activate && make deps
   ```

3. Compare your inventory with the new `inventory/hosts.example.yml` and the roles' `defaults/main.yml`
   for new or renamed variables.
4. Preview what the new baseline would change on one server, apply it there, then everywhere:

   ```sh
   make baseline LIMIT=<host> ARGS="--ask-vault-pass --check --diff"
   make baseline LIMIT=<host> ARGS=--ask-vault-pass
   make baseline ARGS=--ask-vault-pass
   ```

Upgrading never touches existing VMs' disks: `make provision` skips every VM ID that exists. To roll back,
check out the previous tag and run the baseline again; settings the newer version added stay on the server
until you change them.

## To 0.7.0: new names

0.7.0 prepares for operating systems other than Ubuntu, so some names lose "Ubuntu" or "UFW". The old names
still work until 1.0, and every run says which ones your inventory still uses ("DEPRECATED (still read until
1.0): ..."). Rename them when it suits you:

| Before 0.7.0 | From 0.7.0 |
| --- | --- |
| group `ubuntu_servers` | group `servers` (the playbooks read both) |
| `ubuntu_release` (and `ubuntu_point_release`) | `vm_os: ubuntu-<release>`, for example `vm_os: ubuntu-24.04`; the OS catalog pins the point release |
| `ufw_allow`, `ufw_ssh_sources`, `ufw_ssh_limit`, `ufw_default_incoming`, `ufw_logging` | `firewall_allow`, `firewall_ssh_sources`, `firewall_ssh_limit`, `firewall_default_incoming`, `firewall_logging` (`ufw_default_outgoing` keeps its name) |
| `apt_upgrade` | `pkg_upgrade` |
| role `ubuntu_release` (in playbooks of your own) | role `os_catalog` |

A new name set in the inventory wins over the old one. Rename the group together with its `vars:`: a server in
`ubuntu_servers` doesn't get what's set for `servers`, and the other way round. Set `vm_os` per server to install different Ubuntu LTS
releases on one cluster: `make build-installer` builds each release's installer on the nodes that need it.

## A new Ubuntu point release

The OS catalog (`roles/os_catalog/vars/catalog.yml`) pins the point release each project release installs,
by checksum. When Ubuntu publishes a new point release (for example 26.04.2), a weekly check opens an issue
and a project release follows with the new pin. Until you update, the pinned ISO keeps working while Ubuntu
keeps it on `releases.ubuntu.com`; once it moves to `old-releases.ubuntu.com`, `make build-installer` stops
with "Couldn't download ... with the checksum the OS catalog pins". Update ubuntu-server-auto-setup, or set
`ubuntu_iso_base_url` to `https://old-releases.ubuntu.com/releases/26.04`, or set `ubuntu_point_release` to the
new release (checked against Ubuntu's signed `SHA256SUMS` instead, since the catalog doesn't pin it), and run
`make build-installer` again; it downloads the ISO and builds a new hands-off copy. Delete the old ISOs
from the node's ISO storage when no VM uses them. VMs installed from an older point release are brought
up to date by the baseline's upgrade and by unattended upgrades.

Moving an existing server to a new Ubuntu release (24.04 to 26.04) is not something these playbooks do: use
`do-release-upgrade` on the server, then run the baseline again.

## Rotating secrets

- **The Proxmox API token**: create a new token (`pveum user token add ansible@pve automation2 --privsep 1`
  and the matching `pveum acl modify` line from [installing.md](installing.md#2-create-the-proxmox-api-token)),
  put its name and secret in the Vault (`ansible-vault edit inventory/group_vars/all/vault.yml`), check
  that `make provision` still works, then remove the old one (`pveum user token remove ansible@pve automation`).
- **The Vault password**: `ansible-vault rekey inventory/group_vars/all/vault.yml`.
- **The disk encryption passphrase** (`vault_disk_passphrase`): on each encrypted VM,
  `sudo cryptsetup luksChangeKey "$(sudo blkid -t TYPE=crypto_LUKS -o device)"` (the TPM's key slot is
  unaffected), then change it in the Vault so VMs installed later use the new one. Keep the old one until
  every VM has the new one: it is the recovery key at their consoles.
- **The admin password**: change `vault_admin_password`; new VMs get the new password. The baseline sets
  the password only when it creates the user, so on existing servers change it there (`sudo passwd
  sysadmin`) as well.
- **SSH keys**: add or remove them on GitHub, then `make keys ARGS=--ask-vault-pass`. With
  `ssh_keys_exclusive: true` (the default), keys you removed from GitHub are removed from the servers.
