# Roadmap

This is where ubuntu-server-auto-setup intends to go over roughly the next year (from October 2026), and
what it deliberately won't do. It is a statement of intent, not a promise: plans change, and this page is
updated when they do. Decisions about scope are made as described in [GOVERNANCE.md](../GOVERNANCE.md);
ideas and requests are welcome in [GitHub Issues](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/issues).

## Where it stands

The project has just been rewritten from an interactive bash script for Ubuntu 14.04 to 20.04 (still in the
git history, see the README) into Ansible playbooks: a hands-off Ubuntu Server 26.04 install on Proxmox VE
VMs, GitHub SSH keys, and a baseline (SSH hardening, UFW, Fail2Ban, unattended upgrades) that also works on
existing Ubuntu 24.04 and 26.04 servers. **v0.1.0** (October 2026) is the first release of the Ansible
version, signed and with an SBOM and build provenance, and every playbook is covered by integration tests
that run on each pull request. Since then, the releases up to **v0.6.0** have added a container image with a
web UI, Secure Boot and optional disk encryption for new VMs, DHCP and more network cards, several Proxmox
nodes per inventory, passkey sign-in, and a read-only status API for dashboards
([CHANGELOG](../CHANGELOG.md)).

## Over the following year

**Testing**
- Done: integration tests that run every playbook against containers and a mock Proxmox API on every pull
  request, with idempotence checks and an 80% task coverage floor, plus a weekly run against the real
  Ubuntu ISO ([testing.md](testing.md)).
- Done (0.7.0): the baseline tests run on every release the baseline supports, from Ubuntu 22.04 to
  AlmaLinux 10, on each pull request ([testing.md](testing.md)).
- Done: a documented end-to-end test on real Proxmox VE before every release
  ([release-checklist.md](release-checklist.md)).

**Provisioning**
- Done: Ubuntu's GPG signature on `SHA256SUMS` is verified, with its CD image signing key pinned by
  fingerprint, before the ISO's checksum is trusted.
- Done: an OS catalog that pins every supported image by URL and checksum, built from the vendors' indexes
  with their signatures checked against pinned keys, and checked weekly
  ([interfaces.md](interfaces.md#the-os-catalog)).
- Done (0.7.0): the hardened baseline on more operating systems: Ubuntu 22.04, 24.04 and 26.04 LTS, Debian 12
  and 13, CentOS Stream 9 and 10, Rocky Linux 9 and 10, and AlmaLinux 9 and 10 (UFW on Ubuntu and Debian,
  firewalld on the RHEL family); any other release is refused unless you allow it.
- Done (0.7.0): installing all of those from the vendors' cloud images (`vm_install_method: cloud_image`),
  set up by cloud-init in about a minute. Ubuntu keeps its installer ISO as a choice.
- Done: DHCP (the address read from the guest agent) and more network cards with VLANs
  ([interfaces.md](interfaces.md#networks)).
- Done: Secure Boot by default for new VMs, and optional LUKS2 disk encryption unlocked by the VM's
  virtual TPM ([security.md](security.md#secure-boot-and-disk-encryption)), which the web UI's Defaults
  page can also turn on.
- Optional IPv6 network configuration, and more disk layouts.
- Done: the ISO build and the seed files can run as a user without root's privileges on the Proxmox node
  (`playbooks/prepare-node.yml`, [installing.md](installing.md#6-optional-a-node-user-other-than-root)).

**Baseline**
- Done: optional extra hardening, each off by default and documented: kernel and network `sysctl`
  settings, auditd and an AppArmor check ([security.md](security.md#optional-hardening-and-notifications)).
  Limiting `sudo` to a password after provisioning is the default since 0.3.0.
- Done: notifications from unattended-upgrades (mail) and Fail2Ban (mail or a webhook), with an optional
  msmtp relay.
- Time synchronisation settings (chrony or `systemd-timesyncd` servers).

**The web UI and the container image**
- Done: a container image (amd64 and arm64, signed, with an SBOM and provenance) with a web UI that lays
  out the options, writes the inventory and the Vault, and runs the playbooks with a live log, so the
  project runs on Windows with Docker Desktop ([web-ui.md](web-ui.md)).
- Done: passkeys (WebAuthn) as another way to sign in, or the only one with "Require a passkey", on
  `http://localhost` or under the host name in `USAS_PUBLIC_URL` ([web-ui.md](web-ui.md#passkeys)).
- Done: an opt-in, read-only status API for dashboards such as Heimdall, with a token of its own and no
  names, addresses or secrets in its answer ([web-ui.md](web-ui.md#dashboards-heimdall)).
- Done: several Proxmox nodes per inventory: a node per server, picked from the cluster in the web UI,
  with the installer ISO built once per shared storage and on each node for node-local storage.
- Done: a new look (the USAS name and mark, a dark theme by default with light and system themes, the
  Atkinson Hyperlegible fonts), with axe-core and a contrast check of every colour pair in CI.
- Done: an automated keyboard and axe-core check of every page in a real browser in CI (Playwright,
  Chromium).

**Supply chain**
- Done: CI and the image install hash-pinned sets of Python packages (`requirements-ci.txt`,
  `requirements-webui.txt`).
- A tested list of collection versions.

**Security and project health**
- Keep the [OpenSSF Best Practices](https://www.bestpractices.dev/projects/15740) Passing and Silver
  criteria and the OpenSSF Baseline levels 1 and 2 met, and keep the OpenSSF Scorecard score up.
- Signed releases with checksums, an SBOM and SLSA provenance from v0.1.0 on.
- Test every new point release of the supported systems and every Proxmox VE release, and follow Ansible's
  supported versions.

## What ubuntu-server-auto-setup will not do

- **Touch existing VMs' disks.** Provisioning creates VMs that don't exist; it will never reinstall,
  resize or rebuild an existing one. Rebuilding is a deliberate manual step (delete the VM, then run it).
- **Support other hypervisors or clouds.** It is built for Proxmox VE. The baseline works on any server
  running a supported release, but installing elsewhere (VMware, Hyper-V, public clouds) is out of scope.
  (Cloud images are used, as disk images on Proxmox VE.)
- **Support non-LTS or end-of-life releases**, or distributions beyond Ubuntu, Debian and the RHEL family
  above. The OS catalog lists exactly what is supported.
- **Manage applications.** It produces a hardened, up-to-date base server. Web servers, databases,
  containers and their configuration belong in your own playbooks on top.
- **Be a compliance benchmark.** It won't claim CIS or STIG conformance; optional hardening stays opt-in
  and documented.
- **Hold your secrets elsewhere.** Secrets stay in an Ansible Vault you own: on your control node, or in
  the web UI's data volume on your own machine. The project won't add a hosted service, a secret store of
  its own, or user accounts.
- **Become a multi-user automation platform.** The web UI is a local tool for one administrator; for teams,
  use an Ansible UI built for that.
- **Bring back the bash script.** The old `setup.sh` stays in the history only.
