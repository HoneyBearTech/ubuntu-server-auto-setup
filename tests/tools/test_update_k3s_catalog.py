"""Unit tests for tools/update_k3s_catalog.py, offline: GitHub's and GHCR's answers are made up here. Run with
`make catalog-test` (pytest, with coverage)."""

import importlib.util
import json
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
_spec = importlib.util.spec_from_file_location("update_k3s_catalog", ROOT / "tools" / "update_k3s_catalog.py")
ukc = importlib.util.module_from_spec(_spec)
sys.modules["update_k3s_catalog"] = ukc
_spec.loader.exec_module(ukc)

H = {v: f"{n:064x}" for n, v in enumerate(["a", "b", "c", "d", "e", "f", "g", "h"], 1)}
VIP_DIGEST = "sha256:" + "1" * 64
CCM_DIGEST = "sha256:" + "2" * 64
HELM = {"amd64": "3" * 64, "arm64": "4" * 64}
CHART = "5" * 64


def helm_asc(version, arch):
    return f"https://github.com/helm/helm/releases/download/{version}/helm-{version}-linux-{arch}.tar.gz.sha256sum.asc"


def prov(version, sha256, app="v3.5.6"):
    """A chart's provenance file, clear-signed (the fake verify below checks only its shape)."""
    return (f"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\napiVersion: v2\nappVersion: {app}\n"
            f"keywords:\n- - argocd\nname: argo-cd\nversion: {version}\n\n...\nfiles:\n"
            f"  argo-cd-{version}.tgz: sha256:{sha256}\n-----BEGIN PGP SIGNATURE-----\n\nAAAA\n"
            "-----END PGP SIGNATURE-----\n").encode()


def fake_verify(signature, key, what, data=None):
    """Stands in for gpgv: b"bad" is a bad signature; everything else is good."""
    if signature == b"bad":
        raise ukc.CatalogError(f"the signature on {what} is bad or isn't by a pinned key")
    assert key in ukc.KEYS.values()
    return ukc.clearsigned_text(signature) if data is None else data


def sums_url(version, arch):
    return f"https://github.com/k3s-io/k3s/releases/download/{version.replace('+', '%2B')}/sha256sum-{arch}.txt"


class FakeHttp:
    """Answers like GitHub and GHCR from a table: url -> (body, headers) or an exception."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def request(self, url, method="GET", headers=None, tries=3):
        self.calls.append((method, url, headers or {}))
        answer = self.answers.get((method, url), self.answers.get(url))
        if answer is None:
            raise ukc.CatalogError(f"{url}: HTTP 404")
        if isinstance(answer, Exception):
            raise answer
        return answer


def world(k3s_tags=("v1.36.5+k3s1", "v1.37.1+k3s1"), sums=None, vip_tags=("v1.3.1",), ccm_tags=("v0.0.13",),
          digests=None, helm_tags=("v4.3.1", "v3.22.1"), chart_tags=("argo-cd-10.11.6", "argo-workflows-1.2.3"),
          helm=None, charts=None):
    """Upstream as it is: k3s' releases with a checksum file each, kube-vip's and the cloud provider's releases and
    digests, Helm's releases with signed checksum files and the Argo CD chart's with signed provenance files."""
    sums = sums or {("v1.36.5+k3s1", "amd64"): H["a"], ("v1.36.5+k3s1", "arm64"): H["b"],
                    ("v1.37.1+k3s1", "amd64"): H["c"], ("v1.37.1+k3s1", "arm64"): H["d"],
                    ("v1.37.2+k3s1", "amd64"): H["e"], ("v1.37.2+k3s1", "arm64"): H["f"]}
    digests = digests or {("kube-vip/kube-vip", "v1.3.1"): VIP_DIGEST,
                          ("kube-vip/kube-vip-cloud-provider", "v0.0.13"): CCM_DIGEST}
    answers = {
        "https://api.github.com/repos/k3s-io/k3s/releases?per_page=100":
            (json.dumps([{"tag_name": t} for t in k3s_tags]
                        + [{"tag_name": "v1.38.0-rc1+k3s1", "prerelease": True}]).encode(), {}),
        "https://api.github.com/repos/kube-vip/kube-vip/releases?per_page=100":
            (json.dumps([{"tag_name": t} for t in vip_tags]).encode(), {}),
        "https://api.github.com/repos/kube-vip/kube-vip-cloud-provider/releases?per_page=100":
            (json.dumps([{"tag_name": t} for t in ccm_tags]).encode(), {}),
    }
    for (version, arch), digest in sums.items():
        name = ukc.FILES[arch]
        answers[sums_url(version, arch)] = (f"{'0' * 64}  k3s-airgap-images.tar\n{digest}  {name}\n".encode(), {})
        answers[("HEAD", ukc.binary_url(version, arch))] = (b"", {})
    for (repo, tag), digest in digests.items():
        answers[f"https://ghcr.io/token?scope=repository:{repo}:pull"] = (b'{"token": "t"}', {})
        answers[("HEAD", f"https://ghcr.io/v2/{repo}/manifests/{tag}")] = (b"", {"docker-content-digest": digest})
    answers["https://api.github.com/repos/helm/helm/releases?per_page=20"] = (
        json.dumps([{"tag_name": t} for t in helm_tags] + [{"tag_name": "v4.4.0-rc.1", "prerelease": True}]).encode(),
        {})
    answers["https://api.github.com/repos/argoproj/argo-helm/releases?per_page=100"] = (
        json.dumps([{"tag_name": t} for t in chart_tags]).encode(), {})
    for (version, arch), digest in (helm or {("v4.3.1", a): h for a, h in HELM.items()}).items():
        url = ukc.helm_url(version, arch)
        answers[f"{url}.sha256sum"] = (f"{digest}  helm-{version}-linux-{arch}.tar.gz\n".encode(), {})
        answers[helm_asc(version, arch)] = (b"signature", {})
        answers[("HEAD", url)] = (b"", {})
    for version, digest in (charts or {"10.11.6": CHART}).items():
        answers[f"{ukc.chart_url(version)}.prov"] = (prov(version, digest), {})
        answers[("HEAD", ukc.chart_url(version))] = (b"", {})
    return FakeHttp(answers)


def upstream(http, token=None):
    return ukc.Upstream(http, token=token, verify=fake_verify)


def pinned():
    return {
        "k3s_catalog": [
            {"version": v, "binaries": {arch: {"url": ukc.binary_url(v, arch), "sha256": h} for arch, h in hs.items()}}
            for v, hs in (("v1.36.5+k3s1", {"amd64": H["a"], "arm64": H["b"]}),
                          ("v1.37.1+k3s1", {"amd64": H["c"], "arm64": H["d"]}))],
        "k3s_kube_vip_image": {"name": "ghcr.io/kube-vip/kube-vip", "tag": "v1.3.1", "digest": VIP_DIGEST},
        "k3s_kube_vip_cloud_provider_image": {"name": "ghcr.io/kube-vip/kube-vip-cloud-provider", "tag": "v0.0.13",
                                              "digest": CCM_DIGEST},
        "k3s_helm": {"version": "v4.3.1", "binaries": {arch: {"url": ukc.helm_url("v4.3.1", arch), "sha256": h}
                                                      for arch, h in HELM.items()}},
        "k3s_argocd_chart": {"version": "10.11.6", "app_version": "v3.5.6", "url": ukc.chart_url("10.11.6"),
                             "sha256": CHART},
    }


def test_the_rendered_catalog_is_yaml_the_role_reads_and_reads_back():
    text = ukc.render(pinned())
    assert ukc.read_catalog(text) == pinned()
    assert yaml.safe_load(text)["k3s_catalog"][0]["binaries"]["amd64"]["sha256"] == H["a"]


def test_the_committed_catalog_is_as_the_script_writes_it():
    text = ukc.CATALOG.read_text(encoding="utf-8")
    assert ukc.render(ukc.read_catalog(text)) == text


@pytest.mark.parametrize(("change", "message"), [
    (lambda c: c["k3s_catalog"][0].update(version="1.36.5"), "isn't a k3s release"),
    (lambda c: c["k3s_catalog"].append(c["k3s_catalog"][0]), "twice"),
    (lambda c: c["k3s_catalog"][0]["binaries"].pop("arm64"), "needs a binary for each"),
    (lambda c: c["k3s_catalog"][0]["binaries"]["amd64"].update(url="https://example.com/k3s"), "the URL must be"),
    (lambda c: c["k3s_catalog"][0]["binaries"]["amd64"].update(sha256="ABC"), "64 hex digits"),
    (lambda c: c["k3s_kube_vip_image"].update(name="docker.io/other/kube-vip"), "must name"),
    (lambda c: c["k3s_kube_vip_image"].update(digest="latest"), "must name"),
    (lambda c: c.update(k3s_catalog=[]), "no k3s release"),
    (lambda c: c["k3s_helm"].update(version="4.3.1"), "k3s_helm needs a version"),
    (lambda c: c["k3s_helm"]["binaries"].pop("arm64"), "k3s_helm needs a version"),
    (lambda c: c["k3s_helm"]["binaries"]["amd64"].update(url="https://example.com/helm.tgz"), "k3s_helm amd64"),
    (lambda c: c["k3s_argocd_chart"].update(version="v10.11.6"), "isn't a chart version"),
    (lambda c: c["k3s_argocd_chart"].update(url="https://example.com/argo-cd.tgz"), "k3s_argocd_chart"),
    (lambda c: c["k3s_argocd_chart"].update(app_version="latest"), "k3s_argocd_chart"),
])
def test_a_pin_that_isnt_plain_is_refused(change, message):
    catalog = pinned()
    change(catalog)
    with pytest.raises(ukc.CatalogError, match=message):
        ukc.validate(catalog)


@pytest.mark.parametrize("text", [
    "k3s_catalog:\n  - version: v1.36.5+k3s1\n    binaries:\n      amd64:\n        url: x y\n",
    "other: 1\n",
    "k3s_catalog:\n  - version: v1.36.5+k3s1\n    extra: 1\n",
])
def test_a_catalog_not_written_by_the_script_is_refused(text):
    with pytest.raises(ukc.CatalogError):
        ukc.read_catalog(text)


def test_every_pin_checks_out_against_upstream():
    assert ukc.check(upstream(world()), pinned()) == ([], [])


def test_a_changed_checksum_a_missing_binary_and_a_moved_tag_fail_the_check():
    sums = {("v1.36.5+k3s1", "amd64"): H["h"], ("v1.36.5+k3s1", "arm64"): H["b"],
            ("v1.37.1+k3s1", "amd64"): H["c"], ("v1.37.1+k3s1", "arm64"): H["d"]}
    http = world(sums=sums, digests={("kube-vip/kube-vip", "v1.3.1"): "sha256:" + "9" * 64,
                                     ("kube-vip/kube-vip-cloud-provider", "v0.0.13"): CCM_DIGEST})
    del http.answers[("HEAD", ukc.binary_url("v1.37.1+k3s1", "arm64"))]
    failures, newer = ukc.check(upstream(http), pinned())
    assert any("v1.36.5+k3s1` amd64: upstream's checksum is now" in f for f in failures)
    assert any("v1.37.1+k3s1` arm64" in f and "can't be downloaded" in f for f in failures)
    assert any("GHCR now serves sha256:9999" in f for f in failures)
    assert newer == []


def test_newer_releases_are_listed():
    http = world(k3s_tags=("v1.36.5+k3s1", "v1.37.1+k3s1", "v1.37.2+k3s1", "v1.35.9+k3s1"),
                 vip_tags=("v1.3.1", "v1.4.0"),
                 digests={("kube-vip/kube-vip", "v1.3.1"): VIP_DIGEST,
                          ("kube-vip/kube-vip-cloud-provider", "v0.0.13"): CCM_DIGEST})
    failures, newer = ukc.check(upstream(http), pinned())
    assert failures == []
    assert newer == ["k3s `v1.37.2+k3s1`", "`ghcr.io/kube-vip/kube-vip:v1.4.0`"]
    assert "Newer releases" in ukc.report(failures, newer)


def test_building_adds_the_newest_patch_of_the_two_newest_minor_lines_and_keeps_the_rest():
    http = world(k3s_tags=("v1.35.9+k3s1", "v1.36.5+k3s1", "v1.37.1+k3s1", "v1.37.2+k3s1", "garbage"))
    built = ukc.build(upstream(http), pinned())
    assert [r["version"] for r in built["k3s_catalog"]] == ["v1.36.5+k3s1", "v1.37.1+k3s1", "v1.37.2+k3s1"]
    assert built["k3s_catalog"][2]["binaries"]["arm64"] == {"url": ukc.binary_url("v1.37.2+k3s1", "arm64"),
                                                            "sha256": H["f"]}
    assert built["k3s_kube_vip_image"]["digest"] == VIP_DIGEST


def test_a_checksum_file_without_the_binary_is_an_error():
    http = world()
    http.answers[sums_url("v1.36.5+k3s1", "amd64")] = (b"nothing here\n", {})
    with pytest.raises(ukc.CatalogError, match="no line for k3s"):
        upstream(http).sha256("v1.36.5+k3s1", "amd64")


def test_answers_that_arent_what_github_and_ghcr_send_are_errors():
    http = world()
    http.answers["https://api.github.com/repos/k3s-io/k3s/releases?per_page=100"] = (b"<html>", {})
    with pytest.raises(ukc.CatalogError, match="isn't JSON"):
        upstream(http).releases("k3s-io/k3s")
    http.answers["https://api.github.com/repos/k3s-io/k3s/releases?per_page=100"] = (b'{"a": 1}', {})
    with pytest.raises(ukc.CatalogError, match="isn't a list"):
        upstream(http).releases("k3s-io/k3s")
    http.answers["https://ghcr.io/token?scope=repository:kube-vip/kube-vip:pull"] = (b"{}", {})
    with pytest.raises(ukc.CatalogError, match="no token"):
        upstream(http).digest("ghcr.io/kube-vip/kube-vip", "v1.3.1")
    http.answers["https://ghcr.io/token?scope=repository:kube-vip/kube-vip:pull"] = (b'{"token": "t"}', {})
    http.answers[("HEAD", "https://ghcr.io/v2/kube-vip/kube-vip/manifests/v1.3.1")] = (b"", {})
    with pytest.raises(ukc.CatalogError, match="no digest"):
        upstream(http).digest("ghcr.io/kube-vip/kube-vip", "v1.3.1")
    with pytest.raises(ukc.CatalogError, match="no release with a plain version tag"):
        ukc.newest_tag(["latest"])


def test_the_github_token_goes_to_the_api_only():
    http = world()
    ukc.check(upstream(http, token="secret-token"), pinned())  # noqa: S106 (a made-up token)
    for _method, url, headers in http.calls:
        assert ("Authorization" in headers and headers["Authorization"] == "Bearer secret-token") == \
            url.startswith("https://api.github.com/"), url


def test_only_https_from_github_and_ghcr_is_fetched():
    for url in ("http://github.com/x", "https://example.com/x", "https://github.com.example.com/x"):
        with pytest.raises(ukc.CatalogError, match="refusing"):
            ukc.check_url(url)
    ukc.check_url("https://release-assets.githubusercontent.com/x")


def test_http_errors_are_final_and_failed_connections_are_retried(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            if "missing" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
            raise urllib.error.URLError("refused")

    monkeypatch.setattr(ukc, "_OPENER", Opener())
    monkeypatch.setattr(ukc.time, "sleep", lambda _s: None)
    with pytest.raises(ukc.CatalogError, match="HTTP 404"):
        ukc.Http().request("https://github.com/missing")
    assert len(calls) == 1
    with pytest.raises(ukc.CatalogError, match="refused"):
        ukc.Http().request("https://github.com/down")
    assert len(calls) == 4
    assert ukc.Upstream(ukc.Http()).downloadable("https://github.com/missing") is False


def test_main_checks_writes_a_report_and_updates(tmp_path, capsys):
    catalog = tmp_path / "catalog.yml"
    catalog.write_text(ukc.render(pinned()), encoding="utf-8")
    report = tmp_path / "report.md"
    assert ukc.main(["--check", "--catalog", str(catalog), "--report", str(report)], upstream(world())) == 0
    assert report.read_text(encoding="utf-8") == ""
    assert "Every k3s pin checks out" in capsys.readouterr().out

    newer = world(k3s_tags=("v1.36.5+k3s1", "v1.37.1+k3s1", "v1.37.2+k3s1"))
    assert ukc.main(["--check", "--catalog", str(catalog), "--report", str(report)], upstream(newer)) == 0
    assert "v1.37.2+k3s1" in report.read_text(encoding="utf-8")
    assert ukc.main(["--catalog", str(catalog)], upstream(newer)) == 0
    assert "updated" in capsys.readouterr().out
    assert "v1.37.2+k3s1" in catalog.read_text(encoding="utf-8")

    broken = world(sums={("v1.36.5+k3s1", "amd64"): H["h"], ("v1.36.5+k3s1", "arm64"): H["b"],
                         ("v1.37.1+k3s1", "amd64"): H["c"], ("v1.37.1+k3s1", "arm64"): H["d"],
                         ("v1.37.2+k3s1", "amd64"): H["e"], ("v1.37.2+k3s1", "arm64"): H["f"]})
    assert ukc.main(["--check", "--catalog", str(catalog), "--report", str(report)], upstream(broken)) == 1
    assert "no longer check out" in report.read_text(encoding="utf-8")

    catalog.write_text("not: a catalog\n", encoding="utf-8")
    assert ukc.main(["--check", "--catalog", str(catalog), "--report", str(report)], upstream(world())) == 1
    assert "couldn't run" in report.read_text(encoding="utf-8")


def test_newer_helm_and_chart_releases_are_listed_and_built():
    http = world(helm_tags=("v4.3.1", "v4.4.0", "v3.22.2"), chart_tags=("argo-cd-10.11.6", "argo-cd-10.12.0"),
                 helm={**{("v4.3.1", a): h for a, h in HELM.items()}, ("v4.4.0", "amd64"): H["a"],
                       ("v4.4.0", "arm64"): H["b"]},
                 charts={"10.11.6": CHART, "10.12.0": H["c"]})
    failures, newer = ukc.check(upstream(http), pinned())
    assert failures == []
    assert newer == ["Helm `v4.4.0`", "Argo CD chart `10.12.0`"]
    built = ukc.build(upstream(http), pinned())
    assert built["k3s_helm"]["binaries"]["arm64"] == {"url": ukc.helm_url("v4.4.0", "arm64"), "sha256": H["b"]}
    assert built["k3s_argocd_chart"] == {"version": "10.12.0", "app_version": "v3.5.6",
                                         "url": ukc.chart_url("10.12.0"), "sha256": H["c"]}


def test_a_changed_or_unsigned_helm_checksum_and_a_changed_chart_fail_the_check():
    http = world(helm={("v4.3.1", "amd64"): H["h"], ("v4.3.1", "arm64"): HELM["arm64"]}, charts={"10.11.6": H["g"]})
    http.answers[helm_asc("v4.3.1", "arm64")] = (b"bad", {})
    failures, _ = ukc.check(upstream(http), pinned())
    assert any("Helm `v4.3.1` amd64: the signed checksum is now" in f for f in failures)
    assert any("Helm `v4.3.1` arm64" in f and "bad or isn't by a pinned key" in f for f in failures)
    assert any("Argo CD chart `10.11.6`: the signed provenance now says" in f for f in failures)

    http = world()
    del http.answers[("HEAD", ukc.helm_url("v4.3.1", "amd64"))]
    del http.answers[("HEAD", ukc.chart_url("10.11.6"))]
    http.answers["https://api.github.com/repos/argoproj/argo-helm/releases?per_page=100"] = (b"[]", {})
    failures, _ = ukc.check(upstream(http), pinned())
    assert any("Helm `v4.3.1` amd64" in f and "can't be downloaded" in f for f in failures)
    assert any("Argo CD chart `10.11.6`" in f and "can't be downloaded" in f for f in failures)
    assert any("Argo CD chart's releases: no release of the argo-cd chart" in f for f in failures)


@pytest.mark.parametrize(("answer", "message"), [
    (b"bad", "bad or isn't by a pinned key"),
    (prov("10.11.6", CHART).replace(b"version: 10.11.6\n", b"version: 10.11.5\n"), "doesn't give the chart's"),
    (prov("10.11.6", "XYZ"), "doesn't give the chart's"),
    (b"apiVersion: v2\n" + prov("10.11.6", CHART), "isn't one clear-signed message"),
    (prov("10.11.6", CHART) + b"files:\n  argo-cd-10.11.6.tgz: sha256:" + H["a"].encode() + b"\n",
     "isn't one clear-signed message"),
])
def test_a_provenance_file_that_isnt_signed_or_doesnt_say_is_an_error(answer, message):
    http = world()
    http.answers[f"{ukc.chart_url('10.11.6')}.prov"] = (answer, {})
    with pytest.raises(ukc.CatalogError, match=message):
        upstream(http).chart("10.11.6")


def test_a_helm_checksum_file_for_another_file_is_an_error():
    http = world()
    sums = f"{H['a']}  helm-v4.3.0-linux-amd64.tar.gz".encode()
    http.answers[f"{ukc.helm_url('v4.3.1', 'amd64')}.sha256sum"] = (sums, {})
    with pytest.raises(ukc.CatalogError, match="isn't a checksum of helm-v4.3.1-linux-amd64.tar.gz"):
        upstream(http).helm_sha256("v4.3.1", "amd64")


def test_the_pinned_key_files_hold_the_pinned_keys():
    for key in ukc.KEYS.values():
        assert ukc.dearmor(key.path.read_bytes())[:1] in (b"\x99", b"\xc6")
    with pytest.raises(ukc.CatalogError, match="no ASCII-armored public key block"):
        ukc.dearmor(b"not a key\n")


@pytest.mark.skipif(not shutil.which("gpg") or not shutil.which("gpgv"), reason="needs gpg and gpgv")
def test_gpgv_accepts_only_a_good_signature_by_a_pinned_key(tmp_path):
    def signer(name):
        home = tempfile.mkdtemp(prefix="g", dir="/tmp")
        os.chmod(home, 0o700)

        def gpg(*args, data=None):
            return subprocess.run(["gpg", "--homedir", home, "--batch", "--quiet", "--passphrase", "", *args],
                                  input=data, capture_output=True, check=True).stdout
        gpg("--quick-gen-key", f"{name} <{name}@invalid>", "ed25519", "sign", "never")
        fingerprint = next(line.split(b":")[9].decode() for line in gpg("--with-colons", "--list-keys").splitlines()
                           if line.startswith(b"fpr"))
        (tmp_path / f"{name}.asc").write_bytes(gpg("--armor", "--export"))
        return home, gpg, ukc.Key(tmp_path / f"{name}.asc", frozenset({fingerprint}))

    homes = []
    try:
        home, gpg, key = signer("helm")
        homes.append(home)
        other_home, other, stranger = signer("stranger")
        homes.append(other_home)
        data = b"0123  helm-v4.3.1-linux-amd64.tar.gz\n"
        detached = gpg("--detach-sign", "--armor", "--output", "-", data=data)
        assert ukc.verify_signature(detached, key, "sums", data=data) == data
        with pytest.raises(ukc.CatalogError, match="bad or isn't by a pinned key"):
            ukc.verify_signature(detached, key, "sums", data=data + b"x")
        with pytest.raises(ukc.CatalogError, match="bad or isn't by a pinned key"):
            ukc.verify_signature(detached, stranger, "sums", data=data)
        text = b"version: 10.11.6\n- dashed\nfiles:\n  argo-cd-10.11.6.tgz: sha256:" + CHART.encode() + b"\n"
        clear = gpg("--clearsign", "--output", "-", data=text)
        assert ukc.verify_signature(clear, key, "prov") == text
        with pytest.raises(ukc.CatalogError, match="bad or isn't by a pinned key"):
            ukc.verify_signature(clear.replace(b"10.11.6", b"10.11.7", 1), key, "prov")
        with pytest.raises(ukc.CatalogError, match="bad or isn't by a pinned key"):
            ukc.verify_signature(other("--clearsign", "--output", "-", data=text), key, "prov")
    finally:
        for home in homes:
            subprocess.run(["gpgconf", "--homedir", home, "--kill", "all"], check=False, capture_output=True)
            shutil.rmtree(home, ignore_errors=True)


def test_gpgv_output_that_isnt_utf8_is_read(tmp_path):
    fingerprint = "2B8F22F57260EFA67BE1C5824B11F800CD9D2252"
    gpgv = tmp_path / "gpgv"
    gpgv.write_bytes(b"#!/bin/sh\nprintf 'gpgv: Good signature from \"\\305ukasz\"\\n' >&2\n"
                     b"printf '[GNUPG:] GOODSIG X \\305ukasz\\n[GNUPG:] VALIDSIG " + ("A" * 40).encode()
                     + b" 2026-11-01 0 4 0 1 10 01 " + fingerprint.encode() + b"\\n'\n")
    gpgv.chmod(0o755)
    data = b"0123  helm-v4.3.1-linux-amd64.tar.gz\n"
    assert ukc.verify_signature(b"sig", ukc.KEYS["argo-helm"], "sums", data=data, gpgv=str(gpgv)) == data
    with pytest.raises(ukc.CatalogError, match="isn't by a pinned key"):
        ukc.verify_signature(b"sig", ukc.KEYS["helm"], "sums", data=data, gpgv=str(gpgv))
