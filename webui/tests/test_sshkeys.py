"""The UI's key pair, host keys and the Proxmox CA certificate."""

import datetime
import re
import shlex
import shutil
import stat
import subprocess

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import NameOID
from usas_web import sshkeys as k
from usas_web.sshkeys import HostKey, KeyProblem, Keys

from .conftest import REPO


@pytest.fixture
def keys(settings):
    settings.data_dir.mkdir(parents=True)
    return Keys(settings)


def openssh(key, password=None):
    enc = serialization.BestAvailableEncryption(password) if password else serialization.NoEncryption()
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, enc)


def test_generate(keys, settings):
    keys.generate()
    pub = keys.public_key()
    assert pub.startswith("ssh-ed25519 ") and pub.endswith(k.KEY_COMMENT)
    assert stat.S_IMODE(settings.ssh_key.stat().st_mode) == 0o600
    key = serialization.load_ssh_private_key(settings.ssh_key.read_bytes(), None)  # OpenSSH format, no passphrase
    assert isinstance(key, ed25519.Ed25519PrivateKey)
    keys.delete()
    assert keys.public_key() == ""


@pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="no ssh-keygen")
def test_fingerprint_matches_ssh_keygen(keys, settings):
    keys.generate()
    out = subprocess.run(["ssh-keygen", "-lf", str(keys.public_path)], capture_output=True, text=True, check=True)
    assert k.describe_public_key(keys.public_key())["fingerprint"] == out.stdout.split()[1]


def test_upload_encrypted_openssh_key(keys, monkeypatch):
    # Reading one needs the bcrypt module, which the image doesn't have.
    def unsupported(data, password):
        raise k.UnsupportedAlgorithm("Need bcrypt module")

    monkeypatch.setattr(k.serialization, "load_ssh_private_key", unsupported)
    with pytest.raises(KeyProblem, match="ssh-keygen -p"):
        keys.upload(b"-----BEGIN OPENSSH PRIVATE KEY-----\n...", "pass phrase")


def test_upload(keys):
    keys.upload(openssh(ed25519.Ed25519PrivateKey.generate()))
    assert keys.public_key().startswith("ssh-ed25519")
    encrypted = ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(b"pp"))
    keys.upload(encrypted, "pp")
    assert keys.public_key().startswith("ssh-ed25519")
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    keys.upload(pem)
    assert keys.public_key().startswith("ecdsa-sha2-nistp256")
    with pytest.raises(KeyProblem, match="3072"):
        keys.upload(openssh(rsa.generate_private_key(public_exponent=65537, key_size=2048)))
    with pytest.raises(KeyProblem, match="isn't an OpenSSH"):
        keys.upload(b"garbage")
    with pytest.raises(KeyProblem, match="passphrase is wrong"):
        keys.upload(encrypted, "wrong")


def test_describe_public_key():
    assert k.describe_public_key("not a key") is None
    assert k.describe_public_key("ssh-ed25519 !!!") is None
    assert k.describe_public_key("ssh-ed25519 AAAA c o m")["comment"] == "c o m"


def test_check_host():
    assert k.check_host(" pve-1.example.lan ") == "pve-1.example.lan"
    assert k.check_host("[fd00::1]") == "[fd00::1]"
    for bad in ("-oProxyCommand=id", "pve 1", "", "a;b"):
        with pytest.raises(KeyProblem):
            k.check_host(bad)


def completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_scan_trust_forget(keys, settings, monkeypatch):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return completed("# pve:22 SSH-2.0\npve ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMq\n"
                         "pve ssh-rsa AAAAB3NzaC1yc2E=\npve ssh-rsa not*base64\n")

    monkeypatch.setattr(k.subprocess, "run", run)
    found = keys.scan("pve")
    assert calls[0][:2] == ["ssh-keyscan", "-T"] and calls[0][-2:] == ["--", "pve"]
    assert [h.keytype for h in found] == ["ssh-ed25519", "ssh-rsa"]
    settings.ssh_dir.mkdir(exist_ok=True)
    settings.known_hosts.write_text("other ssh-ed25519 AAAAOTHER\npve ssh-ed25519 AAAAOLD\n")
    keys.trust("pve", found)
    assert [h.key for h in keys.trusted("pve")] == ["AAAAC3NzaC1lZDI1NTE5AAAAIOMq", "AAAAB3NzaC1yc2E="]
    assert keys.trusted("other")[0].key == "AAAAOTHER"
    keys.forget("pve")
    assert keys.trusted("pve") == [] and keys.trusted("other")
    monkeypatch.setattr(k.subprocess, "run", lambda argv, **kw: completed(""))
    with pytest.raises(KeyProblem, match="no SSH host keys"):
        keys.scan("pve")

    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(k.subprocess, "run", boom)
    with pytest.raises(KeyProblem, match=r"Couldn't scan pve: no answer within 15 seconds\.$") as caught:
        keys.scan("pve")
    assert caught.value.message == str(caught.value)

    def missing(argv, **kw):
        raise FileNotFoundError(2, "No such file or directory", "ssh-keyscan")

    monkeypatch.setattr(k.subprocess, "run", missing)
    with pytest.raises(KeyProblem, match=r"Couldn't scan pve: No such file or directory\.$"):
        keys.scan("pve")  # the reason only, not the exception's text with its path


def test_node_cpu(keys, monkeypatch):
    keys.generate()
    keys.trust("pve", [HostKey("pve", "ssh-ed25519", "AAAA")])
    seen = []
    cpuinfo = (REPO / "tests/fixtures/cpuinfo-ivybridge").read_text()
    first = [line for line in cpuinfo.splitlines() if re.match(r"^(model name|flags)\s*:", line)][:2]
    monkeypatch.setattr(k.subprocess, "run", lambda argv, **kw: seen.append(argv) or completed("\n".join(first)))
    model, flags = keys.cpu("pve", "root")
    # ssh hands the remote shell one string: the shell must get grep's pattern back as one word.
    host, remote = seen[0][seen[0].index("--") + 1:]
    assert host == "pve" and shlex.split(remote) == ["grep", "-m", "2", "-E", "^(model name|flags)[[:space:]]*:",
                                                     "/proc/cpuinfo"]
    assert model == "Intel(R) Xeon(R) CPU E5-2470 v2 @ 2.40GHz" and "avx" in flags and "avx2" not in flags
    monkeypatch.setattr(k.subprocess, "run", lambda argv, **kw: completed("", 2, "No such file"))
    assert keys.cpu("pve", "root") == ("", set())


def test_ssh_login_and_ca_fetch(keys, settings, monkeypatch):
    with pytest.raises(KeyProblem, match="Create or upload"):
        keys.test_login("pve", "root")
    keys.generate()
    with pytest.raises(KeyProblem, match="Trust"):
        keys.test_login("pve", "root")
    keys.trust("pve", [HostKey("pve", "ssh-ed25519", "AAAA")])
    seen = []
    monkeypatch.setattr(k.subprocess, "run", lambda argv, **kw: seen.append(argv) or completed("PEM"))
    assert keys.test_login("pve", "root") == "Logged in to root@pve with the UI's key."
    argv = seen[0]
    assert "StrictHostKeyChecking=yes" in argv and f"UserKnownHostsFile={settings.known_hosts}" in argv
    assert argv[argv.index("--") + 1:] == ["pve", "true"] and argv[argv.index("-l") + 1] == "root"
    assert keys.fetch_ca("pve", "root") == "PEM"
    assert keys.test_login("pve", "usas-build") == "Logged in to usas-build@pve with the UI's key."
    assert seen[-1][seen[-1].index("-l") + 1] == "usas-build"
    for user in ("-oProxyCommand=id", "Root", "usas build", "a" * 33, ""):
        with pytest.raises(KeyProblem, match="lowercase Linux user name"):
            keys.test_login("pve", user)
    monkeypatch.setattr(k.subprocess, "run", lambda argv, **kw: completed("", 255, "Permission denied"))
    with pytest.raises(KeyProblem, match="Permission denied"):
        keys.test_login("pve", "root")
    with pytest.raises(KeyProblem, match=r"Couldn't read .*Permission denied$"):
        keys.fetch_ca("pve", "root")
    with pytest.raises(KeyProblem, match="usas-build isn't allowed to read it: paste the certificate instead"):
        keys.fetch_ca("pve", "usas-build")

    def boom(argv, **kw):
        raise FileNotFoundError(2, "No such file or directory", "/usr/bin/ssh")

    monkeypatch.setattr(k.subprocess, "run", boom)
    with pytest.raises(KeyProblem, match=r"SSH to pve failed: No such file or directory\.$"):
        keys.test_login("pve", "root")

    def odd(argv, **kw):
        raise OSError("something without an errno")

    monkeypatch.setattr(k.subprocess, "run", odd)
    with pytest.raises(KeyProblem, match=r"SSH to pve failed: OSError\.$"):
        keys.test_login("pve", "root")


def test_the_node_user_pattern_is_the_roles():
    import yaml



    release = yaml.safe_load((REPO / "roles/os_catalog/vars/main.yml").read_text())
    common = yaml.safe_load((REPO / "roles/common/vars/main.yml").read_text())
    assert k.NODE_USER == release["pve_ssh_user_pattern"] == common["common_username_pattern"]


def make_ca() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Proxmox Virtual Environment")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def test_ca(settings, monkeypatch, tmp_path):
    settings.data_dir.mkdir(parents=True)
    system = tmp_path / "system.pem"
    system.write_text("SYSTEM CAS\n")
    monkeypatch.setattr(k, "SYSTEM_CAS", (tmp_path / "missing", system))
    described = k.save_ca(settings, make_ca())
    assert described[0]["subject"] == "CN=Proxmox Virtual Environment" and len(described[0]["fingerprint"]) == 95
    assert settings.ca_bundle.read_text().startswith("SYSTEM CAS\n-----BEGIN CERTIFICATE-----")
    assert k.saved_ca(settings) == described
    k.remove_ca(settings)
    assert k.saved_ca(settings) == [] and not settings.ca_bundle.exists()
    with pytest.raises(KeyProblem, match="PEM certificate"):
        k.save_ca(settings, "nope")
    with pytest.raises(KeyProblem, match="too large"):
        k.save_ca(settings, "x" * 300_000)
    monkeypatch.setattr(k, "SYSTEM_CAS", ())
    assert "BEGIN CERTIFICATE" in k.system_cas()  # certifi's bundle


ED = "AAAAC3NzaC1lZDI1NTE5AAAAIOMq"


def test_vm_aliases():
    assert k.vm_alias("Home-Lab", 101) == "usas-home-lab-101"
    for odd in ("", "a b", "x" * 40, "-lab", "lab\tx", "läb"):
        assert k.vm_alias(odd, 4061) == "usas-pve-4061"
    with pytest.raises(KeyProblem):
        k.vm_alias("lab", 12)
    for bad in ("usas-pve-1", "pve", "usas-pve-101 x", "usas--101", "usas-pve-101\n"):
        with pytest.raises(KeyProblem):
            k.check_alias(bad)


def test_scan_a_vm_on_another_port(keys, monkeypatch):
    calls = []
    monkeypatch.setattr(k.subprocess, "run", lambda argv, **kw: calls.append(argv) or completed(
        f"[10.0.1.5]:2222 ssh-ed25519 {ED}\n"))
    found = keys.scan("10.0.1.5", 2222, "usas-pve-101")
    assert calls[0][-4:] == ["-p", "2222", "--", "10.0.1.5"]
    assert [(h.host, h.key) for h in found] == [("usas-pve-101", ED)]
    assert keys.scan("10.0.1.5", 2222)[0].host == "[10.0.1.5]:2222"
    assert keys.scan("10.0.1.5")[0].host == "10.0.1.5" and "-p" not in calls[-1]
    for port in (0, 65536, True, "22"):
        with pytest.raises(KeyProblem, match="port"):
            keys.scan("10.0.1.5", port)
    with pytest.raises(KeyProblem, match="host-key name"):
        keys.scan("10.0.1.5", 22, "10.0.1.6")


def test_pins_adopt_and_options(keys, settings):
    settings.ssh_dir.mkdir(exist_ok=True)
    settings.known_hosts.write_text(f"10.0.0.10 ssh-ed25519 {ED}\nusas-pve-102 ssh-rsa AAAA\nusas-pve-102 x y\n")
    assert sorted(keys.pins()) == ["usas-pve-102"] and len(keys.pins()["usas-pve-102"]) == 2
    adopted = keys.adopt("usas-pve-4000", "10.0.0.10")
    assert [(h.host, h.key) for h in adopted] == [("usas-pve-4000", ED)]
    assert keys.adopt("usas-pve-4000", "10.0.0.10") == []  # pinned already
    assert keys.adopt("usas-pve-4001", "10.0.0.11") == [] and "usas-pve-4001" not in keys.pins()
    assert keys.trusted("10.0.0.10")  # the address entry stays
    options = keys.ssh_options("usas-pve-4000")
    assert options.endswith("-o StrictHostKeyChecking=yes -o HostKeyAlias=usas-pve-4000")
    with pytest.raises(KeyProblem):
        keys.ssh_options("10.0.0.10")


def test_login_to_a_pinned_vm(keys, settings, monkeypatch):
    keys.generate()
    with pytest.raises(KeyProblem, match="Trust"):
        keys.test_login("10.0.1.5", "ubuntu", 2222, "usas-pve-101")
    keys.trust("usas-pve-101", [HostKey("usas-pve-101", "ssh-ed25519", ED)])
    seen = []
    monkeypatch.setattr(k.subprocess, "run", lambda argv, **kw: seen.append(argv) or completed(""))
    assert keys.test_login("10.0.1.5", "ubuntu", 2222, "usas-pve-101").startswith("Logged in to ubuntu@10.0.1.5")
    argv = seen[0]
    assert "HostKeyAlias=usas-pve-101" in argv and "ControlPath=none" in argv
    assert argv[argv.index("-p") + 1] == "2222"
    with pytest.raises(KeyProblem, match="Trust"):
        keys.test_login("10.0.1.5", "ubuntu", 2222)  # the address itself isn't trusted
