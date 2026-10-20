# ubuntu-server-auto-setup documentation

> [!WARNING]
> **Provisioning creates VMs, and the installer wipes the target VM's disk.** Only VM IDs that don't exist
> yet are touched, but a wrong `pve_vmid` or storage in your inventory still means a new VM and an erased
> disk.
>
> **The baseline changes SSH and firewall settings, and can lock you out** if `ssh_port`, `admin_user`,
> `github_user`, `ufw_allow` or `fail2ban_ignoreip` are wrong. Run against one host first with
> `LIMIT=<host>`, try `ARGS="--check --diff"` first, and keep the Proxmox console open.

## Getting started

- [Quick start](quick-start.md): one VM from nothing to a hardened Ubuntu 26.04 server.
- [Installing](installing.md): what you need, the Proxmox API token and its privileges, the inventory and
  the Vault, and uninstalling.
- [Upgrading, backups and rotating secrets](upgrading.md).
- [Verifying releases](verifying-releases.md): check the signatures, checksums, provenance and tags.

## Using it

The main [README](../README.md) lists the commands and the main variables;
[interfaces.md](interfaces.md) describes every inventory and Vault variable, the commands, and the
systems the playbooks talk to. Each role's `roles/<role>/defaults/main.yml` lists its own variables with
their defaults.

## About the project

- [Architecture](architecture.md), [external interfaces](interfaces.md),
  [security requirements](security.md) and the [assurance case](assurance-case.md).
- [Dependencies and vulnerability management](dependencies.md): how dependencies are chosen and tracked,
  and the remediation policies for dependency and static-analysis findings.
- [Roadmap](roadmap.md), [changelog](../CHANGELOG.md), [support](../SUPPORT.md), [governance](../GOVERNANCE.md),
  [contributing](../CONTRIBUTING.md) and [reporting a vulnerability](../SECURITY.md).
- The project's [OpenSSF Best Practices entry](https://www.bestpractices.dev/projects/15740).
