"""Settings, read once from the environment (see docs/web-ui.md for the list)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")


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
    def settings_file(self) -> Path:
        """UI-only settings that aren't inventory variables (such as the node's SSH address)."""
        return self.data_dir / "webui.json"


def from_env(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    project = Path(env.get("USAS_PROJECT_DIR") or Path(__file__).resolve().parents[2])
    data = Path(env.get("USAS_DATA_DIR") or "/data")
    vault_pass = Path(env.get("USAS_VAULT_PASSWORD_FILE") or data / ".vault-pass")
    secure = (env.get("USAS_SECURE_COOKIES") or "auto").strip().lower()
    if secure not in ("auto", "true"):
        raise ValueError("USAS_SECURE_COOKIES must be auto or true")
    return Settings(
        project_dir=project,
        data_dir=data,
        vault_password_file=vault_pass,
        allowed_hosts=_list(env.get("USAS_ALLOWED_HOSTS"), LOOPBACK_HOSTS),
        secure_cookies=secure,
        trusted_proxies=env.get("USAS_TRUSTED_PROXIES", ""),
        session_hours=int(env.get("USAS_SESSION_HOURS") or 12),
        ansible_playbook=env.get("USAS_ANSIBLE_PLAYBOOK") or "ansible-playbook",
        host=env.get("USAS_HOST") or "0.0.0.0",
        port=int(env.get("USAS_PORT") or 8080),
    )
