"""The Proxmox connection test and the GitHub keys preview, against stand-in HTTP responses."""

import io
import threading
import time

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


def test_proxmox_node_status_and_cluster_name(monkeypatch):
    nodes = Response(data={"data": [{"node": "pve-2", "status": "offline"}, {"node": "pve-1", "status": "online"}]})
    cluster = Response(data={"data": [{"type": "cluster", "name": "homelab"}, {"type": "node", "name": "pve-1"}]})
    result = run(monkeypatch, {"version": Response(data={"data": {"version": "9.1.1"}}), "nodes": nodes,
                               "status": cluster})
    assert result.nodes == ["pve-1", "pve-2"]
    assert result.status == {"pve-1": "online", "pve-2": "offline"}
    assert result.cluster == "homelab"


@pytest.mark.parametrize("status", [Response(status=403), Response(data={"data": [{"type": "node"}]}),
                                    Response(data=None), requests.exceptions.Timeout()])
def test_proxmox_cluster_name_is_optional(monkeypatch, status):
    # A single node has no cluster entry, and a token without Sys.Audit can't read /cluster/status at all.
    result = run(monkeypatch, {"version": Response(data={"data": {"version": "9.1.1"}}),
                               "nodes": Response(data={"data": [{"node": "pve"}]}), "status": status})
    assert result.nodes == ["pve"] and result.status == {"pve": "unknown"} and result.cluster == ""


def test_proxmox_iso_storages(monkeypatch):
    storages = Response(data={"data": [
        {"storage": "local", "type": "dir", "content": "iso,vztmpl,backup"},
        {"storage": "synology-nfs", "type": "nfs", "content": "images,iso", "shared": 1},
        {"storage": "local-lvm", "type": "lvmthin", "content": "images,rootdir"},
        {"storage": "isolated", "type": "dir", "content": "backup"},  # "iso" only as a whole entry counts
    ]})
    result = run(monkeypatch, {"version": Response(data={"data": {"version": "9.1.1"}}),
                               "nodes": Response(data={"data": [{"node": "pve"}]}), "storage": storages})
    assert result.iso_storages == {"local": "dir", "synology-nfs": "nfs, shared"}


def test_proxmox_iso_storages_are_optional(monkeypatch):
    # Without Datastore.Audit the token can't list storages; the field stays free text.
    result = run(monkeypatch, {"version": Response(data={"data": {"version": "9.1.1"}}),
                               "nodes": Response(data={"data": [{"node": "pve"}]}), "storage": Response(status=403)})
    assert result.iso_storages == {}


# --- The VMs page: the API client and the lister ---------------------------------------------------------

API = remote.PveApi("pve", 8006, "ansible@pve", "automation", "s3cret", verify="/ca.pem", timeout=7)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def pve(routes, calls=None, lock=None):
    """A fake requests.get for the Proxmox API: routes maps a path (after /api2/json) to data, a Response,
    an exception, or a function of the call's keyword arguments."""
    def get(url, **kw):
        path = url.split("/api2/json", 1)[1]
        if calls is not None:
            calls.append((path, kw))
        result = routes.get(path, Response(500, {"data": None, "message": f"no route {path}"}))
        if callable(result) and not isinstance(result, Response):
            result = result(**kw)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, Response) else Response(data={"data": result})
    return get


def test_pve_api_get(monkeypatch):
    calls = []
    monkeypatch.setattr(remote.requests, "get", pve({"/version": {"version": "9.1.1"}}, calls))
    assert API.get("/version") == {"version": "9.1.1"}
    path, kw = calls[0]
    assert kw["headers"]["Authorization"] == "PVEAPIToken=ansible@pve!automation=s3cret"
    assert kw["verify"] == "/ca.pem" and kw["timeout"] == 7
    API.get("/version", timeout=5)
    assert calls[-1][1]["timeout"] == 5
    assert "s3cret" not in repr(API) and "s3cret" not in API.identity


@pytest.mark.parametrize("result,message,status", [
    (Response(401), "refused the token", 401),
    (Response(403, {"data": None, "message": "Permission check failed\n"}), "Permission check failed", 403),
    (Response(500), "HTTP 500", 500),
    (Response(500, {"data": None, "message": "QEMU guest agent is not running\n"}), "agent is not running", 500),
    (requests.exceptions.SSLError(), "couldn't be verified", 0),
    (requests.exceptions.ConnectTimeout(), "didn't answer within 7 seconds", 0),
    (requests.exceptions.ConnectionError(), "Couldn't connect to pve:8006", 0),
    (Response(data={"nope": 1}), "doesn't look like", 0),
])
def test_pve_api_problems(monkeypatch, result, message, status):
    monkeypatch.setattr(remote.requests, "get", pve({"/x": result}))
    with pytest.raises(remote.PveError, match=message) as caught:
        API.get("/x")
    assert caught.value.status == status


def iface(name, *addresses):
    return {"name": name, "ip-addresses": [{"ip-address": a, "ip-address-type": "ipv4"} for a in addresses]}


def agent(result):
    return {"result": result}


NOT_RUNNING = Response(500, {"data": None, "message": "QEMU guest agent is not running\n"})


def cluster(**extra):
    resources = [
        {"vmid": 101, "name": "web", "node": "n1", "type": "qemu", "status": "running", "maxcpu": 2},
        {"vmid": 102, "name": "db", "node": "n1", "type": "qemu", "status": "running"},
        {"vmid": 103, "name": "old", "node": "n1", "type": "qemu", "status": "stopped"},
        {"vmid": 104, "name": "tpl", "node": "n1", "type": "qemu", "status": "stopped", "template": 1},
        {"vmid": 200, "name": "ct", "node": "n1", "type": "lxc", "status": "running"},
        {"vmid": 105, "name": "plain", "node": "n1", "type": "qemu", "status": "running", "tags": "ubuntu;ansible"},
    ]
    routes = {
        "/version": {"version": "9.1.1"},
        "/cluster/resources?type=vm": resources,
        "/nodes/n1/qemu/101/config": {"agent": "1", "ostype": "l26",
                                      "description": "Installed by ubuntu-server-auto-setup (Ubuntu 26.04.1)"},
        "/nodes/n1/qemu/101/agent/network-get-interfaces": agent([iface("lo", "127.0.0.1"),
                                                                   iface("eth1", "192.168.5.1"),
                                                                   iface("eth0", "10.0.0.11")]),
        "/nodes/n1/qemu/101/agent/get-osinfo": agent({"id": "ubuntu", "version-id": "26.04",
                                                      "pretty-name": "Ubuntu 26.04 LTS"}),
        "/nodes/n1/qemu/101/agent/get-host-name": agent({"host-name": "web"}),
        "/nodes/n1/qemu/102/config": {"agent": "1"},
        "/nodes/n1/qemu/102/agent/network-get-interfaces": NOT_RUNNING,
        "/nodes/n1/qemu/103/config": {"agent": "1", "ipconfig0": "ip=10.0.0.13/24"},
        "/nodes/n1/qemu/105/config": {"ostype": "l26", "ipconfig0": "ip=10.0.0.15/24"},
    }
    routes.update(extra)
    return routes


def by_id(data):
    return {row["vmid"]: row for row in data["vms"]}


def test_lister_lists_and_classifies(monkeypatch):
    calls = []
    monkeypatch.setattr(remote.requests, "get", pve(cluster(), calls))
    data = remote.VmLister().list(API, {103: {"name": "old-01", "ansible_host": "10.0.0.33"}},
                                  remote.v.subnet("10.0.0.1", 24))
    assert data["version"] == "9.1.1" and data["complete"] and data["forbidden"] == 0
    rows = by_id(data)
    assert list(rows) == [101, 102, 103, 104, 105, 200]
    web = rows[101]
    assert (web["agent"], web["ip"], web["ip_source"], web["addresses"]) == \
        ("ok", "10.0.0.11", "agent", ["10.0.0.11", "192.168.5.1"])
    assert (web["os"], web["hostname"], web["support"], web["ours"], web["name_ok"]) == \
        ("Ubuntu 26.04 LTS", "web", "supported", True, True)
    assert (rows[102]["agent"], rows[102]["ip"], rows[102]["support"]) == ("not-running", "", "unknown")
    # A stopped VM: no agent calls; the inventory's address wins over cloud-init's.
    assert (rows[103]["agent"], rows[103]["ip"], rows[103]["ip_source"], rows[103]["inventory_name"]) == \
        ("stopped", "10.0.0.33", "inventory", "old-01")
    assert (rows[104]["agent"], rows[104]["support"]) == ("none", "unsupported")
    assert (rows[105]["agent"], rows[105]["ip"], rows[105]["ip_source"], rows[105]["ours"]) == \
        ("off", "10.0.0.15", "ipconfig0", True)
    assert (rows[200]["agent"], rows[200]["support"]) == ("none", "unsupported")
    paths = [p for p, _ in calls]
    assert not any("/104/" in p or "/200/" in p or "/103/agent" in p or "/105/agent" in p for p in paths)
    assert all(kw["timeout"] == remote.VmLister.AGENT_TIMEOUT for p, kw in calls if "/agent/" in p)


def test_lister_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(remote.requests, "get", pve(cluster(), calls))
    clock = Clock()
    lister = remote.VmLister(clock)
    lister.list(API, {})
    first = len(calls)
    lister.list(API, {})
    assert len(calls) == first  # everything is cached

    def count(fragment):
        return sum(1 for p, _ in calls[first:] if fragment in p)

    clock.now += 11  # the cluster's resources, not the configs or the agents
    lister.list(API, {})
    assert count("/cluster/resources") == 1 and count("/config") == 0 and count("/agent/") == 0
    clock.now += 50  # 61 s: the addresses again; the agent that wasn't running is still remembered
    lister.list(API, {})
    assert count("network-get-interfaces") == 1 and count("/102/agent") == 0 and count("get-osinfo") == 0
    clock.now += 60  # 121 s: the agent that wasn't running is asked again
    lister.list(API, {})
    assert count("/102/agent") == 1
    clock.now += 600  # the configs and OS info
    lister.list(API, {})
    assert count("/101/config") == 1 and count("get-osinfo") == 1 and count("/version") == 1
    # Another token or host: the caches start over.
    before = len(calls)
    lister.list(remote.PveApi("pve", 8006, "ansible@pve", "other", "s3cret2"), {})
    assert any(p == "/cluster/resources?type=vm" for p, _ in calls[before:])


def test_lister_states(monkeypatch):
    forbidden = Response(403, {"data": None, "message": "Permission check failed (/vms/101, VM.GuestAgent.Audit)"})
    routes = cluster(**{"/nodes/n1/qemu/101/agent/network-get-interfaces": forbidden,
                        "/nodes/n1/qemu/102/agent/network-get-interfaces": requests.exceptions.ReadTimeout(),
                        "/nodes/n1/qemu/105/config": Response(500)})
    monkeypatch.setattr(remote.requests, "get", pve(routes))
    data = remote.VmLister().list(API, {})
    rows = by_id(data)
    assert (rows[101]["agent"], rows[102]["agent"], rows[105]["agent"]) == ("forbidden", "timeout", "unknown")
    assert data["forbidden"] == 1


def test_lister_keeps_the_last_config_when_it_cant_be_read(monkeypatch):
    clock = Clock()
    lister = remote.VmLister(clock)
    monkeypatch.setattr(remote.requests, "get", pve(cluster()))
    lister.list(API, {})
    monkeypatch.setattr(remote.requests, "get", pve(cluster(**{"/nodes/n1/qemu/105/config": Response(500)})))
    clock.now += 301
    assert by_id(lister.list(API, {}))[105]["ip"] == "10.0.0.15"


def test_lister_without_the_cluster(monkeypatch):
    monkeypatch.setattr(remote.requests, "get", pve(cluster(**{"/cluster/resources?type=vm": Response(401),
                                                               "/version": Response(401)})))
    with pytest.raises(remote.PveError, match="refused the token"):
        remote.VmLister().list(API, {})


def test_lister_waits_only_so_long_and_fills_in_later(monkeypatch):
    release = threading.Event()
    running, most = [0], [0]
    lock = threading.Lock()

    def slow(**kw):
        with lock:
            running[0] += 1
            most[0] = max(most[0], running[0])
        release.wait(5)
        with lock:
            running[0] -= 1
        return Response(data={"data": {"result": [iface("eth0", "10.0.0.50")]}})

    resources = [{"vmid": 300 + i, "name": f"vm{i}", "node": "n1", "type": "qemu", "status": "running"}
                 for i in range(8)]
    routes = {"/version": {"version": "8.4.1"}, "/cluster/resources?type=vm": resources}
    for i in range(8):
        routes[f"/nodes/n1/qemu/{300 + i}/config"] = {"agent": "1"}
        routes[f"/nodes/n1/qemu/{300 + i}/agent/network-get-interfaces"] = slow
        routes[f"/nodes/n1/qemu/{300 + i}/agent/get-osinfo"] = {"result": {"id": "ubuntu", "version-id": "24.04"}}
        routes[f"/nodes/n1/qemu/{300 + i}/agent/get-host-name"] = {"result": {"host-name": f"vm{i}"}}
    monkeypatch.setattr(remote.requests, "get", pve(routes))
    lister = remote.VmLister()
    data = lister.list(API, {}, budget=0.3)
    assert not data["complete"] and {r["agent"] for r in data["vms"]} == {"pending"}
    data = lister.list(API, {}, budget=0.1)  # a second listing doesn't ask again for what is on its way
    assert most[0] == remote.VmLister.WORKERS  # never more than 4 at once
    release.set()
    deadline = time.time() + 5
    while time.time() < deadline and not data["complete"]:
        data = lister.list(API, {}, budget=1)
    assert data["complete"] and {r["ip"] for r in data["vms"]} == {"10.0.0.50"}
    assert {r["support"] for r in data["vms"]} == {"supported"}
