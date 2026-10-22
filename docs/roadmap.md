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
that run on each pull request.

## Over the following year

**Testing**
- Done: integration tests that run every playbook against containers and a mock Proxmox API on every pull
  request, with idempotence checks and an 80% task coverage floor, plus a weekly run against the real
  Ubuntu ISO ([testing.md](testing.md)).
- Run the baseline tests against Ubuntu 24.04 as well as 26.04.
- Done: a documented end-to-end test on real Proxmox VE before every release
  ([release-checklist.md](release-checklist.md)).

**Provisioning**
- Done: Ubuntu's GPG signature on `SHA256SUMS` is verified, with its CD image signing key pinned by
  fingerprint, before the ISO's checksum is trusted.
- Done: DHCP (the address read from the guest agent) and more network cards with VLANs
  ([interfaces.md](interfaces.md#networks)).
- Optional IPv6 network configuration, and more disk layouts (including LUKS encryption).
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
- Several Proxmox nodes per inventory (choosing the node per server).
- Done: a new look (the USAS name and mark, a dark theme by default with light and system themes, the
  Atkinson Hyperlegible fonts), with axe-core and a contrast check of every colour pair in CI.
- Done: an automated keyboard and axe-core check of every page in a real browser in CI (Playwright,
  Chromium). Written, not yet run: a screen-reader walk-through with VoiceOver and NVDA, now part of the
  pre-release checklist ([accessibility.md](accessibility.md#screen-reader-walk-through)).

**Supply chain**
- Done: CI and the image install hash-pinned sets of Python packages (`requirements-ci.txt`,
  `requirements-webui.txt`).
- A tested list of collection versions.

**Security and project health**
- Keep the [OpenSSF Best Practices](https://www.bestpractices.dev/projects/15740) Passing and Silver
  criteria and the OpenSSF Baseline levels 1 and 2 met, and keep the OpenSSF Scorecard score up.
- Signed releases with checksums, an SBOM and SLSA provenance from v0.1.0 on.
- Test every new Ubuntu point release and Proxmox VE release, and follow Ansible's supported versions.

## What ubuntu-server-auto-setup will not do

- **Touch existing VMs' disks.** Provisioning creates VMs that don't exist; it will never reinstall,
  resize or rebuild an existing one. Rebuilding is a deliberate manual step (delete the VM, then run it).
- **Support other hypervisors or clouds.** It is built for Proxmox VE. The baseline works on any Ubuntu
  server, but installing elsewhere (VMware, Hyper-V, cloud images) is out of scope.
- **Support non-LTS or end-of-life Ubuntu releases**, or other distributions.
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
