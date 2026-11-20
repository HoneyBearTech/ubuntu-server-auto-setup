# Plan: os-catalog-refresh

## What Sean asked for

"Work all open issues in my repos": here, the OS catalog half of issue #170, "OS catalog: refresh needed".

## What we found

- The weekly catalog check lists newer vendor builds for eight releases' cloud images (Ubuntu 22.04 and 26.04,
  Debian 12 and 13, CentOS Stream 9 and 10, AlmaLinux 9 and 10) than `roles/os_catalog/vars/catalog.yml` pins.
- `make catalog` (`tools/update_os_catalog.py`) pins each release's newest build, checking the vendor's signature on
  its checksum file with the keys in `tools/os-catalog-keys/`. It refuses anything unsigned or signed by another key.
- The same issue asks for a k3s catalog refresh (new k3s patch releases, Helm, Argo CD chart). That is a separate
  change with its own tests, so it gets its own pull request.

## What we'll change

1. Run `make catalog` and keep only the pin updates it writes to `roles/os_catalog/vars/catalog.yml`.
   Check: every changed entry is a newer build of the same release, with the version, URL and checksum from
   the vendor's signed index; nothing else in the file changes; `make catalog-check` then reports no newer
   build and every pin verifies.
2. `CHANGELOG.md`: one line under "Unreleased".
3. Run `make catalog-test`, the linters and the unit tests. On this pull request CI also downloads each newly
   pinned image, checks its checksum and boots it with the baseline ("Real cloud images"), and checks the
   pinned installer ISO if it changed.
4. Record the job in `docs/` (pass map, decisions log).

## Who's on it

Engineer, Security Officer, Auditor. No Researcher: the weekly check already lists what's new.

## Risks

- A vendor build can break the baseline. CI's real-image boot test runs on this pull request, and a failing
  release is left at its old pin and said so.
- Servers already installed aren't changed; the catalog only decides what new VMs install.

## Out of scope

The k3s catalog half of #170 (next pull request); #204 (its fix is #208); the other open pull requests.
