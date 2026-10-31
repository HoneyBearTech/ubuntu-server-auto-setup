# Testing

How ubuntu-server-auto-setup is tested, how to run the tests, and how to add a case. The rules for when a
change needs a test are in the [test policy](../CONTRIBUTING.md#test-policy).

## What runs where

| Suite | What it covers | Command | In CI |
| --- | --- | --- | --- |
| Lint and syntax | yamllint, ansible-lint (`production` profile), a syntax check of every playbook, actionlint, ruff (the web UI's Python), hadolint (the Dockerfile); TypeScript's strict type check of the web UI's app runs with its tests | `make lint`, `make syntax` | every pull request and push to `main` (required) |
| Integration tests | every playbook end to end against containers and a mock Proxmox API, with checks of the results, idempotence and task coverage | `make test` | every pull request and push to `main` (required) |
| Updates tests | the updates playbook against two servers with updates waiting in a signed test repository, on Ubuntu 22.04, 24.04 and 26.04 and Debian 12 and 13: check, apply, reboots, health checks, refusals, idempotence and the updates role's task coverage | `make test-updates` (`OS=debian-12` picks the release) | every pull request and push to `main` (in the required "Integration tests" check) |
| Baseline tests | the baseline on each other release it supports (Ubuntu 22.04 and 24.04, Debian 12 and 13, CentOS Stream, Rocky Linux and AlmaLinux 9 and 10; `tests/run.sh` covers Ubuntu 26.04), against a stand-in for a freshly installed server: check mode, two runs (the second must change nothing), `tests/verify-baseline.yml`, the distribution's unattended-upgrades origins, the reboot check (Debian: a newer kernel in `/boot`; the RHEL family: `dnf needs-restarting` after a core package is installed again), a release the OS catalog doesn't list refused (and run with `baseline_allow_unsupported`), a move to port 2222, notifications and the mail relay, and the baseline roles' task coverage (each run counting its own family's task files) | `make test-baseline` (`OS=debian-12` picks the release) | every pull request and push to `main` (in the required "Integration tests" check) |
| Web UI unit tests | the server against the real roles and ansible-core: sign-in, guards, the API behind every page and form and its validation, the inventory and Vault it writes, runs, the page shell; and the app: every page against a stand-in API, accessibility (axe-core and structure), the colour tokens' contrast, the theme, the router, the log; at least 80% statement coverage each | `make webui-test` | every pull request and push to `main` (required) |
| Web UI browser tests | the app in Chromium against the server and the mock Proxmox API: the whole setup and a run by keyboard only; Tab order, visible focus and no keyboard traps on every page; focus after each action, the phone menu, the run's live status; axe-core's WCAG 2.1 A and AA rules with real layout (and so contrast) on every page in every theme | `make webui-browser` | every pull request and push to `main` (in the required "Web UI tests" check) |
| Image smoke test | the container image built from the checkout, driven like a browser against the mock Proxmox API, including a real `ansible-playbook` run; then `start.ps1` | `make smoke` (needs Docker) | every pull request and push to `main`, and on the release image before it is pushed |
| VMs page integration | the image, the mock Proxmox API and an Ubuntu 26.04 systemd container standing in for a VM the project didn't create: host-key pinning, a check run, the real run after a confirmation, a second check run that changes nothing, a guest claiming another VM's address refused, the audit log, and no secret on the VM | `make webui-vms` (needs Docker) | every pull request (not a required check) |
| Fuzzing | the code that turns inventory values into files and commands, and the OS catalog tool's parsers, with arbitrary inputs | `make fuzz` (Linux x86-64) | every pull request and push to `main`, ten minutes per target weekly |
| OS catalog tool | `tools/update_os_catalog.py` offline, against made-up vendor indexes signed with a throwaway key: the newest build is chosen, signatures by other keys, changed checksum files and missing signatures are refused, unsafe values are refused, the catalog file reads back the same; at least 80% statement coverage | `make catalog-test` (needs `gpg`) | every pull request and push to `main`, in "Lint + syntax check" (required) |
| OS catalog check | every image the catalog pins, against the real vendors: the signature on the checksum file (pinned keys), the checksum, and that the image can still be downloaded; newer builds are listed | `make catalog-check` | pull requests that change the catalog, the script or its keys; weekly, opening or updating an issue when a pin fails or a newer build is out (and closing it when the catalog checks out again) |
| Real installer ISO | the real Ubuntu 26.04 live-server ISO the OS catalog pins, checked against the catalog's checksum and again against Ubuntu's real signature on `SHA256SUMS` with the shipped key (as for an unpinned point release), and its remastering; then an install from it in QEMU with Secure Boot and a TPM 2.0, with an encrypted disk, and boots of the result | `tests/real-iso.sh` (set `USAS_ISO_CACHE` to a folder holding the ISO to skip the download, and `USAS_LOG_DIR` to keep each play's log; a failed play shows its failed tasks), then `tests/secure-boot-install.sh` (Linux with KVM) | weekly, and on pull requests that change the installer roles, the OS catalog or the installer's configuration; CI caches the ISO by its SHA-256 and keeps the logs of a failed run for 7 days |
| Real run | a disposable VM on a real Proxmox VE node | `make provision`, `make baseline` with `LIMIT=` | no, by hand (see the test policy), and before every release with the [pre-release checklist](release-checklist.md) |

## The integration tests

`tests/run.sh` runs the real playbooks, unchanged, against stand-ins for the systems they talk to. Nothing
leaves the machine except the container image builds:

| Real system | Stand-in |
| --- | --- |
| Two Proxmox VE nodes of one cluster (SSH as root and as the node user `prepare-node.yml` creates, ISO storage, a fake `pvesm` that runs for root only) | two Debian containers from `tests/images/pve-node`, `pve-test` with SSH on `127.0.0.1:2211` and `pve-test-2` on `2212`. Each has its own `local` storage folder; a Docker volume mounted at the same path in both stands in for the cluster's shared NFS storage (`synology-nfs`). Their `xorriso` keeps a copy of each seed's user-data for the network checks |
| releases.ubuntu.com and the OS catalog | a small ISO with Ubuntu's GRUB menu (`tests/fixtures/grub.cfg`), pinned as Ubuntu 26.04's installer in a copy of the real catalog that the tests pass as extra vars, and a `SHA256SUMS` in Ubuntu's format, signed (`SHA256SUMS.gpg`) with a test key made when the image is built, served inside that container. Only the test key's public half and fingerprint leave the build; `tests/run.sh` points `installer_signing_keyring` and `installer_signing_fingerprint` at them |
| The Proxmox VE API | `tests/mock_pve.py`: the API calls the roles make, on `https://127.0.0.1:8006`, for a cluster of the two nodes with storages per node (`/nodes/<node>/storage`; `usb-iso` only on `pve-test`). A VM lives on the node it was created on, and calls for it must name that node. After a VM's first start it reports "running" for a few polls, then "stopped", as the installer does when it powers off. VM 4099 never stops. Once an installed VM runs, its guest agent lists one interface per network card with the card's MAC address; VM 4063's `net0` has a DHCP lease, the others only link-local addresses, and VM 4066's agent may not be asked. With `--fixture-vms` (the image smoke test and the browser tests) it also lists fixed VMs with guest agents for the web UI's VMs page: Ubuntu 26.04, 24.04 and 22.04, Debian, Windows, an agent that isn't running, a slow one, one the token may not ask, a stopped VM, a template, a container and hostile strings |
| github.com/&lt;user&gt;.keys | a keys file the mock serves on `http://127.0.0.1:8081` |
| A freshly installed VM | an Ubuntu 26.04 container with systemd, socket-activated SSH, sudo-rs and the admin user, as the autoinstall leaves it (`tests/images/ubuntu-target`), SSH on `127.0.0.1:2201` and `2222` |

The test keys, the mock's TLS certificate and `known_hosts` are created in a temporary directory for each
run; your own `~/.ssh` is never touched. The steps:

1. **build-installer** twice with `local` storage: each server's installer ISO is remastered on its node,
   26.04 on `pve-test` and 24.04 on `pve-test-2` (whose `srv-two` has `vm_os: ubuntu-24.04`), each has the
   `autoinstall` kernel argument and a 3-second GRUB timeout, and the second run changes nothing. With `--limit` leaving the second node out it is skipped, and said so; a node whose host key
   isn't trusted is named before any node builds; servers naming nodes the cluster doesn't have are refused
   in one error, by both playbooks, before anything is created (they're in `ubuntu_servers`, the group's name
   before 0.7.0, which is read and said so). With the shared `synology-nfs` storage the
   ISO is built once, by `pve-test` only (only it has that run's work directory), and a storage a node
   doesn't have is refused. With the real catalog, whose checksum the stand-in ISO doesn't have, the
   download is refused with what to do, and the copy in storage is kept. With a catalog that pins another point
   release and `ubuntu_point_release` set, the ISO is checked against the signed `SHA256SUMS` instead (and a second
   run changes nothing); then it must refuse a `SHA256SUMS` whose checksum was swapped (bad signature), one
   with no `SHA256SUMS.gpg`, one signed by a key other than the pinned one, and a malformed fingerprint.
2. **provision-vms** for `srv-01`: the VM is created with the expected hardware (an EFI disk with Secure
   Boot keys pre-enrolled, no TPM), both ISOs attached, the install awaited, the ISOs detached, the boot
   order set to disk only, the seed deleted, the VM started and its host key trusted. A second run, with
   `vm_secure_boot: false`, skips the existing VM and changes nothing on it. The mock stores `efidisk0` and
   `tpmstate0` the way Proxmox does and refuses what Proxmox would (keys on a 2 MB EFI disk, an EFI disk
   without OVMF, an unknown TPM version). `srv-two` is created through `/nodes/pve-test-2/...`, with its seed
   ISO on that node and the 24.04 installer; a new VM on a node whose host key isn't trusted isn't created.
   OS settings are refused before anything is created: a `vm_os` the catalog doesn't have, an image without
   its family, Debian with `autoinstall`, disk encryption with `cloud_image`, an `ubuntu_release` that
   disagrees with `vm_os`, and malformed values. `tests/verify-aliases.yml` sets every name renamed in 0.7.0 under its
   old name and checks the new names hold the values, and that the run lists the old names.
3. **provision-vms** for `srv-hang`, whose install never finishes: the run must fail and must delete the
   seed ISO (which holds the password hash). The node must have no seed files left.
   Then the networks: `srv-dhcp` (`net_mode: dhcp`) is provisioned with the address its guest agent reports
   and reached there, and a rerun finds it again without changes; `srv-vlan` gets a VLAN on `net0` and two
   more cards, and `tests/verify-network.yml` checks each card's Proxmox setting and the installer's netplan
   configuration (every card matched by its MAC address, one default route). No DHCP lease, an agent the
   token may not ask, options smuggled into a card, an unknown card key, a second default route and a
   repeated MAC address must each stop the run with a message.
   **Cloud images** for `srv-cloud` (Debian 13, `cloud_image`, on the NFS storage, which the mock gives Import
   content): every catalog image is replaced by a small stand-in that the nodes serve like the ISOs.
   `build-installer` downloads Debian's once into the shared storage's Import folder (SHA-512 checked), changes
   nothing on a second run, keeps the good copy when a catalog checksum doesn't match, and refuses a storage
   without Import content. Rocky Linux 10 is refused with a CPU type without x86-64-v3 and on a node whose CPU
   lacks AVX2 (`provision_cpuinfo_file` points at an Ivy Bridge and a Haswell `/proc/cpuinfo` on the test
   node); AlmaLinux 10 isn't checked. Then the VM: `tests/verify-cloud-image.yml` checks that the create call
   imports the image (`import-from`) and attaches only the seed, that the disk was grown to `vm_disk_gb`, that
   the seed was detached after the first boot, and the seed's `user-data` (the admin user, keys, the sudoers
   file, cloud-init disabled afterwards, power-off), `meta-data` and `network-config` (matched by MAC
   address), and that no seed is left.
   **Extra disks and placement** for `srv-disks` (two disks, one `xfs` on the NFS storage, in `k8s_lab_agents`):
   `tests/verify-disks.yml` checks that the create call has only `scsi0` and the agent's tag, and that both disks
   were added, with their serials, in the one config update that detached the installer's CD-ROMs; the mock
   refuses a disk on a storage without disk images or with a bad serial. `tests/inventory/spread.yml` puts
   `srv-01` in `srv-disks`'s spread group: with neither in the run, the check finds `srv-01`'s VM running on
   `srv-disks`'s node and refuses, as it refuses a server in two clusters' groups and disk settings that would
   add an option. The baseline's `data_disks` role runs on loop devices named like QEMU's disks: it formats
   and mounts both, changes nothing on a second run or in check mode, leaves a disk holding another file system
   as it is, and names a missing one.
   **Disk encryption** for `srv-luks`: without Secure Boot, with a layout other than `lvm`, with a malformed
   TPM state storage or with passphrases that are short, hold a line break or braces, the run must stop
   before anything is created. Then a run with `-v --diff` and `pve_hide_secrets: false`, whose output must
   not contain the passphrase, and `tests/verify-disk-encryption.yml`: the VM has `tpmstate0` (TPM 2.0, on
   `pve_tpmstate_storage`), the passphrase never reached the API, and the user-data the installer got (the
   test node's `xorriso` keeps a copy, `tests/fixtures/xorriso`) has the LUKS layout, `tpm2-tools`, the key
   file and enrollment script in the installer's `/run`, and the late commands, while `srv-01`'s has none of
   them. On the stand-in server (with `blkid` and `systemd-cryptenroll` stubbed for the run), the first-boot
   tasks must have bound the TPM slot to PCR 7 and deleted the installer's copy of its configuration. The
   enrollment script itself then runs in a container with stubbed `blkid`, `systemd-cryptenroll` and
   `dracut`: it must add `tpm2-device=auto` to the root disk's crypttab line only, write the dracut drop-in,
   and change nothing on a second run.
4. **A node user other than root**: `prepare-node.yml` must refuse `root` and a storage that doesn't allow
   ISO images, then create `usas-build` (twice: the second run changes nothing). As that user,
   **build-installer** (a rebuild, then a run that changes nothing, then the NFS storage) and
   **provision-vms** for `srv-user`; `tests/verify-node-user.yml` checks the account (no groups, no
   password), the ACL entries on both ISO folders, who owns the ISOs, the work directory and the recorded
   folders, and that no seed is left. A login user that isn't `pve_ssh_user`, a malformed user name and a
   storage `prepare-node.yml` didn't set up must be refused.
5. **baseline** in check mode (`--check --diff`) on the fresh `srv-01`, also with a new SSH port and with an
   admin user that doesn't exist yet: nothing may fail and nothing may change. Then the baseline twice,
   then `tests/verify-baseline.yml`: effective `sshd -T` settings, UFW rules and policies, the Fail2Ban jail,
   the sudoers file, the timezone, automatic updates, the removed installer answers file, exactly the
   published key and the extra key in `authorized_keys`, and password and root logins refused. cloud-init's
   copies of the user-data must be gone too: `user-data.txt*` always, `cloud-config.txt` and `obj.pkl` only
   where the stand-in `cloud-init` reports itself disabled (the Debian and RHEL-family stand-ins). A check run
   on the finished server must report no changes.
6. **Key sync**: the baseline reaches `srv-dhcp` at the address its guest agent reports, without changes; a
   key that is no longer published is removed; an empty key list is refused rather than
   emptying `authorized_keys`.
7. **Port change** to 2222: the firewall opens the new port before sshd moves, the old rule is removed,
   Fail2Ban follows, and a rerun on the new port changes nothing.
8. **Password-required sudo**: the passwordless sudoers file is removed and Ansible carries on with the
   become password. The server's `sudo` is sudo-rs, as on Ubuntu Server 26.04, so this goes through the
   classic sudo (`/usr/bin/sudo.ws`); with that hidden, the baseline must stop before changing anything.
9. **Optional hardening and notifications**, all turned on with `tests/hardening-vars.yml`: a run, a rerun
   that must change nothing, then `tests/verify-hardening.yml`: the `sysctl` drop-in holds exactly the
   expected keys and the kernel has their values, the audit rules and the immutable flag are in place,
   AppArmor is enabled, the time service would use exactly the configured time sources
   (`tests/verify-time-sync.yml`: the sources in chrony's configuration and sources folders on Ubuntu 26.04), apt reads the mail settings, msmtp's files are root-only and `msmtp --pretend`
   reads the relay settings, the relay password reached its file unchanged (quotes, a backslash and `#`
   included), and Fail2Ban has both actions. A ban of a documentation address must reach a webhook
   listener in the container with the right message. A check run must report no changes, values that could
   inject settings must be refused, and turning everything off must remove the files again (and bring the
   OS's own time sources back). In a container
   auditd can't run and AppArmor's policy is the host's, so those two live checks are skipped with the
   reason in the log (the [pre-release checklist](release-checklist.md) covers them on a real VM).
   Kernel-wide `sysctl` keys would change the Docker host's own kernel, so outside CI (`CI=true`) the test
   leaves them out and checks only the per-network-namespace ones. A container can't see some keys at all
   (`net.core.bpf_jit_harden`); the role reports and skips those, and the test checks the rest.
10. **Task coverage**: `tests/callback_plugins/task_coverage.py` records every role task and handler that ran
   (not skipped), and `tests/coverage.py` fails the run if fewer than 80% of them did (every role but
   `updates`, which `tests/updates.sh` measures the same way). Ansible has no
   statement coverage tool; a task is its unit of execution, so this is the closest equivalent. The tasks
   the containers can't reach (waiting for `cloud-init`, the QEMU guest agent, reboots) are listed in the
   report and covered by the real-run part of the test policy and the
   [pre-release checklist](release-checklist.md).

UFW rate-limits new SSH connections (6 in 30 seconds per address), and in the tests every connection comes
from Docker's one address, so the script pauses between some steps.

## The updates tests

`tests/updates.sh` runs `playbooks/updates.yml` against two containers built from
`tests/images/ubuntu-target` (stage `updates`, for the release in `OS`, Ubuntu or Debian): `upd-usas` stands in for a server
this project installed (`servers`, sudo with a password), `upd-other` for one it didn't
(`other_servers`, its own user, passwordless sudo), on `127.0.0.1:2341` and `2342`. The image replaces
Ubuntu's archive with a repository built at image build time (`test-repo.sh`): small packages with newer
versions in a `test-updates` and a `test-security` suite, a held one, one phased at 0 %, one whose update
asks for a reboot (standing in for the kernel) and one standing in for the Secure Boot chain. On Debian, which
writes no reboot-required file, the reboot steps read that file all the same (`updates_os_reboot_check=file`):
a container runs the Docker host's kernel, so a newer kernel in `/boot` would never be booted. A step of its own
checks the kernel comparison instead: a `vmlinuz` newer than the running kernel in `/boot` makes a check run
report the reboot and name `linux-image-<version>`, and removing it clears it. Its
`Release` files are signed with a key made in the build stage; only the public key is in the image, and
apt checks it as it would Ubuntu's. A container shares the Docker host's kernel, whose boot ID doesn't
change, so the image writes a new ID to `/run/usas-boot-id` at each start and the tests point
`updates_boot_time_command` at it; the containers restart on reboot (`--restart=always`).

The steps: a check run reports the pending, security, held and phased packages and the automatic-update
line, and changes nothing; security updates only; everything but an excluded package, whose reboot waits
(`updates_reboot: never`); a second run changes nothing; a reboot held outside its window; everything with
`if_required`, where the container must restart and pass its health check, then a second run changes
nothing; on an encrypted VM, the Secure Boot stand-in holds the reboot; windows, opt-outs and unsupported
releases skip servers; settings that can't be honoured are refused; a busy dpkg lock makes the server
`busy`, then it is updated; a server without Python is skipped and one that doesn't answer fails the run, as
`unreachable`, also when it stops answering after its reboot (a unit stops SSH while the health check waits);
a service the update breaks fails its server's health check and keeps the other server from starting
(`updates_serial=1`); a `dist` upgrade installs all but the held package; and notifications reach a
stand-in relay (`tests/notify_sink.py`: SMTP with STARTTLS and a certificate the run trusts through
`SSL_CERT_FILE`, sign-in checked) and a stand-in webhook, only when something changed, while a relay whose
certificate isn't trusted fails the run and neither the relay password nor the webhook's token appears in
the output; and the cluster inventory against `tests/mock_pve.py --fixture-updates` (a cluster of two nodes
with a VM for each case): `upd-usas` keeps its name and gains its live node, a VM outside the inventory is
`vm-<vmid>` and updated through its pinned alias, one whose address leads to the other container is refused
by its pin, an excluded VM is absent, the VMs to leave alone (container, template, stopped, locked, Windows,
no address, tag `usas-no-updates`) are reported without being contacted, and a cluster file with a bad
filter stops the run. Snapshots run against the same mock: a VM whose disks can't have one is skipped, a
refusal for lack of `VM.Snapshot` fails it with that name, a healthy update takes one on the node the VM runs on
now (`vmstate` 0) and deletes only the playbook's own old snapshot, never the operator's two (one named like
the playbook's), the storage warning appears, `updates_snapshot_required: false` updates a VM without one, an
unhealthy VM keeps its snapshot, and the API token never appears in the output. Waves run against it too:
two VMs fit in one wave of two until both carry the tag `usas-group-web`, or may both reboot on a node that
allows one, and an apply then runs the update play in two batches. Each container gets host keys of its own for this. Each checks the run's JSON report. Last, at least 80% of the updates role's tasks must have run. With `USAS_LOG_DIR` set, a failed run
keeps each play's log, the reports and each container's failed units and recent warnings there; CI
uploads them as an artifact for 7 days. It takes about 6 minutes per release.

## The baseline tests

`tests/baseline.sh` runs only the baseline, against one container built from `tests/images/ubuntu-target`
for the release `OS` names (an Ubuntu LTS release, `debian-12` or `debian-13`, or `centos-stream-`, `rocky-` or
`alma-` with `9` or `10`; `debian-13` by default). A Debian or RHEL-family container stands in for a VM
installed from the vendor's cloud image: classic `sudo`, `sshd` with no socket activation, and the admin user
(in `wheel` on the RHEL family) with the sudoers file the cloud-init seed writes. On the RHEL family firewalld
runs in the container; SELinux doesn't (containers have none), so the SSH port's SELinux label isn't covered
here: the pre-release checklist runs the baseline on real RHEL-family VMs.
It uses `tests/inventory/hosts.yml` and `tests/verify-baseline.yml` as `tests/run.sh` does, and the same
container name and ports, so only one of the two runs at a time. The steps: check mode on the fresh server
changes nothing; a first run, a second that changes nothing, and the checks; check mode again; the
unattended-upgrades origins (no ESM on Debian); a waiting reboot reported and then gone (Debian: a kernel in
`/boot` newer than the one running; Ubuntu: `/var/run/reboot-required`; the RHEL family: nothing right after a
restart, then `glibc` installed again); SSH moved to port 2222 and checked there; notifications, the mail relay,
passwordless sudo and the time source settings of `tests/hardening-vars.yml` on, a rerun that changes nothing,
the time service's sources checked (`tests/verify-time-sync.yml`: systemd-timesyncd's merged settings, or the
sources in chrony's configuration on the RHEL family), then all off again with the OS's time sources back; on
Ubuntu and Debian, the time sources on, a rerun and off again once more with chrony installed in place of
systemd-timesyncd; and at least 80% task coverage of `common`, `firewall`, `ssh_hardening` and `fail2ban`,
counting the task files of the server's own family only (`tests/coverage.py --skip-files`).

## Secure Boot and disk encryption in a VM

What the containers can't show, `tests/secure-boot-install.sh` checks in a real VM, on a CI runner with
KVM, after `tests/real-iso.sh` has remastered the real ISO (`.github/workflows/installer-iso.yml`). The VM is
set up as Proxmox sets up the role's VMs: q35, OVMF with Secure Boot and Microsoft's keys enrolled, a
TPM 2.0 (swtpm), a VirtIO SCSI disk and the two CD-ROMs, with the seed rendered from the role's own
template for an encrypted disk. It installs unattended, then:

- the installed system reports Secure Boot enabled (the shim, GRUB and kernel from the remastered ISO, and
  then the installed ones, were verified by the firmware);
- the root disk is LUKS2 with two key slots, the passphrase's and a TPM2 token, and crypttab (and the
  initrd's copy) has `tpm2-device=auto` and `tries=5`; the first boot needs nobody to type a passphrase;
- the role's `first_boot.yml`, run by Ansible as provisioning runs it, binds the TPM slot to PCR 7 alone,
  and two reboots after that stay unattended;
- the passphrase opens the volume, and after `first_boot.yml` no file on the disk holds it;
- if a boot stops at the passphrase prompt, the test types the passphrase through QEMU's monitor and prints
  what the system says about its crypttab, initramfs, LUKS header, PCR 7 (also as the installer measured
  it) and boot log, then fails;
- recovery as [security.md](security.md#secure-boot-and-disk-encryption) describes it: with the TPM slot
  wiped, the VM stops at the passphrase prompt (on the serial console here, where the test types two
  wrong passphrases, each of which must bring the prompt back, then the right one), comes up, and
  enrolling the TPM again brings back unattended boots.

The VM's serial logs, screen dumps and the LUKS header dump are kept as a workflow artifact when it fails.
It needs about 3 GB for the ISO and up to 8 GB for the VM's disk, and takes 20 to 30 minutes.

## The OS catalog

`roles/os_catalog/vars/catalog.yml` pins every image the project installs (see
[interfaces.md](interfaces.md#the-os-catalog)). Refreshing it is a maintainer's job, done in a pull request:

1. `make catalog` reads each vendor's index, checks the signature on every signed checksum file with `gpgv`
   against the fingerprints pinned in `tools/update_os_catalog.py`, and writes the newest build of every
   supported release. It needs `gpgv` and network access, and nothing else (standard-library Python).
2. Review the diff: the URLs stay on the vendors' hosts, the builds move forward, and `signed` stays `true`
   wherever it was.
3. Open a pull request. The "OS catalog" workflow checks every pin against the vendors again, and the "Installer
   ISO" workflow remasters and installs the newly pinned Ubuntu ISO.

The weekly run of the "OS catalog" workflow opens an issue titled "OS catalog: refresh needed" (or updates
the open one) when a pinned image is gone, no longer matches, or a newer build is out, and closes it once the
catalog checks out again. A gone pin breaks runs that need that image, so it calls for a patch release.

When a vendor replaces a signing key, the check fails with "isn't by <fingerprint>": confirm the new key on the
vendor's own site (security.md lists where), replace the file in `tools/os-catalog-keys/` and the fingerprint
in the script, and say so in the pull request. A new supported release is a new line in the script's
`RELEASES` (with its end-of-life date) and, for a new vendor, a finder and a test.

`make catalog-test` runs the script's unit tests offline: made-up indexes for every vendor, signed with a
throwaway key made for the run.

## Fuzzing

`tests/fuzz/` holds [Atheris](https://github.com/google/atheris) fuzz targets for the places where
inventory values become configuration files or shell commands, for the web UI's status API token
header, for what servers report to the updates playbook, and for what the OS catalog tool reads from the vendors.
Each target for the roles loads the real template, Jinja
expression or allowlist pattern from the role (`tests/fuzz/roles.py`), renders it with Ansible's own
filters and tests, and checks a property for every input the role would accept:

| Target | What it renders | Property |
| --- | --- | --- |
| `fuzz_user_data.py` | `roles/proxmox_vm/templates/user-data.j2` and `meta-data.j2` | the YAML parses and every field holds exactly the given value; the two root shell commands split into exactly the expected words; with disk encryption, the passphrase is exactly the layout's password and the key file's content and nowhere else, the enrollment script is the same for every input, and every passphrase the role accepts is 12 to 512 printable ASCII characters without braces |
| `fuzz_ssh_keys.py` | the authorized-keys expression in `proxmox_vm`, `common` and `proxmox_node_user` | all three roles agree; every entry is one trimmed line of the input that starts with a key type |
| `fuzz_node_user.py` | `pve_ssh_user_pattern`, and the expression in `os_catalog` that reads the ISO folder from the file `prepare-node.yml` writes | the user pattern accepts exactly what `common_username_pattern` does; the folder taken is the storage's entry, an absolute path without control characters, or nothing |
| `fuzz_checksums.py` | the checksum expression in `installer_iso` | the result is empty or a checksum from a line naming exactly the ISO |
| `fuzz_os_settings.py` | `os_catalog_id_pattern`, `vm_os_image_url_pattern` and `vm_os_image_sha256_pattern` in `roles/os_catalog/vars/main.yml`, against the catalog's ids | every catalog id passes; an id that passes is a plain lowercase name, which with a SHA-256 that passes makes a file name Proxmox's Import content accepts; a URL that passes is HTTPS with a host, no user, query, fragment, spaces or control characters, and names a disk image |
| `fuzz_os_catalog.py` | `tools/update_os_catalog.py`: the directory-listing and checksum-file parsers (vendor input), and the catalog file's reader and validation | only plain file names (not `.` or `..`, no path) and hex checksums of the right length come out of a listing or checksum file, exactly as they appear; any catalog text fails with the script's own error or reads as entries with only HTTPS URLs on the vendors' hosts, plain file names and sha256/sha512 checksums, which write back to the same text |
| `fuzz_configs.py` | the sshd drop-in, `jail.local` and the Fail2Ban webhook action | every setting appears once with exactly the given value; nothing else is added; the webhook URL is the `curl` command's last argument, whole, with nothing Fail2Ban would interpolate |
| `fuzz_node_plan.py` | `roles/proxmox_vm/filter_plugins/pve_cluster.py`: the node and ISO storage checks and the ISO plan; `provision_hostname_pattern` for node names | exactly the hosts with an unknown node or an unusable storage are reported; each installer ISO a server needs is built in a node-local storage by its node when the node is in the run, and in a shared storage exactly once by a node that needs it there, nothing else; an accepted node name is safe in an API path |
| `fuzz_data_disks.py` | `roles/data_disks`: the disk patterns and the `data_disks_state` filter; `pve_extra_disks` and `pve_spread_conflicts` in `roles/proxmox_vm/filter_plugins/pve_cluster.py` | a disk the patterns accept becomes exactly one Proxmox disk option with the given storage, size and a serial of at most 20 safe characters; a disk counts as blank (and is formatted) only when `blkid` found nothing, and as ready only with its own file system type and label and no partition table; the spread check reports exactly the groups that would put two VMs on one node, an existing VM counting where it runs |
| `fuzz_baseline_extras.py` | the `hardening_sysctl_settings` expression and `sysctl.conf.j2`, unattended-upgrades' mail settings, `msmtprc.j2`, the time servers' assert and `chrony.sources.j2` and `timesyncd.conf.j2` | the merged settings drop exactly the null keys; each sysctl key, apt option and msmtp command appears once with exactly the given value; every accepted time server or pool is one chrony directive with only `iburst` added, and the timesyncd drop-in has only `[Time]` and one `NTP=` line with exactly the given names |
| `fuzz_cluster_inventory.py` | `plugins/inventory/usas_pve.py`: `plan()` over any cluster listing, configs, agent answers, static inventory and filters; `option_problems` | every host is a static host by `pve_vmid` or `vm-<vmid>`, once; groups are fixed or prefixed; excluded and filtered-out VMs never appear; VMs to leave alone are in `usas_pve_skipped` only; every other VM outside the inventory is pinned by its alias with its own control path and an address it or the inventory reported; static hosts get no address or SSH arguments from the cluster; filter values with spaces, quotes, `;` or line breaks are refused |
| `fuzz_update_reports.py` | `roles/updates/filter_plugins/usas_updates.py`: the parsers of `apt-get -s`, `apt-mark showhold`, `needrestart -b` and `apt-config dump`, the choice of updates, the health comparison, the summary and notifications, and the maintenance windows; the role's patterns | names and versions match the role's patterns, lists stay under their caps, text is clean; held, excluded, new and (for security) non-security packages are never chosen and every other update lands in exactly one list; a window is open exactly when a minute-by-minute walk of its time zone says so, across daylight-saving changes, and an invalid one never is |
| `fuzz_update_plan.py` | `roles/updates/filter_plugins/usas_updates.py`: the wave planner and update groups, the storages read from a VM's config and the nearly full ones, the explanation of a failed API call; the role's wave and snapshot patterns | every server with a plain name is planned exactly once and nothing else is; no wave is empty, larger than `updates_serial`, or over the group or node limit; a server is in a later wave only when no earlier one had room for it; groups are plain lower-case names from `updates_group` and `usas-group-` tags only; the clean-up names only snapshots with both the `usas_upd_` name (a real time) and the description marker, never the operator's or `current`, each once, and exactly the ones older than the age limit or beyond the number to keep (worked out independently); storage names are plain; explanations are clean |
| `fuzz_status_token.py` | `parse_bearer` and `TOKEN_PATTERN` in `webui/usas_web/status.py` (the status API's `Authorization` header) | any header gives nothing or a well-formed token taken from it (the prefix and 43 URL-safe characters, nothing before or after, no line break); a well-formed header in any case and with blanks around the token always gives it back; only the token a digest was made from matches it |
| `fuzz_vm_inventory.py` | `webui/usas_web/vmplan.py` (the VMs page's run plans and their inventory), `inventory_names`, `parse_limit` and `parse_tags` in `webui/usas_web/runner.py`, and `vm_alias`, `ALIAS` and `Keys.pins` in `webui/usas_web/sshkeys.py` | any listing and settings give a plan or a refusal, nothing else; a plan's inventory loads back to exactly the hosts planned (servers by name, other VMs as `vm-<vmid>` in `usas_vms`), with every typed or reported value `!unsafe` and no line break; a VM outside the inventory gets none of the Vault's secrets, passwordless sudo, a usable IPv4 address and its pinned alias; the plan's limit and tags pass the runner's own checks unchanged; aliases always match `ALIAS`, and any `known_hosts` text yields only aliases |

`make fuzz` runs each target for `FUZZ_SECONDS` (default 60). Atheris only ships Linux x86-64 wheels, so it
runs in CI (`.github/workflows/fuzz.yml`) or on such a machine:
`pip install --require-hashes -r requirements-ci.txt -r requirements-fuzz.txt`. A finding prints the input
and saves it as `tests/fuzz/crash-*` (uploaded as an artifact in CI); fix the role, then add a case to the
integration tests that the fix refuses.

## The web UI

The web UI is two parts: the server (`webui/usas_web`, Python) and the app in the browser
(`webui/frontend`, TypeScript and React). Each has its own tests; `make webui-test` runs both.

### The server

**Unit tests** (`webui/tests/`, pytest, run with `cd webui && python -m pytest`) use
FastAPI's test client with a temporary data directory and the real roles of the checkout. A stand-in
`ansible-playbook` script prints its arguments, so runs, logs, redaction, streaming and stopping are tested
without touching any system. They cover:

| File | What |
| --- | --- |
| `test_schema.py` | every field names a role variable and every pattern in `roles/*/vars/main.yml` is used (except the ones `NOT_FORM_FORMATS` lists: the sysctl mapping's formats, file paths and package lists); defaults, help and patterns come from the role files; the UI's own ranges match the roles' assert tasks; valid and invalid values for every kind of field |
| `test_store.py` | the inventory and Vault round-trip; files are `0600` and the Vault is encrypted; typed values are tagged `!unsafe`, and `ansible` reads the inventory without templating them; an all-digit password and other number-like text load as text, and an older Vault that held them as `!unsafe` is written again at start-up |
| `test_auth.py` | argon2id hashing and rehashing, the setup code, throttling, sessions and their expiry, CSRF tokens |
| `test_passkeys.py` | passkeys with a software authenticator (`softauthn.py`, ES256 with "none" attestation): `USAS_PUBLIC_URL`, adding (the password and a name needed, the options, the same passkey twice, the limit), signing in, and every response that must be refused (another origin or RP ID, no user verification, a replayed, expired or another session's challenge, a signature counter going back, another user handle, unreadable or oversized answers), throttling, "Require a passkey", a broken file failing closed, removal signing out its sessions, the CSRF header, and the log lines |
| `test_status.py` | the status API for dashboards: off until a token exists, making one (sign-in, CSRF and the password needed, shown once, only an HMAC on disk, `0600`), replacing and revoking it, a new Vault password or an unreadable file accepting no token; every refused call (no, malformed or wrong token, the query string, other headers, other methods) with its log line; the throttle for wrong tokens apart from the sign-in's and the per-minute limit; no session or cookie; the host check and HTTPS rule; the token opening no other route; the answer's fields with runs, servers and the cluster, and that it holds no name, address, VM ID or secret; the cluster asked at most once a minute (failures too) |
| `test_app.py` | first run, sign-in and sign-out, open redirects, throttling, password change, the CSRF header (a token in the form isn't enough), `Origin` and `Sec-Fetch-Site`, no cross-origin answers, allowed hosts and HTTPS, cookies and security headers, the page shell and the built app's files, the theme cookie, every page's API and form with its errors, the connection test, the CA, the SSH key and host keys, the servers table, runs, streaming, history and stopping |
| `test_runner.py`, `test_remote.py`, `test_sshkeys.py`, `test_config.py` | the command line and environment of runs, `--limit` checks, the Proxmox and GitHub checks against stand-in responses, key formats and fingerprints, `known_hosts` handling, settings |
| `test_accessibility.py` | the HTML the server sends for every page: language, title, one `h1`, landmarks, the skip link, unique IDs, a message without JavaScript, and nothing inline the Content Security Policy would block ([accessibility.md](accessibility.md)) |
| `test_frontend_fixtures.py` | the API answers the app's tests use (`webui/frontend/test/fixtures/sections.json`) are still what the server answers; after a change to a field, write them again with `USAS_UPDATE_FIXTURES=1 python -m pytest tests/test_frontend_fixtures.py` |

The run fails below 80% statement coverage (`webui/pytest.ini`; about 98% at the time of writing).

### The app

**Unit tests** (`webui/frontend/test/`, [Vitest](https://vitest.dev/) with jsdom and Testing Library,
run with `npm test` in `webui/frontend` after `npm ci`) render the app as the browser would, against a
stand-in for the server's API (`test/server.ts`) that records every call, with the server's real answers
for the settings pages (`test/fixtures/sections.json`):

| File | What |
| --- | --- |
| `a11y.test.tsx` | every page, signed in and out, empty and with errors, with axe-core's WCAG 2.1 A and AA rules and the same structural checks as `test_accessibility.py` |
| `contrast.test.ts` | the WCAG contrast of every colour pair, from `src/styles/tokens.css`, in both themes |
| `app.test.tsx` | the theme switch and what it posts, the router (links, back and forward, titles, focus), sign-in, sign-out, first run, the password change, an expired session, the API client |
| `passkeys.test.tsx` | the base64url helpers, when a page can use passkeys (secure context, WebAuthn, host name), the `navigator.credentials` calls and the JSON they send (with a stand-in authenticator), the sign-in page with and without passkeys and with one required, the account page's list, adding (with errors in the summary), removing (focus to the section) and requiring |
| `status-token.test.tsx` | the account page's status token: off, creating it (the token shown once in a read-only field with the focus, Copy, and the fallback that selects it), the wrong password linked to its field, too many tries, the token's facts, replacing, revoking through the confirmation step with the focus on each step, a broken file, and axe-core in each state |
| `pages.test.tsx` | every page: what it shows from the API, what its forms post (field names, the CSRF header, the button's `action`), errors by field and in the summary, the connection test and the node dropdown, the servers table's rows, the SSH key and host keys, starting, following and stopping a run, the log's colours |

`npm run check` type-checks the app and its tests (TypeScript, strict). The run fails below 80% statement
coverage (`vite.config.ts`; about 98% at the time of writing).

**The smoke test** (`tests/webui-smoke.sh`, `make smoke`) builds the image, starts it next to
`tests/mock_pve.py` on a private Docker network with the hardening from `docker-compose.yml` (read-only
root, no capabilities), and drives it with `tests/webui_smoke.py` (standard library only) the way the app
does: the page shell links the app the image built, its script, stylesheet and fonts are served, and
nothing is inline; a wrong setup code, a post without its CSRF token and one from another origin are
refused; the first password is set; the theme is remembered;
the connection test fails on the mock's certificate until its CA is saved, then passes; every page is
saved; invalid values are refused with the roles' patterns; a provisioning run for a VM that already exists
goes through `ansible-playbook`, which reads the token from the Vault and calls the mock API over verified
TLS; its log streams, contains no secret and lands in the history; adding a passkey starts with a
challenge for `localhost` that needs user verification. Finally it checks that the Vault in the
volume is encrypted, that no file there is readable by others, and that the process isn't root. CI then
starts the same image with `start.ps1` under PowerShell 7.

**The VMs page's runs** (`tests/webui-vms.sh`, `make webui-vms`) start the image, the mock (with
`--vm-target`: VM 4071's guest agent reports a systemd container's address, and VM 4072 claims the same one)
and that container, which has only its own user `ubuntu` with passwordless sudo, on a private network with
names of their own (`usas-vms-*`), so it can run next to `make test`. `tests/webui_vms.py` pins VM 4071's
host key after checking the scanned fingerprints against the container's own `ssh-keygen -lf`, refuses a
plan before that and a real run before a passing check, runs the full baseline in check mode (nothing
installed), then for real after the confirmation (the firewall is on with the VM's own rule, the host name
and the user's password are unchanged, sudo stays passwordless, the UI's key stays), refuses a second real
run of the same check, finds nothing to change in a second check run, and gets a refused connection for VM
4072, whose pinned key isn't the container's. The audit log must name each step, and no secret of the UI may
reach the VM, the run files or the audit log.

### The browser tests

`tests/webui-browser.sh` (`make webui-browser`) runs the app in a real browser with
[Playwright](https://playwright.dev/) (Chromium; `webui/frontend/e2e/`). It sets up what the smoke test
does, without Docker: a certificate for `tests/mock_pve.py` and a published key, the mock, and the web UI
from the checkout on `127.0.0.1` with an empty data directory, whose first-run setup code goes to the
tests; the VM the run provisions already exists in the mock, so the run is a real `ansible-playbook` run
that needs no SSH. The mock runs with `--fixture-vms`, so the VMs page has guests to list. The tests run in
three projects (`playwright.config.ts`):

| File | What |
| --- | --- |
| `flow.spec.ts` | by keyboard only, the way someone without a mouse does it: the skip link; the first-run setup; the Proxmox form typed field by field, the connection test failing on the certificate, saving the CA, the test passing, picking the node from its dropdown (arrow keys, Escape, Tab) and saving; a provisioning run chosen with the arrow keys, started with Enter and followed to the end with focus left where it was and the status changing in a polite live region; then signing in again, with a wrong password first. Leaves a signed-in session for `pages.spec.ts` |
| `pages.spec.ts` | every page: axe-core (WCAG 2.0 and 2.1, A and AA) with real layout in the dark and light themes and "System" with the browser preferring each, with each accent palette (one, Ember, for now), also with a form error and with the phone menu open; Tab order, focus outlines that are visible and not covered, no keyboard trap, Shift+Tab back; the phone menu (Enter, Escape, focus back on the button, closing when focus leaves); the servers table's add and remove; the theme switch with the arrow keys |
| `vms.spec.ts` | the VMs page against the fixture guests: sorting with Enter and Space (`aria-sort`, the caption, focus kept), the filters by keyboard with the count in its live region, each address's source, a hostile guest's markup shown as text, a refresh (with the browser's clock run forward) that keeps the rows and the focus, and axe-core at 375 pixels in the dark and light themes without sideways scrolling. Runs in the `pages` project |
| `status-token.spec.ts` | the status token by keyboard: creating it (the new token gets the focus), Copy, a dashboard's call with it from a client without cookies (the answer's keys, no cookie, a wrong token refused), the last use shown, axe-core on the section and the confirmation in the dark and light themes, and revoking it through the confirmation step. Runs in the `pages` project |
| `passkeys.spec.ts` | passkeys with Chromium's virtual authenticator (DevTools `WebAuthn.addVirtualAuthenticator`, with user verification): adding one by keyboard, signing out and in with it, "Require a passkey" (no password field, axe-core), the explanation at `127.0.0.1`, turning it off and removing the passkey. Runs last, in the `passkeys` project, since requiring a passkey signs out the session the others use |
| `helpers.ts` | walking a page with Tab (`tabCycle`, `checkKeyboard`), axe-core (`checkAxe`), and posting a form as the app does for the pages the flow doesn't fill in by keyboard |

A failing test leaves a screenshot and a trace in `webui/frontend/test-results/` (CI uploads them as the
`playwright-results` artifact); open a trace with `npx playwright show-trace <file>` in `webui/frontend`.
To watch the tests, pass Playwright's options through: `tests/webui-browser.sh --headed`, or
`--debug -g "phone"` for one test.

What a screen reader says can't be tested this way, and isn't tested by hand either
([accessibility.md](accessibility.md#known-gaps)).

## Running them

You need Docker (privileged containers, for systemd), Python with the CI packages and the collections:

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements-ci.txt
ansible-galaxy collection install -r requirements.yml
make test
```

A run takes about 10 minutes. On failure the logs stay in the temporary directory the script prints.
Ports 2201, 2211, 2222, 8006 and 8081 on `127.0.0.1` must be free (2341, 2342, 2345 to 2350 for `make test-updates`). The web UI's tests need the Python
packages and Node 24 with the app's packages (`make app` installs them; then `make webui-test`); the smoke
test needs Docker and ports 18006 and 18080 (`make smoke`). The browser tests (which build the app first) need the
app's packages and the collections installed as above, Playwright's Chromium once (`cd webui/frontend && npx playwright install
chromium`), and ports 18106, 18180 and 18181 (`make webui-browser`; `APP_PORT`, `MOCK_PORT` and
`KEYS_PORT` change them).

## Adding a case

- A new behaviour in a role: add the check to the matching `tests/verify-*.yml` playbook (what should
  happen), and to `tests/run.sh` if it needs its own run, for example with `-e` variables or a `play_fails`
  step for what should be refused.
- A new Proxmox API call: add it to `tests/mock_pve.py`, which answers anything it doesn't know with HTTP
  501, so a missing call fails the test rather than passing silently.
- A new or changed role variable that the web UI shows: add or change its field in
  `webui/usas_web/schema.py` (the schema tests fail until a new allowlist pattern has a field), and a case
  for its valid and invalid values in `webui/tests/test_schema.py`.
- A change to the web UI's server: a test in `webui/tests/` for what should happen and what should be
  refused. A change to the app: a test in `webui/frontend/test/` for what it shows and posts; a new page
  goes into `PAGES` in `a11y.test.tsx` and in `e2e/pages.spec.ts` (and the server's `PAGES` in `main.py`),
  a new colour into `tokens.css` and a pair in `contrast.test.ts`, a new accent palette into `PALETTES` in
  `e2e/pages.spec.ts`, and a new way of using the keyboard (a menu, a dialog) into the browser tests.
- A bug fix: add the check that would have caught the bug before fixing it, and see it fail.
