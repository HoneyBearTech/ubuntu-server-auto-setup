"""Unit tests for tools/update_k3s_catalog.py, offline: GitHub's and GHCR's answers are made up here. Run with
`make catalog-test` (pytest, with coverage)."""

import importlib.util
import json
import pathlib
import sys
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
          digests=None):
    """Upstream as it is: k3s' releases with a checksum file each, kube-vip's and the cloud provider's releases and
    digests."""
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
    return FakeHttp(answers)


def pinned():
    return {
        "k3s_catalog": [
            {"version": v, "binaries": {arch: {"url": ukc.binary_url(v, arch), "sha256": h} for arch, h in hs.items()}}
            for v, hs in (("v1.36.5+k3s1", {"amd64": H["a"], "arm64": H["b"]}),
                          ("v1.37.1+k3s1", {"amd64": H["c"], "arm64": H["d"]}))],
        "k3s_kube_vip_image": {"name": "ghcr.io/kube-vip/kube-vip", "tag": "v1.3.1", "digest": VIP_DIGEST},
        "k3s_kube_vip_cloud_provider_image": {"name": "ghcr.io/kube-vip/kube-vip-cloud-provider", "tag": "v0.0.13",
                                              "digest": CCM_DIGEST},
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
    upstream = ukc.Upstream(world())
    assert ukc.check(upstream, pinned()) == ([], [])


def test_a_changed_checksum_a_missing_binary_and_a_moved_tag_fail_the_check():
    sums = {("v1.36.5+k3s1", "amd64"): H["h"], ("v1.36.5+k3s1", "arm64"): H["b"],
            ("v1.37.1+k3s1", "amd64"): H["c"], ("v1.37.1+k3s1", "arm64"): H["d"]}
    http = world(sums=sums, digests={("kube-vip/kube-vip", "v1.3.1"): "sha256:" + "9" * 64,
                                     ("kube-vip/kube-vip-cloud-provider", "v0.0.13"): CCM_DIGEST})
    del http.answers[("HEAD", ukc.binary_url("v1.37.1+k3s1", "arm64"))]
    failures, newer = ukc.check(ukc.Upstream(http), pinned())
    assert any("v1.36.5+k3s1` amd64: upstream's checksum is now" in f for f in failures)
    assert any("v1.37.1+k3s1` arm64" in f and "can't be downloaded" in f for f in failures)
    assert any("GHCR now serves sha256:9999" in f for f in failures)
    assert newer == []


def test_newer_releases_are_listed():
    http = world(k3s_tags=("v1.36.5+k3s1", "v1.37.1+k3s1", "v1.37.2+k3s1", "v1.35.9+k3s1"),
                 vip_tags=("v1.3.1", "v1.4.0"),
                 digests={("kube-vip/kube-vip", "v1.3.1"): VIP_DIGEST,
                          ("kube-vip/kube-vip-cloud-provider", "v0.0.13"): CCM_DIGEST})
    failures, newer = ukc.check(ukc.Upstream(http), pinned())
    assert failures == []
    assert newer == ["k3s `v1.37.2+k3s1`", "`ghcr.io/kube-vip/kube-vip:v1.4.0`"]
    assert "Newer releases" in ukc.report(failures, newer)


def test_building_adds_the_newest_patch_of_the_two_newest_minor_lines_and_keeps_the_rest():
    http = world(k3s_tags=("v1.35.9+k3s1", "v1.36.5+k3s1", "v1.37.1+k3s1", "v1.37.2+k3s1", "garbage"))
    built = ukc.build(ukc.Upstream(http), pinned())
    assert [r["version"] for r in built["k3s_catalog"]] == ["v1.36.5+k3s1", "v1.37.1+k3s1", "v1.37.2+k3s1"]
    assert built["k3s_catalog"][2]["binaries"]["arm64"] == {"url": ukc.binary_url("v1.37.2+k3s1", "arm64"),
                                                            "sha256": H["f"]}
    assert built["k3s_kube_vip_image"]["digest"] == VIP_DIGEST


def test_a_checksum_file_without_the_binary_is_an_error():
    http = world()
    http.answers[sums_url("v1.36.5+k3s1", "amd64")] = (b"nothing here\n", {})
    with pytest.raises(ukc.CatalogError, match="no line for k3s"):
        ukc.Upstream(http).sha256("v1.36.5+k3s1", "amd64")


def test_answers_that_arent_what_github_and_ghcr_send_are_errors():
    http = world()
    http.answers["https://api.github.com/repos/k3s-io/k3s/releases?per_page=100"] = (b"<html>", {})
    with pytest.raises(ukc.CatalogError, match="isn't JSON"):
        ukc.Upstream(http).releases("k3s-io/k3s")
    http.answers["https://api.github.com/repos/k3s-io/k3s/releases?per_page=100"] = (b'{"a": 1}', {})
    with pytest.raises(ukc.CatalogError, match="isn't a list"):
        ukc.Upstream(http).releases("k3s-io/k3s")
    http.answers["https://ghcr.io/token?scope=repository:kube-vip/kube-vip:pull"] = (b"{}", {})
    with pytest.raises(ukc.CatalogError, match="no token"):
        ukc.Upstream(http).digest("ghcr.io/kube-vip/kube-vip", "v1.3.1")
    http.answers["https://ghcr.io/token?scope=repository:kube-vip/kube-vip:pull"] = (b'{"token": "t"}', {})
    http.answers[("HEAD", "https://ghcr.io/v2/kube-vip/kube-vip/manifests/v1.3.1")] = (b"", {})
    with pytest.raises(ukc.CatalogError, match="no digest"):
        ukc.Upstream(http).digest("ghcr.io/kube-vip/kube-vip", "v1.3.1")
    with pytest.raises(ukc.CatalogError, match="no release with a plain version tag"):
        ukc.newest_tag(["latest"])


def test_the_github_token_goes_to_the_api_only():
    http = world()
    upstream = ukc.Upstream(http, token="secret-token")  # noqa: S106 (a made-up token)
    ukc.check(upstream, pinned())
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
    assert ukc.main(["--check", "--catalog", str(catalog), "--report", str(report)], ukc.Upstream(world())) == 0
    assert report.read_text(encoding="utf-8") == ""
    assert "Every k3s pin checks out" in capsys.readouterr().out

    newer = world(k3s_tags=("v1.36.5+k3s1", "v1.37.1+k3s1", "v1.37.2+k3s1"))
    assert ukc.main(["--check", "--catalog", str(catalog), "--report", str(report)], ukc.Upstream(newer)) == 0
    assert "v1.37.2+k3s1" in report.read_text(encoding="utf-8")
    assert ukc.main(["--catalog", str(catalog)], ukc.Upstream(newer)) == 0
    assert "updated" in capsys.readouterr().out
    assert "v1.37.2+k3s1" in catalog.read_text(encoding="utf-8")

    broken = world(sums={("v1.36.5+k3s1", "amd64"): H["h"], ("v1.36.5+k3s1", "arm64"): H["b"],
                         ("v1.37.1+k3s1", "amd64"): H["c"], ("v1.37.1+k3s1", "arm64"): H["d"],
                         ("v1.37.2+k3s1", "amd64"): H["e"], ("v1.37.2+k3s1", "arm64"): H["f"]})
    assert ukc.main(["--check", "--catalog", str(catalog), "--report", str(report)], ukc.Upstream(broken)) == 1
    assert "no longer check out" in report.read_text(encoding="utf-8")

    catalog.write_text("not: a catalog\n", encoding="utf-8")
    assert ukc.main(["--check", "--catalog", str(catalog), "--report", str(report)], ukc.Upstream(world())) == 1
    assert "couldn't run" in report.read_text(encoding="utf-8")
