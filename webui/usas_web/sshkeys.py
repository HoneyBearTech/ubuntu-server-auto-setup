"""The UI's SSH key, the Proxmox node's host key, and the Proxmox CA certificate.

One key pair reaches the Proxmox node (as pve_ssh_user, root unless prepare-node.yml set up another user, for
the ISO build and the seed files) and the servers (as the admin user; its public key is added to
admin_extra_ssh_keys). The node's host key is trusted only after the
user has compared its fingerprint with the one the node shows, and is then checked on every connection.
"""

import base64
import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from .config import Settings
from .problems import Problem
from .schema import API_HOST
from .store import write_private

KEY_COMMENT = "ubuntu-server-auto-setup-webui"
SCAN_TYPES = "ed25519,ecdsa,rsa"
TIMEOUT = 15
PVE_CA_PATH = "/etc/pve/pve-root-ca.pem"
# A Linux user name, as roles/os_catalog/vars/main.yml's pve_ssh_user_pattern (the tests check they match).
NODE_USER = r"^[a-z_][a-z0-9_-]{0,31}\Z"
SYSTEM_CAS = (Path("/etc/ssl/certs/ca-certificates.crt"), Path("/etc/pki/tls/certs/ca-bundle.crt"))


def _why(exc: Exception) -> str:
    """Why a command couldn't run, in a few words (not the exception's own text)."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"no answer within {TIMEOUT} seconds"
    return getattr(exc, "strerror", None) or exc.__class__.__name__


class KeyProblem(Problem, ValueError):
    """A key, certificate or host the UI can't use; the message is shown to the user."""


@dataclass(frozen=True)
class HostKey:
    host: str
    keytype: str
    key: str

    @property
    def line(self) -> str:
        return f"{self.host} {self.keytype} {self.key}"

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.key)


def fingerprint(blob_b64: str) -> str:
    """OpenSSH's SHA256 fingerprint of a base64 key blob."""
    digest = hashlib.sha256(base64.b64decode(blob_b64, validate=True)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def describe_public_key(line: str) -> dict[str, str] | None:
    """Type, fingerprint and comment of an authorized_keys line, or None if it isn't one."""
    parts = line.strip().split(None, 2)
    if len(parts) < 2 or not re.match(r"^(ssh-|ecdsa-|sk-)", parts[0]):
        return None
    try:
        fp = fingerprint(parts[1])
    except ValueError:
        return None
    return {"type": parts[0], "fingerprint": fp, "comment": parts[2] if len(parts) > 2 else ""}


def check_user(user: str) -> str:
    if not re.match(NODE_USER, user):
        raise KeyProblem("The node's SSH user must be a lowercase Linux user name (Proxmox page).")
    return user


def check_host(host: str) -> str:
    host = host.strip()
    if not re.match(API_HOST, host):
        raise KeyProblem("Enter a host name or an IP address.")
    return host


class Keys:
    def __init__(self, settings: Settings):
        self.settings = settings

    # --- The UI's key pair -------------------------------------------------------------------------------

    @property
    def public_path(self) -> Path:
        return self.settings.ssh_key.with_suffix(".pub")

    def public_key(self) -> str:
        return self.public_path.read_text().strip() if self.public_path.exists() else ""

    def _write(self, private_key) -> None:
        private = private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()
        )
        public = private_key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        write_private(self.settings.ssh_key, private)
        write_private(self.public_path, public + f" {KEY_COMMENT}\n".encode())

    def generate(self) -> None:
        self._write(ed25519.Ed25519PrivateKey.generate())

    def upload(self, data: bytes, passphrase: str = "") -> None:
        try:
            key = serialization.load_ssh_private_key(data.strip() + b"\n", passphrase.encode() or None)
        except UnsupportedAlgorithm:
            raise KeyProblem(
                "Keys in the OpenSSH format with a passphrase can't be read here. Remove the passphrase from a copy "
                "(ssh-keygen -p -f copy) or create a new key."
            ) from None
        except (ValueError, TypeError) as exc:
            try:
                key = serialization.load_pem_private_key(data, passphrase.encode() or None)
            except (ValueError, TypeError):
                raise KeyProblem(
                    "That isn't an OpenSSH or PEM private key, or the passphrase is wrong." f" ({exc})"
                ) from None
        if isinstance(key, rsa.RSAPrivateKey) and key.key_size < 3072:
            raise KeyProblem("RSA keys must have at least 3072 bits; an Ed25519 key is better.")
        if not isinstance(key, (ed25519.Ed25519PrivateKey, ec.EllipticCurvePrivateKey, rsa.RSAPrivateKey)):
            raise KeyProblem("Use an Ed25519, ECDSA or RSA key.")
        self._write(key)

    def delete(self) -> None:
        self.settings.ssh_key.unlink(missing_ok=True)
        self.public_path.unlink(missing_ok=True)

    # --- Host keys -------------------------------------------------------------------------------------

    def scan(self, host: str) -> list[HostKey]:
        """The host keys the host offers now (not trusted yet)."""
        host = check_host(host)
        try:
            result = subprocess.run(  # noqa: S603 (an argument list, no shell)
                ["ssh-keyscan", "-T", "5", "-t", SCAN_TYPES, "--", host],
                capture_output=True, text=True, timeout=TIMEOUT, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise KeyProblem(f"Couldn't scan {host}: {_why(exc)}.") from None
        keys = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and not line.startswith("#"):
                keys.append(HostKey(host=host, keytype=parts[1], key=parts[2]))
        if not keys:
            raise KeyProblem(f"{host} offered no SSH host keys (is SSH running on port 22?).")
        return keys

    def trusted(self, host: str) -> list[HostKey]:
        path = self.settings.known_hosts
        if not path.exists():
            return []
        keys = []
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 3 and host in parts[0].split(","):
                keys.append(HostKey(host=host, keytype=parts[1], key=parts[2]))
        return keys

    def trust(self, host: str, keys: list[HostKey]) -> None:
        """Replaces the known_hosts entries for host with keys."""
        path = self.settings.known_hosts
        lines = path.read_text().splitlines() if path.exists() else []
        kept = [line for line in lines if host not in (line.split() or [""])[0].split(",")]
        write_private(path, "\n".join(kept + [k.line for k in keys]) + "\n")

    def forget(self, host: str) -> None:
        self.trust(host, [])

    # --- Using the key ---------------------------------------------------------------------------------

    def _ssh(self, host: str, user: str, *command: str) -> subprocess.CompletedProcess:
        host, user = check_host(host), check_user(user)
        if not self.settings.ssh_key.exists():
            raise KeyProblem("Create or upload the SSH key first.")
        if not self.trusted(host):
            raise KeyProblem(f"Trust {host}'s host key first.")
        argv = [
            "ssh", "-i", str(self.settings.ssh_key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", f"UserKnownHostsFile={self.settings.known_hosts}", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=10", "-l", user, "--", host, *command,
        ]
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT, check=False)  # noqa: S603
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise KeyProblem(f"SSH to {host} failed: {_why(exc)}.") from None

    def test_login(self, host: str, user: str) -> str:
        result = self._ssh(host, user, "true")
        if result.returncode != 0:
            raise KeyProblem(f"SSH to {user}@{host} failed: {result.stderr.strip()[-300:] or result.returncode}")
        return f"Logged in to {user}@{host} with the UI's key."

    def fetch_ca(self, host: str, user: str) -> str:
        """The cluster CA certificate from the node, read over the verified SSH connection."""
        result = self._ssh(host, user, "cat", PVE_CA_PATH)
        if result.returncode != 0:
            # Only root (and Proxmox's own www-data group) can read /etc/pve.
            hint = "" if user == "root" else f" {user} isn't allowed to read it: paste the certificate instead."
            raise KeyProblem(f"Couldn't read {PVE_CA_PATH} on {host}: {result.stderr.strip()[-300:]}{hint}")
        return result.stdout


# --- The Proxmox CA --------------------------------------------------------------------------------------


def parse_certificates(pem: str) -> list[x509.Certificate]:
    if len(pem) > 200_000:
        raise KeyProblem("That certificate file is too large.")
    try:
        certs = x509.load_pem_x509_certificates(pem.encode())
    except ValueError:
        raise KeyProblem("That isn't a PEM certificate (-----BEGIN CERTIFICATE-----).") from None
    return certs


def describe_certificate(cert: x509.Certificate) -> dict[str, str]:
    return {
        "subject": cert.subject.rfc4514_string(),
        "fingerprint": cert.fingerprint(hashes.SHA256()).hex(":").upper(),
        "expires": cert.not_valid_after_utc.strftime("%Y-%m-%d"),
    }


def system_cas() -> str:
    for path in SYSTEM_CAS:
        if path.exists():
            return path.read_text()
    import certifi

    return Path(certifi.where()).read_text()


def save_ca(settings: Settings, pem: str) -> list[dict[str, str]]:
    certs = parse_certificates(pem)
    text = "".join(c.public_bytes(serialization.Encoding.PEM).decode() for c in certs)
    write_private(settings.ca_file, text)
    write_private(settings.ca_bundle, system_cas().rstrip("\n") + "\n" + text)
    return [describe_certificate(c) for c in certs]


def remove_ca(settings: Settings) -> None:
    settings.ca_file.unlink(missing_ok=True)
    settings.ca_bundle.unlink(missing_ok=True)


def saved_ca(settings: Settings) -> list[dict[str, str]]:
    if not settings.ca_file.exists():
        return []
    return [describe_certificate(c) for c in parse_certificates(settings.ca_file.read_text())]
