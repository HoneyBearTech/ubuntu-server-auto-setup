#!/usr/bin/env python3
"""Fuzz target: what servers report to the updates playbook, and its maintenance windows
(roles/updates/filter_plugins/usas_updates.py).

Properties, for any output a server could send (apt-get -s, apt-mark showhold, needrestart -b, apt-config dump,
package_facts and service_facts results) and any window:
- the parsers never raise, every package and service name they return matches the role's patterns
  (roles/updates/vars/main.yml), every version is plain, and no list is longer than the cap;
- choosing the updates never names a held or excluded package, a package that isn't installed yet, or (for
  security) one whose candidate isn't from a security suite, and every pending update ends up in exactly one of
  install, excluded and not_security, unless it is held or new;
- report text has no control or direction characters and respects its length limit;
- no window is always open; a window either has problems or is valid, a valid one is open exactly when now falls inside one of its
  openings (worked out here minute by minute in local time, across daylight-saving changes), and an invalid one
  is never open;
- the filter's patterns are the role's, and the health URL pattern is the webhook's.
"""

import datetime
import re
import sys
import zoneinfo

import atheris

with atheris.instrument_imports():
    import roles

U = roles.filter_plugin("updates", "usas_updates")
V = roles.role_vars("updates")
PACKAGE = re.compile(V["updates_package_pattern"])
SERVICE = re.compile(V["updates_service_pattern"])
VERSION = re.compile(r"^[A-Za-z0-9.+~:-]{1,128}\Z")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
NAMES = ["usas-test-tool", "libc6", "linux-image-6.8.0-1-generic", "libfoo:i386", "Bad_Name", "a", "x" * 200,
         "pkg\nInst evil", ""]
SUITES = ["Ubuntu:26.04/resolute-security", "Ubuntu:26.04/resolute-updates", "UbuntuESM:x/resolute-infra-security",
          "Debian-Security:13/stable-security", "USAS-Test:test-updates", "weird:", "a/b/c-security"]
# Daylight-saving changes in 2026 (Europe, the US, Lord Howe's half hour), where windows are easiest to get wrong.
CHANGES = [datetime.datetime(2026, 3, 29, 1, tzinfo=datetime.timezone.utc),
           datetime.datetime(2026, 10, 25, 1, tzinfo=datetime.timezone.utc),
           datetime.datetime(2026, 3, 8, 8, tzinfo=datetime.timezone.utc),
           datetime.datetime(2026, 11, 1, 7, tzinfo=datetime.timezone.utc),
           datetime.datetime(2026, 4, 4, 15, tzinfo=datetime.timezone.utc),
           datetime.datetime(2026, 10, 3, 15, 30, tzinfo=datetime.timezone.utc)]
ZONES = ["UTC", "Europe/Berlin", "America/Chicago", "Australia/Lord_Howe", "Asia/Kathmandu", "Mars/Base", "../etc"]

assert U.PACKAGE.pattern == V["updates_package_pattern"]
assert U.SERVICE.pattern == V["updates_service_pattern"]
assert V["updates_url_pattern"] == roles.role_vars("fail2ban")["fail2ban_webhook_url_pattern"]
for name in ("updates_package_pattern", "updates_package_glob_pattern", "updates_service_pattern",
             "updates_url_pattern", "updates_seconds_pattern"):
    assert V[name].endswith(r"\Z"), name


def line(fdp):
    kind = fdp.ConsumeIntInRange(0, 6)
    name = fdp.PickValueInList(NAMES) if fdp.ConsumeBool() else fdp.ConsumeUnicodeNoSurrogates(20)
    version = fdp.PickValueInList(["1.0", "2:1.2-0ubuntu1~24.04.1", "", "1 0", "[x]"])
    origins = ", ".join(fdp.PickValueInList(SUITES) for _ in range(fdp.ConsumeIntInRange(0, 3)))
    if kind == 0:
        return f"Inst {name} [{version}] ({fdp.PickValueInList(['1.1', '3.0~rc1', ''])} {origins} [amd64])"
    if kind == 1:
        return f"Inst {name} ({version} {origins} [all])"
    if kind == 2:
        return f"Remv {name} [{version}]"
    if kind == 3:
        return fdp.PickValueInList(list(U.SECTIONS)) if fdp.ConsumeBool() else "  " + " ".join([name] * 3)
    if kind == 4:
        return f"NEEDRESTART-{fdp.PickValueInList(['SVC', 'KSTA', 'KEXP', 'VER'])}: {name}{version}"
    if kind == 5:
        key = fdp.PickValueInList(["APT::Periodic::Unattended-Upgrade", "Unattended-Upgrade::Allowed-Origins:",
                                   "Unattended-Upgrade::Automatic-Reboot", "Unattended-Upgrade::Automatic-Reboot-Time"])
        return f'{key} "{fdp.PickValueInList(["1", "0", "true", "${distro_codename}-updates", name, "7d", "always"])}";'
    return fdp.ConsumeUnicodeNoSurrogates(40)


def clean(text, limit):
    assert not CONTROL.search(text) and len(text) <= max(limit, 1), (text, limit)


def window(fdp):
    w = {}
    if fdp.ConsumeBool():
        w["days"] = [fdp.PickValueInList(list(U.DAYS) + ["xyz"]) for _ in range(fdp.ConsumeIntInRange(0, 3))]
    if fdp.ConsumeIntInRange(0, 9):
        w["start"] = fdp.PickValueInList(["02:00", "02:30", "00:00", "23:59", "01:30", "24:00", "2:00"])
    if fdp.ConsumeIntInRange(0, 9):
        w["duration"] = fdp.PickValueInList(["3h", "90m", "1h30m", "24h", "99h59m", "0h", "1d", "1m"])
    if fdp.ConsumeIntInRange(0, 9):
        w["timezone"] = fdp.PickValueInList(ZONES)
    if not fdp.ConsumeIntInRange(0, 9):
        w["extra"] = 1
    return w


def open_by_minutes(w, now):
    """The window, worked out the slow way: walk local wall-clock time, a minute at a time, from each listed
    day's start; the window is open for duration minutes of real time from the first such moment."""
    zone = zoneinfo.ZoneInfo(w.get("timezone", "UTC"))
    hours, minutes = map(int, w["start"].split(":"))
    m = re.match(r"^(?:(\d+)h)?(?:(\d+)m)?$", w["duration"])
    length = datetime.timedelta(hours=int(m.group(1) or 0), minutes=int(m.group(2) or 0))
    local = now.astimezone(zone)
    for offset in range(-8, 2):
        day = local.date() + datetime.timedelta(days=offset)
        if U.DAYS[day.weekday()] not in w.get("days", list(U.DAYS)):
            continue
        # The first real instant whose wall-clock time on that day is at or after the start time.
        wanted = datetime.datetime(day.year, day.month, day.day, hours, minutes)
        probe = datetime.datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(datetime.timezone.utc)
        probe -= datetime.timedelta(hours=3)
        while probe.astimezone(zone).replace(tzinfo=None) < wanted:
            probe += datetime.timedelta(minutes=1)
        if probe <= now < probe + length:
            return True
    return False


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    text = "\n".join(line(fdp) for _ in range(fdp.ConsumeIntInRange(0, 30)))
    limit = fdp.ConsumeIntInRange(0, 6)

    sim = U.usas_apt_simulation(text, limit)
    for key in ("upgrades", "removals", "kept_back", "phased"):
        assert len(sim[key]) <= limit, (key, sim)
    for u in sim["upgrades"]:
        assert PACKAGE.match(u["name"]) and VERSION.match(u["candidate"]), u
        assert u["current"] == "" or VERSION.match(u["current"]), u
        assert u["new"] == (u["current"] == "") and u["security"] == any(s.endswith("-security") for s in u["suites"])
    assert all(PACKAGE.match(n) for k in ("removals", "kept_back", "phased") for n in sim[k]), sim
    assert len({u["name"] for u in sim["upgrades"]}) == len(sim["upgrades"]), sim

    held = U.usas_package_names(text, limit)
    assert len(held) <= limit and all(PACKAGE.match(n) for n in held) and len(set(held)) == len(held)

    scope = fdp.PickValueInList(["all", "security"])
    excludes = [fdp.PickValueInList(["usas-test-*", "lib*", "linux-image-*", "*"]) for _ in range(fdp.ConsumeIntInRange(0, 2))]
    chosen = U.usas_select_updates(sim["upgrades"], scope, excludes, held)
    install, excluded, other = chosen["install"], chosen["excluded"], chosen["not_security"]
    for u in sim["upgrades"]:
        name = u["name"]
        buckets = [name in install, name in excluded, name in other]
        if u["new"] or name in held:
            assert not any(buckets), (u, chosen)
        else:
            assert sum(buckets) == 1, (u, chosen)
    assert not any(U.usas_glob_any(n, excludes) for n in install), chosen
    if scope == "security":
        security = {u["name"] for u in sim["upgrades"] if u["security"]}
        assert set(install) <= security, chosen

    failed_units = U.usas_failed_units(text, limit + 1)
    assert len(failed_units) <= limit + 1 and len(set(failed_units)) == len(failed_units)
    assert all(SERVICE.match(u) for u in failed_units), failed_units

    restart = U.usas_needrestart(text)
    assert all(SERVICE.match(s) for s in restart["services"]) and restart["kernel_status"] in (None, 0, 1, 2, 3)
    clean(restart["kernel_expected"], 128)

    auto = U.usas_apt_auto_updates(text, fdp.PickValueInList(["enabled", "disabled", "masked", ""]), fdp.ConsumeBool())
    clean(auto["line"], 300)
    assert auto["line"].startswith("Automatic: ") and (auto["enabled"] or not auto["reboot"])

    clean(U.usas_clean_text(text, limit + 1), limit + 1)
    clean(U.usas_hint_warning(text, fdp.PickValueInList(["Ubuntu", "Debian", "", "U\nbuntu"]), text), 200)

    services = {fdp.PickValueInList(NAMES + ["ssh.service", "cron.service"]): {
        "state": fdp.PickValueInList(["running", "stopped", "failed", "inactive"]),
        "status": fdp.PickValueInList(["enabled", "static", "disabled"])} for _ in range(fdp.ConsumeIntInRange(0, 5))}
    running = U.usas_running_services(services)
    assert all(SERVICE.match(s) and services[s]["state"] == "running" and services[s]["status"] == "enabled"
               for s in running)
    assert U.usas_health_problems(running, services) == []

    results = [{"host": fdp.PickValueInList(NAMES), "status": fdp.PickValueInList(list(U.STATUSES) + ["weird"]),
                "reason": text[:300], "upgraded": [1] * fdp.ConsumeIntInRange(0, 2)}
               for _ in range(fdp.ConsumeIntInRange(0, 4))]
    summary = U.usas_updates_summary(results)
    assert summary["total"] == len(results) and sum(summary["counts"].values()) == len(results)
    mode = fdp.PickValueInList(["check", "apply"])
    message = U.usas_notify_message(summary, {"id": text[:50], "mode": mode, "started": text[:30]})
    clean(message["subject"], 150)
    for body_line in message["body"].splitlines():
        clean(body_line, 300)
    errors = summary["counts"]["failed"] + summary["counts"]["unreachable"]
    changed = summary["counts"]["pending" if mode == "check" else "updated"]
    assert U.usas_notify_due(summary, mode, "always")
    assert U.usas_notify_due(summary, mode, "only-on-error") == (errors > 0)
    assert U.usas_notify_due(summary, mode, "on-change") == (errors > 0 or changed > 0)
    assert not U.usas_notify_due(summary, mode, "sometimes")

    w = window(fdp)
    problems = U.usas_window_problems(w)
    if fdp.ConsumeBool():
        now = fdp.PickValueInList(CHANGES) + datetime.timedelta(minutes=fdp.ConsumeIntInRange(-36 * 60, 36 * 60))
    else:
        now = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(
            minutes=fdp.ConsumeIntInRange(0, 2 * 366 * 24 * 60))
    inside = U.usas_in_window(w, now.isoformat())
    if not w:
        assert inside is True and not problems  # no window: any time
    elif problems:
        assert inside is False, (w, problems)
    else:
        assert inside == open_by_minutes(w, now), (w, now, inside)
    clean(U.usas_window_text(w), 80)


atheris.Setup(sys.argv, one_input)
atheris.Fuzz()
