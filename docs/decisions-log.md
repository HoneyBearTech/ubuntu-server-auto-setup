# Decisions log

Why things in ubuntu-server-auto-setup are the way they are: each entry gives the decision, the reason, what was
considered and not done, and review findings that were declined, with why. Newest first. What shipped when is in the
[pass map](pass-map.md).

## 2026-11-20: refresh the OS catalog, including Rocky Linux and the point releases

Job `os-catalog-refresh`.

**Decision.** Keep everything `make catalog` wrote: ten cloud-image pins, not the eight the weekly issue (#170)
listed. Rocky Linux 9 and 10 also had newer builds, and Rocky Linux and AlmaLinux move to point releases 9.9 and
10.3.

**Why.** The catalog tool pins the newest build of each release; both vendors update only their newest point
release, so 9.8 and 10.2 stop getting fixes. Nothing in the roles, playbooks or web UI depends on a point release:
they accept any minor version of a supported major, and image file names come from the catalog. Servers already
installed are not changed. CI's real-image boot test checks each new image with the baseline.

**Not done.** The k3s catalog half of #170 (new k3s patch releases, Helm, Argo CD chart): a separate change with its
own tests, in its own pull request. The plan file still says "eight releases"; it records what was approved, and
this entry records the difference.

**Review findings.** Fixed: the changelog said every image was checked against signed checksums, but Debian and
CentOS Stream publish no signatures (the catalog marks them `signed: false`). Noted, no change: the plan said the
installer ISO is checked "if it changed"; CI checks it on any catalog change.
