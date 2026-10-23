"""Settings, read once from the environment (see docs/web-ui.md for the list)."""

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")
DEFAULT_PORTS = {"http": 80, "https": 443}


def _list(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(item.strip().lower() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    # The checkout or image directory with ansible.cfg, playbooks/ and roles/.
    project_dir: Path
    # Everything the UI writes: the inventory, the Vault, the SSH key, the runs.
    data_dir: Path
    # The file that holds the Vault password. Defaults to data_dir/.vault-pass; point it at a Docker secret
    # to keep the Vault password off the data volume.
    vault_password_file: Path
    # Host names the UI answers to (the Host header), against DNS rebinding.
    allowed_hosts: tuple[str, ...] = LOOPBACK_HOSTS
    # "auto": Secure cookies when the request came over HTTPS. "true": always (behind a TLS proxy).
    secure_cookies: str = "auto"
    # Addresses of reverse proxies whose X-Forwarded-Proto/For headers are trusted (uvicorn).
    trusted_proxies: str = ""
    session_hours: int = 12
    # The address the browser opens the UI at (scheme://host[:port]): passkeys belong to its host name and are
    # accepted only from it. Empty: http://localhost on any port, which is where the documented command
    # publishes it.
    public_url: str = ""
    ansible_playbook: str = "ansible-playbook"
    host: str = "0.0.0.0"  # inside the container; publish the port on 127.0.0.1 only
    port: int = 8080
    # Lets the tests set extra environment for ansible-playbook runs.
    run_env: dict[str, str] = field(default_factory=dict)

    @property
    def inventory_file(self) -> Path:
        return self.data_dir / "inventory" / "hosts.yml"

    @property
    def vault_file(self) -> Path:
        return self.data_dir / "inventory" / "group_vars" / "all" / "vault.yml"

    @property
    def ssh_dir(self) -> Path:
        return self.data_dir / "ssh"

    @property
    def ssh_key(self) -> Path:
        return self.ssh_dir / "id_ed25519"

    @property
    def known_hosts(self) -> Path:
        return self.ssh_dir / "known_hosts"

    @property
    def ca_file(self) -> Path:
        """The Proxmox CA certificate the user saved (PEM)."""
        return self.data_dir / "pve-ca.pem"

    @property
    def ca_bundle(self) -> Path:
        """The system CAs plus the Proxmox CA, for SSL_CERT_FILE and REQUESTS_CA_BUNDLE."""
        return self.data_dir / "ca-bundle.pem"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def auth_file(self) -> Path:
        return self.data_dir / "auth.json"

    @property
    def passkeys_file(self) -> Path:
        return self.data_dir / "passkeys.json"

    @property
    def status_token_file(self) -> Path:
        """The status API's token, as an HMAC (never the token itself)."""
        return self.data_dir / "status-token.json"

    @property
    def rp_id(self) -> str:
        """The WebAuthn relying party ID: the host name passkeys are made for."""
        return urlsplit(self.public_url).hostname or "localhost"

    @property
    def settings_file(self) -> Path:
        """UI-only settings that aren't inventory variables (such as the node's SSH address)."""
        return self.data_dir / "webui.json"


def public_origin(value: str) -> str:
    """USAS_PUBLIC_URL as the origin a browser reports (scheme://host[:port], no default port), or ValueError."""
    if not value:
        return ""
    try:
        url = urlsplit(value.strip())
        port = url.port
    except ValueError as exc:
        raise ValueError(f"USAS_PUBLIC_URL isn't a URL: {exc}") from None
    host = url.hostname or ""
    if url.scheme not in DEFAULT_PORTS or not host or url.username or url.password or url.query or url.fragment \
            or url.path not in ("", "/"):
        raise ValueError("USAS_PUBLIC_URL must be http(s)://host[:port], such as https://usas.example.lan")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("USAS_PUBLIC_URL must use a host name, not an IP address: passkeys need one")
    if url.scheme != "https" and host != "localhost":
        raise ValueError("USAS_PUBLIC_URL must use https unless the host is localhost")
    netloc = host if port in (None, DEFAULT_PORTS[url.scheme]) else f"{host}:{port}"
    return f"{url.scheme}://{netloc}"


def from_env(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    project = Path(env.get("USAS_PROJECT_DIR") or Path(__file__).resolve().parents[2])
    data = Path(env.get("USAS_DATA_DIR") or "/data")
    vault_pass = Path(env.get("USAS_VAULT_PASSWORD_FILE") or data / ".vault-pass")
    secure = (env.get("USAS_SECURE_COOKIES") or "auto").strip().lower()
    if secure not in ("auto", "true"):
        raise ValueError("USAS_SECURE_COOKIES must be auto or true")
    public = public_origin(env.get("USAS_PUBLIC_URL", ""))
    allowed = _list(env.get("USAS_ALLOWED_HOSTS"), LOOPBACK_HOSTS)
    public_host = urlsplit(public).hostname
    if public_host and public_host not in allowed:
        allowed = (*allowed, public_host)  # the UI answers to the name it is published under
    return Settings(
        project_dir=project,
        data_dir=data,
        vault_password_file=vault_pass,
        allowed_hosts=allowed,
        secure_cookies=secure,
        trusted_proxies=env.get("USAS_TRUSTED_PROXIES", ""),
        session_hours=int(env.get("USAS_SESSION_HOURS") or 12),
        public_url=public,
        ansible_playbook=env.get("USAS_ANSIBLE_PLAYBOOK") or "ansible-playbook",
        host=env.get("USAS_HOST") or "0.0.0.0",
        port=int(env.get("USAS_PORT") or 8080),
    )
