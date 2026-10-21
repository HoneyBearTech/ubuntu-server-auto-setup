#!/usr/bin/env python3
"""Fuzz target: the sshd drop-in, Fail2Ban's jail.local and its webhook action built from inventory values.

Property: for any values the ssh_hardening and fail2ban roles accept, every setting appears exactly once
with exactly the given value, and nothing else is added (a value can't inject another directive, or another
argument into the webhook's curl command).
"""

import configparser
import shlex
import sys

import atheris

with atheris.instrument_imports():
    import roles

SSHD = roles.template("ssh_hardening", "00-hardening.conf.j2")
JAIL = roles.template("fail2ban", "jail.local.j2")
WEBHOOK = roles.template("fail2ban", "usas-webhook.conf.j2")
SSH = roles.role_vars("ssh_hardening")
F2B = roles.role_vars("fail2ban")
WORD_CHARS = "abcz09_.*?@!%:-[]=,/ \n\t#"
TIME_CHARS = "0123456789smhdw- \n"
EMAIL_CHARS = "az09._+-@ \n%<>\"'=#;"
URL_CHARS = "az09.:/?&=_~+-%<>\"' \n#;$`()"


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
        "fail2ban_destemail": email(fdp),
        "fail2ban_sender": email(fdp),
        "fail2ban_webhook_url": url(fdp),
        "fail2ban_webhook_format": fdp.PickValueInList(F2B["fail2ban_webhook_formats"]),
        "ansible_managed": "managed",
    }
    times = ("fail2ban_bantime", "fail2ban_findtime", "fail2ban_bantime_maxtime")
    if not (all(roles.matches(F2B["fail2ban_time_pattern"], values[t]) for t in times)
            and all(roles.matches(F2B["fail2ban_ignoreip_pattern"], i) for i in values["fail2ban_ignoreip"])
            and roles.matches(F2B["fail2ban_banaction_pattern"], values["fail2ban_banaction"])
            and all(values[m] == "" or roles.matches(F2B["fail2ban_email_pattern"], values[m])
                    for m in ("fail2ban_destemail", "fail2ban_sender"))
            and (values["fail2ban_webhook_url"] == ""
                 or roles.matches(F2B["fail2ban_webhook_url_pattern"], values["fail2ban_webhook_url"]))):
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
    if values["fail2ban_destemail"]:
        default["destemail"] = values["fail2ban_destemail"]
        if values["fail2ban_sender"]:
            default["sender"] = values["fail2ban_sender"]
    if values["fail2ban_destemail"] or values["fail2ban_webhook_url"]:
        default["action"] = "%(action_mw)s" if values["fail2ban_destemail"] else "%(action_)s"
        if values["fail2ban_webhook_url"]:
            default["action"] += "\nusas-webhook"
            webhook(values)
    assert dict(parser.defaults()) == default, (dict(parser.defaults()), default)
    assert parser.sections() == ["sshd"], parser.sections()
    sshd_own = dict(parser._sections["sshd"])  # the section's own keys, without the inherited defaults
    assert sshd_own == {"enabled": "true", "backend": "systemd", "mode": values["fail2ban_sshd_mode"],
                        "port": str(values["ssh_port"])}, sshd_own


def email(fdp):
    """Empty, a plain address, or (sometimes) one with characters the role must refuse."""
    choice = fdp.ConsumeIntInRange(0, 3)
    if choice == 0:
        return ""
    if choice == 1:
        return roles.from_alphabet(fdp, EMAIL_CHARS, 20)
    return roles.from_alphabet(fdp, "az09._+-", 8) + "@" + roles.hostname(fdp) + "." + roles.hostname(fdp)


def url(fdp):
    """Empty, a URL with a path and query, or (sometimes) one with characters the role must refuse."""
    choice = fdp.ConsumeIntInRange(0, 3)
    if choice == 0:
        return ""
    tail = URL_CHARS if choice == 1 else "az09./_~+=-"
    query = ("?" + roles.from_alphabet(fdp, tail + "&", 16)) if fdp.ConsumeBool() else ""
    return (fdp.PickValueInList(["http://", "https://", "ftp://"]) + roles.hostname(fdp)
            + fdp.PickValueInList(["", ":8443"]) + "/" + roles.from_alphabet(fdp, tail, 24) + query)


def webhook(values):
    """The URL is the curl command's last argument, whole, and Fail2Ban finds nothing to interpolate in it."""
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.read_string(WEBHOOK.render(values))
    assert parser.sections() == ["Definition", "Init"], parser.sections()
    for key, value in parser["Definition"].items():
        assert key in ("actionstart", "actionstop", "actioncheck", "actionban", "actionunban"), key
        assert key == "actionban" or value == "", (key, value)
    args = shlex.split(parser["Definition"]["actionban"])
    assert args[0] == "curl" and args[-1] == values["fail2ban_webhook_url"], args
    assert len(args) == (11 if values["fail2ban_webhook_format"] == "json" else 13), args
    url = values["fail2ban_webhook_url"]
    assert not any(c in url for c in "%<>'\"$` \n"), url


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
