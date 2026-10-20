# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities using [GitHub's private vulnerability reporting](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/security/advisories/new) rather than a public issue.

Include what you found, how to reproduce it (the playbook, the relevant inventory variables with secrets and
addresses replaced by placeholders, and the Proxmox VE and Ubuntu versions), which version or commit you
tested, and what an attacker gains. If you'd like to be credited under a particular name, or not at all,
say so.

## How reports are handled

This is a personal project maintained by one person (see [GOVERNANCE.md](GOVERNANCE.md)), so these are targets rather than a contractual SLA:

1. **Acknowledge** the report within 7 days.
2. **Triage** it: reproduce the problem, decide whether it is a vulnerability in ubuntu-server-auto-setup (see "Scope" below), and agree its severity with you. If it isn't a vulnerability, you'll get an explanation, and the report may move to a public issue with your agreement.
3. **Fix** it privately in a [GitHub security advisory](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/about-repository-security-advisories), with a check that catches it again where one is practical, and invite you to review the fix if you want to.
4. **Release** the fix, then publish the advisory (requesting a CVE where it applies) with the affected and fixed versions and any workaround, including what to change on servers that were already installed or hardened with an affected version. The aim is to release fixes for critical and high-severity issues within 30 days of the report and others within 90 days, and to keep you updated at least every 14 days until then.
5. **Credit** the reporter in the advisory and the release notes, unless you ask to stay anonymous.

Please keep the details private until the advisory is published, or for 90 days after your report if no fix has been released by then; if you need a different timeline, say so in the report.

## Supported Versions

The latest release and `main` receive security fixes; a release stops receiving them when the next one is published. Details are in [SUPPORT.md](SUPPORT.md).

## Published vulnerabilities

Fixed vulnerabilities in ubuntu-server-auto-setup are published as [GitHub security advisories](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/security/advisories) (with a CVE where one applies), naming the affected and fixed versions, how to tell whether you're affected, and how to fix or work around it, and they're listed in the release notes. How vulnerabilities in the project's dependencies (Ansible, its collections and the Python packages) are handled is described in [docs/dependencies.md](docs/dependencies.md).

## Scope

These playbooks hold a Proxmox API token, log in to Proxmox nodes as root, install operating systems (wiping the target VM's disk) and change SSH and firewall settings on real servers. Treat the control node you run them from, its inventory and its Vault password as sensitive.

What the project does and doesn't protect against is described in [docs/security.md](docs/security.md) (its security requirements), and the reasoning behind it in [docs/assurance-case.md](docs/assurance-case.md) (threat model, trust boundaries and the defences against common weaknesses). Read them before you run it or report.

In scope, for example: secrets (the Vault values, the API token, the password hash) ending up in logs, files or places the documentation says they don't; a server left weaker than [docs/security.md](docs/security.md) says (password SSH still on, root login allowed, the firewall not enabled); the ISO checksum check being bypassable; the seed ISO not being removed; data from GitHub or the inventory being able to inject extra installer or shell commands; a workflow in this repository that leaks credentials or lets untrusted code run with write access.

Out of scope: problems caused by an inventory that is wrong in the ways the README warns about (a lockout from a wrong `ssh_port` or `ufw_allow` is expected behaviour); anyone who already controls the control node, the Proxmox nodes as root, or the GitHub account whose keys are installed; and vulnerabilities in Ubuntu, Proxmox VE, Ansible or its collections themselves (please report those upstream; we'll update or work around them as described in [docs/dependencies.md](docs/dependencies.md)).
