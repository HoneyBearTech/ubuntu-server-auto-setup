#!/usr/bin/env python3
"""Fuzz target: the optional hardening and notification files built from inventory values: the sysctl
drop-in, unattended-upgrades' mail settings, msmtp's configuration and the firewall's DOCKER-USER rules.

Property: for any values the hardening, common and firewall roles accept, each setting appears exactly once with
exactly the given value, and nothing else is added (a value can't inject another sysctl key, apt option, msmtp
command, or iptables rule or option).
"""

import sys

import atheris

with atheris.instrument_imports():
    import roles

SYSCTL = roles.template("hardening", "sysctl.conf.j2")
SYSCTL_SETTINGS = roles.set_fact_expression("roles/hardening/tasks/sysctl.yml", "hardening_sysctl_settings")
APT = roles.template("common", "52unattended-upgrades-local.j2")
MSMTP = roles.template("common", "msmtprc.j2")
DOCKER_USER = roles.template("firewall", "docker-user.rules.j2")
DOCKER_TASKS = "roles/firewall/tasks/docker_checks.yml"
DOCKER_SOURCES_OK = roles.assert_conditions(DOCKER_TASKS, "Check the Docker port sources")
DOCKER_RULE_OK = roles.assert_conditions(DOCKER_TASKS, "Check the Docker port rules")
HARD = roles.role_vars("hardening")
COMMON = roles.role_vars("common")
FIREWALL = roles.role_vars("firewall")
KEY_CHARS = "akz09_.-= \n#*"
VALUE_CHARS = "az09_.:/- \n=#|"
EMAIL_CHARS = "az09._+-@ \n\"';{}"
HOST_CHARS = "az09.:- \n\"#"
USER_CHARS = "az09._@+=- \n\"#"
NET_CHARS = "09af:./ \n-j#"
PORT_CHARS = "0159: \n-j"


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


def network(fdp):
    """Mostly IPv4 or IPv6 addresses and networks, sometimes with characters the role must refuse."""
    choice = fdp.ConsumeIntInRange(0, 5)
    if choice == 0:
        return roles.from_alphabet(fdp, NET_CHARS, 24)
    if choice == 1:
        return fdp.ConsumeIntInRange(0, 300)
    if choice == 2:
        prefix = f"/{fdp.ConsumeIntInRange(0, 130)}" if fdp.ConsumeBool() else ""
        return roles.from_alphabet(fdp, "0123456789abcdefABCDEF:", 24) + prefix
    return roles.ipv4(fdp) + (f"/{fdp.ConsumeIntInRange(0, 40)}" if fdp.ConsumeBool() else "")


def docker_rule(fdp):
    """A firewall_docker_allow entry: mostly well formed, sometimes with a bad value, key or type."""
    if fdp.ConsumeIntInRange(0, 15) == 0:
        return roles.from_alphabet(fdp, PORT_CHARS, 8)
    rule = {}
    choice = fdp.ConsumeIntInRange(0, 4)
    if choice == 0:
        rule["port"] = roles.from_alphabet(fdp, PORT_CHARS, 12)
    elif choice == 1:
        rule["port"] = f"{fdp.ConsumeIntInRange(0, 70000)}:{fdp.ConsumeIntInRange(0, 70000)}"
    elif choice != 2 or fdp.ConsumeBool():
        rule["port"] = fdp.ConsumeIntInRange(0, 70000) if fdp.ConsumeBool() else str(fdp.ConsumeIntInRange(0, 70000))
    if fdp.ConsumeBool():
        rule["proto"] = fdp.PickValueInList(["tcp", "udp", "any", "TCP", "icmp", "tcp -j ACCEPT", 6])
    if fdp.ConsumeBool():
        rule["from"] = "any" if fdp.ConsumeBool() else network(fdp)
    if fdp.ConsumeIntInRange(0, 15) == 0:
        rule["comment"] = "x"
    return rule


def docker_user(fdp):
    sources = [network(fdp) for _ in range(fdp.ConsumeIntInRange(0, 4))]
    allow = [docker_rule(fdp) for _ in range(fdp.ConsumeIntInRange(0, 4))]
    context = {**FIREWALL, "firewall_docker_sources": sources, "firewall_docker_allow": allow}
    if not (roles.accepted(DOCKER_SOURCES_OK, context)
            and all(roles.accepted(DOCKER_RULE_OK, {**context, "item": rule}) for rule in allow)):
        return
    logging = fdp.PickValueInList(["off", "low", "full", False, True])
    for family in (4, 6):
        text = DOCKER_USER.render(firewall_docker_family=family, firewall_logging=logging, **context)
        v6 = family == 6
        # What the rules must be, built here from the accepted values: one line each, in order, nothing more.
        expected = ["*filter", ":DOCKER-USER - [0:0]",
                    "-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN",
                    "-A DOCKER-USER -m conntrack ! --ctstate DNAT -j RETURN"]
        expected += [f"-A DOCKER-USER -s {s} -j RETURN" for s in sources if (":" in s) == v6]
        for rule in allow:
            source = str(rule.get("from", "any"))
            if source != "any" and (":" in source) != v6:
                continue
            proto = rule.get("proto", "tcp")
            for p in (["tcp", "udp"] if proto == "any" else [proto]):
                expected.append(f"-A DOCKER-USER -p {p} -m conntrack --ctorigdstport {rule['port']}"
                                + ("" if source == "any" else f" -s {source}") + " -j RETURN")
        if str(logging).lower() not in ("off", "false"):
            expected.append('-A DOCKER-USER -m limit --limit 3/min --limit-burst 10 -j LOG '
                            '--log-prefix "[USAS DOCKER BLOCK] "')
        expected += ["-A DOCKER-USER -j DROP", "COMMIT"]
        assert text.splitlines() == expected, (text, expected)
        # Every value is one word of plain characters: none can end a rule, add one, or add an option.
        for line in text.splitlines()[2:-1]:
            words = line.split(" ")
            assert words[:2] == ["-A", "DOCKER-USER"] and "" not in words, line
            values = [words[i + 1] for i, w in enumerate(words) if w in ("-s", "--ctorigdstport", "-p")]
            assert all(not v.startswith("-") and set(v) <= set("0123456789abcdefABCDEF:./tcpu") for v in values), line


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    fdp.PickValueInList([sysctl, apt, msmtp, docker_user])(fdp)


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
