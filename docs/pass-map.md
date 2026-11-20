# Pass map

A short record of each piece of work on ubuntu-server-auto-setup: what shipped, or what a review found, and where
to look. Newest first. The reasons behind the choices are in the [decisions log](decisions-log.md); what is planned
next is in the [roadmap](roadmap.md).

| Date | Job | What shipped or was found | Where |
| --- | --- | --- | --- |
| 2026-11-20 | `renovate-migrate` | Dependency updates move from Dependabot to Renovate (`renovate.json`): every Dependabot ecosystem, group and hold carried over, plus `requirements.yml`, the `docker run` tool images and `python-version` / `node-version` in the workflows. Patch and minor updates of Python and npm packages and Actions merge on their own after a 3-day wait and every required check; the CentOS Stream digests of the test image merge with no wait; everything else waits for the maintainer. `.github/dependabot.yml` removed (Dependabot alerts stay on); the docs and CONTRIBUTING's branch rule (no approving review required) updated. | [#213](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/pull/213); `docs/plans/renovate-migrate.md` |
| 2026-11-20 | `os-catalog-refresh` | `make catalog` pinned the newest cloud images for all ten cloud-image releases (Ubuntu 22.04 and 26.04, Debian 12 and 13, CentOS Stream 9 and 10, Rocky Linux 9 and 10, AlmaLinux 9 and 10); Rocky Linux and AlmaLinux move to 9.9 and 10.3. Every checksum re-checked by hand against the vendor, and every published signature against the pinned key. Security review clear; code audit approved after a changelog wording fix. The k3s half of #170 is still to do. | This pull request; `docs/plans/os-catalog-refresh.md` |
