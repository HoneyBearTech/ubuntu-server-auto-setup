"""Unit tests for tools/update_os_catalog.py, offline: the vendors' indexes are made up here and signed with a
throwaway key. Run with `make catalog-test` (pytest, with coverage)."""

import hashlib
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import urllib.error

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("update_os_catalog", ROOT / "tools" / "update_os_catalog.py")
uoc = importlib.util.module_from_spec(_spec)
sys.modules["update_os_catalog"] = uoc
_spec.loader.exec_module(uoc)

pytestmark = pytest.mark.skipif(not shutil.which("gpg") or not shutil.which("gpgv"), reason="needs gpg and gpgv")


class Signer:
    """A throwaway signing key in its own GnuPG home (short path: gpg-agent's socket must fit)."""

    def __init__(self, name):
        self.home = tempfile.mkdtemp(prefix="g", dir="/tmp")
        os.chmod(self.home, 0o700)
        self.gpg(["--quick-gen-key", f"{name} <{name}@invalid>", "ed25519", "sign", "never"])
        out = self.gpg(["--with-colons", "--list-keys"]).stdout
        self.fingerprint = next(line.split(":")[9] for line in out.splitlines() if line.startswith("fpr"))
        self.armored = self.gpg(["--armor", "--export"]).stdout.encode()
        self.binary = self.gpg(["--export"], text=False).stdout

    def gpg(self, args, data=None, text=True):
        return subprocess.run(["gpg", "--homedir", self.home, "--batch", "--quiet", "--passphrase", "", *args],
                              input=data, capture_output=True, check=True, text=text)

    def sign(self, data: bytes) -> bytes:
        return self.gpg(["--detach-sign", "--output", "-"], data=data, text=False).stdout

    def key(self, folder: pathlib.Path, armored=True) -> "uoc.Key":
        path = folder / f"{self.fingerprint}.{'asc' if armored else 'gpg'}"
        path.write_bytes(self.armored if armored else self.binary)
        return uoc.Key(path, self.fingerprint)

    def close(self):
        subprocess.run(["gpgconf", "--homedir", self.home, "--kill", "all"], check=False, capture_output=True)
        shutil.rmtree(self.home, ignore_errors=True)


@pytest.fixture(scope="session")
def signer():
    s = Signer("vendor")
    yield s
    s.close()


@pytest.fixture(scope="session")
def stranger():
    s = Signer("stranger")
    yield s
    s.close()


@pytest.fixture
def keys(signer, tmp_path, monkeypatch):
    """Every vendor key replaced by the throwaway one."""
    key = signer.key(tmp_path)
    monkeypatch.setattr(uoc, "KEYS", {name: key for name in uoc.KEYS})
    return key


def digest(text, algorithm="sha256"):
    return hashlib.new(algorithm, text.encode()).hexdigest()


def listing(*names):
    links = "".join(f'<a href="{n}">{n}</a>\n' for n in names)
    return f'<html><a href="?C=N;O=D">Name</a><a href="/">Parent</a>\n{links}</html>'


class FakeVendors:
    """Indexes of every vendor with two builds of each release (old, new), signed where the vendor signs."""

    def __init__(self, signer):
        self.files = {}
        self.signer = signer
        self.heads = set()
        for release in uoc.RELEASES:
            getattr(self, release.family)(release)

    def add(self, url, text, sign=False):
        self.files[url] = text.encode()
        if sign:
            self.files[url + (".gpg" if url.endswith("SHA256SUMS") else ".asc")] = self.signer.sign(text.encode())

    def ubuntu(self, r):
        base = f"https://releases.ubuntu.com/{r.version}"
        isos = [f"ubuntu-{r.version}{p}-live-server-amd64.iso" for p in ("", ".1", ".10", ".9")]
        sums = "".join(f"{digest(n)} *{n}\n" for n in isos) + f"{digest('d')} *ubuntu-{r.version}-desktop-amd64.iso\n"
        self.add(f"{base}/SHA256SUMS", sums, sign=True)
        self.heads.update(f"{base}/{n}" for n in isos)
        base = f"https://cloud-images.ubuntu.com/releases/{r.codename}"
        self.add(f"{base}/", listing("release-20260101/", "release-20260102.1/", "release/", "current/"))
        for serial in ("20260101", "20260102.1"):
            img = f"ubuntu-{r.version}-server-cloudimg-amd64.img"
            self.add(f"{base}/release-{serial}/SHA256SUMS", f"{digest(serial + img)} *{img}\n", sign=True)
            self.heads.add(f"{base}/release-{serial}/{img}")

    def debian(self, r):
        base = f"https://cloud.debian.org/images/cloud/{r.codename}"
        self.add(f"{base}/", listing("20260101-0001/", "20260102-0002/", "latest/", "daily/"))
        for build in ("20260101-0001", "20260102-0002"):
            img = f"debian-{r.version}-genericcloud-amd64-{build}.qcow2"
            self.add(f"{base}/{build}/SHA512SUMS", f"{digest(img, 'sha512')}  {img}\n")
            self.heads.add(f"{base}/{build}/{img}")

    def rhel(self, r):
        v = r.version
        if r.name == "CentOS Stream":
            base = f"https://cloud.centos.org/centos/{v}-stream/x86_64/images"
            names = [f"CentOS-Stream-GenericCloud-x86_64-{v}-20260101.0.x86_64.qcow2",
                     f"CentOS-Stream-GenericCloud-{v}-20260102.1.x86_64.qcow2"]
            self.add(f"{base}/", listing(*names, f"CentOS-Stream-GenericCloud-x86_64-{v}-latest.x86_64.qcow2",
                                         *(n + ".SHA256SUM" for n in names)))
            for n in names:
                self.add(f"{base}/{n}.SHA256SUM", f"# {n}: 1 bytes\nSHA256 ({n}) = {digest(n)}\n")
        elif r.name == "Rocky Linux":
            base = f"https://dl.rockylinux.org/pub/rocky/{v}/images/x86_64"
            names = [f"Rocky-{v}-GenericCloud-Base-{v}.1-20260101.0.x86_64.qcow2",
                     f"Rocky-{v}-GenericCloud-Base-{v}.2-20260101.0.x86_64.qcow2"]
            self.add(f"{base}/", listing(*names, f"Rocky-{v}-GenericCloud-Base.latest.x86_64.qcow2",
                                         f"Rocky-{v}-GenericCloud-LVM-{v}.2-20260101.0.x86_64.qcow2"))
            for n in names:
                self.add(f"{base}/{n}.CHECKSUM", f"SHA256 ({n}) = {digest(n)}\n", sign=True)
        else:
            base = f"https://repo.almalinux.org/almalinux/{v}/cloud/x86_64/images"
            names = [f"AlmaLinux-{v}-GenericCloud-{v}.2-20260101.x86_64.qcow2",
                     f"AlmaLinux-{v}-GenericCloud-{v}.2-20260102.0.x86_64.qcow2"]
            self.add(f"{base}/", listing(*names, f"AlmaLinux-{v}-GenericCloud-latest.x86_64.qcow2", "CHECKSUM"))
            self.add(f"{base}/CHECKSUM", "".join(f"{digest(n)}  {n}\n" for n in names), sign=True)
        self.heads.update(f"{base}/{n}" for n in names)

    def fetch(self, url):
        uoc.check_url(url)
        if url not in self.files:
            raise uoc.CatalogError(f"{url}: HTTP 404")
        return self.files[url]

    def exists(self, url):
        return url in self.heads


@pytest.fixture
def fake(signer, keys):
    return FakeVendors(signer)


def by_id(entries):
    return {e["id"]: e for e in entries}


def test_build_pins_the_newest_build_of_every_release(fake):
    entries = by_id(uoc.build(uoc.Vendors(fake.fetch)))
    assert list(entries) == [r.id for r in uoc.RELEASES]
    iso = entries["ubuntu-26.04"]["methods"]["autoinstall"]
    # 26.04.10 is newer than 26.04.9, which is newer than 26.04.
    assert iso == {"url": "https://releases.ubuntu.com/26.04/ubuntu-26.04.10-live-server-amd64.iso",
                   "checksum": f"sha256:{digest('ubuntu-26.04.10-live-server-amd64.iso')}",
                   "file": "ubuntu-26.04.10-live-server-amd64.iso", "build": "26.04.10", "signed": True}
    cloud = entries["ubuntu-24.04"]["methods"]["cloud_image"]
    assert cloud["url"].endswith("/noble/release-20260102.1/ubuntu-24.04-server-cloudimg-amd64.img")
    assert cloud["file"] == "ubuntu-24.04-server-cloudimg-amd64-20260102.1.qcow2"
    debian = entries["debian-13"]["methods"]["cloud_image"]
    assert debian["checksum"].startswith("sha512:") and debian["signed"] is False
    assert debian["build"] == "20260102-0002"
    assert entries["centos-stream-10"]["methods"]["cloud_image"]["build"] == "20260102.1"
    assert entries["centos-stream-10"]["methods"]["cloud_image"]["signed"] is False
    assert entries["rocky-9"]["methods"]["cloud_image"]["build"] == "9.2-20260101.0"
    assert entries["rocky-9"]["methods"]["cloud_image"]["signed"] is True
    alma = entries["alma-10"]["methods"]["cloud_image"]
    assert alma["file"] == "AlmaLinux-10-GenericCloud-10.2-20260102.0.x86_64.qcow2"
    assert set(entries["rocky-10"]["methods"]) == {"cloud_image"}
    assert entries["debian-12"]["eol"] == "2028-06-30" and entries["debian-12"]["codename"] == "bookworm"


def test_the_catalog_file_reads_back_the_same_with_the_standard_library_and_yaml(fake):
    entries = uoc.build(uoc.Vendors(fake.fetch))
    text = uoc.render(entries)
    assert uoc.read_catalog(text) == entries
    assert yaml.safe_load(text) == {"os_catalog": entries}


def test_the_committed_catalog_is_valid_and_reads_back_the_same():
    text = uoc.CATALOG.read_text(encoding="utf-8")
    entries = uoc.read_catalog(text)
    uoc.validate(entries)
    assert yaml.safe_load(text) == {"os_catalog": entries}
    assert uoc.render(entries) == text
    assert [e["id"] for e in entries] == [r.id for r in uoc.RELEASES]


def test_the_pinned_keys_are_the_files_in_the_repository():
    for key in uoc.KEYS.values():
        keyring = uoc.dearmor(key.path.read_bytes())
        out = subprocess.run(["gpg", "--show-keys", "--with-colons"], input=keyring, capture_output=True, check=True)
        fingerprints = [line.split(b":")[9].decode() for line in out.stdout.splitlines() if line.startswith(b"fpr")]
        assert key.fingerprint in fingerprints, key.path
    installer = yaml.safe_load((ROOT / "roles" / "installer_iso" / "defaults" / "main.yml").read_text())
    assert uoc.KEYS["ubuntu-cdimage"].fingerprint == installer["installer_signing_fingerprint"]


def test_a_signature_by_another_key_is_refused(fake, stranger):
    url = "https://repo.almalinux.org/almalinux/9/cloud/x86_64/images/CHECKSUM"
    fake.files[url + ".asc"] = stranger.sign(fake.files[url])
    with pytest.raises(uoc.CatalogError, match="isn't by"):
        uoc.build(uoc.Vendors(fake.fetch))


def test_a_changed_checksum_file_fails_its_signature(fake):
    url = "https://releases.ubuntu.com/22.04/SHA256SUMS"
    fake.files[url] = fake.files[url].replace(b"a", b"b")
    with pytest.raises(uoc.CatalogError, match="signature on https://releases.ubuntu.com/22.04/SHA256SUMS"):
        uoc.build(uoc.Vendors(fake.fetch))


def test_a_missing_signature_is_an_error(fake):
    del fake.files["https://dl.rockylinux.org/pub/rocky/10/images/x86_64/"
                   "Rocky-10-GenericCloud-Base-10.2-20260101.0.x86_64.qcow2.CHECKSUM.asc"]
    with pytest.raises(uoc.CatalogError, match="HTTP 404"):
        uoc.build(uoc.Vendors(fake.fetch))


def test_a_binary_keyring_works_too(fake, signer, tmp_path, monkeypatch):
    key = signer.key(tmp_path, armored=False)
    monkeypatch.setattr(uoc, "KEYS", {name: key for name in uoc.KEYS})
    assert uoc.build(uoc.Vendors(fake.fetch))


def test_a_release_with_no_build_is_an_error(fake):
    fake.files["https://cloud.debian.org/images/cloud/trixie/"] = listing("latest/", "daily/").encode()
    with pytest.raises(uoc.CatalogError, match="debian-13: the vendor's index lists no cloud_image build"):
        uoc.build(uoc.Vendors(fake.fetch))


def test_an_image_the_checksum_file_doesnt_list_is_an_error(fake):
    fake.files["https://cloud.debian.org/images/cloud/bookworm/20260102-0002/SHA512SUMS"] = b"\n"
    with pytest.raises(uoc.CatalogError, match="doesn't list debian-12-genericcloud-amd64-20260102-0002.qcow2"):
        uoc.build(uoc.Vendors(fake.fetch))


def test_check_passes_a_current_catalog(fake):
    entries = uoc.build(uoc.Vendors(fake.fetch))
    assert uoc.check(uoc.Vendors(fake.fetch), entries, fake.exists) == ([], [])
    assert uoc.report([], []) == ""


def test_check_lists_newer_builds_without_failing(fake):
    entries = uoc.build(uoc.Vendors(fake.fetch))
    old = uoc.Vendors(fake.fetch).candidates(uoc.RELEASES[3], "cloud_image")[0].resolve()
    by_id(entries)["debian-12"]["methods"]["cloud_image"] = old.as_dict()
    failures, newer = uoc.check(uoc.Vendors(fake.fetch), entries, fake.exists)
    assert failures == []
    assert newer == ["`debian-12` cloud_image: 20260102-0002 (pinned: 20260101-0001)"]
    assert "**Newer builds:**" in uoc.report(failures, newer)


def test_check_fails_a_pin_that_is_gone_changed_or_not_downloadable(fake):
    entries = uoc.build(uoc.Vendors(fake.fetch))
    pins = by_id(entries)
    pins["rocky-9"]["methods"]["cloud_image"]["build"] = "9.0-20250101.0"
    pins["alma-9"]["methods"]["cloud_image"]["checksum"] = "sha256:" + "0" * 64
    del pins["ubuntu-22.04"]["methods"]["cloud_image"]
    entries.remove(pins["centos-stream-9"])
    fake.heads.discard(pins["debian-13"]["methods"]["cloud_image"]["url"])
    del fake.files["https://releases.ubuntu.com/24.04/SHA256SUMS.gpg"]
    failures, newer = uoc.check(uoc.Vendors(fake.fetch), entries, fake.exists)
    text = "\n".join(failures)
    assert "`rocky-9` cloud_image: build 9.0-20250101.0 is gone" in text
    assert "`alma-9` cloud_image: the vendor now says" in text
    assert "`ubuntu-22.04` has no `cloud_image` entry" in text
    assert "`centos-stream-9` is missing from the catalog" in text
    assert "`debian-13` cloud_image: https://cloud.debian.org/" in text and "can't be downloaded" in text
    assert "`ubuntu-24.04` autoinstall: https://releases.ubuntu.com/24.04/SHA256SUMS.gpg: HTTP 404" in text
    assert len(failures) == 6
    assert "**Pinned images that no longer check out**" in uoc.report(failures, newer)


def test_main_updates_then_checks(fake, tmp_path, monkeypatch, capsys):
    vendors = uoc.Vendors
    monkeypatch.setattr(uoc, "Vendors", lambda: vendors(fake.fetch))
    monkeypatch.setattr(uoc, "url_exists", fake.exists)
    catalog = tmp_path / "catalog.yml"
    assert uoc.main(["--catalog", str(catalog)]) == 0
    assert "updated" in capsys.readouterr().out
    assert uoc.main(["--catalog", str(catalog)]) == 0
    assert "already current" in capsys.readouterr().out
    report = tmp_path / "report.md"
    assert uoc.main(["--check", "--catalog", str(catalog), "--report", str(report)]) == 0
    assert report.read_text() == "" and "checks out" in capsys.readouterr().out
    fake.heads.clear()
    assert uoc.main(["--check", "--catalog", str(catalog), "--report", str(report)]) == 1
    assert "can't be downloaded" in report.read_text()
    catalog.write_text("os_catalog:\n  oops\n")
    assert uoc.main(["--check", "--catalog", str(catalog), "--report", str(report)]) == 1
    assert "isn't in the layout" in capsys.readouterr().err
    assert report.read_text().startswith("The OS catalog check couldn't run: catalog line 2")


@pytest.mark.parametrize("text", [
    "os_catalog:\n    id: \"x\"\n",  # a field before any entry
    "os_catalog:\n  - id: \"x\"\n          url: \"y\"\n",  # an image field outside a method
    "os_catalog:\n  - id: \"x\"\n  url\n",
    "os_catalog:\n  - id: x\n",  # unquoted
    "os_catalog:\n  - id: [1]\n",
    "os_catalog:\n  - id: 1\n",
    "os_catalog:\n  - id: \"x\"\n    methods: \"x\"\n",
])
def test_read_catalog_refuses_other_layouts(text):
    with pytest.raises(uoc.CatalogError):
        uoc.read_catalog(text)


@pytest.mark.parametrize(("change", "message"), [
    ({"id": "Ubuntu 26.04"}, "bad or missing id"),
    ({"family": "arch"}, "bad or missing family"),
    ({"name": "<b>Ubuntu</b>"}, "bad or missing name"),
    ({"version": "26.04 LTS"}, "bad or missing version"),
    ({"codename": None}, "bad or missing codename"),
    ({"eol": "soon"}, "bad or missing eol"),
    ({"url": "http://releases.ubuntu.com/x.iso"}, "bad or missing url"),
    ({"url": "https://example.com/x.iso"}, "only HTTPS from"),
    ({"checksum": "md5:00"}, "bad or missing checksum"),
    ({"signed": "yes"}, "signed must be true or false"),
    ({"file": "../x.iso"}, "bad or missing file"),
    ({"file": ".."}, "bad or missing file"),
    ({"file": "x.img", "method": "cloud_image"}, "bad or missing file"),
    ({"build": "1 2"}, "bad or missing build"),
    ({"method": "kickstart"}, "install methods must be some of autoinstall, cloud_image"),
    ({"family": "debian"}, "install methods must be some of cloud_image"),
])
def test_validate_refuses_unsafe_entries(change, message):
    method = change.pop("method", "autoinstall")
    entry = {"id": "ubuntu-26.04", "family": "ubuntu", "name": "Ubuntu", "version": "26.04", "codename": "resolute",
             "eol": "2031-05-29", "methods": {method: {
                 "url": "https://releases.ubuntu.com/26.04/x.qcow2", "checksum": "sha256:" + "a" * 64,
                 "file": "x.qcow2", "build": "26.04.1", "signed": True}}}
    for name, value in change.items():
        (entry if name in entry else entry["methods"][method])[name] = value
    with pytest.raises(uoc.CatalogError, match=message):
        uoc.validate([entry])


def test_validate_refuses_missing_and_extra_fields():
    entries = uoc.read_catalog(uoc.CATALOG.read_text(encoding="utf-8"))
    del entries[0]["eol"]
    with pytest.raises(uoc.CatalogError, match="fields must be id, family, name, version, codename, eol, methods"):
        uoc.validate(entries)
    entries = uoc.read_catalog(uoc.CATALOG.read_text(encoding="utf-8"))
    entries[0]["methods"]["autoinstall"]["mirror"] = "x"
    with pytest.raises(uoc.CatalogError, match="fields must be url, checksum, file, build and signed"):
        uoc.validate(entries)


def test_validate_refuses_an_entry_without_methods():
    entries = uoc.read_catalog(uoc.CATALOG.read_text(encoding="utf-8"))
    entries[0]["methods"] = {}
    with pytest.raises(uoc.CatalogError, match="install methods"):
        uoc.validate(entries)


def test_validate_refuses_duplicates():
    entries = uoc.read_catalog(uoc.CATALOG.read_text(encoding="utf-8"))
    with pytest.raises(uoc.CatalogError, match="twice"):
        uoc.validate(entries + entries[:1])


def test_parse_listing_keeps_plain_names_only():
    text = ('<a href="?C=M;O=A">x</a><a href="/images/cloud/">up</a><a HREF="release-20260101/">r</a>'
            '<a href="https://cloud-images.ubuntu.com/releases/noble/release-20260102/">abs</a>'
            '<a href="a%20b.qcow2">sp</a><a href="x.qcow2?raw=1">q</a><a href="&#46;&#46;/">dots</a>'
            '<a href="http://evil/../../etc/passwd">p</a><a href="">e</a>')
    assert uoc.parse_listing(text) == ["cloud/", "release-20260101/", "release-20260102/", "x.qcow2", "passwd"]


def test_parse_sums():
    good = "a" * 64
    assert uoc.parse_sha256sums(f"{good} *x.iso\r\n{good.upper()}  y.img\n{good} ../z\n# c\n{good[1:]} *w\n") == {
        "x.iso": good, "y.img": good}
    assert uoc.parse_sha512sums(f"{'b' * 128}  d.qcow2\n{'b' * 64}  e\n") == {"d.qcow2": "b" * 128}
    assert uoc.parse_bsd_sums(f"SHA256 (r.qcow2) = {good}\nSHA256 (../x) = {good}\nMD5 (m) = {good}\n", "SHA256") == {
        "r.qcow2": good}


def test_version_key_sorts_numbers_as_numbers():
    builds = ["26.04.10", "26.04", "26.04.9", "26.04.1"]
    assert sorted(builds, key=uoc.version_key) == ["26.04", "26.04.1", "26.04.9", "26.04.10"]
    assert uoc.version_key("20260102.1") > uoc.version_key("20260102")


def test_only_https_from_the_vendors(monkeypatch):
    for url in ("http://releases.ubuntu.com/", "https://releases.ubuntu.com.evil.example/", "file:///etc/passwd"):
        with pytest.raises(uoc.CatalogError, match="refusing"):
            uoc.check_url(url)
    with pytest.raises(uoc.CatalogError, match="refusing"):
        uoc.fetch_https("https://example.com/")
    with pytest.raises(uoc.CatalogError, match="refusing"):
        uoc._VendorOnly().redirect_request(None, None, 302, "", {}, "https://example.com/SHA256SUMS")
    with pytest.raises(uoc.CatalogError, match="HTTPS only"):
        uoc._HttpsOnly().redirect_request(None, None, 302, "", {}, "http://mirror.example/x.qcow2")


class _Response:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size):
        return self.data[:size]


def test_fetch_retries_connections_but_not_http_errors(monkeypatch):
    calls = []

    def flaky(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.URLError("timed out")
        if request.full_url.endswith("/gone"):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        return _Response(b"x" * (uoc.MAX_INDEX_BYTES + 1) if request.full_url.endswith("/big") else b"ok")

    monkeypatch.setattr(uoc._OPENER, "open", flaky)
    monkeypatch.setattr(uoc.time, "sleep", lambda s: None)
    assert uoc.fetch_https("https://releases.ubuntu.com/26.04/SHA256SUMS") == b"ok"
    assert len(calls) == 2
    with pytest.raises(uoc.CatalogError, match="HTTP 404"):
        uoc.fetch_https("https://releases.ubuntu.com/gone")
    with pytest.raises(uoc.CatalogError, match="larger than an index"):
        uoc.fetch_https("https://releases.ubuntu.com/big")
    monkeypatch.setattr(uoc._OPENER, "open", lambda r, timeout: (_ for _ in ()).throw(TimeoutError("slow")))
    with pytest.raises(uoc.CatalogError, match="slow"):
        uoc.fetch_https("https://releases.ubuntu.com/slow", tries=2)


def test_url_exists(monkeypatch):
    monkeypatch.setattr(uoc._MIRROR_OPENER, "open", lambda r, timeout: _Response(b""))
    assert uoc.url_exists("https://cloud.debian.org/images/x.qcow2")

    def missing(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(uoc._MIRROR_OPENER, "open", missing)
    assert not uoc.url_exists("https://cloud.debian.org/images/x.qcow2")
