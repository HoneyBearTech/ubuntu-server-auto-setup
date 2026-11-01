![USAS, Unattended Server Auto-Setup](https://raw.githubusercontent.com/HoneyBearTech/ubuntu-server-auto-setup/main/docs/images/usas-banner.png)

**Linux servers on Proxmox VE, installed hands-off and hardened, from a web UI in one container.**

This image holds [ubuntu-server-auto-setup](https://github.com/HoneyBearTech/ubuntu-server-auto-setup) (USAS,
Unattended Server Auto-Setup): Ansible playbooks that install Ubuntu 22.04 to 26.04, Debian 12 and 13, and CentOS
Stream, Rocky Linux and AlmaLinux 9 and 10 on new Proxmox VE VMs with no hands on the keyboard, from the
vendor's cloud image or Ubuntu's installer, load your GitHub SSH keys, and apply a hardened baseline (SSH, UFW
or firewalld, Fail2Ban, automatic security updates). Its web UI lays out every option as forms, writes an ordinary Ansible inventory
and an encrypted Vault, and runs the playbooks with a live log. It runs the same on Windows (Docker Desktop
with WSL 2), Linux and macOS.

![A cloud image or the installer ISO, then a Proxmox VM, then an unattended install, then a hardened server](https://raw.githubusercontent.com/HoneyBearTech/ubuntu-server-auto-setup/main/docs/images/usas-flow.png)

> **Provisioning creates VMs and the installer wipes their disks; the baseline changes SSH and firewall
> settings and can lock you out.** Run against one server first, and try check mode before a real run.

## Start it

**Windows**: download
[`start.ps1`](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/blob/main/start.ps1) and run it in
PowerShell. It starts Docker Desktop if needed, creates the container, puts the first-run setup code on the
clipboard and opens the browser:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

**Linux and macOS** (or Windows without the script):

```sh
docker run -d --name usas --restart unless-stopped -p 127.0.0.1:8080:8080 -v usas-data:/data \
  honeybeartech/ubuntu-server-auto-setup
docker logs usas    # the setup code for the first sign-in
```

Open <http://localhost:8080>, enter the setup code, choose a password, and work through the pages:
Proxmox, SSH key, defaults, servers, hardening, run. The UI opens in a dark theme; light, or your system's
choice, is one click away.

![The web UI's overview in the dark theme: the set-up steps as cards marked done or to do, and the recent runs with their status](https://raw.githubusercontent.com/HoneyBearTech/ubuntu-server-auto-setup/main/docs/images/webui-overview-dark.png)

## Ports, volume and security

- **Port 8080**, the web UI. Publish it on `127.0.0.1` only, as above: the UI holds your Proxmox API token
  and a root SSH key, and can create VMs and change SSH and firewall settings on every server. To reach it
  from elsewhere, use an SSH tunnel, or a reverse proxy with HTTPS and `USAS_ALLOWED_HOSTS`
  ([exposing it](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/blob/main/docs/web-ui.md#exposing-it-beyond-localhost)).
- **`/data`**, a volume with everything the UI keeps: the inventory, the Vault and (unless you move it
  out with `USAS_VAULT_PASSWORD_FILE`) its password, the SSH key, the run history. Back it up like a
  secret.
- The container runs as UID 10001. With the project's
  [`docker-compose.yml`](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/blob/main/docker-compose.yml)
  it also gets a read-only root file system, no capabilities and `no-new-privileges`.
- Sign-in with an argon2id-hashed password set with a one-time code from the log, CSRF and origin checks,
  allowed host names, a strict Content Security Policy; secrets are never shown again once saved.

## Tags

| Tag | What |
| --- | --- |
| `X.Y.Z` | A release, such as `0.6.0`. |
| `X.Y` | The latest patch release of that minor version. |
| `latest` | The latest release. |

Every tag is built for `linux/amd64` and `linux/arm64`, scanned with Trivy, signed with cosign, and has an
SBOM and SLSA provenance attached. The same images are mirrored at
`ghcr.io/honeybeartech/ubuntu-server-auto-setup`. How to verify them:
[verifying releases](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/blob/main/docs/verifying-releases.md#the-container-image).
Inside: the `python:3.14-slim` (Debian) base, ansible-core 2.20, the collections, the playbooks and roles,
and the web UI.

## Learn more

- [The web UI and the container image](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/blob/main/docs/web-ui.md):
  every step, every setting, where the data lives, backups and upgrades.
- [The project on GitHub](https://github.com/HoneyBearTech/ubuntu-server-auto-setup): the playbooks, the
  command line, the documentation.
- [Changelog](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/blob/main/CHANGELOG.md),
  [security policy](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/blob/main/SECURITY.md),
  [license (MIT)](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/blob/main/LICENSE).
