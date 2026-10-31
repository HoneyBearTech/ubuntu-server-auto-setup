#!/usr/bin/env python3
"""Fuzz target: the optional hardening and notification files built from inventory values: the sysctl
drop-in, unattended-upgrades' mail settings, msmtp's configuration, and the time sources for chrony and
systemd-timesyncd.

Property: for any values the hardening and common roles accept, each setting appears exactly once with exactly
the given value, and nothing else is added (a value can't inject another sysctl key, apt option, msmtp command,
chrony directive or option, or timesyncd setting).
"""

import sys

import atheris

with atheris.instrument_imports():
    import roles

SYSCTL = roles.template("hardening", "sysctl.conf.j2")
SYSCTL_SETTINGS = roles.set_fact_expression("roles/hardening/tasks/sysctl.yml", "hardening_sysctl_settings")
APT = roles.template("common", "52unattended-upgrades-local.j2")
MSMTP = roles.template("common", "msmtprc.j2")
CHRONY = roles.template("common", "chrony.sources.j2")
TIMESYNCD = roles.template("common", "timesyncd.conf.j2")
TIME_SERVERS = roles.assert_conditions("roles/common/tasks/main.yml", "Check the time servers")
HARD = roles.role_vars("hardening")
COMMON = roles.role_vars("common")
KEY_CHARS = "akz09_.-= \n#*"
VALUE_CHARS = "az09_.:/- \n=#|"
EMAIL_CHARS = "az09._+-@ \n\"';{}"
HOST_CHARS = "az09.:- \n\"#"
USER_CHARS = "az09._@+=- \n\"#"
TIME_CHARS = "az09.:-[] \n\t#%;"


def email(fdp):
    """Mostly plain addresses, sometimes with characters the role must refuse."""
    if fdp.ConsumeIntInRange(0, 3) == 0:
        return roles.from_alphabet(fdp, EMAIL_CHARS, 20)
    return roles.from_alphabet(fdp, "az09._+-", 8) + "@" + roles.hostname(fdp) + "." + roles.hostname(fdp)


def sysctl_key(fdp):
    """Mostly dotted names, sometimes with characters the role must refuse."""
    if fdp.ConsumeIntInRange(0, 3) == 0:
        return roles.from_alphabet(fdp, KEY_CHARS, 20)
    return ".".join(["net"] + [roles.from_alphabet(fdp, "az09_-", 6) or "x" for _ in range(fdp.ConsumeIntInRange(1, 3))])


def sysctl(fdp):
    def entries(n):
        out = {}
        for _ in range(n):
            value = None if fdp.ConsumeIntInRange(0, 4) == 0 else (
                fdp.ConsumeIntInRange(-5, 70000) if fdp.ConsumeBool() else roles.from_alphabet(fdp, VALUE_CHARS, 12))
            out[sysctl_key(fdp)] = value
        return out

    base = {k: v for k, v in entries(fdp.ConsumeIntInRange(0, 5)).items() if v is not None}
    extra = entries(fdp.ConsumeIntInRange(0, 4))
    settings = SYSCTL_SETTINGS.render(hardening_sysctl=base, hardening_sysctl_extra=extra)
    expected = {**base, **extra}
    assert settings == {k: v for k, v in expected.items() if v is not None}, (settings, expected)
    if not (all(roles.matches(HARD["hardening_sysctl_key_pattern"], k) for k in settings)
            and all(roles.matches(HARD["hardening_sysctl_value_pattern"], str(v)) for v in settings.values())):
        return
    lines = [line for line in SYSCTL.render(hardening_sysctl_settings=settings, ansible_managed="managed").splitlines()
             if line and not line.startswith("#")]
    parsed = {}
    for line in lines:
        key, sep, value = line.partition(" = ")
        assert sep and key not in parsed and "=" not in value, line
        parsed[key] = value
    assert parsed == {k: str(v) for k, v in settings.items()}, (parsed, settings)


def apt(fdp):
    values = {
        "unattended_upgrades_origins": ["${distro_id}:${distro_codename}"],
        "unattended_upgrades_remove_unused": True,
        "unattended_upgrades_auto_reboot": False,
        "unattended_upgrades_reboot_time": "04:00",
        "unattended_upgrades_mail": email(fdp) if fdp.ConsumeBool() else "",
        "unattended_upgrades_mail_report": fdp.PickValueInList(COMMON["common_mail_report_values"]),
        "ansible_managed": "managed",
    }
    mail = values["unattended_upgrades_mail"]
    if mail and not roles.matches(COMMON["common_email_pattern"], mail):
        return
    options = [line for line in APT.render(values).splitlines() if line.startswith("Unattended-Upgrade::")]
    mail_lines = [line for line in options if line.startswith("Unattended-Upgrade::Mail")]
    if mail:
        assert mail_lines == [f'Unattended-Upgrade::Mail "{mail}";',
                              f'Unattended-Upgrade::MailReport "{values["unattended_upgrades_mail_report"]}";'], options
    else:
        assert mail_lines == [], options
    # Each option is one statement: a quote or ";" in a value would end it early.
    assert all(line.count('"') == 2 and line.endswith('";') for line in options[1:]), options


def msmtp(fdp):
    values = {
        "mail_relay_host": roles.hostname(fdp) if fdp.ConsumeBool() else roles.from_alphabet(fdp, HOST_CHARS, 20),
        "mail_relay_port": fdp.ConsumeIntInRange(1, 65535),
        "mail_relay_tls": fdp.PickValueInList(COMMON["common_mail_tls_values"]),
        "mail_relay_from": email(fdp),
        "mail_relay_user": roles.from_alphabet(fdp, USER_CHARS, 16),
        "common_msmtp_password_file": COMMON["common_msmtp_password_file"],
        "ansible_managed": "managed",
    }
    if not (roles.matches(COMMON["common_mail_host_pattern"], values["mail_relay_host"])
            and roles.matches(COMMON["common_email_pattern"], values["mail_relay_from"])
            and roles.matches(COMMON["common_mail_user_pattern"], values["mail_relay_user"])):
        return
    sections = {}
    current = None
    for line in MSMTP.render(values).splitlines():
        if not line or line.startswith("#"):
            continue
        command, _, argument = line.partition(" ")
        if command in ("defaults", "account"):
            current = sections.setdefault(line, {})
            continue
        assert current is not None and command not in current, line
        current[command] = argument
    account = sections.pop("account default")
    expected = {"host": values["mail_relay_host"], "port": str(values["mail_relay_port"]),
                "from": values["mail_relay_from"]}
    if values["mail_relay_user"]:
        expected.update({"auth": "on", "user": values["mail_relay_user"],
                         "passwordeval": f'"cat {COMMON["common_msmtp_password_file"]}"'})
    else:
        expected["auth"] = "off"
    assert account == expected, (account, expected)
    assert list(sections) == ["defaults"], sections
    tls = sections["defaults"]
    assert tls["tls"] == ("off" if values["mail_relay_tls"] == "off" else "on"), tls


def time_server(fdp):
    """Host names, IPv4 and IPv6 addresses, sometimes with characters the role must refuse, or not a string."""
    kind = fdp.ConsumeIntInRange(0, 5)
    if kind == 0:
        return roles.from_alphabet(fdp, TIME_CHARS, 30)
    if kind == 1:
        return fdp.ConsumeIntInRange(0, 1000)
    if kind == 2:
        return roles.ipv4(fdp)
    if kind == 3:
        return ":".join(roles.from_alphabet(fdp, "09af", 4) for _ in range(fdp.ConsumeIntInRange(2, 8)))
    return ".".join(roles.hostname(fdp) for _ in range(fdp.ConsumeIntInRange(1, 3)))


def time_sources(fdp):
    def entries():
        if fdp.ConsumeIntInRange(0, 9) == 0:  # a single value where a list belongs
            return time_server(fdp)
        return [time_server(fdp) for _ in range(fdp.ConsumeIntInRange(0, 4))]

    values = {"time_sync_servers": entries(), "time_sync_pools": entries(),
              "common_time_server_pattern": COMMON["common_time_server_pattern"], "ansible_managed": "managed"}
    if not roles.accepted(TIME_SERVERS, values):
        return
    servers, pools = values["time_sync_servers"], values["time_sync_pools"]
    for name in servers + pools:
        assert roles.matches(COMMON["common_time_server_pattern"], name), name
    directives = [line.split(" ") for line in CHRONY.render(values).splitlines() if line and not line.startswith("#")]
    assert directives == [["server", s, "iburst"] for s in servers] + [["pool", p, "iburst"] for p in pools], directives
    lines = [line for line in TIMESYNCD.render(values).splitlines() if line and not line.startswith("#")]
    assert lines == ["[Time]", "NTP=" + " ".join(servers + pools)], lines
    assert lines[1].split("=", 1)[1].split() == servers + pools, lines


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    fdp.PickValueInList([sysctl, apt, msmtp, time_sources])(fdp)


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
