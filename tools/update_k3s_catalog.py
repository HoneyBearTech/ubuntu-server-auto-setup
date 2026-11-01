#!/usr/bin/env python3
"""Builds and checks the k3s catalog, roles/k3s/vars/catalog.yml.

The catalog works like a lock file: for each k3s release it pins the binary's URL and SHA-256 per architecture,
it pins kube-vip's and its cloud provider's images by digest, and Helm's archive and Argo CD's chart (for the
optional Argo CD bootstrap) by SHA-256. A playbook run checks the hashes and pulls the images by digest and trusts
nothing else. This script reads what upstream publishes, over HTTPS from GitHub, GHCR and get.helm.sh only: k3s'
sha256sum-<arch>.txt (k3s signs neither it nor its binaries), and the images' digests (kube-vip signs neither). So
those pins are only as good as the HTTPS connection and the review of the pull request that changes them, and the
weekly check below notices if upstream's answer ever changes. Helm signs its checksum files and Argo's chart
repository signs each chart's provenance file: their hashes are taken only from a good signature by a key in
tools/k3s-catalog-keys/ (gpgv checks it).

    update_k3s_catalog.py            add the newest patch release of k3s' two newest minor lines, and pin the newest
                                     kube-vip, cloud provider, Helm and Argo CD chart; pinned k3s releases are kept
                                     (remove them by hand)
    update_k3s_catalog.py --check    verify every pin against upstream again (checksum, download, digest) and list
                                     newer releases; exit 1 if a pin fails

--report FILE writes what --check found as Markdown (the weekly OS catalog workflow puts it in its issue). Only the
standard library and gpgv are needed (make k3s-catalog, make k3s-catalog-check). GITHUB_TOKEN, if set, is sent to
GitHub's API only, for its higher rate limit. See docs/testing.md.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

ROOT = pathlib.Path(__file__).resolve().parents[1]
CATALOG = ROOT / "roles" / "k3s" / "vars" / "catalog.yml"
KEY_DIR = ROOT / "tools" / "k3s-catalog-keys"

# Everything is read from these hosts, over HTTPS: GitHub's API and releases (whose downloads redirect to its
# asset hosts) and GHCR.
HOSTS = frozenset(
    {"api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
     "ghcr.io", "get.helm.sh"}
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
HELM_REPO = "helm/helm"
CHART_REPO = "argoproj/argo-helm"
# Argo's chart repository tags each chart's release <chart>-<version>.
CHART_TAG = re.compile(r"^argo-cd-([0-9]+)\.([0-9]+)\.([0-9]+)\Z")
CHART_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\Z")
# Releases asked for at a time: Helm's notes are long, so 100 of them don't fit in MAX_BYTES.
PER_PAGE = {HELM_REPO: 20}
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
BOOTSTRAP_COMMENT = """\
# The optional Argo CD bootstrap (k8s_bootstrap_argocd): Helm's archive, by the SHA-256 in its signed checksum
# file, and Argo CD's chart, by the SHA-256 in its signed provenance file (app_version is the Argo CD release in it).
"""


@dataclasses.dataclass(frozen=True)
class Key:
    """Signing keys: their file (ASCII-armored) and the primary keys' fingerprints a signature may be by."""

    path: pathlib.Path
    fingerprints: frozenset


# Helm's KEYS file (github.com/helm/helm, KEYS): the maintainers who sign its releases. Argo's chart repository's key
# (argoproj.github.io/argo-helm/pgp_keys.asc, "Argo Helm maintainers"), which signs every chart's provenance file.
KEYS = {
    "helm": Key(KEY_DIR / "helm-KEYS.asc", frozenset({
        "672C657BE06B4B30969C4A57461449C25E36B98E", "F1261BDE929012C8FF2E501D6EA5D7598529A53E",
        "967F8AC5E2216F9F4FD270AD92AA783CBAAE8E3B", "76939899B137D575D3274E756DCCB9D752D35BA8",
        "49D09C86C3DC8DA3F0A076221EF612347F8A9958", "ABA2529598F6626C420D335B62F49E747D911B60",
        "4AB45F1CB0D292975C6371436E2A23D806B6E6DD", "208DD36ED5BB3745A16743A4C7C6FBB5B91C1155",
        "7FEC81FACC7FFB2A010ADD13C2D40F4D8196E874", "BF888333D96A1C18E2682AAED79D67C9EC016739"})),
    "argo-helm": Key(KEY_DIR / "argo-helm.asc", frozenset({"2B8F22F57260EFA67BE1C5824B11F800CD9D2252"})),
}


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


def helm_url(version: str, arch: str) -> str:
    return f"https://get.helm.sh/helm-{version}-linux-{arch}.tar.gz"


def chart_url(version: str) -> str:
    return f"https://github.com/{CHART_REPO}/releases/download/argo-cd-{version}/argo-cd-{version}.tgz"


def chart_key(version: str) -> tuple[int, ...]:
    if not CHART_VERSION.match(version):
        raise CatalogError(f"{version!r} isn't a chart version (1.2.3)")
    return tuple(int(part) for part in version.split("."))


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
    helm, chart = catalog["k3s_helm"], catalog["k3s_argocd_chart"]
    out += ["", BOOTSTRAP_COMMENT.rstrip("\n"), "k3s_helm:", f"  version: {helm['version']}", "  binaries:"]
    for arch in sorted(helm["binaries"]):
        binary = helm["binaries"][arch]
        out += [f"    {arch}:", f"      url: {binary['url']}", f"      sha256: \"{binary['sha256']}\""]
    out += ["k3s_argocd_chart:", f"  version: {chart['version']}", f"  app_version: {chart['app_version']}",
            f"  url: {chart['url']}", f"  sha256: \"{chart['sha256']}\""]
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
            if key in IMAGES or key == "k3s_argocd_chart":
                image = catalog[key] = {}
            elif key == "k3s_helm":
                catalog[key] = {"binaries": {}}
                arch = None
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
        elif section == "k3s_helm" and indent == 2 and key == "version" and value is not None:
            catalog[section]["version"] = value
        elif section == "k3s_helm" and indent == 2 and key == "binaries" and value is None:
            continue
        elif section == "k3s_helm" and indent == 4 and value is None:
            arch = key
            catalog[section]["binaries"][arch] = {}
        elif section == "k3s_helm" and arch is not None and indent == 6 and key in ("url", "sha256"):
            catalog[section]["binaries"][arch][key] = value.strip('"') if key == "sha256" else value
        elif section == "k3s_argocd_chart" and indent == 2 and key in ("version", "app_version", "url", "sha256"):
            image[key] = value.strip('"') if key == "sha256" else value
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
    helm = catalog.get("k3s_helm") or {}
    if not TAG.match(helm.get("version", "")) or set(helm.get("binaries", {})) != set(FILES):
        raise CatalogError(f"k3s_helm needs a version (v1.2.3) and a binary for each of {', '.join(sorted(FILES))}")
    for arch, binary in helm["binaries"].items():
        if binary.get("url") != helm_url(helm["version"], arch) or not SHA256.match(binary.get("sha256", "")):
            raise CatalogError(f"k3s_helm {arch}: the URL must be {helm_url(helm['version'], arch)}, "
                               "sha256 64 hex digits")
    chart = catalog.get("k3s_argocd_chart") or {}
    chart_key(chart.get("version", ""))
    if (chart.get("url") != chart_url(chart["version"]) or not SHA256.match(chart.get("sha256", ""))
            or not TAG.match(chart.get("app_version", ""))):
        raise CatalogError(f"k3s_argocd_chart: the URL must be {chart_url(chart['version'])}, sha256 64 hex digits, "
                           "app_version a release (v1.2.3)")


# --- upstream -------------------------------------------------------------------------------------------------


def check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in HOSTS:
        raise CatalogError(f"refusing {url}: only HTTPS from {', '.join(sorted(HOSTS))}")


def dearmor(data: bytes) -> bytes:
    """A binary keyring from an ASCII-armored key block (gpgv reads only binary keyrings everywhere)."""
    lines = data.decode("ascii").splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("-----BEGIN PGP PUBLIC KEY BLOCK")), None)
    if start is None:
        raise CatalogError("the key file has no ASCII-armored public key block")
    body_start = next(i for i in range(start + 1, len(lines)) if not lines[i].strip()) + 1
    body = []
    for line in lines[body_start:]:
        if line.startswith(("=", "-----END")):
            break
        body.append(line.strip())
    return base64.b64decode("".join(body), validate=True)


_CLEARSIGNED = re.compile(
    rb"\A-----BEGIN PGP SIGNED MESSAGE-----\r?\n(?:Hash: [A-Za-z0-9, ]+\r?\n)+\r?\n(?P<text>.*?)\r?\n"
    rb"-----BEGIN PGP SIGNATURE-----\r?\n.*?-----END PGP SIGNATURE-----\s*\Z", re.DOTALL)


def clearsigned_text(signed: bytes) -> bytes:
    """The text of a clear-signed message (dash-escaping undone), which must be the whole file: one message, nothing
    before or after it that the signature wouldn't cover."""
    found = _CLEARSIGNED.match(signed)
    if not found or signed.count(b"-----BEGIN PGP S") != 2:
        raise CatalogError("isn't one clear-signed message and nothing else")
    return b"\n".join(line[2:] if line.startswith(b"- ") else line for line in found["text"].splitlines()) + b"\n"


def verify_signature(signature: bytes, key: Key, what: str, data: bytes | None = None, gpgv: str = "gpgv") -> bytes:
    """What's signed, if signature is a good signature by one of key's keys: a detached signature on data, or (data
    None) a clear-signed message, whose text is returned. Fails otherwise."""
    text = clearsigned_text(signature) if data is None else data
    with tempfile.TemporaryDirectory(prefix="usas-k3s-catalog-") as tmp:
        folder = pathlib.Path(tmp)
        (folder / "keyring.gpg").write_bytes(dearmor(key.path.read_bytes()))
        (folder / "signature").write_bytes(signature)
        args = [gpgv, "--status-fd", "1", "--keyring", str(folder / "keyring.gpg"), str(folder / "signature")]
        if data is not None:
            (folder / "data").write_bytes(data)
            args.append(str(folder / "data"))
        result = subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603 (fixed arguments)
    # VALIDSIG's last field is the primary key's fingerprint: only the pinned keys pass.
    signers = re.findall(r"^\[GNUPG:\] VALIDSIG [0-9A-F]{40} .* ([0-9A-F]{40})$", result.stdout, flags=re.MULTILINE)
    if result.returncode != 0 or not signers or not set(signers) <= key.fingerprints:
        raise CatalogError(f"the signature on {what} is bad or isn't by a pinned key: {result.stderr.strip()}")
    return text


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

    def __init__(self, http: Http | None = None, token: str | None = None,
                 verify: Callable[..., bytes] | None = None):
        self.http = http or Http()
        self.token = token
        self.verify = verify or verify_signature

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
        """The tags of repo's releases that are neither drafts nor pre-releases (the newest PER_PAGE[repo], 100 if
        it isn't there)."""
        found = self._api(f"{repo}/releases?per_page={PER_PAGE.get(repo, 100)}")
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

    def helm_sha256(self, version: str, arch: str) -> str:
        """The SHA-256 of Helm's archive, from its checksum file, signed by one of Helm's maintainers."""
        name = helm_url(version, arch).rsplit("/", 1)[1]
        sums, _ = self.http.request(f"{helm_url(version, arch)}.sha256sum")
        signature, _ = self.http.request(
            f"https://github.com/{HELM_REPO}/releases/download/{version}/{name}.sha256sum.asc")
        self.verify(signature, KEYS["helm"], f"{name}.sha256sum", data=sums)
        parts = sums.decode("utf-8", "replace").split()
        if len(parts) == 2 and parts[1] == name and SHA256.match(parts[0]):
            return parts[0]
        raise CatalogError(f"{name}.sha256sum isn't a checksum of {name}")

    def chart(self, version: str) -> dict:
        """Argo CD's chart: its URL, SHA-256 and Argo CD release, from the chart's signed provenance file."""
        url = chart_url(version)
        prov, _ = self.http.request(f"{url}.prov")
        try:
            signed = self.verify(prov, KEYS["argo-helm"], f"argo-cd-{version}.tgz.prov")
        except CatalogError as err:
            raise CatalogError(f"argo-cd-{version}.tgz.prov: {err}") from None
        text = signed.decode("utf-8", "replace")
        found = re.search(rf"^  argo-cd-{re.escape(version)}\.tgz: sha256:([0-9a-f]{{64}})$", text, flags=re.MULTILINE)
        app = re.search(r"^appVersion: (v[0-9]+\.[0-9]+\.[0-9]+)$", text, flags=re.MULTILINE)
        named = re.search(rf"^version: {re.escape(version)}$", text, flags=re.MULTILINE)
        if not (found and app and named):
            raise CatalogError(f"argo-cd-{version}.tgz.prov doesn't give the chart's version, appVersion and sha256")
        return {"version": version, "app_version": app[1], "url": url, "sha256": found[1]}

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


def newest_chart(tags: list[str]) -> str:
    found = [t.removeprefix("argo-cd-") for t in tags if CHART_TAG.match(t)]
    if not found:
        raise CatalogError("no release of the argo-cd chart (argo-cd-1.2.3)")
    return max(found, key=chart_key)


def build_helm(upstream: Upstream, version: str) -> dict:
    return {"version": version, "binaries": {
        arch: {"url": helm_url(version, arch), "sha256": upstream.helm_sha256(version, arch)} for arch in FILES}}


def build(upstream: Upstream, catalog: dict) -> dict:
    """The catalog with the releases wanted_versions names added (pinned ones are kept), the newest images, and the
    newest Helm and Argo CD chart."""
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
    result["k3s_helm"] = build_helm(upstream, newest_tag(upstream.releases(HELM_REPO)))
    result["k3s_argocd_chart"] = upstream.chart(newest_chart(upstream.releases(CHART_REPO)))
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
    helm = catalog["k3s_helm"]
    for arch, binary in sorted(helm["binaries"].items()):
        what = f"Helm `{helm['version']}` {arch}"
        try:
            published = upstream.helm_sha256(helm["version"], arch)
        except CatalogError as err:
            failures.append(f"{what}: {err}")
            continue
        if published != binary["sha256"]:
            failures.append(f"{what}: the signed checksum is now {published}, the catalog pins {binary['sha256']}")
        elif not downloadable(binary["url"]):
            failures.append(f"{what}: {binary['url']} can't be downloaded any more")
    chart = catalog["k3s_argocd_chart"]
    what = f"Argo CD chart `{chart['version']}`"
    try:
        signed = upstream.chart(chart["version"])
        if signed != chart:
            failures.append(f"{what}: the signed provenance now says {signed['sha256']} ({signed['app_version']}), "
                            f"the catalog pins {chart['sha256']} ({chart['app_version']})")
        elif not downloadable(chart["url"]):
            failures.append(f"{what}: {chart['url']} can't be downloaded any more")
    except CatalogError as err:
        failures.append(f"{what}: {err}")
    for what, repo, pin, newest in (("Helm", HELM_REPO, helm["version"], newest_tag),
                                    ("Argo CD chart", CHART_REPO, chart["version"], newest_chart)):
        try:
            found = newest(upstream.releases(repo))
            if found != pin:
                newer.append(f"{what} `{found}`")
        except CatalogError as err:
            failures.append(f"{what}'s releases: {err}")
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
