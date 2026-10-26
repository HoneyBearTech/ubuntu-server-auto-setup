#!/usr/bin/env python3
"""Builds and checks the OS catalog, roles/os_catalog/vars/catalog.yml.

The catalog works like a lock file: for each supported release it pins the exact image URL and the image's own
checksum. A playbook run checks that checksum and nothing else, so what the catalog says is what gets installed.
This script is where the vendors' signatures are checked: it reads each vendor's index over HTTPS, and where the
vendor signs its checksum file (Ubuntu, Rocky Linux, AlmaLinux), it verifies the signature with gpgv against a
signing key whose fingerprint is pinned below. CentOS Stream and Debian's cloud images publish no signature, so
their checksums are taken over HTTPS only, and their entries say `signed: false`.

    update_os_catalog.py            write the newest build of every release to the catalog
    update_os_catalog.py --check    verify every pinned entry again (signature, checksum, URL still there) and
                                    list newer builds; exit 1 if a pinned entry fails

--report FILE writes what --check found as Markdown (the weekly workflow puts it in an issue). Only the
standard library and gpgv are needed (make catalog, make catalog-check). See docs/testing.md.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import html
import json
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
from functools import partial

ROOT = pathlib.Path(__file__).resolve().parents[1]
CATALOG = ROOT / "roles" / "os_catalog" / "vars" / "catalog.yml"
KEY_DIR = ROOT / "tools" / "os-catalog-keys"

# Image and checksum files are fetched only from these hosts, over HTTPS.
HOSTS = frozenset(
    {
        "releases.ubuntu.com",
        "cloud-images.ubuntu.com",
        "cloud.debian.org",
        "cloud.centos.org",
        "dl.rockylinux.org",
        "repo.almalinux.org",
    }
)
MAX_INDEX_BYTES = 16 * 1024 * 1024
HEADERS = {"User-Agent": "ubuntu-server-auto-setup os catalog"}
# A plain file name: no path, and not "." or ".." or hidden.
FILE_NAME = re.compile(r"^[A-Za-z0-9_+-][A-Za-z0-9._+-]*\Z")
# What the import/ folder of a Proxmox storage accepts as a disk image.
IMPORT_FILE = re.compile(r"^[A-Za-z0-9._+-]+\.(qcow2|raw|vmdk)\Z")
CHECKSUM = re.compile(r"^(sha256:[0-9a-f]{64}|sha512:[0-9a-f]{128})\Z")


class CatalogError(Exception):
    """A vendor index that can't be read or trusted."""


@dataclasses.dataclass(frozen=True)
class Key:
    """A signing key: its file (armored, or a binary keyring) and its primary key's fingerprint."""

    path: pathlib.Path
    fingerprint: str


# Fingerprints checked against each vendor's own documentation (docs/security.md lists where).
KEYS = {
    # Ubuntu CD Image Automatic Signing Key (2012): the ISOs' SHA256SUMS. The installer role uses the same keyring.
    "ubuntu-cdimage": Key(
        ROOT / "roles" / "installer_iso" / "files" / "ubuntu-cdimage-keyring.gpg",
        "843938DF228D22F7B3742BC0D94AA3F0EFE21092",
    ),
    # UEC Image Automatic Signing Key: the cloud images' SHA256SUMS.
    "ubuntu-cloud": Key(KEY_DIR / "ubuntu-cloud-images.asc", "D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81"),
    "rocky-9": Key(KEY_DIR / "rocky-9.asc", "21CB256AE16FC54C6E652949702D426D350D275D"),
    "rocky-10": Key(KEY_DIR / "rocky-10.asc", "FC226859C0860BF0DDB95B085B106C736FEDFC85"),
    "alma-9": Key(KEY_DIR / "almalinux-9.asc", "BF18AC2876178908D6E71267D36CB86CB86B3716"),
    "alma-10": Key(KEY_DIR / "almalinux-10.asc", "EE6DB7B98F5BF5EDD9DA0DE5DEE5C11CC2A1E572"),
}


@dataclasses.dataclass(frozen=True)
class Release:
    """A supported release. eol is the end of the vendor's standard (Ubuntu) or long-term (Debian) support."""

    id: str
    family: str
    name: str
    version: str
    codename: str
    eol: str


# End-of-life dates: Ubuntu's and Debian's from distro-info-data (eol-server; eol-lts), the RHEL family's from
# the vendors' lifecycle pages. A new release is added here, with its finders below.
RELEASES = (
    Release("ubuntu-22.04", "ubuntu", "Ubuntu", "22.04", "jammy", "2027-06-01"),
    Release("ubuntu-24.04", "ubuntu", "Ubuntu", "24.04", "noble", "2029-05-31"),
    Release("ubuntu-26.04", "ubuntu", "Ubuntu", "26.04", "resolute", "2031-05-29"),
    Release("debian-12", "debian", "Debian", "12", "bookworm", "2028-06-30"),
    Release("debian-13", "debian", "Debian", "13", "trixie", "2030-06-30"),
    Release("centos-stream-9", "rhel", "CentOS Stream", "9", "", "2027-05-31"),
    Release("centos-stream-10", "rhel", "CentOS Stream", "10", "", "2030-05-31"),
    Release("rocky-9", "rhel", "Rocky Linux", "9", "", "2032-05-31"),
    Release("rocky-10", "rhel", "Rocky Linux", "10", "", "2035-05-31"),
    Release("alma-9", "rhel", "AlmaLinux", "9", "", "2032-05-31"),
    Release("alma-10", "rhel", "AlmaLinux", "10", "", "2035-05-31"),
)


@dataclasses.dataclass(frozen=True)
class Image:
    """One pinned download: where it is, its checksum, the file name it's stored under, and its build."""

    url: str
    checksum: str
    file: str
    build: str
    signed: bool

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class Candidate:
    """A build a vendor's index lists. key sorts builds oldest to newest."""

    build: str
    key: tuple
    resolve: Callable[[], Image]


class Vendors:
    """Reads the vendors' indexes. fetch is replaced in the tests."""

    def __init__(self, fetch: Callable[[str], bytes] | None = None, gpgv: str = "gpgv"):
        self.fetch = fetch or fetch_https
        self.gpgv = gpgv

    # --- helpers -------------------------------------------------------------------------------------------

    def text(self, url: str) -> str:
        return self.fetch(url).decode("utf-8", "replace")

    def listing(self, url: str) -> list[str]:
        return parse_listing(self.text(url))

    def verified(self, url: str, sig_url: str, key: Key) -> str:
        """The file at url, after checking its detached signature at sig_url."""
        data = self.fetch(url)
        verify_signature(data, self.fetch(sig_url), key, self.gpgv, url)
        return data.decode("utf-8", "replace")

    # --- finders: every build the index lists, newest last -------------------------------------------------

    def candidates(self, release: Release, method: str) -> list[Candidate]:
        finder = {
            ("ubuntu", "autoinstall"): self.ubuntu_iso,
            ("ubuntu", "cloud_image"): self.ubuntu_cloud,
            ("debian", "cloud_image"): self.debian_cloud,
            ("rhel", "cloud_image"): self.rhel_cloud,
        }[(release.family, method)]
        return sorted(finder(release), key=lambda c: c.key)

    def ubuntu_iso(self, release: Release) -> list[Candidate]:
        base = f"https://releases.ubuntu.com/{release.version}"
        sums = self.verified(f"{base}/SHA256SUMS", f"{base}/SHA256SUMS.gpg", KEYS["ubuntu-cdimage"])
        found = parse_sha256sums(sums)
        name = re.compile(rf"^ubuntu-({re.escape(release.version)}(?:\.\d+)?)-live-server-amd64\.iso\Z")
        out = []
        for file, digest in found.items():
            match = name.match(file)
            if match:
                point = match.group(1)
                image = Image(f"{base}/{file}", f"sha256:{digest}", file, point, True)
                out.append(Candidate(point, version_key(point), lambda image=image: image))
        return out

    def ubuntu_cloud(self, release: Release) -> list[Candidate]:
        base = f"https://cloud-images.ubuntu.com/releases/{release.codename}"
        out = []
        for entry in self.listing(f"{base}/"):
            match = re.match(r"^release-(\d{8}(?:\.\d+)?)/?\Z", entry)
            if match:
                serial = match.group(1)
                out.append(Candidate(serial, version_key(serial), partial(self._ubuntu_cloud, base, release, serial)))
        return out

    def _ubuntu_cloud(self, base: str, release: Release, serial: str) -> Image:
        folder = f"{base}/release-{serial}"
        signed = self.verified(f"{folder}/SHA256SUMS", f"{folder}/SHA256SUMS.gpg", KEYS["ubuntu-cloud"])
        sums = parse_sha256sums(signed)
        file = f"ubuntu-{release.version}-server-cloudimg-amd64.img"
        digest = need(sums, file, f"{folder}/SHA256SUMS")
        # The .img is a qcow2 image; Proxmox's import/ content only takes names ending in .qcow2, .raw or .vmdk.
        stored = f"ubuntu-{release.version}-server-cloudimg-amd64-{serial}.qcow2"
        return Image(f"{folder}/{file}", f"sha256:{digest}", stored, serial, True)

    def debian_cloud(self, release: Release) -> list[Candidate]:
        base = f"https://cloud.debian.org/images/cloud/{release.codename}"
        out = []
        for entry in self.listing(f"{base}/"):
            match = re.match(r"^(\d{8}-\d{4})/?\Z", entry)
            if match:
                build = match.group(1)
                out.append(Candidate(build, version_key(build), partial(self._debian_cloud, base, release, build)))
        return out

    def _debian_cloud(self, base: str, release: Release, build: str) -> Image:
        folder = f"{base}/{build}"
        # Debian signs its ISOs' checksums, but not the cloud images': HTTPS only.
        sums = parse_sha512sums(self.text(f"{folder}/SHA512SUMS"))
        file = f"debian-{release.version}-genericcloud-amd64-{build}.qcow2"
        digest = need(sums, file, f"{folder}/SHA512SUMS")
        return Image(f"{folder}/{file}", f"sha512:{digest}", file, build, False)

    def rhel_cloud(self, release: Release) -> list[Candidate]:
        major = re.escape(release.version)
        if release.id.startswith("centos-stream-"):
            base = f"https://cloud.centos.org/centos/{release.version}-stream/x86_64/images"
            pattern = rf"^CentOS-Stream-GenericCloud-(?:x86_64-)?{major}-(\d{{8}}\.\d+)\.x86_64\.qcow2\Z"
            resolve = self._centos
        elif release.id.startswith("rocky-"):
            base = f"https://dl.rockylinux.org/pub/rocky/{release.version}/images/x86_64"
            pattern = rf"^Rocky-{major}-GenericCloud-Base-({major}\.\d+-\d{{8}}\.\d+)\.x86_64\.qcow2\Z"
            resolve = self._rocky
        else:
            base = f"https://repo.almalinux.org/almalinux/{release.version}/cloud/x86_64/images"
            pattern = rf"^AlmaLinux-{major}-GenericCloud-({major}\.\d+-\d{{8}}(?:\.\d+)?)\.x86_64\.qcow2\Z"
            resolve = self._alma
        out = []
        for entry in self.listing(f"{base}/"):
            match = re.match(pattern, entry)
            if match:
                build = match.group(1)
                out.append(Candidate(build, version_key(build), partial(resolve, base, release, build, entry)))
        return out

    def _centos(self, base: str, release: Release, build: str, file: str) -> Image:
        # CentOS Stream publishes no signature: HTTPS only.
        sums = parse_bsd_sums(self.text(f"{base}/{file}.SHA256SUM"), "SHA256")
        return Image(f"{base}/{file}", f"sha256:{need(sums, file, f'{base}/{file}.SHA256SUM')}", file, build, False)

    def _rocky(self, base: str, release: Release, build: str, file: str) -> Image:
        url = f"{base}/{file}.CHECKSUM"
        sums = parse_bsd_sums(self.verified(url, f"{url}.asc", KEYS[f"rocky-{release.version}"]), "SHA256")
        return Image(f"{base}/{file}", f"sha256:{need(sums, file, url)}", file, build, True)

    def _alma(self, base: str, release: Release, build: str, file: str) -> Image:
        url = f"{base}/CHECKSUM"
        sums = parse_sha256sums(self.verified(url, f"{url}.asc", KEYS[f"alma-{release.version}"]))
        return Image(f"{base}/{file}", f"sha256:{need(sums, file, url)}", file, build, True)


def methods_for(release: Release) -> tuple[str, ...]:
    return ("autoinstall", "cloud_image") if release.family == "ubuntu" else ("cloud_image",)


# --- parsing (vendor input, untrusted: tests/fuzz/fuzz_os_catalog.py) ----------------------------------------


def parse_listing(text: str) -> list[str]:
    """The last path segment of every link in a directory listing, a trailing slash kept for folders."""
    out = []
    for href in re.findall(r'href\s*=\s*"([^"]*)"', text, flags=re.IGNORECASE):
        path = urllib.parse.urlsplit(html.unescape(href)).path
        if not path or path.startswith("?"):
            continue
        folder = path.endswith("/")
        name = urllib.parse.unquote(path.rstrip("/").rsplit("/", 1)[-1])
        if FILE_NAME.match(name):
            out.append(name + ("/" if folder else ""))
    return out


def parse_sha256sums(text: str) -> dict[str, str]:
    """`<hex>  <file>` or `<hex> *<file>` lines (sha256sum's output); anything else is skipped."""
    return _parse_gnu(text, 64)


def parse_sha512sums(text: str) -> dict[str, str]:
    return _parse_gnu(text, 128)


def _parse_gnu(text: str, length: int) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        match = re.match(rf"^([0-9a-fA-F]{{{length}}}) [ *]([A-Za-z0-9._+-]+)\Z", line.strip("\r"))
        if match:
            out[match.group(2)] = match.group(1).lower()
    return out


def parse_bsd_sums(text: str, algorithm: str) -> dict[str, str]:
    """`SHA256 (<file>) = <hex>` lines (sha256sum --tag's output, as Rocky and CentOS publish)."""
    length = {"SHA256": 64, "SHA512": 128}[algorithm]
    out = {}
    for line in text.splitlines():
        match = re.match(rf"^{algorithm} \(([A-Za-z0-9._+-]+)\) = ([0-9a-fA-F]{{{length}}})\Z", line.strip("\r"))
        if match:
            out[match.group(1)] = match.group(2).lower()
    return out


def version_key(text: str) -> tuple:
    """Sorts 26.04 < 26.04.1 < 26.04.10 and 20261016 < 20261016.1: numbers as numbers."""
    return tuple(int(part) for part in re.findall(r"\d+", text))


def need(sums: dict[str, str], file: str, where: str) -> str:
    if file not in sums:
        raise CatalogError(f"{where} doesn't list {file}")
    return sums[file]


# --- fetching and signatures -------------------------------------------------------------------------------


class _VendorOnly(urllib.request.HTTPRedirectHandler):
    """Indexes and checksum files are read from the vendors' own hosts only, even after a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """Images are served by the vendors' mirrors (Debian's redirect to them): any host, HTTPS only. The pinned
    checksum, not the host, is what a run trusts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise CatalogError(f"refusing a redirect to {newurl}: HTTPS only")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_VendorOnly)
_MIRROR_OPENER = urllib.request.build_opener(_HttpsOnly)


def check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in HOSTS:
        raise CatalogError(f"refusing {url}: only HTTPS from {', '.join(sorted(HOSTS))}")


def fetch_https(url: str, tries: int = 3) -> bytes:
    """The body at url. Connections that fail are retried; an HTTP error is final."""
    check_url(url)
    request = urllib.request.Request(url, headers=HEADERS)  # noqa: S310 (check_url: HTTPS only)
    for attempt in range(1, tries + 1):
        try:
            with _OPENER.open(request, timeout=60) as response:
                data = response.read(MAX_INDEX_BYTES + 1)
            if len(data) > MAX_INDEX_BYTES:
                raise CatalogError(f"{url} is larger than an index should be")
            return data
        except urllib.error.HTTPError as err:
            raise CatalogError(f"{url}: HTTP {err.code}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            if attempt == tries:
                raise CatalogError(f"{url}: {getattr(err, 'reason', err)}") from None
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def url_exists(url: str) -> bool:
    """Whether a HEAD request for url succeeds (images are too large to fetch here)."""
    check_url(url)
    request = urllib.request.Request(url, method="HEAD", headers=HEADERS)  # noqa: S310 (check_url: HTTPS only)
    try:
        with _MIRROR_OPENER.open(request, timeout=60):
            return True
    except (urllib.error.URLError, TimeoutError, ConnectionError, CatalogError):
        return False


def dearmor(data: bytes) -> bytes:
    """A binary keyring from an ASCII-armored key (gpgv reads only binary keyrings everywhere)."""
    if not data.lstrip().startswith(b"-----BEGIN PGP PUBLIC KEY BLOCK-----"):
        return data
    lines = data.decode("ascii").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("-----BEGIN"))
    body_start = next(i for i in range(start + 1, len(lines)) if not lines[i].strip()) + 1
    body = []
    for line in lines[body_start:]:
        if line.startswith(("=", "-----END")):
            break
        body.append(line.strip())
    return base64.b64decode("".join(body), validate=True)


def verify_signature(data: bytes, signature: bytes, key: Key, gpgv: str, what: str) -> None:
    """Fails unless signature is a good signature on data by the key with key.fingerprint."""
    with tempfile.TemporaryDirectory(prefix="usas-catalog-") as tmp:
        folder = pathlib.Path(tmp)
        (folder / "keyring.gpg").write_bytes(dearmor(key.path.read_bytes()))
        (folder / "data").write_bytes(data)
        (folder / "data.sig").write_bytes(signature)
        result = subprocess.run(
            [gpgv, "--status-fd", "1", "--keyring", str(folder / "keyring.gpg"), str(folder / "data.sig"),
             str(folder / "data")],
            capture_output=True, text=True, check=False,
        )
    # VALIDSIG's last field is the primary key's fingerprint: only the pinned key passes.
    if result.returncode != 0 or not re.search(
        rf"^\[GNUPG:\] VALIDSIG [0-9A-F]{{40}} .* {key.fingerprint}$", result.stdout, flags=re.MULTILINE
    ):
        raise CatalogError(f"the signature on {what} is bad or isn't by {key.fingerprint}: {result.stderr.strip()}")


# --- the catalog file --------------------------------------------------------------------------------------

HEADER = """\
---
# The OS catalog: every release the playbooks install, each image pinned by URL and checksum, like a lock file.
# Written by tools/update_os_catalog.py (make catalog), which checks the vendors' signatures where they sign their
# checksums (signed: true); don't edit it by hand. See docs/interfaces.md.
# yamllint disable rule:line-length
"""


def render(entries: list[dict]) -> str:
    """The catalog as YAML, in the one fixed layout read_catalog reads."""
    lines = [HEADER.rstrip("\n"), "os_catalog:"]
    for entry in entries:
        first = True
        for name in ("id", "family", "name", "version", "codename", "eol"):
            lines.append(f"  {'- ' if first else '  '}{name}: {json.dumps(entry[name])}")
            first = False
        lines.append("    methods:")
        for method, image in sorted(entry["methods"].items()):
            lines.append(f"      {method}:")
            for name in ("url", "checksum", "file", "build", "signed"):
                lines.append(f"        {name}: {json.dumps(image[name])}")
    return "\n".join(lines) + "\n"


def read_catalog(text: str) -> list[dict]:
    """Reads what render writes (the standard library has no YAML parser)."""
    entries: list[dict] = []
    method: dict | None = None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith(("#", "---")) or line == "os_catalog:":
            continue
        match = re.match(r"^( *)(- )?([a-z_]+):(?: (.*))?\Z", line)
        if not match:
            raise CatalogError(f"catalog line {number} isn't in the layout this script writes")
        indent, dash, name, value = len(match.group(1)), match.group(2), match.group(3), match.group(4)
        if indent == 2 and dash:
            entries.append({"methods": {}})
        if not entries:
            raise CatalogError(f"catalog line {number}: no entry has started")
        if indent in (2, 4) and value is not None and name != "methods":
            entries[-1][name] = _scalar(value, number)
        elif indent == 6 and value is None:
            method = entries[-1]["methods"].setdefault(name, {})
        elif indent == 8 and method is not None and value is not None:
            method[name] = _scalar(value, number)
        elif not (indent == 4 and name == "methods" and value is None):
            raise CatalogError(f"catalog line {number} isn't in the layout this script writes")
    return entries


def _scalar(value: str, number: int) -> str | bool:
    """A quoted string or true/false, as render writes them."""
    try:
        parsed = json.loads(value)
    except ValueError:
        parsed = None
    if not isinstance(parsed, str | bool):
        raise CatalogError(f"catalog line {number}: the value isn't a quoted string, true or false")
    return parsed


def entry_for(release: Release, images: dict[str, Image]) -> dict:
    entry = {f: getattr(release, f) for f in ("id", "family", "name", "version", "codename", "eol")}
    entry["methods"] = {method: image.as_dict() for method, image in images.items()}
    return entry


# What each field of an entry and of an image must look like (they end up in file names, URLs and the web UI).
ENTRY_FIELDS = {
    "id": re.compile(r"^[a-z][a-z0-9.-]*\Z"),
    "family": re.compile(r"^(ubuntu|debian|rhel)\Z"),
    "name": re.compile(r"^[A-Z][A-Za-z ]*\Z"),
    "version": re.compile(r"^\d+(\.\d+)?\Z"),
    "codename": re.compile(r"^[a-z]*\Z"),
    "eol": re.compile(r"^\d{4}-\d{2}-\d{2}\Z"),
}
IMAGE_FIELDS = {"url": re.compile(r"^https://\S+\Z"), "checksum": CHECKSUM, "build": FILE_NAME}


def validate(entries: list[dict]) -> None:
    """Fails unless every entry has the fields the roles read, with safe values."""
    seen = set()
    for entry in entries:
        if set(entry) != {*ENTRY_FIELDS, "methods"}:
            raise CatalogError(f"{entry.get('id', 'an entry')}: fields must be {', '.join([*ENTRY_FIELDS, 'methods'])}")
        for name, pattern in ENTRY_FIELDS.items():
            if not isinstance(entry.get(name), str) or not pattern.match(entry[name]):
                raise CatalogError(f"{entry.get('id', 'an entry')}: bad or missing {name}")
        if entry["id"] in seen:
            raise CatalogError(f"{entry['id']} is in the catalog twice")
        seen.add(entry["id"])
        allowed = {"autoinstall", "cloud_image"} if entry["family"] == "ubuntu" else {"cloud_image"}
        if not entry["methods"] or not set(entry["methods"]) <= allowed:
            raise CatalogError(f"{entry['id']}: install methods must be some of {', '.join(sorted(allowed))}")
        for method, image in entry["methods"].items():
            if set(image) != {*IMAGE_FIELDS, "file", "signed"}:
                raise CatalogError(f"{entry['id']} {method}: fields must be url, checksum, file, build and signed")
            for name, pattern in IMAGE_FIELDS.items():
                if not isinstance(image.get(name), str) or not pattern.match(image[name]):
                    raise CatalogError(f"{entry['id']} {method}: bad or missing {name}")
            check_url(image["url"])
            # Proxmox's import/ content takes only disk image names.
            pattern = IMPORT_FILE if method == "cloud_image" else FILE_NAME
            if not isinstance(image.get("file"), str) or not pattern.match(image["file"]):
                raise CatalogError(f"{entry['id']} {method}: bad or missing file")
            if not isinstance(image.get("signed"), bool):
                raise CatalogError(f"{entry['id']} {method}: signed must be true or false")


# --- commands ----------------------------------------------------------------------------------------------


def build(vendors: Vendors) -> list[dict]:
    entries = []
    for release in RELEASES:
        images = {}
        for method in methods_for(release):
            found = vendors.candidates(release, method)
            images[method] = newest(found, release, method).resolve()
        entries.append(entry_for(release, images))
    validate(entries)
    return entries


def newest(found: list[Candidate], release: Release, method: str) -> Candidate:
    if not found:
        raise CatalogError(f"{release.id}: the vendor's index lists no {method} build")
    return found[-1]


def check(vendors: Vendors, entries: list[dict], exists: Callable[[str], bool]) -> tuple[list[str], list[str]]:
    """Verifies each pinned image again. Returns (failures, newer builds)."""
    failures: list[str] = []
    newer: list[str] = []
    pinned = {entry["id"]: entry for entry in entries}
    for release in RELEASES:
        entry = pinned.get(release.id)
        if entry is None:
            failures.append(f"`{release.id}` is missing from the catalog")
            continue
        for method in methods_for(release):
            image = entry["methods"].get(method)
            if image is None:
                failures.append(f"`{release.id}` has no `{method}` entry")
                continue
            try:
                found = vendors.candidates(release, method)
                match = [c for c in found if c.build == image["build"]]
                if not match:
                    failures.append(f"`{release.id}` {method}: build {image['build']} is gone from the vendor's index")
                else:
                    fresh = match[0].resolve()
                    if fresh.as_dict() != image:
                        failures.append(f"`{release.id}` {method}: the vendor now says {fresh.as_dict()}, "
                                        f"the catalog {image}")
                    elif not exists(image["url"]):
                        failures.append(f"`{release.id}` {method}: {image['url']} can't be downloaded")
                latest = newest(found, release, method)
                if latest.build != image["build"] and latest.key > version_key(image["build"]):
                    newer.append(f"`{release.id}` {method}: {latest.build} (pinned: {image['build']})")
            except CatalogError as err:
                failures.append(f"`{release.id}` {method}: {err}")
    return failures, newer


def report(failures: list[str], newer: list[str]) -> str:
    if not failures and not newer:
        return ""
    out = ["The OS catalog (`roles/os_catalog/vars/catalog.yml`) needs a refresh.", ""]
    if failures:
        out += ["**Pinned images that no longer check out** (runs that need them fail):", ""]
        out += [f"- {line}" for line in failures] + [""]
    if newer:
        out += ["**Newer builds:**", ""] + [f"- {line}" for line in newer] + [""]
    out += ["Run `make catalog`, review the diff and open a pull request (docs/testing.md, \"The OS catalog\")."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verify the pinned entries instead of updating them")
    parser.add_argument("--report", type=pathlib.Path, help="with --check: write the findings as Markdown here")
    parser.add_argument("--catalog", type=pathlib.Path, default=CATALOG, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    vendors = Vendors()
    try:
        if not args.check:
            text = render(build(vendors))
            changed = not args.catalog.exists() or args.catalog.read_text(encoding="utf-8") != text
            args.catalog.write_text(text, encoding="utf-8")
            print(f"{args.catalog.relative_to(ROOT) if args.catalog.is_relative_to(ROOT) else args.catalog}: "
                  f"{'updated' if changed else 'already current'}")
            return 0
        entries = read_catalog(args.catalog.read_text(encoding="utf-8"))
        validate(entries)
        failures, newer = check(vendors, entries, url_exists)
    except CatalogError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        if args.check and args.report:
            args.report.write_text(f"The OS catalog check couldn't run: {err}\n", encoding="utf-8")
        return 1
    text = report(failures, newer)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text or "Every pinned image checks out, and none has a newer build.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
