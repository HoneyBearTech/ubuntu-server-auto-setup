# Contributing to ubuntu-server-auto-setup

ubuntu-server-auto-setup is a personal project with a single maintainer (see [GOVERNANCE.md](GOVERNANCE.md)).
Contributions are welcome, but there is no service-level agreement, and review may take a while. Releases
are tagged `vMAJOR.MINOR.PATCH`; only the latest release and `main` are supported ([SUPPORT.md](SUPPORT.md)).
Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting bugs and suggesting changes

- Use [GitHub Issues](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/issues) for bugs, questions
  and ideas. Say which playbook failed, your Proxmox VE, Ubuntu and ansible-core versions, and include the
  failing task's output (run with `-v`) and the relevant inventory variables, with secrets, host names and
  addresses replaced by placeholders. For anything bigger than a small fix, please open an issue first so we
  can agree on the approach. The [roadmap](docs/roadmap.md) says what's planned and what's out of scope.
- **Do not report security vulnerabilities in a public issue.** Follow [SECURITY.md](SECURITY.md) and use
  GitHub's private vulnerability reporting instead.

## Development setup

What you need:

- `git`, `make` and Python 3.11 or later (CI uses 3.12);
- for trying changes for real: a Proxmox VE 8 or 9 node you can SSH into as root, an API token as described
  in [docs/installing.md](docs/installing.md), and a VM ID range and IP addresses you can throw away.

```sh
git clone https://github.com/HoneyBearTech/ubuntu-server-auto-setup.git && cd ubuntu-server-auto-setup
python3 -m venv .venv && . .venv/bin/activate
make deps     # Python packages from requirements*.txt, collections from requirements.yml
make lint     # yamllint . and ansible-lint
make syntax   # syntax-check playbooks/site.yml against inventory/hosts.example.yml
```

Dependencies come only from `requirements.txt`, `requirements-dev.txt` and `requirements.yml`; how they're
chosen and kept up to date is in [docs/dependencies.md](docs/dependencies.md). Nothing is compiled: a
release is the tagged source, packaged by `.github/workflows/release.yml` (see
[docs/verifying-releases.md](docs/verifying-releases.md)). How the playbooks and roles fit together is in
[docs/architecture.md](docs/architecture.md).

## When and how tests run

| Check | What it tests | Runs locally with | Runs in CI |
| --- | --- | --- | --- |
| yamllint | YAML syntax and style, with the rules in `.yamllint` | `yamllint .` (part of `make lint`) | every pull request and push to `main` (required) |
| ansible-lint | Ansible best practices with the `production` profile in `.ansible-lint`: FQCN modules, named tasks, idempotence hints, risky permissions and shell use, secrets without `no_log` | `ansible-lint` (part of `make lint`) | every pull request and push to `main` (required) |
| Syntax check | every playbook parses, every role and module resolves | `make syntax` | every pull request and push to `main` (required) |
| gitleaks | no secrets in the change or anywhere in the history | `gitleaks git .` | every pull request and push to `main` (required) |
| CodeQL | the GitHub Actions workflows (CodeQL has no Ansible analysis) | n/a | every pull request, `main` and weekly |
| Dependency review | new or changed dependencies with known vulnerabilities or disallowed licenses | n/a | every pull request |
| DCO | every commit is signed off | `git log` | every pull request (required) |
| Integration tests | every playbook end to end: the installer ISO is remastered, VMs are created and installed through a mock Proxmox API (including a failed install that must clean up), and the baseline is applied to an Ubuntu 26.04 container and checked (SSH settings, firewall rules, Fail2Ban jail, sudo, refused password and root logins, key sync, port change). Every playbook must change nothing on a second run, and at least 80% of role tasks must run | `make test` (needs Docker) | every pull request and push to `main` (required) |
| Fuzzing | the templates and expressions that turn inventory values into files and commands, with arbitrary inputs (Atheris) | `make fuzz` (Linux x86-64) | every pull request and push to `main`, longer weekly |
| Real installer ISO | the remastering against the real Ubuntu 26.04 ISO, including identical BIOS and UEFI boot records | `tests/real-iso.sh` (downloads about 3 GB) | weekly, and on pull requests that change the installer roles |
| Real run | the playbooks against a disposable VM on a real Proxmox node | `make provision` / `make baseline` with `LIMIT=` | no: by hand, see the test policy |

A pull request can only merge when the required checks pass. A failing check's log names the file, line and
rule. How the integration tests are built, and how to add a case, is in [docs/testing.md](docs/testing.md).

## Coding standards

Contributions must follow these standards; CI enforces the first two, and a pull request that fails them
can't be merged.

- **Ansible**: the [ansible-lint `production` profile](https://ansible.readthedocs.io/projects/lint/profiles/)
  as configured in `.ansible-lint` (warnings fail). In particular: fully-qualified module names
  (`ansible.builtin.copy`), a `name` on every task and play, `ansible.builtin.command` with `argv` rather
  than `shell` unless a pipe or redirect is unavoidable, explicit file `mode`s, `changed_when` on commands,
  and `no_log: true` on every task that handles a password, hash, token or key.
- **YAML**: [yamllint](https://yamllint.readthedocs.io/) with the rules in `.yamllint` (lines up to 140
  characters, `true`/`false` only, quoted octal modes such as `"0644"`).
- **Roles**: every variable a role reads is listed in its `defaults/main.yml` with a comment saying what it
  does; variables that form the inventory interface (`admin_user`, `github_user`, `ssh_port`, ...) keep
  their short names and are documented in [docs/interfaces.md](docs/interfaces.md) and the README.
- **Idempotence**: a second run of any playbook against the same hosts must report no changes. Tasks that
  can't tell whether they changed something say so with `changed_when`.
- **Safety**: nothing may wipe, recreate or reconfigure a VM that already exists; anything that can lock
  an operator out (SSH, firewall, users) must keep working with `--check --diff`.
- **Workflows**: top-level read-only permissions, actions pinned to full commit SHAs with a version comment,
  and untrusted event data only through `env:`.
- **Exceptions** are rare: suppress a single finding on its own line (`# noqa: <rule>`) with a short reason,
  never a whole file or rule without one.
- Beyond the tools, follow the style of the surrounding code rather than introducing a new one.

## Test policy

- **New or changed behaviour must come with automated tests.** A pull request that adds or changes what a
  playbook or role does must add or extend a case in the integration tests (`tests/`, see
  [docs/testing.md](docs/testing.md)) covering what should happen and, where it applies, what should be
  refused, and must keep `make lint`, `make syntax` and `make test` passing, including the 80% task coverage
  floor. This is mandatory: the maintainer doesn't merge behaviour changes without them.
- **Bug fixes must add a regression test** to the integration tests that fails without the fix, unless the
  bug can only happen on real hardware or a real Proxmox node; then the pull request says why and how the
  fix was verified.
- **Changes the containers can't show** (a real install, `cloud-init`, the QEMU guest agent, reboots) are
  also run against a disposable VM on a real Proxmox node, and the pull request describes that run: the
  commands, the Proxmox VE and Ubuntu versions, and that a second run reported no changes.
- Security-relevant changes (SSH, firewall, users, secrets, disk handling) must show that the protection
  still works, for example that password SSH and root login are refused after the change.

## Developer Certificate of Origin

Every commit must be signed off to certify the [Developer Certificate of Origin](https://developercertificate.org/)
(DCO): that you wrote the change, or otherwise have the right to submit it under the project's license.
Sign off by adding a `Signed-off-by` line with your name and e-mail address (a GitHub `noreply` address is
fine) to the commit message, which `git commit -s` does for you:

```text
Signed-off-by: Your Name <you@example.com>
```

A check on every pull request fails if a commit lacks the sign-off. To fix it, amend or rebase with
`--signoff` (for example `git rebase --signoff main`) and force-push your branch.

## Pull requests

- `main` is protected: changes land only through a pull request that is up to date with `main`, has passed
  the required CI checks and has one approval; pushing to it dismisses earlier approvals. Pull requests are
  squash-merged. While the project has a single maintainer, the maintainer's own pull requests are merged
  with the administrator bypass, which works only through a pull request (never a direct push), after the
  same checks pass. Dependabot's patch and minor updates are approved by a workflow.
- Keep each pull request focused on one change, and describe what it does and why.
- Update `README.md`, `docs/` and the role's `defaults/main.yml` when you change a variable or user-visible
  behaviour, and add a line to `CHANGELOG.md` under "Unreleased".
- Follow the [coding standards](#coding-standards) and the [test policy](#test-policy), and sign off your
  commits ([DCO](#developer-certificate-of-origin)).
- Never commit secrets, a real inventory, a plaintext Vault file, vault password files or `.env` files.
  Secret scanning, push protection and gitleaks are enabled on the repository.
- These playbooks wipe disks and change SSH and firewall settings on real servers, so treat changes to
  provisioning, SSH, the firewall, users or secret handling with extra care, and call them out in the pull
  request description.

## License

By contributing, you agree that your contribution is licensed under the project's [MIT License](LICENSE).
