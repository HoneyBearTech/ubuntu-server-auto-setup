"""Settings from the environment."""

from pathlib import Path

import pytest
from usas_web.config import LOOPBACK_HOSTS, from_env


def test_defaults():
    s = from_env({})
    assert s.data_dir == Path("/data") and s.vault_password_file == Path("/data/.vault-pass")
    assert s.allowed_hosts == LOOPBACK_HOSTS and s.secure_cookies == "auto" and s.port == 8080
    assert (s.project_dir / "playbooks").is_dir()
    assert s.inventory_file == Path("/data/inventory/hosts.yml")
    assert s.vault_file == Path("/data/inventory/group_vars/all/vault.yml")
    assert s.known_hosts == Path("/data/ssh/known_hosts") and s.ca_bundle == Path("/data/ca-bundle.pem")


def test_overrides():
    s = from_env({"USAS_DATA_DIR": "/srv/usas", "USAS_ALLOWED_HOSTS": "Usas.Example.LAN, localhost,,",
                  "USAS_SECURE_COOKIES": "TRUE", "USAS_VAULT_PASSWORD_FILE": "/run/secrets/vault",
                  "USAS_PORT": "9000", "USAS_SESSION_HOURS": "1", "USAS_TRUSTED_PROXIES": "10.0.0.5"})
    assert s.allowed_hosts == ("usas.example.lan", "localhost")
    assert s.secure_cookies == "true" and s.vault_password_file == Path("/run/secrets/vault")
    assert s.port == 9000 and s.session_hours == 1 and s.trusted_proxies == "10.0.0.5"


def test_bad_cookie_setting():
    with pytest.raises(ValueError, match="USAS_SECURE_COOKIES"):
        from_env({"USAS_SECURE_COOKIES": "sometimes"})


def test_the_scheduler_setting():
    from usas_web.config import from_env as env

    assert env({}).scheduler and not env({"USAS_SCHEDULER": "OFF"}).scheduler
    with pytest.raises(ValueError, match="USAS_SCHEDULER"):
        env({"USAS_SCHEDULER": "sometimes"})
