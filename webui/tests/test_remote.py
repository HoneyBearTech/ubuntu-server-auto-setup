"""The Proxmox connection test and the GitHub keys preview, against stand-in HTTP responses."""

import io

import pytest
import requests
from usas_web import remote
from usas_web.remote import RemoteProblem, keys_url, preview_keys
from usas_web.remote import test_proxmox as check_proxmox

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"


class Response:
    def __init__(self, status=200, data=None, body=b""):
        self.status_code = status
        self._data = data
        self.raw = io.BytesIO(body)
        self.raw.read = lambda n, decode_content=True, _r=self.raw.read: _r(n)

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)


def fake_get(responses, calls=None):
    def get(url, **kw):
        if calls is not None:
            calls.append((url, kw))
        result = responses[url.rsplit("/", 1)[-1]]
        if isinstance(result, Exception):
            raise result
        return result
    return get


def run(monkeypatch, responses, validate=True, ca=None, calls=None):
    monkeypatch.setattr(remote.requests, "get", fake_get(responses, calls))
    return check_proxmox("pve", 8006, "ansible@pve", "automation", "s3cret", validate, ca)


def test_proxmox_ok(monkeypatch, tmp_path):
    calls = []
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    result = run(monkeypatch, {"version": Response(data={"data": {"version": "9.1.1"}}),
                               "nodes": Response(data={"data": [{"node": "pve-2"}, {"node": "pve-1"}]})},
                 ca=ca, calls=calls)
    assert result.version == "9.1.1" and result.nodes == ["pve-1", "pve-2"]
    url, kw = calls[0]
    assert url == "https://pve:8006/api2/json/version"
    assert kw["headers"]["Authorization"] == "PVEAPIToken=ansible@pve!automation=s3cret"
    assert kw["verify"] == str(ca)
    run(monkeypatch, {"version": Response(data={"data": {}}), "nodes": Response(data={"data": []})},
        validate=False, calls=calls)
    assert calls[-1][1]["verify"] is False


@pytest.mark.parametrize("responses,message", [
    ({"version": Response(401)}, "refused the token"),
    ({"version": Response(500)}, "HTTP 500"),
    ({"version": requests.exceptions.SSLError()}, "couldn't be verified"),
    ({"version": requests.exceptions.ConnectionError()}, "Couldn't connect"),
    ({"version": requests.exceptions.Timeout()}, "didn't answer"),
    ({"version": Response(data={"data": {}}), "nodes": Response(data={"nope": 1})}, "doesn't look like"),
    ({"version": Response(data=None), "nodes": Response(data={"data": []})}, "doesn't look like"),
])
def test_proxmox_problems(monkeypatch, responses, message):
    with pytest.raises(RemoteProblem, match=message):
        run(monkeypatch, responses)


def test_keys_url():
    assert keys_url("https://github.com/{{ github_user }}.keys", "octocat") == "https://github.com/octocat.keys"


def test_preview_keys_filters_like_the_roles(monkeypatch):
    body = f"{KEY}\n\n  {KEY} with comment  \nnot-a-key AAAA\nssh-rsa !!!notbase64\n".encode()
    monkeypatch.setattr(remote.requests, "get", lambda url, **kw: Response(body=body))
    keys = preview_keys("https://github.com/octocat.keys")
    assert [k["comment"] for k in keys] == ["", "with comment"]
    assert keys[0]["fingerprint"].startswith("SHA256:") and keys[0]["type"] == "ssh-ed25519"


@pytest.mark.parametrize("response,message", [
    (Response(404), "No such GitHub user"),
    (Response(500), "Couldn't fetch"),
    (Response(body=b"x" * (remote.MAX_KEYS_BYTES + 10)), "too large"),
])
def test_preview_problems(monkeypatch, response, message):
    monkeypatch.setattr(remote.requests, "get", lambda url, **kw: response)
    with pytest.raises(RemoteProblem, match=message):
        preview_keys("https://github.com/x.keys")


def test_preview_needs_http():
    with pytest.raises(RemoteProblem, match="https"):
        preview_keys("file:///etc/passwd")
