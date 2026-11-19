# Fix main's CI (November 2026)

**Asked for:** get `main`'s checks green again (red since 14 November 2026, last green 5 November), so the waiting
dependency updates can merge.

## What we found

- **CentOS Stream images gone.** quay.io moves the `stream9` and `stream10` tags about once a week and deletes
  the digest a tag left within days. The pins from 4 November were gone by 14 November, so the CentOS Stream
  baseline and updates jobs couldn't build their container. Dependabot did propose the new digests (10 November),
  but that pull request was closed unmerged, and its weekly check is slower than quay.io's clean-up.
- **OSV scan.** `opentelemetry-api` 1.45.1 (pulled in by fastapi) has GHSA-mf5v-w3w3-f6cg; 1.45.2 fixes it.
  Its update pull request was closed unmerged.
- **Kubernetes on Rocky Linux 9.** Nothing in the repository changed between the green and the red run:
  `ubuntu-latest` moved from the ubuntu-24.04 runner image to ubuntu-26.04. The four k3s nodes share the runner's
  kernel, and on the new image the agent's kubelet fails with `inotify_init: too many open files` (the
  kernel's default of 128 inotify instances per user), so the agent never registers and the "node is Ready" wait
  times out. The remotedialer `close 1006` lines are the servers noticing that agent, not the cause.

## What we changed

1. `tests/images/ubuntu-target/Dockerfile`: CentOS Stream 9 and 10 pinned to the digests their tags have now.
   Checked by the CentOS Stream baseline and updates jobs.
2. `.github/dependabot.yml`: the test target image gets its own Docker entry, checked daily instead of weekly,
   so a new CentOS Stream digest is proposed the day it appears. Checked by Dependabot's next run.
3. `requirements-ci.txt`, `requirements-webui.txt`: `opentelemetry-api` 1.45.2, with the hashes PyPI publishes.
   Checked by the OSV scan and every job that installs them.
4. `.github/workflows/checks.yml`: the Kubernetes job raises the runner's inotify limits before the test, and
   prints the old values. Checked by the three Kubernetes jobs.
5. `docs/dependencies.md`, `docs/testing.md`: describe the two changes above.

## Decisions

- Raise the inotify limits rather than the "node is Ready" retries: more retries can't help a kubelet that
  can't start. Only CI needs it: a real server is a VM with its own kernel.
- Don't pin the runner back to ubuntu-24.04: the servers the project targets run 26.04, and the old image will
  be retired.
- Considered letting the CentOS Stream digest updates merge on their own once the checks pass, like patch
  updates. Not done here: it widens what merges without the maintainer's review, so it's the maintainer's call.
  Until then, those update pull requests need merging within a few days.
- The one failed assertion in the AlmaLinux 10 updates job on one pull request (the update wave numbers)
  passed everywhere else; treated as flaky unless it comes back.
