# The web UI and the container image

The container image `honeybeartech/ubuntu-server-auto-setup` on Docker Hub (mirrored to `ghcr.io/honeybeartech/ubuntu-server-auto-setup`) holds everything the playbooks need
(ansible-core, the collections, the playbooks and roles) plus a web UI, **USAS**, that lays out the options
as forms and runs the playbooks for you. It is the easiest way to use the project on Windows, and works the
same on Linux and macOS: you need only Docker.

The UI writes an ordinary Ansible inventory and an Ansible Vault file, the same ones the command line uses,
and runs `ansible-playbook` with them. Everything the playbooks do, and every warning in the
[README](../README.md), applies unchanged: **provisioning creates VMs and the installer wipes their disks;
the baseline changes SSH and firewall settings and can lock you out.**

## Starting it

The UI holds a Proxmox API token and a root SSH key, so it listens on `127.0.0.1` only. Read
[exposing it](#exposing-it-beyond-localhost) before you change that.

The image is published with each release from the first one that includes the web UI. Before that, or to
run your own changes, build it from a checkout: `docker compose up -d --build`, or
`docker build -t honeybeartech/ubuntu-server-auto-setup .` before the commands below.

**Windows** (Docker Desktop with the WSL 2 backend): download [`start.ps1`](../start.ps1) and run it in
PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

It starts Docker Desktop if needed, pulls the image, creates the container `usas` with its data in the
Docker volume `usas-data`, waits until it is healthy, puts the first-run setup code on the clipboard and
opens <http://localhost:8080>. Later runs just start it again. `-Update` pulls the latest image and
recreates the container (the data stays in the volume), `-Verify` checks the image's signature with
cosign first, `-Stop` stops it, and `-Port 8081` uses another port.

**Docker** (any system):

```sh
docker run -d --name usas --restart unless-stopped -p 127.0.0.1:8080:8080 -v usas-data:/data \
  honeybeartech/ubuntu-server-auto-setup
docker logs usas    # shows the setup code on the first start
```

**Docker Compose**: [`docker-compose.yml`](../docker-compose.yml) runs the same image with a read-only root
file system, no Linux capabilities and `no-new-privileges`: `docker compose up -d`, then
`docker compose logs usas` for the setup code. `docker compose up -d --build` builds the image from a
checkout instead.

Then open <http://localhost:8080>.

### The first sign-in

The first time, the UI asks for the **setup code** from the container's log and a **password** of at least
12 characters. The code proves that whoever sets the password also started the container: without it,
anyone (or any web page, through DNS rebinding) who reached the port first could choose the password. A new
code is printed every time the container starts until a password is set.

Forgot the password? Delete the password file and restart; the next start prints a new setup code. Your
settings, the Vault and the SSH key stay as they are:

```sh
docker exec usas rm /data/auth.json && docker restart usas && docker logs usas
```

Sessions end after 2 hours without activity, after 12 hours in any case, when you sign out, and when the
container restarts.

## The interface

![The run page in the dark theme: a provisioning run that succeeded, with its options and the coloured Ansible log](images/webui-run-dark.png)

The UI opens in a **dark theme**. The theme switch at the bottom of the sidebar (on a phone: under **Menu**)
offers **Dark**, **Light** and **System**, which follows the light or dark setting of your browser or
operating system. The choice is kept for that browser in a cookie (`usas_theme`) and applied before the
page is drawn, so pages don't flash in the other theme.

The sidebar lists the pages in the order you need them and marks the set-up pages done or still to do.
Below 960 pixels wide it folds into a top bar with a **Menu** button; the UI works down to a 375-pixel
phone screen. Movement is kept to a minimum (a pulsing dot on a running run), and none at all when the
system asks for reduced motion.

![The Proxmox page in the light theme: the node name is a dropdown of the cluster's nodes, beside the last connection test](images/webui-proxmox-light.png)

The UI is an app in the browser and needs JavaScript; without it, each page says so. Every value it sends
is checked again on the server, which is the only place that decides what is saved.

## Walking through it

The overview lists the steps and whether each is done.

1. **Proxmox**: the API host and port, the token's user, name and secret, and whether to verify the API's
   TLS certificate (keep it on). **Test the connection** calls the API with what you typed, without
   saving the form, and lists the cluster's nodes with their status (and the cluster's name, if the token
   may read it). From then on the node the VMs are created on is a dropdown of those nodes, such as
   `hephaestus (online)`; node names are case-sensitive, and a name that isn't in the cluster is refused.
   The **ISO storage** (under More settings) becomes a dropdown of the storages that allow ISO images
   in the same way, if the token may list storages; the playbooks find its folder on the node themselves.
   Test again to refresh the lists (they are kept in `webui.json`). Then the storage, the bridge and an
   optional VLAN tag. The token needs the privileges in
   [installing.md](installing.md#2-create-the-proxmox-api-token).

   Proxmox nodes use certificates from the cluster's own CA. Paste `/etc/pve/pve-root-ca.pem` from the
   node into **Cluster CA certificate**, or fetch it over SSH once the next step is done. The UI then
   trusts that CA (with the usual public ones) for the connection test and every playbook run, so
   verification can stay on.
2. **SSH key**: the UI's own key pair. **Create a key** makes an Ed25519 key in the data volume; or upload
   one (OpenSSH or PEM; a key with a passphrase must be in PEM format, or have the passphrase removed). Add
   the public key to `/root/.ssh/authorized_keys` on the Proxmox node (the page shows the command). Then
   enter the node's SSH address, **scan** its host key, compare the fingerprints with what the node shows
   (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` in the node's shell), and **trust** them. Only then
   will the UI and the playbooks connect, and only to that key. **Test the SSH login** checks the whole
   path.
3. **Defaults**: the GitHub user whose keys are installed (**Preview the keys** shows what GitHub returns,
   filtered exactly as the roles filter it), extra public keys, whether to authorize the UI's own key on the
   servers (on by default: the baseline logs in with it), the admin user and password, the time zone, the
   gateway, prefix length, DNS servers and search domains, and the installer's disk layout.
4. **Servers**: one row per VM: its name (also the VM's name in Proxmox), static IPv4 address, VM ID, and
   optionally cores, memory, disk and extra firewall rules for that server (`443/tcp`, `53/udp`,
   `5432/tcp from 10.0.0.0/24`, separated by commas). Empty sizes use the role defaults.
5. **Hardening**: the SSH port, the users allowed to log in over SSH, root login, passwordless sudo, the
   firewall's SSH sources and rules for every server, and the Fail2Ban settings.
6. **Run**: choose `build-installer`, `provision`, `baseline`, `site` or `keys` (the baseline's key sync
   only), optionally check mode (`--check`), differences (`--diff`) and a limit (servers or groups,
   separated by commas). The run's output streams live, with Ansible's status lines coloured (`ok`,
   `changed`, `skipping`, `fatal`, the play recap's counts) and numbered. **Follow the output** keeps the
   newest line in view; scrolling up pauses it. **Wrap long lines** wraps instead of scrolling sideways.
   **Stop the run** interrupts it like Ctrl-C. A
   provisioning run stopped between creating the seed ISO and deleting it can leave the seed ISO on the
   node ([installing.md](installing.md#troubleshooting)). **History** keeps the last 200 runs with their
   logs.

Only one run happens at a time. Each form field is a role variable: the field names it, and its default,
help text and the format it accepts come from the role itself (see [how the forms are built](#how-the-forms-are-built)).

## What is stored where

Everything is in the `/data` volume, readable only by the container's user (mode `0600` files in `0700`
directories):

| Path | What |
| --- | --- |
| `inventory/hosts.yml` | The inventory: every setting you saved, the servers, the Proxmox node. |
| `inventory/group_vars/all/vault.yml` | The admin password and the API token, encrypted with Ansible Vault (AES-256). |
| `.vault-pass` | The Vault password: 32 random bytes made on the first start (see below). |
| `ssh/id_ed25519`, `ssh/id_ed25519.pub` | The UI's SSH key pair. |
| `ssh/known_hosts` | The trusted host keys: the Proxmox node's, and each new VM's (added by provisioning). |
| `pve-ca.pem`, `ca-bundle.pem` | The cluster CA you saved, and that CA together with the public ones. |
| `auth.json` | The UI password's argon2id hash. |
| `webui.json` | The cluster's name and nodes from the last connection test, for the node dropdown. |
| `runs/` | The run history: one `.json` and one `.log` per run. |

**The Vault password is in the same volume by default**, so the encryption protects `vault.yml` when it is
copied on its own (into a backup, a ticket or a repository), not against someone who has the whole volume.
To keep it out of the volume, give the container the password as a file, for example a Docker secret, and
point `USAS_VAULT_PASSWORD_FILE` at it. Set it before the first start, or re-encrypt the Vault when you
switch (`ansible-vault rekey`).

The inventory is a normal Ansible inventory. Values you type are written with Ansible's `!unsafe` tag, so
Ansible never treats them as templates. To run something the UI doesn't offer, use the image's own
Ansible:

```sh
docker exec -it usas ansible-playbook -i /data/inventory/hosts.yml \
  --vault-password-file /data/.vault-pass playbooks/baseline.yml --tags firewall --limit web-01
```

Hand edits to `hosts.yml` survive only as far as the UI can read them back: it keeps the variables it has
fields for, and rewrites the file when you save a page.

### Backing up and moving

Back up the volume while the container is stopped:

```sh
docker stop usas
docker run --rm -v usas-data:/data -v "$PWD:/backup" alpine tar czf /backup/usas-data.tgz -C /data .
docker start usas
```

The archive holds the SSH key and, unless you moved it out, the Vault password: keep it as safe as the
Proxmox node's root password.

### Upgrading

`start.ps1 -Update`, or `docker pull` and recreate the container with the same volume. Read the
[changelog](../CHANGELOG.md) first; variable changes between versions are listed under "Upgrading".

## Settings

The container reads these environment variables; the defaults suit the documented `docker run` command.

| Variable | Default | Meaning |
| --- | --- | --- |
| `USAS_DATA_DIR` | `/data` | Where everything is stored. |
| `USAS_VAULT_PASSWORD_FILE` | `/data/.vault-pass` | The Vault password file. If you set it, the file must exist. |
| `USAS_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | The host names the UI answers to (the `Host` header); anything else gets an error. This stops DNS rebinding. |
| `USAS_SECURE_COOKIES` | `auto` | `auto` marks the session cookie `Secure` (and names it `__Host-usas_session`) when the request came over HTTPS; `true` always does. |
| `USAS_TRUSTED_PROXIES` | (none) | Addresses of a reverse proxy whose `X-Forwarded-Proto` and `X-Forwarded-For` headers are trusted. |
| `USAS_SESSION_HOURS` | `12` | The longest a session lasts. |
| `USAS_PORT` | `8080` | The port inside the container. |
| `USAS_VERSION` | the image's version | The version in the page footer. Release images set it from their tag; images you build yourself say `dev`. |

## Security

The UI can create VMs, wipe their disks and change SSH and firewall settings on every server in the
inventory, and the data volume holds a root SSH key and the Proxmox API token. Treat access to it like
root access to the Proxmox node.

What it does to protect them:

- **Sign-in required** for everything except the health check. One password, stored as an argon2id hash
  (`auth.json`); failed attempts are slowed down (after 5, each failure doubles the wait, up to 15
  minutes). The first password needs the setup code from the log.
- **Sessions** are random 256-bit IDs kept in memory, in an `HttpOnly`, `SameSite=Strict` cookie (also
  `Secure` over HTTPS). Signing in issues a new ID; changing the password signs out every other session.
- **CSRF**: every post carries the session's token in an `X-CSRF-Token` header (a page on another site
  can't set that header without the server's permission, which it never gives), and requests whose
  `Origin` or `Sec-Fetch-Site` header says they came from another site are refused. The API answers no
  cross-origin requests.
- **Strict headers**: a Content Security Policy that allows only the UI's own scripts, styles and fonts and
  nothing inline, no framing (`frame-ancestors 'none'`, `X-Frame-Options`), `nosniff`, no caching of pages
  or API answers, no referrer to other sites. The app's files are built into the image (no CDN), so the UI
  works on a network without internet access.
- **Host check**: only the names in `USAS_ALLOWED_HOSTS` are served, and names other than `localhost`
  only over HTTPS.
- **Secrets aren't shown again** once saved (the page says only that a value is saved), aren't written to
  the inventory in plain text, and are replaced by `********` in run logs should a task ever print one.
- **Input validation**: every field is checked against the same allowlist patterns the roles check (from
  `roles/*/vars/main.yml`), plus formats of its own for values the roles take as they are (the API host,
  the token name, firewall rules); the `--limit` must name hosts or groups in the inventory.
- **No shell**: `ansible-playbook`, `ssh` and `ssh-keyscan` run with argument lists.
- **The container** runs as an unprivileged user (UID 10001), and with Compose also with a read-only root
  file system, no capabilities and `no-new-privileges`.

What it doesn't protect against: anyone with access to the Docker host or the data volume (they have the
key, the Vault and, by default, its password); someone who knows the UI password; a browser extension or
malware in the browser you use it from; and everything the playbooks themselves don't protect against
([security.md](security.md#what-it-does-not-protect-against)).

### Exposing it beyond localhost

Don't publish the port on a network unless you have to. Anyone who can reach it can try to sign in, and
the UI has a single password, no second factor and no accounts. If you need to reach it from another
machine, prefer an SSH tunnel (`ssh -L 8080:127.0.0.1:8080 docker-host`) or a VPN to the Docker host.

If you do publish it, put it behind a reverse proxy that serves HTTPS, then:

- add the proxy's host name to `USAS_ALLOWED_HOSTS` (the UI refuses other names over plain HTTP);
- set `USAS_TRUSTED_PROXIES` to the proxy's address, so the UI sees the original scheme and client address;
- publish the container's port only to the proxy, not on `0.0.0.0`;
- consider the proxy's own authentication (client certificates or single sign-on) as a second factor.

## How the forms are built

Each field names a role variable. When the UI starts, it reads the variable's default and the comment
above it from `roles/<role>/defaults/main.yml`, and the allowlist pattern or list of allowed values from
`roles/<role>/vars/main.yml`, the same ones the role's `assert` tasks use. A field for a variable or pattern
that no role defines stops the UI at startup, and the unit tests fail if a role gains a pattern that no
field uses, so the forms can't drift from the playbooks. A value equal to the role's default isn't written
to the inventory, so a new release's default applies to it.

## HTTP endpoints

The UI is a React app (`webui/frontend`) served by FastAPI (`webui/usas_web`). Every page URL answers with
the same small HTML page, after the same sign-in checks; the app then asks `/api/<page>` for the page's data
and posts its forms there, with the same field names the forms always had. The API is the UI's own, not one
for other programs (use the inventory and `ansible-playbook` for that), so there is no OpenAPI document.
For reference:

| Path | Methods | What |
| --- | --- | --- |
| `/healthz` | GET | `ok`; the container's health check. No sign-in. |
| `/`, `/proxmox`, `/ssh`, `/defaults`, `/servers`, `/hardening`, `/run`, `/runs`, `/runs/<id>`, `/account`, `/setup`, `/login` | GET | The pages (HTML). Without a sign-in they redirect to `/login?next=…` (or `/setup` on the first run). |
| `/api/session` | GET | Whether a password is set and the session signed in, its CSRF token, the version and the theme. |
| `/api/setup`, `/api/login`, `/api/logout`, `/api/account` | POST | First-run password, sign-in, sign-out, password change. |
| `/api/theme` | POST | Remember the theme (`dark`, `light` or `system`) for this browser. |
| `/api/overview`, `/api/proxmox`, `/api/ssh`, `/api/defaults`, `/api/servers`, `/api/hardening`, `/api/run`, `/api/runs`, `/api/runs/<id>` | GET | A page's data: the fields (label, help, default, variable), the saved values (never a secret), the steps, the runs. |
| `/api/proxmox`, `/api/ssh`, `/api/defaults`, `/api/servers`, `/api/hardening` | POST | Save (or test, preview, scan…) with the form's `action`; errors come back by field name with status 422. |
| `/api/run` | POST | Start a run. |
| `/api/runs/<id>/stop` | POST | Interrupt the run. |
| `/ssh/id_ed25519.pub` | GET | The UI's public key, as a download. |
| `/runs/<id>/log` | GET | The run's log as text. |
| `/runs/<id>/stream` | GET | The run's output as server-sent events, resuming from `Last-Event-ID`. |
| `/static/app/*` | GET | The app's script, stylesheet and fonts; their names carry a hash of their contents. No sign-in. |
| `/static/favicon.svg`, `/favicon.ico` | GET | The icon (SVG), linked with a hash of its contents (`?v=`). No sign-in. |

Every POST needs the session's CSRF token in the `X-CSRF-Token` header; forms are sent as
`multipart/form-data`. An API call without a sign-in answers 401 with where to sign in.

## Limitations

- One Proxmox node per inventory (the VMs are created on it; the API can be any node of the cluster),
  IPv4 only, as the playbooks.
- One password, no accounts or second factor. Passkeys were considered and are on the
  [roadmap](roadmap.md); they need a stable host name, which a localhost tool doesn't have.
- One run at a time, and sessions don't survive a restart of the container.
- It needs JavaScript (any current browser).
- English only.

## Troubleshooting

- **"This host name isn't allowed"**: open it as `http://localhost:8080`, or add the name you use to
  `USAS_ALLOWED_HOSTS` (and serve it over HTTPS).
- **"Cross-site request refused" or "The form expired"**: reload the page and send the form again. Browser
  extensions that strip the `Origin` header cause the first.
- **The page says "The web UI's app isn't built"**: you are running the UI from a checkout without
  building the app. Run `make app` (or `npm ci` and `npm run build` in `webui/frontend`); `make ui` does it
  for you. The container image always has it.
- **The connection test says the certificate couldn't be verified**: add the cluster CA on the Proxmox page.
  If the API host is an IP address, the node's certificate must list that address; otherwise use the
  node's name.
- **SSH to the node fails with "Host key verification failed"**: the node's host key changed (reinstall,
  or a different machine at that address). Check it on the node, forget the old key and trust the new one.
- **Windows: "running scripts is disabled on this system"**: use
  `powershell -ExecutionPolicy Bypass -File .\start.ps1`, or `Unblock-File .\start.ps1` once.
- **Windows: port 8080 is in use**: `.\start.ps1 -Port 8081` (on a new container; with an existing one, add
  `-Update`).
