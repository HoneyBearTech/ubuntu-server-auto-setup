# Pre-release checklist on real Proxmox VE

CI tests every playbook against containers and a mock Proxmox API, and remasters the real Ubuntu ISO every
week and installs from it in QEMU under Secure Boot ([testing.md](testing.md)), but it never runs on
Proxmox VE itself. Before tagging a release, the maintainer
runs this checklist on real Proxmox VE hardware, against the commit that will be tagged. It is the
"real run" part of the [test policy](../CONTRIBUTING.md#test-policy).

It is based on the first end-to-end run on a real cluster, which verified v0.2.x and turned up what the
containers couldn't show: node names are case-sensitive; the Proxmox modules' 5-second API timeout was too
short for allocating disks (now `pve_api_timeout`, 60 seconds); an ISO storage other than `local` needs its
folder asked from Proxmox (`pvesm path`); the admin's keys must be exactly the ones GitHub publishes (the
web UI's own SSH key is a separate key, not a personal one); and passwordless sudo stayed after the first
run (now removed by default, `admin_sudo_nopasswd: false`). The steps below check each of them again.

Use a disposable VM ID and address, never a server you need: provisioning wipes the VM's disk. In the
commands, `192.0.2.10` stands for the test VM's address, `test-01` for its inventory name, `9001` for its
VM ID, `sysadmin` for the admin user, `<iso-storage>` for the ISO storage and `0.6.0` for the version to
be tagged; replace them with yours.

## Pre-flight

- [ ] **What is under test.** The commit (`git rev-parse HEAD` in the checkout) and the image built from
  it, with the version it will be tagged as, so the web UI shows that version:

  ```bash
  docker build --build-arg USAS_VERSION=0.6.0 -t ubuntu-server-auto-setup:rc .
  docker run -d --name usas-rc -p 127.0.0.1:8080:8080 -v usas-rc-data:/data ubuntu-server-auto-setup:rc
  ```

  Use a new volume (`usas-rc-data`), so the first-run setup is tested too. The setup code is in
  `docker logs usas-rc`.
- [ ] **Versions.** Proxmox VE on every node, and ansible-core and the collections in the image and in
  the checkout:

  ```bash
  pveversion                         # on each Proxmox node
  docker exec usas-rc ansible --version
  docker exec usas-rc ansible-galaxy collection list
  ansible --version && ansible-galaxy collection list   # in the checkout's virtual environment
  ```

- [ ] **A cluster.** At least two nodes, so the node dropdown and shared storage are tested; note whether
  any node name has capital letters (one that does tests the case handling):

  ```bash
  pvecm status
  pvesh get /nodes
  ```

- [ ] **ISO storage other than `local`.** A storage shared by the cluster (NFS, for example) that allows ISO
  images, set as `pve_iso_storage`:

  ```bash
  pvesm status --content iso
  ```

- [ ] **A test GitHub key.** The admin's keys come from `https://github.com/<github_user>.keys`. Note which
  keys that returns now; if you add a key to the account for this run, note it for the clean-up.

## build-installer

- [ ] The run ends with `failed=0`, and the ISO's checksum (and signature) verification passes.
- [ ] The remastered ISO is in the chosen storage, and every node sees it there:

  ```bash
  pvesm list <iso-storage> --content iso | grep autoinstall    # on each node
  ```

- [ ] A second run reports `changed=0`.

## provision-vms

- [ ] In the web UI's Proxmox page, **Test the connection** lists every node of the cluster in the node
  dropdown, spelled exactly as `pvesh get /nodes` shows them, and the ISO storage dropdown (if the token
  may list storages) offers `<iso-storage>`.
- [ ] The run ends with `failed=0`, with no API timeout while the disk is allocated.
- [ ] The VM boots the installer, the autoinstall completes without a keyboard, and the VM powers off,
  then starts from its disk. Watch it on the VM's console in Proxmox.
- [ ] The CD-ROMs are detached, the VM boots from its disk only, and no seed ISO or seed folder is left on
  the node (the seed holds the password hash):

  ```bash
  qm config 9001 | grep -E '^(ide2|ide3|boot):'     # no ide2 or ide3; boot: order=scsi0
  ls "$(dirname "$(pvesm path <iso-storage>:iso/seed-test-01.iso)")" | grep seed-   # nothing
  ls /var/tmp/ubuntu-server-auto-setup/            # no seed-test-01
  ```

- [ ] A second run skips the VM ("VM 9001 already exists; skipping install") and reports `changed=0`.
- [ ] **DHCP**, if the release touches networking: a second test VM (`test-02`, VM ID `9002`) with
  `net_mode: dhcp`, no `ansible_host` and a `net_mac`, on a network with a DHCP server. Provisioning prints
  "test-02 has <address> from DHCP on net0", and that is the address the DHCP server leased to `net_mac`.
  The baseline then connects to it, in the same `make site` run and again with `make baseline` on its own.
  On Proxmox VE 9 the token's `VM.GuestAgent.Audit` is enough; without it, the run stops naming that
  privilege:

  ```bash
  qm config 9002 | grep -E '^(net0|agent):'   # net0: virtio=<net_mac>,bridge=...; agent: enabled=1
  qm guest cmd 9002 network-get-interfaces | grep -i -A8 '<net_mac>'   # its ipv4 address
  ```

- [ ] **An extra network card on a VLAN**, if the release touches networking: `test-01` with
  `vm_extra_nics: [{bridge: <bridge>, vlan: <tag>, mode: static, address: <address>, prefix: <prefix>}]` on a
  VLAN the bridge carries (`bridge-vlan-aware yes`). The VM has `net1` with that tag, and in the VM the card
  whose MAC address is `net1`'s has the address and comes up after a reboot; the default route still goes
  through `net0`, and SSH answers on both addresses (the firewall's rules apply to every card):

  ```bash
  qm config 9001 | grep -E '^net[0-9]:'       # net1: virtio=<mac>,bridge=<bridge>,tag=<tag>
  ssh sysadmin@192.0.2.10 'ip -br address; ip route show default; sudo netplan get ethernets'
  ```

## Secure Boot and disk encryption

CI installs from the real ISO under Secure Boot with a TPM 2.0 in QEMU, set up the way Proxmox sets up the
VMs, but not on Proxmox itself. Here the test VM (Secure Boot is the default) and a second disposable VM
with disk encryption, `test-05` (VM ID `9005`, address `192.0.2.14`), with `vm_disk_encryption: true`
and `vm_disk_passphrase: "{{ vault_disk_passphrase }}"` (set on the web UI's **Defaults** page, under
**Disk encryption**, if the release touches it: the saved passphrase is never shown again, and the page
refuses encryption without Secure Boot keys, the `lvm` layout or a passphrase), are checked on the real
cluster.

- [ ] Both VMs have an EFI disk with pre-enrolled keys, and only the encrypted one has a TPM state volume,
  version 2.0, on `pve_tpmstate_storage`. Note whether the EFI disks carry `ms-cert=2023k`:

  ```bash
  qm config 9001 | grep -E '^(bios|machine|efidisk0|tpmstate0):'
  qm config 9005 | grep -E '^(bios|machine|efidisk0|tpmstate0):'
  ```

- [ ] Both report Secure Boot on:

  ```bash
  ssh sysadmin@192.0.2.10 mokutil --sb-state      # SecureBoot enabled
  ssh sysadmin@192.0.2.14 mokutil --sb-state
  ```

- [ ] `test-05`'s root file system is on LUKS2, with a TPM2 token bound to PCR 7 and two key slots (the
  passphrase's and the TPM's), and its crypttab line has `tpm2-device=auto,tries=5`:

  ```bash
  ssh sysadmin@192.0.2.14 'lsblk -o NAME,TYPE,FSTYPE,MOUNTPOINTS; cat /etc/crypttab'
  ssh sysadmin@192.0.2.14 'for m in $(sudo dmsetup ls --target crypt | cut -f1); do sudo cryptsetup status "$m"; done'
  ssh sysadmin@192.0.2.14 'sudo systemd-cryptenroll "$(sudo blkid -t TYPE=crypto_LUKS -o device)"'   # password, tpm2
  ```

- [ ] A reboot unlocks without anyone typing: watch the console in Proxmox (no passphrase prompt) and wait
  for SSH:

  ```bash
  ssh sysadmin@192.0.2.14 sudo systemctl reboot; sleep 60; ssh sysadmin@192.0.2.14 uptime
  ```

- [ ] The recovery passphrase works: it opens the LUKS volume (with `--disable-external-tokens`, or cryptsetup
  unlocks with the TPM token and accepts any passphrase), and after the TPM slot is wiped the VM stops
  at the passphrase prompt on its console, which asks again after a wrong passphrase (type one wrong one
  first, then the Vault passphrase, which unlocks it); the command in
  [security.md](security.md#secure-boot-and-disk-encryption) enrolls the TPM again, and the next reboot is
  unattended again:

  ```bash
  ssh -t sysadmin@192.0.2.14 'sudo cryptsetup open --test-passphrase --disable-external-tokens "$(sudo blkid -t TYPE=crypto_LUKS -o device)" && echo passphrase works'
  ssh -t sysadmin@192.0.2.14 'sudo systemd-cryptenroll --wipe-slot=tpm2 "$(sudo blkid -t TYPE=crypto_LUKS -o device)"'
  ssh sysadmin@192.0.2.14 sudo systemctl reboot   # on the console: a wrong passphrase, then the right one; then:
  ssh -t sysadmin@192.0.2.14 'sudo systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 "$(sudo blkid -t TYPE=crypto_LUKS -o device)"'
  ssh sysadmin@192.0.2.14 sudo systemctl reboot; sleep 60; ssh sysadmin@192.0.2.14 uptime
  ```

- [ ] The installer's copy of its configuration is gone from `test-05`, and no seed is left on the node:

  ```bash
  ssh sysadmin@192.0.2.14 'sudo ls /var/log/installer/'    # no autoinstall-user-data
  ```

- [ ] After its baseline run, no copy of the password hash is left where cloud-init keeps its data on
  `test-05`. The command prints nothing. Any file it lists is a copy the baseline doesn't remove yet: open an
  issue before releasing.

  ```bash
  ssh sysadmin@192.0.2.14 'sudo grep -rlF "\$6\$" /var/lib/cloud /etc/cloud'
  ```

- [ ] A Proxmox backup of `test-05` restores to a VM that boots unattended (the backup carries the TPM
  state, as [security.md](security.md#secure-boot-and-disk-encryption) says), then delete the restored VM.

## Several nodes

On a cluster of two nodes (the owner's: `hephaestus` and `hyve`; replace them with yours), with a second
disposable server `test-04` (VM ID `9004`, address `192.0.2.13`) placed on the other node: `pve_node: hyve`
on `test-04`, the group's `pve_node` left at `hephaestus`, both nodes in `proxmox_nodes` with their host keys
pinned ([host-keys.md](host-keys.md#clustered-nodes)).

- [ ] **Shared storage, one build.** With `pve_iso_storage` the shared `<iso-storage>`, delete the remastered
  ISO from it, then run `make build-installer`. The plan says `<iso-storage> on hyve (shared): built by
  hephaestus`; only `hephaestus` appears in the build play, and both nodes list the new ISO:

  ```bash
  pvesm list <iso-storage> --content iso | grep autoinstall    # on each node
  ```

- [ ] **Local storage, one build per node.** With `-e pve_iso_storage=local`, the plan says `built by` each
  node for its own `local`, and `pvesm list local --content iso | grep autoinstall` finds the ISO on both.
  A second run reports `changed=0`.
- [ ] **A limit leaves a node out.** `make build-installer LIMIT=test-04 ARGS="-e pve_iso_storage=local"`
  reports `local on hyve: skipped, --limit leaves its node out`; with `LIMIT=test-04,hyve` it builds there.
- [ ] **A wrong node name is refused before anything is created.** With `pve_node: Hyve` on `test-04`,
  `make provision` stops with `pve_node isn't a node in this cluster for 1 host(s): test-04 ('Hyve', did you
  mean hyve)`, listing the cluster's nodes, and `qm list` on both nodes shows no new VM. Put `hyve` back.
- [ ] **An unpinned node is named before anything is built.** Remove `hyve`'s entry from the control node's
  `known_hosts` (`ssh-keygen -R <hyve's address>`): `make build-installer ARGS="-e pve_iso_storage=local"`
  stops with `Node hyve (...): its SSH host key isn't in known_hosts`, before any node builds. Pin it again,
  comparing the fingerprint with `hyve`'s console.
- [ ] **Each VM on its node.** `make provision LIMIT=test-04` creates VM `9004` on `hyve` (`qm config 9004`
  works there, not on `hephaestus`), the install completes, and no seed ISO or seed folder is left on `hyve`.
  A second run skips it and reports `changed=0`.
- [ ] **Web UI.** After **Test the connection**, the Servers page offers both nodes in each row's node
  dropdown (Default first); setting `test-04` to `hyve` writes `pve_node: hyve` for it. The SSH key page
  lists both nodes with their own address, scan, trust and login test; the overview's SSH step is done only
  when both are trusted. The Run page shows `<iso-storage>` built once by `hephaestus` (or each node for
  `local`), and a `build-installer` run from the UI matches it.

## A node user other than root

The optional `prepare-node.yml` ([installing.md](installing.md#6-optional-a-node-user-other-than-root)),
then the ISO build and another test VM (`test-03`, VM ID `9003`, address `192.0.2.12`) as that user.
`<node>` is the node's address.

- [ ] `prepare-node.yml` with `pve_ssh_user: usas-build`, the control node's public key in
  `proxmox_node_user_ssh_keys` and `proxmox_node_user_iso_storages: [local, <iso-storage>]` ends with
  `failed=0`, and a second run reports `changed=0`. If `<iso-storage>` is NFS or CIFS and the ACL doesn't
  take, the run says so; note what the file server needed.
- [ ] On the node, as root: the user has no groups but its own, no password and no sudo; each ISO folder has
  the ACL entry and is still root's; the recorded folders are the ones `pvesm path` gives:

  ```bash
  id usas-build; getent shadow usas-build | cut -d: -f2     # groups=...(usas-build) only; *
  ls /etc/sudoers.d/; command -v sudo && sudo -l -U usas-build
  getfacl "$(dirname "$(pvesm path local:iso/x.iso)")" "$(dirname "$(pvesm path <iso-storage>:iso/x.iso)")"
  cat /etc/ubuntu-server-auto-setup/iso-dirs.json
  ```

- [ ] As the user, Proxmox's configuration stays out of reach:

  ```bash
  ssh usas-build@<node> pvesm status            # please run as root
  ssh usas-build@<node> cat /etc/pve/storage.cfg  # Permission denied
  ```

- [ ] `build-installer.yml` with `pve_ssh_user: usas-build` and `-e installer_iso_rebuild=true` ends with
  `failed=0`; the new ISO is `usas-build`'s and every node still sees it; a second run (without the rebuild)
  reports `changed=0`:

  ```bash
  ls -l "$(dirname "$(pvesm path <iso-storage>:iso/x.iso)")"/*autoinstall.iso
  pvesm list <iso-storage> --content iso | grep autoinstall    # on each node
  ```

- [ ] `provision-vms.yml --limit test-03` with `pve_ssh_user: usas-build` installs the VM, and no seed ISO or
  seed folder is left (as in [provision-vms](#provision-vms)).
- [ ] In the web UI, **Node SSH user** `usas-build` (Proxmox page, More settings): the SSH page shows the
  user's `authorized_keys` command, **Test the SSH login** answers `Logged in to usas-build@...`, and a
  **build-installer** run from the UI succeeds. **Fetch** the CA certificate over SSH fails with the
  message to paste it instead.

## baseline

- [ ] The run ends with `failed=0`, and a second run reports `changed=0`.
- [ ] SSH works with the admin's key only; a password and root are refused:

  ```bash
  ssh sysadmin@192.0.2.10 true && echo key login works
  ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive sysadmin@192.0.2.10
  ssh root@192.0.2.10
  ```

  The last two must end with `Permission denied (publickey)`.
- [ ] The admin's `authorized_keys` holds exactly the keys GitHub publishes (plus the web UI's key, if
  it is authorized, and any `admin_extra_ssh_keys`):

  ```bash
  curl -fsS https://github.com/<github_user>.keys
  ssh sysadmin@192.0.2.10 cat .ssh/authorized_keys
  ```

- [ ] Then, logged in as the admin on the VM (`ssh sysadmin@192.0.2.10`):

  ```bash
  sudo -k; sudo -n true     # must fail: sudo needs a password
  sudo sshd -T | grep -Ei '^(port|passwordauthentication|permitrootlogin|kbdinteractiveauthentication) '
  sudo ufw status verbose   # active, deny (incoming), the SSH port LIMIT IN
  sudo fail2ban-client status sshd
  systemctl is-enabled unattended-upgrades && apt-config dump | grep -E 'Periodic::(Update-Package-Lists|Unattended-Upgrade)'
  hostnamectl --static      # test-01
  ```

  With `admin_sudo_nopasswd: true`, `sudo -n true` succeeds instead; test that only if the release
  changes it.
- [ ] **SSH port change**, if the release touches SSH, the firewall or Fail2Ban: run the baseline with
  another port, check that the new port works and 22 is closed, and that a rerun reports `changed=0`:

  ```bash
  make baseline LIMIT=test-01 ARGS="-e ssh_port=2222"
  ssh -t -p 2222 sysadmin@192.0.2.10 'sudo ufw status; sudo grep -E "^port" /etc/fail2ban/jail.local'
  ssh -o ConnectTimeout=5 sysadmin@192.0.2.10 true    # must fail
  make baseline LIMIT=test-01 ARGS="-e ssh_port=2222"
  ```

- [ ] **Optional hardening**, if the release touches the `hardening` role (CI's containers can't run
  auditd or load AppArmor policy): turn on the extras, check them, and check that a rerun reports
  `changed=0`:

  ```bash
  make baseline LIMIT=test-01 ARGS="-e hardening_sysctl_enabled=true -e hardening_auditd_enabled=true -e hardening_apparmor_enabled=true"
  ssh -t sysadmin@192.0.2.10 'sudo sysctl kernel.kptr_restrict net.ipv4.conf.all.rp_filter; sudo auditctl -l; sudo aa-status | head -3'
  make baseline LIMIT=test-01 ARGS="-e hardening_sysctl_enabled=true -e hardening_auditd_enabled=true -e hardening_apparmor_enabled=true"
  ```

  `auditctl -l` must list the `identity`, `sudoers`, `sshd`, `time-change`, `modules` and `audit-*` rules.

- [ ] **A RHEL-family server**, if the release touches the baseline (containers have no SELinux): on a Rocky
  Linux or AlmaLinux VM in the inventory (any one is enough; record which), run the baseline, then move SSH to
  another port, which needs SELinux's port label:

  ```bash
  make baseline LIMIT=<rhel-vm>
  make baseline LIMIT=<rhel-vm> ARGS="-e ssh_port=2222"
  ssh -t -p 2222 <admin>@<address> 'getenforce; sudo semanage port -l | grep ^ssh_port_t; sudo firewall-cmd --list-rich-rules; sudo fail2ban-client status sshd; systemctl is-enabled dnf-automatic.timer'
  make baseline LIMIT=<rhel-vm> ARGS="-e ssh_port=2222"
  ```

  `getenforce` must say `Enforcing`, `ssh_port_t` must list 2222, the rich rules must allow 2222 and not 22,
  and the last run must report `changed=0`.

Run the playbooks once through the web UI of the image under test, and the second runs from the CLI in a
checkout of the same commit (`make build-installer`, `make provision LIMIT=test-01`,
`make baseline LIMIT=test-01`), so both paths are covered. `make site LIMIT=test-01` runs all three.

## updates

If the release touches `playbooks/updates.yml` or `roles/updates`. Use the test VM from above
(`test-01`, installed by this run, so it is in `servers`) and, for a server this project didn't
install, a disposable Ubuntu 24.04 VM made by hand with an `ops` user that has passwordless sudo and your
key, added to `other_servers` (`legacy-01` at `192.0.2.20` below). Compare `legacy-01`'s host key with its
console (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`) before you add it to `known_hosts`.

- [ ] **Check, changing nothing.** The run ends with `failed=0` and `changed=0`; each server shows
  `pending` or `nothing`, with its automatic-update line, and the report is written:

  ```bash
  make updates-check LIMIT=test-01,legacy-01
  ls -l reports/updates/
  python3 -m json.tool "$(ls -t reports/updates/*.json | head -1)" | less
  ```

  Compare the pending list with the server's own view: `ssh sysadmin@192.0.2.10 apt list --upgradable`.
- [ ] **Apply with reboots.** The servers that need a reboot restart and come back, the health check is
  `ok`, and a second run reports `changed=0` and `nothing` for both:

  ```bash
  make updates LIMIT=test-01,legacy-01 ARGS="-e updates_reboot=if_required"
  make updates LIMIT=test-01,legacy-01 ARGS="-e updates_reboot=if_required"
  ```

- [ ] **The admin password stays off servers this project didn't install.** This must stop before
  connecting, naming `legacy-01`:

  ```bash
  make updates-check LIMIT=legacy-01 ARGS="-e ansible_become_password={{ vault_admin_password }}"
  ```

- [ ] **Busy servers wait.** On `test-01`, start the daily upgrade (`ssh -t sysadmin@192.0.2.10 sudo
  systemctl start --no-block apt-daily-upgrade.service`) and at once run
  `make updates-check LIMIT=test-01 ARGS="-e updates_lock_wait=5"`: if the upgrade is still running, the
  server shows `busy` and the run doesn't fail. Run it again a few minutes later: `pending` or `nothing`.
- [ ] **Encrypted VM**, if one from the Secure Boot section is still there and a `shim-signed`, `grub-efi-amd64-signed` or
  `secureboot-db` update is pending on it (`apt list --upgradable`): `make updates LIMIT=<it>
  ARGS="-e updates_reboot=if_required"` installs it and holds the reboot, naming the package and the
  recovery steps; reboot from the console and follow "Recovery" in docs/security.md. If no such update is
  pending, note "not available" in the record.
- [ ] **Snapshots.** Add `VM.Snapshot` to the token's role (docs/installing.md) and take an operator snapshot
  of `test-01` first (`qm snapshot <vmid> mine`). On a VM with disks on LVM-thin or ZFS, a run with
  `ARGS="-e updates_snapshot=true"` and an update pending takes `usas_upd_<date>_<time>` on the VM's current
  node before installing (watch **Tasks** in the Proxmox UI), the report names it, and `mine` is still there
  after a second run with an update pending (the older `usas_upd_` one is gone, `updates_snapshot_keep: 1`).
  Migrate the VM to another node first, without changing `pve_node`, and the snapshot still goes to the node
  it runs on. A VM with a raw disk on NFS or directory storage is `skipped` with "disks don't support
  snapshots". Remove `VM.Snapshot` again (`pveum role modify UbuntuAutoSetup --privs <the list without it>`)
  and a run fails the VM naming `VM.Snapshot`. Roll back once by hand following docs/updates.md
  ("Rolling back"), then delete the snapshots you made.
- [ ] **Waves.** Tag two VMs `usas-group-web` in the Proxmox UI (with updates pending on both). `make
  updates-check LIMIT=<both> ARGS="-e updates_serial=2"` puts them in waves 1 and 2 of 2 ("wave 1 of 2" in
  the report), and `make updates LIMIT=<both> ARGS="-e updates_serial=2 -e updates_reboot=if_required"`
  reboots the second only after the first is back and healthy. Remove the tags afterwards.

## Web UI

- [ ] First run: the setup code from `docker logs usas-rc` sets the password; signing out and in again
  works, and a wrong password is refused.
- [ ] **Passkeys**, by hand with a real authenticator (Touch ID, a password manager or a security key; CI
  only uses a virtual one), at `http://localhost:<port>`: on the account page, add a passkey (a name and
  the current password; a wrong password is refused), sign out, and **Sign in with a passkey**. The account
  page shows when it was added and last used. **Require a passkey** hides the password form and refuses a
  password post; deleting `passkeys.json` from the data volume brings password sign-in back. Remove the
  passkey from the account page and from the authenticator afterwards. `docker logs usas-rc` records each
  sign-in and the passkey being added, required and removed.
- [ ] Posts from another site, and posts without the session's CSRF token, are refused (each answers
  403):

  ```bash
  curl -si -X POST -H 'Origin: https://attacker.example' -F theme=dark http://localhost:8080/api/theme | head -n 1
  curl -si -X POST -F theme=dark http://localhost:8080/api/theme | head -n 1
  ```

- [ ] A run's output streams to the page as it happens, and the finished run is in **History** with its
  log.
- [ ] **VMs** lists every VM and container of the cluster, as `pvesh get /cluster/resources --type vm`
  does. `test-01` shows its address from the guest agent (after the baseline) and *Supported*; a VM
  without an agent shows the address from the Servers page or cloud-init; a template and a container say
  *Not supported*. On Proxmox VE 9 without `VM.GuestAgent.Audit` on the token, the page says to add it;
  on Proxmox VE 8 it says `VM.Monitor` is optional. A run log keeps streaming while the VMs page
  refreshes.
- [ ] **Running the baseline from the VMs page**, if the release touches the VMs page, the runner, host keys
  or the baseline. Use a second disposable VM that this project didn't install, with Ubuntu 26.04 from the
  cloud image or the installer, the QEMU guest agent running, a user `ubuntu` with passwordless sudo and the
  web UI's public key in its `authorized_keys` (`test-06`, VM ID `9006`, address `192.0.2.15` below; made by hand in Proxmox). Never a server you need: the
  real run changes its SSH, firewall and packages.
  - [ ] On **VMs**, VM 9006 can be selected and shows its address from the guest agent. **Review the
    plan** before pinning says to trust its host key first.
  - [ ] **Scan the host key**; on the VM's console in Proxmox, `for f in /etc/ssh/ssh_host_*_key.pub; do
    ssh-keygen -lf "$f"; done` lists the same fingerprints; pin them. **Test the login** as `ubuntu` works.
  - [ ] **Review the plan** (Full baseline, extra firewall rule `8080/tcp`): the host is `vm-9006`, and the
    warnings name the firewall's default deny and the key sync. **Run the check**: it passes, the results
    table shows changes for `vm-9006`, and nothing changed on the VM (`ssh ubuntu@192.0.2.15 'command -v ufw
    || echo none'` prints `none` on a VM without UFW).
  - [ ] On the check run's page, **Run the baseline for real** without the box ticked is refused; with it,
    the run passes. A second click is refused ("run already"). A new check run of the same plan reports no
    changes in the results table.
  - [ ] On the VM: `sudo ufw status` is active with `8080/tcp`; `hostnamectl --static` is unchanged; `sudo
    -n true` still works (passwordless); `sudo grep -r "<the admin password>" /etc /home /var/log` finds
    nothing; `authorized_keys` still has the web UI's key.
  - [ ] Test-01 (installed by this project) selected on the VMs page starts from the key recorded at
    provisioning (no scan needed), and its check run passes with its own address and user.
  - [ ] **History → Download the audit log**: `hostkey.pin`, `hostkey.adopt`, `run.check`, `run.apply`,
    `run.refused` and `run.finished` entries, with the VMs, addresses and fingerprints, and no secret.
  - [ ] **Forget the host key** of VM 9006, then pin a wrong one by hand and try a check run: it fails with
    `Host key verification failed`:

    ```bash
    docker exec usas-rc sh -c "ssh-keygen -q -t ed25519 -N '' -f /tmp/k && echo usas-<cluster>-9006 \$(cut -d' ' -f1,2 /tmp/k.pub) >> /data/ssh/known_hosts"
    ```

    Then forget it again on the VMs page.
- [ ] **The status API** ([web-ui.md](web-ui.md#dashboards-heimdall)): before any token exists,
  `/api/status` answers 404. On the account page, **Create a token** (a wrong password is refused); the
  token is shown once. A call with it as `Authorization: Bearer` answers the summary, which holds no host,
  node or VM names, addresses or VM IDs, and its `vms` and `servers` counts match the VMs page and the
  Servers page; no token, a wrong one, and the token in a query parameter or a cookie answer 401.
  **Replace the token**: the old one gets 401, the new one works. **Revoke the token**: the API answers 404
  again, with the token too. `docker logs usas-rc` records the token being made, used, refused and revoked:

  ```bash
  curl -si http://localhost:8080/api/status | head -n 1                     # 404 while off
  curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/status   # the summary
  curl -si -H 'Authorization: Bearer usas_st_...' http://localhost:8080/api/status | head -n 1      # 401 (malformed)
  ```

- [ ] The version in the page footer is the version being released, and the API says the same:

  ```bash
  curl -s http://localhost:8080/api/session | grep -o '"version":"[^"]*"'
  ```

## The published image

After the tag is pushed and the release workflow has finished, check what it published, in both
registries ([verifying-releases.md](verifying-releases.md#the-container-image)):

- [ ] The signature and the build provenance verify on Docker Hub and on GHCR:

  ```bash
  for image in docker.io/honeybeartech/ubuntu-server-auto-setup:0.6.0 ghcr.io/honeybeartech/ubuntu-server-auto-setup:0.6.0; do
    cosign verify "$image" \
      --certificate-identity-regexp '^https://github\.com/HoneyBearTech/ubuntu-server-auto-setup/\.github/workflows/release\.yml@refs/tags/v' \
      --certificate-oidc-issuer https://token.actions.githubusercontent.com
    gh attestation verify "oci://$image" --repo HoneyBearTech/ubuntu-server-auto-setup \
      --signer-workflow HoneyBearTech/ubuntu-server-auto-setup/.github/workflows/release.yml
  done
  ```

- [ ] The release files verify as in [verifying-releases.md](verifying-releases.md#the-release-files), and
  the digest in the release's `images.txt` is the one both registries serve.
- [ ] The published image starts, and its footer shows the released version.

## Clean-up

- [ ] Delete the test VMs and any seed ISO a stopped run left behind:

  ```bash
  qm stop 9001; qm destroy 9001 --purge     # on the node that holds the VM (and 9002, if made)
  qm stop 9003; qm destroy 9003 --purge
  qm stop 9004; qm destroy 9004 --purge     # on hyve
  qm stop 9005; qm destroy 9005 --purge     # the encrypted test VM (its TPM state volume goes too)
  qm stop 9006; qm destroy 9006 --purge     # the VM made by hand for the VMs page's runs
  rm -f "$(pvesm path <iso-storage>:iso/seed-test-01.iso)"
  rm -rf /var/tmp/ubuntu-server-auto-setup/seed-test-01 /var/tmp/ubuntu-server-auto-setup/seed-test-03 \
    /var/tmp/ubuntu-server-auto-setup/seed-test-04
  ```

- [ ] Remove the test keys: any key added to the GitHub account for the run, the test image's key from
  `/root/.ssh/authorized_keys` on the Proxmox node (if it isn't the key you keep), and the test VM's entries
  in your `known_hosts`:

  ```bash
  ssh-keygen -R 192.0.2.10 && ssh-keygen -R '[192.0.2.10]:2222'
  ```

- [ ] Keep the node user, or remove it: `userdel -r usas-build`, `setfacl -x u:usas-build <folder>` on each
  ISO folder and `rm -r /etc/ubuntu-server-auto-setup`, then set `pve_ssh_user` back to `root`.
- [ ] Remove the test container and its volume (it holds the API token and an SSH key):

  ```bash
  docker rm -f usas-rc && docker volume rm usas-rc-data
  ```

## Recording the result

Put the result in the release pull request's description (the "Prepare vX.Y.Z" pull request), so it is
public and next to the change. Record what was tested, not addresses or host names:

```text
Real Proxmox run (docs/release-checklist.md), <date>
- Under test: commit <sha>, image built with USAS_VERSION=<version>
- Proxmox VE <version>, <n>-node cluster, ISO storage: <type, e.g. NFS shared>
- ansible-core <version>; collections: community.general <version>, ... (ansible-galaxy collection list)
- Ubuntu <point release> live server ISO (the one roles/os_catalog/vars/catalog.yml pins)
- build-installer, provision-vms, baseline: failed=0; second runs changed=0
- Hand checks: key-only SSH, password and root refused, UFW, Fail2Ban, unattended-upgrades, hostname,
  sudo needs a password, SSH port change (or: not tested, why)
- Web UI: setup, sign-in, passkey added and used (authenticator), cross-site and token-less posts refused,
  run streamed, status API off (404), token made, used and revoked, version <version>
- Anything that failed, and the issue or pull request that fixes it
```

The published image's checks come after the tag; add them to the release's discussion or a comment on the
pull request. A failed step blocks the release until it is fixed or explained there.
