"""Checks against outside systems: the Proxmox API (test connection, the VMs page) and the GitHub keys
(preview)."""

import hashlib
import ipaddress
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from . import vms as v
from .problems import Problem
from .sshkeys import describe_public_key

TIMEOUT = 10
MAX_KEYS_BYTES = 256 * 1024


class RemoteProblem(Problem, RuntimeError):
    """A check failed; the message is shown to the user."""


@dataclass
class PveResult:
    version: str
    nodes: list[str]  # node names, sorted
    status: dict[str, str] = field(default_factory=dict)  # node name -> "online", "offline", ...
    cluster: str = ""  # the cluster's name; empty for a single node, or if the token can't read it
    iso_storages: dict[str, str] = field(default_factory=dict)  # storage ID -> type ("nfs, shared"), ISO content
    import_storages: list[str] | None = None  # those of them that allow Import content too; None if not listed


def api_url(host: str, port: int) -> str:
    return f"https://{host}:{port}/api2/json"


def tls_verify(validate: bool, ca_bundle: Path | None) -> bool | str:
    """What requests verifies the API's certificate with: the bundle with the cluster's CA if there is one,
    as the playbooks do."""
    return (str(ca_bundle) if ca_bundle and ca_bundle.exists() else True) if validate else False


def test_proxmox(host: str, port: int, user: str, token_id: str, secret: str, validate: bool,
                 ca_bundle: Path | None) -> PveResult:
    """Calls /version and /nodes with the token, verifying TLS as the playbooks will."""
    verify = tls_verify(validate, ca_bundle)
    headers = {"Authorization": f"PVEAPIToken={user}!{token_id}={secret}"}
    base = api_url(host, port)
    try:
        version = requests.get(f"{base}/version", headers=headers, verify=verify, timeout=TIMEOUT)
        if version.status_code == 401:
            raise RemoteProblem("Proxmox refused the token (401). Check the user, the token name and the secret.")
        version.raise_for_status()
        nodes = requests.get(f"{base}/nodes", headers=headers, verify=verify, timeout=TIMEOUT)
        nodes.raise_for_status()
    except requests.exceptions.SSLError:
        raise RemoteProblem(
            "The API's TLS certificate couldn't be verified. Add the cluster's CA certificate "
            "(/etc/pve/pve-root-ca.pem on the node) below, rather than turning verification off."
        ) from None
    except requests.exceptions.ConnectionError:
        raise RemoteProblem(f"Couldn't connect to {host}:{port}. Check the host, the port and the network.") from None
    except requests.exceptions.Timeout:
        raise RemoteProblem(f"{host}:{port} didn't answer within {TIMEOUT} seconds.") from None
    except requests.exceptions.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        raise RemoteProblem(f"Proxmox answered with HTTP {code}. The token may lack a privilege.") from None
    except ValueError:
        raise RemoteProblem("That doesn't look like a Proxmox VE API.") from None
    try:
        data = version.json()["data"]
        status = {str(n["node"]): str(n.get("status", "unknown")) for n in nodes.json()["data"]}
    except (ValueError, KeyError, TypeError):
        raise RemoteProblem("That doesn't look like a Proxmox VE API.") from None
    storages, imports = iso_storages(base, headers, verify)
    return PveResult(version=str(data.get("version", "?")), nodes=sorted(status), status=status,
                     cluster=cluster_name(base, headers, verify), iso_storages=storages, import_storages=imports)


def iso_storages(base: str, headers: dict[str, str], verify: bool | str) -> tuple[dict[str, str], list[str] | None]:
    """The storages whose content includes ISO images, from /storage, and those of them that allow Import content
    too (where cloud images go); ({}, None) if the token can't list them."""
    try:
        response = requests.get(f"{base}/storage", headers=headers, verify=verify, timeout=TIMEOUT)
        response.raise_for_status()
        found, imports = {}, []
        for entry in response.json()["data"]:
            content = str(entry.get("content", "")).split(",")
            if "iso" in content:
                kind = str(entry.get("type", "?")) + (", shared" if entry.get("shared") else "")
                found[str(entry["storage"])] = kind
                if "import" in content:
                    imports.append(str(entry["storage"]))
        return dict(sorted(found.items())), sorted(imports)
    except (requests.exceptions.RequestException, ValueError, KeyError, TypeError, AttributeError):
        return {}, None


def cluster_name(base: str, headers: dict[str, str], verify: bool | str) -> str:
    """The cluster's name from /cluster/status, or "" for a single node or a token without Sys.Audit."""
    try:
        response = requests.get(f"{base}/cluster/status", headers=headers, verify=verify, timeout=TIMEOUT)
        response.raise_for_status()
        entries = response.json()["data"]
        return next((str(e["name"]) for e in entries if e.get("type") == "cluster"), "")
    except (requests.exceptions.RequestException, ValueError, KeyError, TypeError, AttributeError):
        return ""


def keys_url(template: str, github_user: str) -> str:
    return template.replace("{{ github_user }}", github_user)


def preview_keys(url: str) -> list[dict[str, str]]:
    """The keys the roles would install from url: lines that start with a key type, as the roles filter them."""
    if not re.match(r"^https?://", url):
        raise RemoteProblem("The keys URL must start with https://.")
    try:
        response = requests.get(url, timeout=TIMEOUT, stream=True)
        if response.status_code == 404:
            raise RemoteProblem("No such GitHub user.")
        response.raise_for_status()
        body = response.raw.read(MAX_KEYS_BYTES + 1, decode_content=True)
    except requests.exceptions.RequestException as exc:
        raise RemoteProblem(f"Couldn't fetch the keys: {exc.__class__.__name__}.") from None
    if len(body) > MAX_KEYS_BYTES:
        raise RemoteProblem("The keys file is too large.")
    keys = []
    for line in body.decode("utf-8", "replace").splitlines():
        if re.match(r"^(ssh-|ecdsa-|sk-)", line.strip()):
            described = describe_public_key(line)
            if described:
                keys.append(described)
    return keys


# --- The VMs page: a read-only view of the cluster's VMs ------------------------------------------------------


class PveError(RemoteProblem):
    """A Proxmox API call failed. status is the HTTP status, or 0 if no answer came."""

    def __init__(self, message: str, status: int = 0, timeout: bool = False):
        super().__init__(message)
        self.status = status
        self.timeout = timeout


@dataclass(frozen=True)
class PveApi:
    """Read-only calls to the Proxmox API with the token, over verified TLS (unless the user turned
    verification off for the playbooks too), each with a short timeout."""

    host: str
    port: int
    user: str
    token_id: str
    secret: str = field(repr=False)
    verify: bool | str = True
    timeout: float = TIMEOUT

    @property
    def identity(self) -> str:
        """Names the API and the token without the secret: the caches start over when it changes."""
        digest = hashlib.sha256(self.secret.encode()).hexdigest()[:16]
        return f"{self.user}!{self.token_id}@{self.host}:{self.port}/{digest}/{self.verify}"

    def get(self, path: str, timeout: float | None = None) -> Any:
        """The data of GET path. Raises PveError with a message for the user."""
        headers = {"Authorization": f"PVEAPIToken={self.user}!{self.token_id}={self.secret}"}
        seconds = timeout or self.timeout
        try:
            response = requests.get(f"{api_url(self.host, self.port)}{path}", headers=headers, verify=self.verify,
                                    timeout=seconds)
        except requests.exceptions.SSLError:
            raise PveError("The API's TLS certificate couldn't be verified. Save the cluster's CA certificate on "
                           "the Proxmox page.") from None
        except requests.exceptions.Timeout:
            raise PveError(f"Proxmox didn't answer within {seconds:g} seconds.", timeout=True) from None
        except requests.exceptions.RequestException:
            raise PveError(f"Couldn't connect to {self.host}:{self.port}.") from None
        if response.status_code == 401:
            raise PveError("Proxmox refused the token (401). Check it on the Proxmox page.", 401)
        if response.status_code >= 400:
            # Proxmox puts the reason in "message" ("QEMU guest agent is not running").
            try:
                reason = v.text(response.json().get("message"), 200)
            except (ValueError, AttributeError):
                reason = ""
            raise PveError(reason or f"Proxmox answered with HTTP {response.status_code}.", response.status_code)
        try:
            return response.json()["data"]
        except (ValueError, KeyError, TypeError):
            raise PveError("That doesn't look like a Proxmox VE API.") from None


@dataclass
class AgentData:
    """What one VM's guest agent reported, or why it reported nothing."""

    state: str  # "ok", "not-running", "forbidden", "timeout", "error"
    addresses: list[str] = field(default_factory=list)
    os: v.OsInfo | None = None
    hostname: str = ""


class VmLister:
    """Lists the cluster's VMs for the VMs page. The answers are cached and shared by every session, the
    guest-agent calls run at most WORKERS at a time with a short timeout each, and a listing waits for them
    no longer than BUDGET seconds: what arrives later fills in on the next refresh. It blocks, so the web UI
    calls it in a thread (the event loop keeps serving the run logs meanwhile)."""

    RESOURCES_TTL = 10
    CONFIG_TTL = 300
    NETWORK_TTL = 60
    OSINFO_TTL = 600
    NEGATIVE_TTL = 120  # an agent that isn't running, didn't answer or is forbidden
    VERSION_TTL = 600
    AGENT_TIMEOUT = 5
    BUDGET = 15
    WORKERS = 4

    def __init__(self, clock: Callable[[], float] = time.monotonic, releases: tuple[v.Release, ...] = ()):
        self.clock = clock
        self.releases = releases  # what the playbooks support: the OS catalog's releases
        self._lock = threading.Lock()
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._inflight: dict[tuple, Future] = {}
        self._identity = ""
        self._pool = ThreadPoolExecutor(max_workers=self.WORKERS, thread_name_prefix="usas-pve")

    # --- Cache ---------------------------------------------------------------------------------------------

    def _get(self, key: tuple, ttl: float) -> tuple[bool, Any]:
        with self._lock:
            hit = self._cache.get(key)
        if hit is None or self.clock() - hit[0] > ttl:
            return False, None
        return True, hit[1]

    def _put(self, key: tuple, value: Any) -> None:
        with self._lock:
            self._cache[key] = (self.clock(), value)

    def _use(self, api: PveApi) -> None:
        """Starts the caches over when the API settings or the token change."""
        with self._lock:
            if api.identity != self._identity:
                self._cache.clear()
                self._identity = api.identity

    # --- Calls ---------------------------------------------------------------------------------------------

    def version(self, api: PveApi) -> str:
        hit, value = self._get(("version",), self.VERSION_TTL)
        if hit:
            return value
        try:
            data = api.get("/version")
            value = v.text(data.get("version"), 32) if isinstance(data, dict) else ""
        except PveError:
            value = ""
        self._put(("version",), value)
        return value

    def resources(self, api: PveApi) -> list[v.Vm]:
        hit, value = self._get(("resources",), self.RESOURCES_TTL)
        if not hit:
            value = v.parse_resources(api.get("/cluster/resources?type=vm"))
            self._put(("resources",), value)
        return value

    def _config(self, api: PveApi, vm: v.Vm) -> v.VmConfig | None:
        key = ("config", vm.node, vm.vmid)
        hit, value = self._get(key, self.CONFIG_TTL)
        if hit:
            return value
        try:
            value = v.parse_config(api.get(f"/nodes/{vm.node}/qemu/{vm.vmid}/config"))
        except PveError:
            hit, value = self._get(key, float("inf"))  # keep showing the last one
            return value
        self._put(key, value)
        return value

    def _agent(self, api: PveApi, vm: v.Vm, command: str) -> Any:
        result = api.get(f"/nodes/{vm.node}/qemu/{vm.vmid}/agent/{command}", timeout=self.AGENT_TIMEOUT)
        return result.get("result") if isinstance(result, dict) else None

    def _agent_data(self, api: PveApi, vm: v.Vm) -> AgentData:
        key = ("agent", vm.node, vm.vmid)
        hit, failed = self._get(("agent-failed", vm.node, vm.vmid), self.NEGATIVE_TTL)
        if hit:
            return failed
        try:
            hit, addresses = self._get(("network", vm.node, vm.vmid), self.NETWORK_TTL)
            if not hit:
                addresses = v.parse_interfaces(self._agent(api, vm, "network-get-interfaces"))
                self._put(("network", vm.node, vm.vmid), addresses)
            hit, about = self._get(key, self.OSINFO_TTL)
            if not hit:
                about = (v.parse_osinfo(self._agent(api, vm, "get-osinfo")),
                         v.parse_hostname(self._agent(api, vm, "get-host-name")))
                self._put(key, about)
        except PveError as exc:
            state = ("forbidden" if exc.status == 403 else "timeout" if exc.timeout
                     else "not-running" if "not running" in exc.message.lower() else "error")
            failed = AgentData(state)
            self._put(("agent-failed", vm.node, vm.vmid), failed)
            return failed
        return AgentData("ok", addresses, *about)

    def _details(self, api: PveApi, vm: v.Vm) -> None:
        """Fetches what the caches don't have for one VM: its config, then its agent's data."""
        config = self._config(api, vm)
        if config and config.agent and vm.status == "running":
            self._agent_data(api, vm)

    def _submit(self, api: PveApi, vm: v.Vm) -> Future:
        key = (vm.node, vm.vmid)
        with self._lock:
            future = self._inflight.get(key)
            if future is None:
                future = self._pool.submit(self._details, api, vm)
                self._inflight[key] = future
                future.add_done_callback(lambda _f, k=key: self._forget(k))
        return future

    def _forget(self, key: tuple) -> None:
        with self._lock:
            self._inflight.pop(key, None)

    # --- The listing ---------------------------------------------------------------------------------------

    def _row(self, vm: v.Vm, servers: dict[int, dict[str, str]], network: ipaddress.IPv4Network | None,
             pending: set[tuple], exclude: tuple[tuple[int, int], ...] = ()) -> dict[str, Any]:
        qemu = vm.type == "qemu" and not vm.template
        _, config = self._get(("config", vm.node, vm.vmid), float("inf")) if qemu else (False, None)
        waiting = (vm.node, vm.vmid) in pending
        agent = AgentData("none")  # containers and templates have no guest agent
        if qemu and config is None:
            agent = AgentData("pending" if waiting else "unknown")
        elif qemu and not config.agent:
            agent = AgentData("off")
        elif qemu and vm.status != "running":
            agent = AgentData("stopped")
        elif qemu:
            hit, failed = self._get(("agent-failed", vm.node, vm.vmid), self.NEGATIVE_TTL)
            got_net, addresses = self._get(("network", vm.node, vm.vmid), float("inf"))
            got_os, about = self._get(("agent", vm.node, vm.vmid), float("inf"))
            if hit:
                agent = failed
            elif got_net and got_os:
                agent = AgentData("ok", list(addresses), *about)
            else:
                agent = AgentData("pending" if waiting else "error")
        # A Kubernetes cluster's API address and LoadBalancer pool move between its members: never a VM's own.
        addresses = [a for a in agent.addresses if not v.excluded(a, exclude)]
        if network is not None:
            addresses = sorted(addresses, key=lambda a: ipaddress.IPv4Address(a) not in network)
        server = servers.get(vm.vmid, {})
        ip, source = v.pick_ip(addresses, server.get("ansible_host", ""), config.ipconfig0 if config else "")
        support, reason = v.classify(vm, config, agent.os, self.releases)
        return {
            "vmid": vm.vmid, "name": vm.name, "node": vm.node, "type": vm.type, "status": vm.status,
            "template": vm.template, "lock": vm.lock, "tags": vm.tags, "hastate": vm.hastate, "cpu": round(vm.cpu, 4),
            "maxcpu": vm.maxcpu, "mem": vm.mem, "maxmem": vm.maxmem, "uptime": vm.uptime,
            "agent": agent.state, "addresses": addresses, "ip": ip, "ip_source": source,
            "os": agent.os.pretty if agent.os else "", "hostname": agent.hostname,
            "support": support, "reason": reason, "inventory_name": server.get("name", ""),
            "ours": bool(config and config.ours) or {"ubuntu", "ansible"} <= set(vm.tags),
            "name_ok": bool(v.HOSTNAME_PATTERN.match(vm.name)),
        }

    def list(self, api: PveApi, servers: dict[int, dict[str, str]],
             network: ipaddress.IPv4Network | None = None, budget: float | None = None,
             exclude: tuple[tuple[int, int], ...] = ()) -> dict[str, Any]:
        """The page's data: every VM and container, with addresses and support; addresses in exclude
        (usas_vms.address_ranges) are left out. Raises PveError when the cluster can't be listed at all."""
        self._use(api)
        started = self.clock()
        version = self.version(api)
        vms = self.resources(api)
        futures = {(vm.node, vm.vmid): self._submit(api, vm) for vm in vms if vm.type == "qemu" and not vm.template}
        remaining = max(0.0, (self.BUDGET if budget is None else budget) - (self.clock() - started))
        wait(list(futures.values()), timeout=remaining)
        pending = {key for key, future in futures.items() if not future.done()}
        rows = [self._row(vm, servers, network, pending, exclude) for vm in vms]
        return {"version": version, "vms": rows, "complete": not pending,
                "forbidden": sum(1 for r in rows if r["agent"] == "forbidden")}
