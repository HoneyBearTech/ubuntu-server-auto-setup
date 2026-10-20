# Support

ubuntu-server-auto-setup is maintained by one person in their own time (see [GOVERNANCE.md](GOVERNANCE.md)),
so this is a best-effort policy, not a contract.

## Getting help

- **Questions, bugs and feature ideas**: [GitHub Issues](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/issues).
  Say which version or commit you run, your Proxmox VE, Ubuntu and ansible-core versions, which playbook
  failed, and what you expected. Replace secrets, host names and addresses with placeholders.
- **Security vulnerabilities**: never in a public issue; see [SECURITY.md](SECURITY.md).
- **Documentation**: [docs/](docs/README.md), starting with the [quick start](docs/quick-start.md).

## Which versions are supported, and for how long

| Version | Supported with | Until |
| --- | --- | --- |
| The latest release | bug fixes and security fixes | the next release is published |
| An older release | nothing | it stopped being supported when the next release came out |
| `main` | bug fixes and security fixes | always (it's where fixes land first) |

There is no release of the Ansible version yet; until the first one (v0.1.0), `main` is the only supported
line.

- A fix is released as a new version (a patch release such as 0.1.1 for fixes only), never applied to an
  older release.
- **A release stops receiving security updates the moment the next release is published.** The release
  notes and [CHANGELOG.md](CHANGELOG.md) say what changed and whether upgrading needs steps; upgrading is
  described in [docs/upgrading.md](docs/upgrading.md).
- Before 1.0, a minor release (0.2.0) may rename variables or change behaviour; such changes are listed in
  the changelog under "Upgrading".
- If a supported line will end differently, it will be announced in the release notes and in this table
  first.

The old interactive bash script (`setup.sh`, Ubuntu 14.04 to 20.04) is not supported; it remains in the git
history only (see the README's "Previous version").

## Which platforms are supported

| What | Supported |
| --- | --- |
| Installed by `provision-vms.yml` | Ubuntu Server 26.04 LTS (amd64) |
| Hardened by `baseline.yml` | Ubuntu Server 24.04 LTS and 26.04 LTS |
| Hypervisor | Proxmox VE 8 and 9 |
| Control node | Linux or macOS, Python 3.11+, ansible-core 2.19 or 2.20 |

A new Ubuntu LTS or Proxmox VE major version is added in a release once it has been tested; an end-of-life
one is dropped in the next minor release, and the changelog says so.
