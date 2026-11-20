# Pass map

What each piece of maintenance shipped, newest first. The reasons are in the [decisions log](decisions-log.md).

| Date | Job | What shipped | Pull request |
| --- | --- | --- | --- |
| 2026-11-20 | renovate-migrate | Dependency updates move from Dependabot to Renovate (`renovate.json`): every Dependabot ecosystem, group and hold carried over, plus `requirements.yml`, the `docker run` tool images and `python-version` / `node-version` in the workflows. Patch and minor updates of Python and npm packages and Actions merge on their own after a 3-day wait and every required check; the CentOS Stream digests of the test image merge with no wait; everything else waits for the maintainer. `.github/dependabot.yml` removed (Dependabot alerts stay on); the docs and CONTRIBUTING's branch rule (no approving review required) updated. | [#213](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/pull/213) |
