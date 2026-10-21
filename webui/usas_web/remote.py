"""Checks against outside systems: the Proxmox API (test connection) and the GitHub keys (preview)."""

import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .sshkeys import describe_public_key

TIMEOUT = 10
MAX_KEYS_BYTES = 256 * 1024


class RemoteProblem(RuntimeError):
    """A check failed; the message is shown to the user."""


@dataclass
class PveResult:
    version: str
    nodes: list[str]  # node names, sorted
    status: dict[str, str] = field(default_factory=dict)  # node name -> "online", "offline", ...
    cluster: str = ""  # the cluster's name; empty for a single node, or if the token can't read it
    iso_storages: dict[str, str] = field(default_factory=dict)  # storage ID -> type ("nfs, shared"), ISO content


def api_url(host: str, port: int) -> str:
    return f"https://{host}:{port}/api2/json"


def test_proxmox(host: str, port: int, user: str, token_id: str, secret: str, validate: bool,
                 ca_bundle: Path | None) -> PveResult:
    """Calls /version and /nodes with the token, verifying TLS as the playbooks will."""
    verify: bool | str = (str(ca_bundle) if ca_bundle and ca_bundle.exists() else True) if validate else False
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
    return PveResult(version=str(data.get("version", "?")), nodes=sorted(status), status=status,
                     cluster=cluster_name(base, headers, verify), iso_storages=iso_storages(base, headers, verify))


def iso_storages(base: str, headers: dict[str, str], verify: bool | str) -> dict[str, str]:
    """The storages whose content includes ISO images, from /storage; {} if the token can't list them."""
    try:
        response = requests.get(f"{base}/storage", headers=headers, verify=verify, timeout=TIMEOUT)
        response.raise_for_status()
        found = {}
        for entry in response.json()["data"]:
            if "iso" in str(entry.get("content", "")).split(","):
                kind = str(entry.get("type", "?")) + (", shared" if entry.get("shared") else "")
                found[str(entry["storage"])] = kind
        return dict(sorted(found.items()))
    except (requests.exceptions.RequestException, ValueError, KeyError, TypeError, AttributeError):
        return {}


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
