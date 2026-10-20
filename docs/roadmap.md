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
- A documented end-to-end test against a real Proxmox VE node before every release.

**Provisioning**
- Verify the GPG signature on Ubuntu's `SHA256SUMS`, not only the checksum.
- Optional IPv6 and DHCP network configuration, and more disk layouts (including LUKS encryption).
- Running the ISO build as a non-root user on the Proxmox node.

**Baseline**
- Optional extra hardening, each off by default and documented: kernel and network `sysctl` settings,
  auditd, time synchronisation settings, and limiting `sudo` to a password after provisioning.
- Notifications (mail or a webhook) from unattended-upgrades and Fail2Ban.

**Supply chain**
- Done: CI installs a hash-pinned set of Python packages (`requirements-ci.txt`).
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
- **Hold your secrets.** Secrets stay in your own Ansible Vault; the project won't add a secret store, a
  web UI or a service of its own.
- **Bring back the bash script.** The old `setup.sh` stays in the history only.
