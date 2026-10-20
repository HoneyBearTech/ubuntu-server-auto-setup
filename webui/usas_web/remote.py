"""Checks against outside systems: the Proxmox API (test connection) and the GitHub keys (preview)."""

import re
from dataclasses import dataclass
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
    nodes: list[str]


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
        names = sorted(str(n["node"]) for n in nodes.json()["data"])
    except (ValueError, KeyError, TypeError):
        raise RemoteProblem("That doesn't look like a Proxmox VE API.") from None
    return PveResult(version=str(data.get("version", "?")), nodes=names)


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
