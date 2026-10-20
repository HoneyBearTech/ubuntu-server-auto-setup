#!/usr/bin/env python3
"""Fuzz target: the sshd drop-in and Fail2Ban's jail.local built from inventory values.

Property: for any values the ssh_hardening and fail2ban roles accept, every setting appears exactly once
with exactly the given value, and nothing else is added (a value can't inject another directive).
"""

import configparser
import sys

import atheris

with atheris.instrument_imports():
    import roles

SSHD = roles.template("ssh_hardening", "00-hardening.conf.j2")
JAIL = roles.template("fail2ban", "jail.local.j2")
SSH = roles.role_vars("ssh_hardening")
F2B = roles.role_vars("fail2ban")
WORD_CHARS = "abcz09_.*?@!%:-[]=,/ \n\t#"
TIME_CHARS = "0123456789smhdw- \n"


def sshd(fdp):
    values = {
        "ssh_port": fdp.ConsumeIntInRange(1, 65535),
        "ssh_permit_root_login": fdp.PickValueInList(SSH["ssh_permit_root_login_values"]),
        "ssh_password_authentication": fdp.ConsumeBool(),
        "ssh_max_auth_tries": fdp.ConsumeIntInRange(1, 10),
        "ssh_login_grace_time": fdp.ConsumeIntInRange(0, 600),
        "ssh_client_alive_interval": fdp.ConsumeIntInRange(0, 3600),
        "ssh_client_alive_count_max": fdp.ConsumeIntInRange(0, 10),
        "ssh_x11_forwarding": fdp.ConsumeBool(),
        "ssh_allow_agent_forwarding": fdp.ConsumeBool(),
        "ssh_allow_tcp_forwarding": fdp.ConsumeBool(),
        "ssh_allow_users": [roles.from_alphabet(fdp, WORD_CHARS, 12) for _ in range(fdp.ConsumeIntInRange(0, 3))],
        "ansible_managed": "managed",
    }
    if not all(roles.matches(SSH["ssh_allow_users_pattern"], u) for u in values["ssh_allow_users"]):
        return
    settings = {}
    for line in SSHD.render(values).splitlines():
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        assert key not in settings, (key, line)
        settings[key] = value
    yes_no = lambda b: "yes" if b else "no"  # noqa: E731
    expected = {
        "Port": str(values["ssh_port"]), "PermitRootLogin": values["ssh_permit_root_login"],
        "PubkeyAuthentication": "yes", "PasswordAuthentication": yes_no(values["ssh_password_authentication"]),
        "KbdInteractiveAuthentication": "no", "PermitEmptyPasswords": "no",
        "MaxAuthTries": str(values["ssh_max_auth_tries"]), "LoginGraceTime": str(values["ssh_login_grace_time"]),
        "ClientAliveInterval": str(values["ssh_client_alive_interval"]),
        "ClientAliveCountMax": str(values["ssh_client_alive_count_max"]),
        "X11Forwarding": yes_no(values["ssh_x11_forwarding"]),
        "AllowAgentForwarding": yes_no(values["ssh_allow_agent_forwarding"]),
        "AllowTcpForwarding": yes_no(values["ssh_allow_tcp_forwarding"]),
    }
    if not values["ssh_password_authentication"]:
        expected["AuthenticationMethods"] = "publickey"
    if values["ssh_allow_users"]:
        expected["AllowUsers"] = " ".join(values["ssh_allow_users"])
    assert settings == expected, (settings, expected)


def jail(fdp):
    values = {
        "fail2ban_bantime": roles.from_alphabet(fdp, TIME_CHARS, 6),
        "fail2ban_findtime": roles.from_alphabet(fdp, TIME_CHARS, 6),
        "fail2ban_bantime_maxtime": roles.from_alphabet(fdp, TIME_CHARS, 6),
        "fail2ban_maxretry": fdp.ConsumeIntInRange(1, 100),
        "fail2ban_bantime_increment": fdp.ConsumeBool(),
        "fail2ban_ignoreip": [roles.from_alphabet(fdp, WORD_CHARS, 15) for _ in range(fdp.ConsumeIntInRange(0, 3))],
        "fail2ban_banaction": roles.from_alphabet(fdp, WORD_CHARS, 20) if fdp.ConsumeBool() else "",
        "fail2ban_sshd_mode": fdp.PickValueInList(F2B["fail2ban_sshd_modes"]),
        "ssh_port": fdp.ConsumeIntInRange(1, 65535),
        "ansible_managed": "managed",
    }
    times = ("fail2ban_bantime", "fail2ban_findtime", "fail2ban_bantime_maxtime")
    if not (all(roles.matches(F2B["fail2ban_time_pattern"], values[t]) for t in times)
            and all(roles.matches(F2B["fail2ban_ignoreip_pattern"], i) for i in values["fail2ban_ignoreip"])
            and roles.matches(F2B["fail2ban_banaction_pattern"], values["fail2ban_banaction"])):
        return
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.read_string(JAIL.render(values))
    default = {
        "bantime": values["fail2ban_bantime"], "findtime": values["fail2ban_findtime"],
        "maxretry": str(values["fail2ban_maxretry"]),
        "bantime.increment": "true" if values["fail2ban_bantime_increment"] else "false",
        "bantime.maxtime": values["fail2ban_bantime_maxtime"],
        "ignoreip": " ".join(["127.0.0.1/8", "::1"] + values["fail2ban_ignoreip"]),
    }
    if values["fail2ban_banaction"]:
        default["banaction"] = values["fail2ban_banaction"]
    assert dict(parser.defaults()) == default, (dict(parser.defaults()), default)
    assert parser.sections() == ["sshd"], parser.sections()
    sshd_own = dict(parser._sections["sshd"])  # the section's own keys, without the inherited defaults
    assert sshd_own == {"enabled": "true", "backend": "systemd", "mode": values["fail2ban_sshd_mode"],
                        "port": str(values["ssh_port"])}, sshd_own


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    if fdp.ConsumeBool():
        sshd(fdp)
    else:
        jail(fdp)


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
