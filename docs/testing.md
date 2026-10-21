# Testing

How ubuntu-server-auto-setup is tested, how to run the tests, and how to add a case. The rules for when a
change needs a test are in the [test policy](../CONTRIBUTING.md#test-policy).

## What runs where

| Suite | What it covers | Command | In CI |
| --- | --- | --- | --- |
| Lint and syntax | yamllint, ansible-lint (`production` profile), a syntax check of every playbook, actionlint, ruff (the web UI's Python), hadolint (the Dockerfile); TypeScript's strict type check of the web UI's app runs with its tests | `make lint`, `make syntax` | every pull request and push to `main` (required) |
| Integration tests | every playbook end to end against containers and a mock Proxmox API, with checks of the results, idempotence and task coverage | `make test` | every pull request and push to `main` (required) |
| Web UI unit tests | the server against the real roles and ansible-core: sign-in, guards, the API behind every page and form and its validation, the inventory and Vault it writes, runs, the page shell; and the app: every page against a stand-in API, accessibility (axe-core and structure), the colour tokens' contrast, the theme, the router, the log; at least 80% statement coverage each | `make webui-test` | every pull request and push to `main` (required) |
| Image smoke test | the container image built from the checkout, driven like a browser against the mock Proxmox API, including a real `ansible-playbook` run; then `start.ps1` | `make smoke` (needs Docker) | every pull request and push to `main`, and on the release image before it is pushed |
| Fuzzing | the code that turns inventory values into files and commands, with arbitrary inputs | `make fuzz` (Linux x86-64) | every pull request and push to `main`, ten minutes per target weekly |
| Real installer ISO | the remastering against the real Ubuntu 26.04 live-server ISO | `tests/real-iso.sh` | weekly, and on pull requests that change the installer roles |
| Real run | a disposable VM on a real Proxmox VE node | `make provision`, `make baseline` with `LIMIT=` | no, by hand (see the test policy), and before every release with the [pre-release checklist](release-checklist.md) |

## The integration tests

`tests/run.sh` runs the real playbooks, unchanged, against stand-ins for the systems they talk to. Nothing
leaves the machine except the container image builds:

| Real system | Stand-in |
| --- | --- |
| A Proxmox VE node (SSH as root, ISO storage) | a Debian container with SSH on `127.0.0.1:2211` (`tests/images/pve-node`) |
| releases.ubuntu.com | a small ISO with Ubuntu's GRUB menu (`tests/fixtures/grub.cfg`) and a `SHA256SUMS` in Ubuntu's format, served inside that container |
| The Proxmox VE API | `tests/mock_pve.py`: the API calls the roles make, on `https://127.0.0.1:8006`. After a VM's first start it reports "running" for a few polls, then "stopped", as the installer does when it powers off. VM 4099 never stops |
| github.com/&lt;user&gt;.keys | a keys file the mock serves on `http://127.0.0.1:8081` |
| A freshly installed VM | an Ubuntu 26.04 container with systemd, socket-activated SSH, sudo-rs and the admin user, as the autoinstall leaves it (`tests/images/ubuntu-target`), SSH on `127.0.0.1:2201` and `2222` |

The test keys, the mock's TLS certificate and `known_hosts` are created in a temporary directory for each
run; your own `~/.ssh` is never touched. The steps:

1. **build-installer** twice: the remastered ISO has the `autoinstall` kernel argument and a 3-second GRUB
   timeout, and the second run changes nothing.
2. **provision-vms** for `srv-01`: the VM is created with the expected hardware, both ISOs attached, the
   install awaited, the ISOs detached, the boot order set to disk only, the seed deleted, the VM started and
   its host key trusted. A second run skips the existing VM.
3. **provision-vms** for `srv-hang`, whose install never finishes: the run must fail and must delete the
   seed ISO (which holds the password hash). The node must have no seed files left.
4. **baseline** twice on `srv-01`, then `tests/verify-baseline.yml`: effective `sshd -T` settings, UFW
   rules and policies, the Fail2Ban jail, the sudoers file, the timezone, automatic updates, the removed
   installer answers file, exactly the published key in `authorized_keys`, and password and root logins
   refused.
5. **Key sync**: a key that is no longer published is removed; an empty key list is refused rather than
   emptying `authorized_keys`.
6. **Port change** to 2222: the firewall opens the new port before sshd moves, the old rule is removed,
   Fail2Ban follows, and a rerun on the new port changes nothing.
7. **Password-required sudo**: the passwordless sudoers file is removed and Ansible carries on with the
   become password.
8. **Task coverage**: `tests/callback_plugins/task_coverage.py` records every role task and handler that ran
   (not skipped), and `tests/coverage.py` fails the run if fewer than 80% of them did. Ansible has no
   statement coverage tool; a task is its unit of execution, so this is the closest equivalent. The tasks
   the containers can't reach (waiting for `cloud-init`, the QEMU guest agent, reboots) are listed in the
   report and covered by the real-run part of the test policy and the
   [pre-release checklist](release-checklist.md).

UFW rate-limits new SSH connections (6 in 30 seconds per address), and in the tests every connection comes
from Docker's one address, so the script pauses between some steps.

## Fuzzing

`tests/fuzz/` holds [Atheris](https://github.com/google/atheris) fuzz targets for the places where
inventory values become configuration files or shell commands. Each target loads the real template, Jinja
expression or allowlist pattern from the role (`tests/fuzz/roles.py`), renders it with Ansible's own
filters and tests, and checks a property for every input the role would accept:

| Target | What it renders | Property |
| --- | --- | --- |
| `fuzz_user_data.py` | `roles/proxmox_vm/templates/user-data.j2` and `meta-data.j2` | the YAML parses and every field holds exactly the given value; the two root shell commands split into exactly the expected words |
| `fuzz_ssh_keys.py` | the authorized-keys expression in `proxmox_vm` and `common` | both roles agree; every entry is one trimmed line of the input that starts with a key type |
| `fuzz_checksums.py` | the checksum expression in `installer_iso` | the result is empty or a checksum from a line naming exactly the ISO |
| `fuzz_configs.py` | the sshd drop-in and `jail.local` | every setting appears once with exactly the given value; nothing else is added |

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
| `test_schema.py` | every field names a role variable and every pattern in `roles/*/vars/main.yml` is used; defaults, help and patterns come from the role files; the UI's own ranges match the roles' assert tasks; valid and invalid values for every kind of field |
| `test_store.py` | the inventory and Vault round-trip; files are `0600` and the Vault is encrypted; typed values are tagged `!unsafe`, and `ansible` reads the inventory without templating them |
| `test_auth.py` | argon2id hashing and rehashing, the setup code, throttling, sessions and their expiry, CSRF tokens |
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
TLS; its log streams, contains no secret and lands in the history. Finally it checks that the Vault in the
volume is encrypted, that no file there is readable by others, and that the process isn't root. CI then
starts the same image with `start.ps1` under PowerShell 7.

The pages were also checked with axe-core in a real browser (with contrast) and by keyboard by hand; see
[accessibility.md](accessibility.md).

## Running them

You need Docker (privileged containers, for systemd), Python with the CI packages and the collections:

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements-ci.txt
ansible-galaxy collection install -r requirements.yml
make test
```

A run takes about 10 minutes. On failure the logs stay in the temporary directory the script prints.
Ports 2201, 2211, 2222, 8006 and 8081 on `127.0.0.1` must be free. The web UI's tests need the Python
packages and Node 24 with the app's packages (`make app` installs them; then `make webui-test`); the smoke
test needs Docker and ports 18006 and 18080 (`make smoke`).

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
  goes into `PAGES` in `a11y.test.tsx` (and the server's `PAGES` in `main.py`), and a new colour into
  `tokens.css` and a pair in `contrast.test.ts`.
- A bug fix: add the check that would have caught the bug before fixing it, and see it fail.
