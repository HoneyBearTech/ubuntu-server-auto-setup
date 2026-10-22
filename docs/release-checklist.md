# Pre-release checklist on real Proxmox VE

CI tests every playbook against containers and a mock Proxmox API, and remasters the real Ubuntu ISO every
week ([testing.md](testing.md)), but it never boots a real VM. Before tagging a release, the maintainer
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
VM ID, `sysadmin` for the admin user, `<iso-storage>` for the ISO storage and `0.4.0` for the version to
be tagged; replace them with yours.

## Pre-flight

- [ ] **What is under test.** The commit (`git rev-parse HEAD` in the checkout) and the image built from
  it, with the version it will be tagged as, so the web UI shows that version:

  ```bash
  docker build --build-arg USAS_VERSION=0.4.0 -t ubuntu-server-auto-setup:rc .
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

Run the playbooks once through the web UI of the image under test, and the second runs from the CLI in a
checkout of the same commit (`make build-installer`, `make provision LIMIT=test-01`,
`make baseline LIMIT=test-01`), so both paths are covered. `make site LIMIT=test-01` runs all three.

## Web UI

- [ ] First run: the setup code from `docker logs usas-rc` sets the password; signing out and in again
  works, and a wrong password is refused.
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
- [ ] The [screen-reader walk-through](accessibility.md#screen-reader-walk-through) with VoiceOver on
  macOS and with NVDA on Windows, on the image under test before its first-run setup (it starts with the
  first-run page). Add a row per screen reader to its results table in the release pull request, and an
  issue for each problem. Until a release has done this, the docs say it hasn't been run.
- [ ] The version in the page footer is the version being released, and the API says the same:

  ```bash
  curl -s http://localhost:8080/api/session | grep -o '"version":"[^"]*"'
  ```

## The published image

After the tag is pushed and the release workflow has finished, check what it published, in both
registries ([verifying-releases.md](verifying-releases.md#the-container-image)):

- [ ] The signature and the build provenance verify on Docker Hub and on GHCR:

  ```bash
  for image in docker.io/honeybeartech/ubuntu-server-auto-setup:0.4.0 ghcr.io/honeybeartech/ubuntu-server-auto-setup:0.4.0; do
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
  rm -f "$(pvesm path <iso-storage>:iso/seed-test-01.iso)"
  rm -rf /var/tmp/ubuntu-server-auto-setup/seed-test-01
  ```

- [ ] Remove the test keys: any key added to the GitHub account for the run, the test image's key from
  `/root/.ssh/authorized_keys` on the Proxmox node (if it isn't the key you keep), and the test VM's entries
  in your `known_hosts`:

  ```bash
  ssh-keygen -R 192.0.2.10 && ssh-keygen -R '[192.0.2.10]:2222'
  ```

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
- Ubuntu <point release> live server ISO
- build-installer, provision-vms, baseline: failed=0; second runs changed=0
- Hand checks: key-only SSH, password and root refused, UFW, Fail2Ban, unattended-upgrades, hostname,
  sudo needs a password, SSH port change (or: not tested, why)
- Web UI: setup, sign-in, cross-site and token-less posts refused, run streamed, version <version>
- Screen readers: VoiceOver <version> with Safari <version>, NVDA <version> with <browser> <version>:
  steps passed, problems and their issues (or: not run, why)
- Anything that failed, and the issue or pull request that fixes it
```

The published image's checks come after the tag; add them to the release's discussion or a comment on the
pull request. A failed step blocks the release until it is fixed or explained there.
