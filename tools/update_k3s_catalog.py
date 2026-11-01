#!/usr/bin/env python3
"""Builds and checks the k3s catalog, roles/k3s/vars/catalog.yml.

The catalog works like a lock file: for each k3s release it pins the binary's URL and SHA-256 per architecture,
and it pins kube-vip's and its cloud provider's images by digest. A playbook run checks the binary's hash and pulls
the images by digest and trusts nothing else. This script reads what upstream publishes, over HTTPS from GitHub and
GHCR only: k3s' sha256sum-<arch>.txt (k3s signs neither it nor its binaries), and the images' digests (kube-vip
signs neither). So the pins are only as good as the HTTPS connection and the review of the pull request that
changes them, and the weekly check below notices if upstream's answer ever changes.

    update_k3s_catalog.py            add the newest patch release of k3s' two newest minor lines, and pin the newest
                                     kube-vip and cloud provider; pinned releases are kept (remove them by hand)
    update_k3s_catalog.py --check    verify every pin against upstream again (checksum, download, digest) and list
                                     newer releases; exit 1 if a pin fails

--report FILE writes what --check found as Markdown (the weekly OS catalog workflow puts it in its issue). Only the
standard library is needed (make k3s-catalog, make k3s-catalog-check). GITHUB_TOKEN, if set, is sent to GitHub's
API only, for its higher rate limit. See docs/testing.md.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

ROOT = pathlib.Path(__file__).resolve().parents[1]
CATALOG = ROOT / "roles" / "k3s" / "vars" / "catalog.yml"

# Everything is read from these hosts, over HTTPS: GitHub's API and releases (whose downloads redirect to its
# asset hosts) and GHCR.
HOSTS = frozenset(
    {"api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
     "ghcr.io"}
)
MAX_BYTES = 4 * 1024 * 1024
HEADERS = {"User-Agent": "ubuntu-server-auto-setup k3s catalog"}
K3S_REPO = "k3s-io/k3s"
# The binary's file name in a release, by the catalog's architecture.
FILES = {"amd64": "k3s", "arm64": "k3s-arm64"}
IMAGES = {
    "k3s_kube_vip_image": ("ghcr.io/kube-vip/kube-vip", "kube-vip/kube-vip"),
    "k3s_kube_vip_cloud_provider_image": ("ghcr.io/kube-vip/kube-vip-cloud-provider",
                                          "kube-vip/kube-vip-cloud-provider"),
}
# What the registry is asked for: the multi-arch index, as the nodes pull it.
MANIFEST_TYPES = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
VERSION = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)\+k3s([0-9]+)\Z")
TAG = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)\Z")
SHA256 = re.compile(r"^[0-9a-f]{64}\Z")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}\Z")
# How many of k3s' newest minor lines the catalog follows.
MINOR_LINES = 2

HEADER = """\
---
# The k3s catalog: the k3s releases the k3s role installs and the kube-vip images it deploys, each pinned like a
# lock file. A binary is installed only if it matches its SHA-256 here, an image is pulled only by its digest here.
# k3s publishes its checksums (sha256sum-<arch>.txt) over HTTPS with no signature, and kube-vip its images on
# GHCR with no signature either, so these pins are taken over HTTPS and reviewed in the pull request that changes
# them. tools/update_k3s_catalog.py checks them against upstream again, and the weekly OS catalog workflow opens
# an issue when one no longer matches or upstream has a newer release.
"""
IMAGES_COMMENT = """\
# kube-vip (the API address and LoadBalancer addresses, by ARP) and its cloud provider (hands out the pool's
# addresses): multi-arch image indexes, by digest.
"""


class CatalogError(Exception):
    """An upstream answer that can't be read or trusted, or a catalog that isn't as this script writes it."""


# --- the catalog file -----------------------------------------------------------------------------------------


def version_key(version: str) -> tuple[int, ...]:
    found = VERSION.match(version)
    if not found:
        raise CatalogError(f"{version!r} isn't a k3s release (v1.2.3+k3s1)")
    return tuple(int(part) for part in found.groups())


def binary_url(version: str, arch: str) -> str:
    return f"https://github.com/{K3S_REPO}/releases/download/{urllib.parse.quote(version, safe='')}/{FILES[arch]}"


def render(catalog: dict) -> str:
    out = [HEADER.rstrip("\n"), "k3s_catalog:"]
    for release in sorted(catalog["k3s_catalog"], key=lambda r: version_key(r["version"])):
        out += [f"  - version: {release['version']}", "    binaries:"]
        for arch in sorted(release["binaries"]):
            binary = release["binaries"][arch]
            out += [f"      {arch}:", f"        url: {binary['url']}", f"        sha256: \"{binary['sha256']}\""]
    out += ["", IMAGES_COMMENT.rstrip("\n")]
    for key in IMAGES:
        image = catalog[key]
        out += [f"{key}:", f"  name: {image['name']}", f"  tag: {image['tag']}", f"  digest: {image['digest']}"]
    return "\n".join(out) + "\n"


_LINE = re.compile(r"^(?P<indent> *)(?P<dash>- )?(?P<key>[a-z0-9_]+):(?: (?P<value>\S+))?$")


def read_catalog(text: str) -> dict:
    """The catalog as render writes it (no YAML library: the standard library only)."""
    catalog: dict = {"k3s_catalog": []}
    section = release = arch = image = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line or line.startswith("#") or line == "---":
            continue
        found = _LINE.match(line)
        if not found:
            raise CatalogError(f"catalog line {number} isn't as this script writes it: {line!r}")
        indent, key, value = len(found["indent"]), found["key"], found["value"]
        if indent == 0:
            section = key
            if key in IMAGES:
                image = catalog[key] = {}
            elif key != "k3s_catalog":
                raise CatalogError(f"catalog line {number}: unknown key {key!r}")
        elif section == "k3s_catalog" and found["dash"] and key == "version":
            release = {"version": value, "binaries": {}}
            catalog["k3s_catalog"].append(release)
        elif section == "k3s_catalog" and release is not None and key == "binaries" and value is None:
            continue
        elif section == "k3s_catalog" and release is not None and indent == 6 and value is None:
            arch = key
            release["binaries"][arch] = {}
        elif section == "k3s_catalog" and arch is not None and indent == 8 and key in ("url", "sha256"):
            # The checksum is quoted, so YAML never reads one that's all digits as a number.
            release["binaries"][arch][key] = value.strip('"') if key == "sha256" else value
        elif section in IMAGES and indent == 2 and key in ("name", "tag", "digest"):
            image[key] = value
        else:
            raise CatalogError(f"catalog line {number} isn't as this script writes it: {line!r}")
    validate(catalog)
    return catalog


def validate(catalog: dict) -> None:
    """Everything a pin may be: known names, plain values, the URL each release's binary has."""
    releases = catalog.get("k3s_catalog") or []
    if not releases:
        raise CatalogError("the catalog has no k3s release")
    seen = set()
    for release in releases:
        version = release.get("version", "")
        version_key(version)
        if version in seen:
            raise CatalogError(f"{version} is in the catalog twice")
        seen.add(version)
        if set(release.get("binaries", {})) != set(FILES):
            raise CatalogError(f"{version}: needs a binary for each of {', '.join(sorted(FILES))}")
        for arch, binary in release["binaries"].items():
            if binary.get("url") != binary_url(version, arch):
                raise CatalogError(f"{version} {arch}: the URL must be {binary_url(version, arch)}")
            if not SHA256.match(binary.get("sha256", "")):
                raise CatalogError(f"{version} {arch}: sha256 must be 64 hex digits")
    for key, (name, _repo) in IMAGES.items():
        image = catalog.get(key) or {}
        plain = TAG.match(image.get("tag", "")) and DIGEST.match(image.get("digest", ""))
        if image.get("name") != name or not plain:
            raise CatalogError(f"{key} must name {name}, a tag (v1.2.3) and a digest (sha256:...)")


# --- upstream -------------------------------------------------------------------------------------------------


def check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in HOSTS:
        raise CatalogError(f"refusing {url}: only HTTPS from {', '.join(sorted(HOSTS))}")


class _UpstreamOnly(urllib.request.HTTPRedirectHandler):
    """Redirects only to the hosts above (a release download goes to GitHub's asset host)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_UpstreamOnly)


class Http:
    """GET and HEAD over HTTPS from HOSTS, with retries for connections that fail (an HTTP error is final)."""

    def request(self, url: str, method: str = "GET", headers: dict | None = None, tries: int = 3):
        check_url(url)
        request = urllib.request.Request(url, method=method, headers={**HEADERS, **(headers or {})})  # noqa: S310
        for attempt in range(1, tries + 1):
            try:
                with _OPENER.open(request, timeout=60) as response:
                    data = response.read(MAX_BYTES + 1) if method == "GET" else b""
                    if len(data) > MAX_BYTES:
                        raise CatalogError(f"{url} is larger than it should be")
                    return data, {k.lower(): v for k, v in response.headers.items()}
            except urllib.error.HTTPError as err:
                raise CatalogError(f"{url}: HTTP {err.code}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
                if attempt == tries:
                    raise CatalogError(f"{url}: {getattr(err, 'reason', err)}") from None
                time.sleep(5 * attempt)
        raise AssertionError("unreachable")


class Upstream:
    """What k3s and kube-vip publish: releases on GitHub, checksums next to the binaries, digests on GHCR."""

    def __init__(self, http: Http | None = None, token: str | None = None):
        self.http = http or Http()
        self.token = token

    def _api(self, path: str):
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data, _ = self.http.request(f"https://api.github.com/repos/{path}", headers=headers)
        try:
            return json.loads(data)
        except ValueError:
            raise CatalogError(f"GitHub's answer for {path} isn't JSON") from None

    def releases(self, repo: str) -> list[str]:
        """The tags of repo's releases that are neither drafts nor pre-releases (the newest 100)."""
        found = self._api(f"{repo}/releases?per_page=100")
        if not isinstance(found, list):
            raise CatalogError(f"GitHub's release list for {repo} isn't a list")
        return [r["tag_name"] for r in found
                if isinstance(r, dict) and isinstance(r.get("tag_name"), str) and not r.get("draft")
                and not r.get("prerelease")]

    def sha256(self, version: str, arch: str) -> str:
        """The SHA-256 k3s' sha256sum-<arch>.txt gives for the release's binary."""
        data, _ = self.http.request(binary_url(version, arch).rsplit("/", 1)[0] + f"/sha256sum-{arch}.txt")
        for line in data.decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == FILES[arch] and SHA256.match(parts[0]):
                return parts[0]
        raise CatalogError(f"{version}: sha256sum-{arch}.txt has no line for {FILES[arch]}")

    def downloadable(self, url: str) -> bool:
        try:
            self.http.request(url, method="HEAD")
            return True
        except CatalogError:
            return False

    def digest(self, name: str, tag: str) -> str:
        """The digest GHCR serves for name:tag (the index a node pulls)."""
        repo = name.removeprefix("ghcr.io/")
        data, _ = self.http.request(f"https://ghcr.io/token?scope=repository:{repo}:pull")
        try:
            token = json.loads(data)["token"]
        except (ValueError, KeyError, TypeError):
            raise CatalogError(f"GHCR gave no token for {repo}") from None
        _, headers = self.http.request(f"https://ghcr.io/v2/{repo}/manifests/{tag}", method="HEAD",
                                       headers={"Authorization": f"Bearer {token}", "Accept": MANIFEST_TYPES})
        digest = headers.get("docker-content-digest", "")
        if not DIGEST.match(digest):
            raise CatalogError(f"GHCR gave no digest for {name}:{tag}")
        return digest


# --- building and checking ------------------------------------------------------------------------------------


def wanted_versions(tags: list[str]) -> list[str]:
    """The newest patch release (and its newest +k3sN) of each of k3s' MINOR_LINES newest minor lines."""
    newest: dict[tuple[int, int], str] = {}
    for tag in tags:
        if not VERSION.match(tag):
            continue
        key = version_key(tag)
        line = key[:2]
        if line not in newest or version_key(newest[line]) < key:
            newest[line] = tag
    return [newest[line] for line in sorted(newest)[-MINOR_LINES:]]


def newest_tag(tags: list[str]) -> str:
    plain = [t for t in tags if TAG.match(t)]
    if not plain:
        raise CatalogError("no release with a plain version tag (v1.2.3)")
    return max(plain, key=lambda t: tuple(int(p) for p in TAG.match(t).groups()))


def build(upstream: Upstream, catalog: dict) -> dict:
    """The catalog with the releases wanted_versions names added (pinned ones are kept) and the newest images."""
    have = {r["version"] for r in catalog["k3s_catalog"]}
    releases = list(catalog["k3s_catalog"])
    for version in wanted_versions(upstream.releases(K3S_REPO)):
        if version not in have:
            releases.append({"version": version, "binaries": {
                arch: {"url": binary_url(version, arch), "sha256": upstream.sha256(version, arch)} for arch in FILES}})
    result = {"k3s_catalog": releases}
    for key, (name, repo) in IMAGES.items():
        tag = newest_tag(upstream.releases(repo))
        result[key] = {"name": name, "tag": tag, "digest": upstream.digest(name, tag)}
    validate(result)
    return result


def check(upstream: Upstream, catalog: dict, downloadable: Callable[[str], bool] | None = None):
    """Every pin against upstream again: (failures, newer)."""
    downloadable = downloadable or upstream.downloadable
    failures: list[str] = []
    newer: list[str] = []
    for release in catalog["k3s_catalog"]:
        for arch, binary in sorted(release["binaries"].items()):
            what = f"k3s `{release['version']}` {arch}"
            try:
                published = upstream.sha256(release["version"], arch)
            except CatalogError as err:
                failures.append(f"{what}: {err}")
                continue
            if published != binary["sha256"]:
                failures.append(f"{what}: upstream's checksum is now {published}, the catalog pins {binary['sha256']}")
            elif not downloadable(binary["url"]):
                failures.append(f"{what}: {binary['url']} can't be downloaded any more")
    try:
        pinned = {r["version"] for r in catalog["k3s_catalog"]}
        newer += [f"k3s `{v}`" for v in wanted_versions(upstream.releases(K3S_REPO)) if v not in pinned]
    except CatalogError as err:
        failures.append(f"k3s' releases: {err}")
    for key, (name, repo) in IMAGES.items():
        image = catalog[key]
        what = f"`{name}:{image['tag']}`"
        try:
            served = upstream.digest(name, image["tag"])
            if served != image["digest"]:
                failures.append(f"{what}: GHCR now serves {served}, the catalog pins {image['digest']}")
            tag = newest_tag(upstream.releases(repo))
            if tag != image["tag"]:
                newer.append(f"`{name}:{tag}`")
        except CatalogError as err:
            failures.append(f"{what}: {err}")
    return failures, newer


def report(failures: list[str], newer: list[str]) -> str:
    if not failures and not newer:
        return ""
    out = ["The k3s catalog (`roles/k3s/vars/catalog.yml`) needs a refresh.", ""]
    if failures:
        out += ["**Pins that no longer check out** (runs that need them fail):", ""]
        out += [f"- {line}" for line in failures] + [""]
    if newer:
        out += ["**Newer releases:**", ""] + [f"- {line}" for line in newer] + [""]
    out += ["Run `make k3s-catalog`, review the diff and open a pull request (docs/testing.md, \"The k3s catalog\")."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None, upstream: Upstream | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verify the pins instead of updating them")
    parser.add_argument("--report", type=pathlib.Path, help="with --check: write the findings as Markdown here")
    parser.add_argument("--catalog", type=pathlib.Path, default=CATALOG, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    upstream = upstream or Upstream(token=os.environ.get("GITHUB_TOKEN") or None)
    try:
        catalog = read_catalog(args.catalog.read_text(encoding="utf-8"))
        if not args.check:
            text = render(build(upstream, catalog))
            changed = args.catalog.read_text(encoding="utf-8") != text
            args.catalog.write_text(text, encoding="utf-8")
            print(f"{args.catalog.name}: {'updated' if changed else 'already current'}")
            return 0
        failures, newer = check(upstream, catalog)
    except CatalogError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        if args.check and args.report:
            args.report.write_text(f"The k3s catalog check couldn't run: {err}\n", encoding="utf-8")
        return 1
    text = report(failures, newer)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text or "Every k3s pin checks out, and upstream has nothing newer.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
