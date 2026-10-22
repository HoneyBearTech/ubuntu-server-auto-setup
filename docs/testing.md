# Testing

How ubuntu-server-auto-setup is tested, how to run the tests, and how to add a case. The rules for when a
change needs a test are in the [test policy](../CONTRIBUTING.md#test-policy).

## What runs where

| Suite | What it covers | Command | In CI |
| --- | --- | --- | --- |
| Lint and syntax | yamllint, ansible-lint (`production` profile), a syntax check of every playbook, actionlint, ruff (the web UI's Python), hadolint (the Dockerfile); TypeScript's strict type check of the web UI's app runs with its tests | `make lint`, `make syntax` | every pull request and push to `main` (required) |
| Integration tests | every playbook end to end against containers and a mock Proxmox API, with checks of the results, idempotence and task coverage | `make test` | every pull request and push to `main` (required) |
| Web UI unit tests | the server against the real roles and ansible-core: sign-in, guards, the API behind every page and form and its validation, the inventory and Vault it writes, runs, the page shell; and the app: every page against a stand-in API, accessibility (axe-core and structure), the colour tokens' contrast, the theme, the router, the log; at least 80% statement coverage each | `make webui-test` | every pull request and push to `main` (required) |
| Web UI browser tests | the app in Chromium against the server and the mock Proxmox API: the whole setup and a run by keyboard only; Tab order, visible focus and no keyboard traps on every page; focus after each action, the phone menu, the run's live status; axe-core's WCAG 2.1 A and AA rules with real layout (and so contrast) on every page in every theme | `make webui-browser` | every pull request and push to `main` (in the required "Web UI tests" check) |
| Image smoke test | the container image built from the checkout, driven like a browser against the mock Proxmox API, including a real `ansible-playbook` run; then `start.ps1` | `make smoke` (needs Docker) | every pull request and push to `main`, and on the release image before it is pushed |
| Fuzzing | the code that turns inventory values into files and commands, with arbitrary inputs | `make fuzz` (Linux x86-64) | every pull request and push to `main`, ten minutes per target weekly |
| Real installer ISO | Ubuntu's real signature on `SHA256SUMS`, checked with the shipped key, and the remastering of the real Ubuntu 26.04 live-server ISO | `tests/real-iso.sh` (set `USAS_ISO_CACHE` to a folder holding the ISO to skip the download) | weekly, and on pull requests that change the installer roles; CI caches the ISO by its SHA-256 |
| Real run | a disposable VM on a real Proxmox VE node | `make provision`, `make baseline` with `LIMIT=` | no, by hand (see the test policy), and before every release with the [pre-release checklist](release-checklist.md) |

## The integration tests

`tests/run.sh` runs the real playbooks, unchanged, against stand-ins for the systems they talk to. Nothing
leaves the machine except the container image builds:

| Real system | Stand-in |
| --- | --- |
| A Proxmox VE node (SSH as root, ISO storage) | a Debian container with SSH on `127.0.0.1:2211` (`tests/images/pve-node`); its `xorriso` keeps a copy of each seed's user-data for the network checks |
| releases.ubuntu.com | a small ISO with Ubuntu's GRUB menu (`tests/fixtures/grub.cfg`) and a `SHA256SUMS` in Ubuntu's format, signed (`SHA256SUMS.gpg`) with a test key made when the image is built, served inside that container. Only the test key's public half and fingerprint leave the build; `tests/run.sh` points `installer_signing_keyring` and `installer_signing_fingerprint` at them |
| The Proxmox VE API | `tests/mock_pve.py`: the API calls the roles make, on `https://127.0.0.1:8006`. After a VM's first start it reports "running" for a few polls, then "stopped", as the installer does when it powers off. VM 4099 never stops. Once an installed VM runs, its guest agent lists one interface per network card with the card's MAC address; VM 4063's `net0` has a DHCP lease, the others only link-local addresses, and VM 4066's agent may not be asked. With `--fixture-vms` (the image smoke test and the browser tests) it also lists fixed VMs with guest agents for the web UI's VMs page: Ubuntu 26.04, 24.04 and 22.04, Debian, Windows, an agent that isn't running, a slow one, one the token may not ask, a stopped VM, a template, a container and hostile strings |
| github.com/&lt;user&gt;.keys | a keys file the mock serves on `http://127.0.0.1:8081` |
| A freshly installed VM | an Ubuntu 26.04 container with systemd, socket-activated SSH, sudo-rs and the admin user, as the autoinstall leaves it (`tests/images/ubuntu-target`), SSH on `127.0.0.1:2201` and `2222` |

The test keys, the mock's TLS certificate and `known_hosts` are created in a temporary directory for each
run; your own `~/.ssh` is never touched. The steps:

1. **build-installer** twice: the remastered ISO has the `autoinstall` kernel argument and a 3-second GRUB
   timeout, and the second run changes nothing. Then it must refuse a `SHA256SUMS` whose checksum was
   swapped (bad signature), one with no `SHA256SUMS.gpg`, one signed by a key other than the pinned one,
   and a malformed fingerprint.
2. **provision-vms** for `srv-01`: the VM is created with the expected hardware, both ISOs attached, the
   install awaited, the ISOs detached, the boot order set to disk only, the seed deleted, the VM started and
   its host key trusted. A second run skips the existing VM.
3. **provision-vms** for `srv-hang`, whose install never finishes: the run must fail and must delete the
   seed ISO (which holds the password hash). The node must have no seed files left.
   Then the networks: `srv-dhcp` (`net_mode: dhcp`) is provisioned with the address its guest agent reports
   and reached there, and a rerun finds it again without changes; `srv-vlan` gets a VLAN on `net0` and two
   more cards, and `tests/verify-network.yml` checks each card's Proxmox setting and the installer's netplan
   configuration (every card matched by its MAC address, one default route). No DHCP lease, an agent the
   token may not ask, options smuggled into a card, an unknown card key, a second default route and a
   repeated MAC address must each stop the run with a message.
4. **baseline** in check mode (`--check --diff`) on the fresh `srv-01`, also with a new SSH port and with an
   admin user that doesn't exist yet: nothing may fail and nothing may change. Then the baseline twice,
   then `tests/verify-baseline.yml`: effective `sshd -T` settings, UFW rules and policies, the Fail2Ban jail,
   the sudoers file, the timezone, automatic updates, the removed installer answers file, exactly the
   published key and the extra key in `authorized_keys`, and password and root logins refused. A check run
   on the finished server must report no changes.
5. **Key sync**: the baseline reaches `srv-dhcp` at the address its guest agent reports, without changes; a
   key that is no longer published is removed; an empty key list is refused rather than
   emptying `authorized_keys`.
6. **Port change** to 2222: the firewall opens the new port before sshd moves, the old rule is removed,
   Fail2Ban follows, and a rerun on the new port changes nothing.
7. **Password-required sudo**: the passwordless sudoers file is removed and Ansible carries on with the
   become password. The server's `sudo` is sudo-rs, as on Ubuntu Server 26.04, so this goes through the
   classic sudo (`/usr/bin/sudo.ws`); with that hidden, the baseline must stop before changing anything.
8. **Optional hardening and notifications**, all turned on with `tests/hardening-vars.yml`: a run, a rerun
   that must change nothing, then `tests/verify-hardening.yml`: the `sysctl` drop-in holds exactly the
   expected keys and the kernel has their values, the audit rules and the immutable flag are in place,
   AppArmor is enabled, apt reads the mail settings, msmtp's files are root-only and `msmtp --pretend`
   reads the relay settings, the relay password reached its file unchanged (quotes, a backslash and `#`
   included), and Fail2Ban has both actions. A ban of a documentation address must reach a webhook
   listener in the container with the right message. A check run must report no changes, values that could
   inject settings must be refused, and turning everything off must remove the files again. In a container
   auditd can't run and AppArmor's policy is the host's, so those two live checks are skipped with the
   reason in the log (the [pre-release checklist](release-checklist.md) covers them on a real VM).
   Kernel-wide `sysctl` keys would change the Docker host's own kernel, so outside CI (`CI=true`) the test
   leaves them out and checks only the per-network-namespace ones. A container can't see some keys at all
   (`net.core.bpf_jit_harden`); the role reports and skips those, and the test checks the rest.
9. **Task coverage**: `tests/callback_plugins/task_coverage.py` records every role task and handler that ran
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
| `fuzz_configs.py` | the sshd drop-in, `jail.local` and the Fail2Ban webhook action | every setting appears once with exactly the given value; nothing else is added; the webhook URL is the `curl` command's last argument, whole, with nothing Fail2Ban would interpolate |
| `fuzz_baseline_extras.py` | the `hardening_sysctl_settings` expression and `sysctl.conf.j2`, unattended-upgrades' mail settings, `msmtprc.j2` | the merged settings drop exactly the null keys; each sysctl key, apt option and msmtp command appears once with exactly the given value |

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
| `test_store.py` | the inventory and Vault round-trip; files are `0600` and the Vault is encrypted; typed values are tagged `!unsafe`, and `ansible` reads the inventory without templating them |
| `test_auth.py` | argon2id hashing and rehashing, the setup code, throttling, sessions and their expiry, CSRF tokens |
| `test_passkeys.py` | passkeys with a software authenticator (`softauthn.py`, ES256 with "none" attestation): `USAS_PUBLIC_URL`, adding (the password and a name needed, the options, the same passkey twice, the limit), signing in, and every response that must be refused (another origin or RP ID, no user verification, a replayed, expired or another session's challenge, a signature counter going back, another user handle, unreadable or oversized answers), throttling, "Require a passkey", a broken file failing closed, removal signing out its sessions, the CSRF header, and the log lines |
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
| `passkeys.spec.ts` | passkeys with Chromium's virtual authenticator (DevTools `WebAuthn.addVirtualAuthenticator`, with user verification): adding one by keyboard, signing out and in with it, "Require a passkey" (no password field, axe-core), the explanation at `127.0.0.1`, turning it off and removing the passkey. Runs last, in the `passkeys` project, since requiring a passkey signs out the session the others use |
| `helpers.ts` | walking a page with Tab (`tabCycle`, `checkKeyboard`), axe-core (`checkAxe`), and posting a form as the app does for the pages the flow doesn't fill in by keyboard |

A failing test leaves a screenshot and a trace in `webui/frontend/test-results/` (CI uploads them as the
`playwright-results` artifact); open a trace with `npx playwright show-trace <file>` in `webui/frontend`.
To watch the tests, pass Playwright's options through: `tests/webui-browser.sh --headed`, or
`--debug -g "phone"` for one test.

What a screen reader says can't be tested this way; the [screen-reader
walk-through](accessibility.md#screen-reader-walk-through) is done by hand before each release.

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
