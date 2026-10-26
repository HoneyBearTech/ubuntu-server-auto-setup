"""Filters for the updates playbook: reading what a server reports about its packages, deciding what to install,
and checking maintenance windows.

Everything a server reports comes from a host that may be hostile, or just unusual: it is parsed defensively,
package and service names are checked against the role's patterns (roles/updates/vars/main.yml), control
characters are removed, and every list is capped. The results are for the report only; what gets installed is
always the operator's selection applied to names that passed the checks. tests/fuzz/fuzz_update_reports.py
checks the parsers and the window logic.
"""

import datetime
import fnmatch
import re
import zoneinfo

DOCUMENTATION = """
name: usas_updates
short_description: Parse package-manager output and check maintenance windows for the updates playbook
description: See the module docstring and roles/updates/tasks/.
"""

# The same patterns as roles/updates/vars/main.yml (tests/fuzz/fuzz_update_reports.py checks they agree).
PACKAGE = re.compile(r"^[a-z0-9][a-z0-9+.-]{1,127}(:[a-z0-9][a-z0-9-]{0,31})?\Z")
VERSION = re.compile(r"^[A-Za-z0-9.+~:-]{1,128}\Z")
SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._:\\-]{0,254}\Z")
SUITE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+-]{0,127}\Z")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
MAX_ENTRIES = 5000
MAX_TEXT = 200
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TIME = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])\Z")
DURATION = re.compile(r"^(?:([1-9][0-9]?)h)?(?:([1-9][0-9]?)m)?\Z")
TIMEZONE = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]{0,63}(/[A-Za-z0-9_+-]{1,64}){0,2}\Z")
MAX_WINDOW = datetime.timedelta(days=7)

# "Inst name [current] (candidate Label:Version/suite, Label:suite [arch])", or without [current] for a package
# that isn't installed yet; "Remv name [version]".
INST = re.compile(r"^Inst (\S+) (?:\[(\S+)\] )?\((\S+) ([^\[\]()]*?)(?: \[[^\]]*\])?\)")
REMV = re.compile(r"^Remv (\S+)(?: \[(\S+)\])?")
SECTIONS = {
    "The following packages have been kept back:": "kept_back",
    "The following upgrades have been deferred due to phasing:": "phased",
}


def usas_clean_text(value, limit=MAX_TEXT):
    """A string a server reported, made safe for a report: control and direction characters removed, whitespace
    collapsed, at most limit characters."""
    if value is None:
        return ""
    cleaned = " ".join(CONTROL.sub(" ", str(value)).split())
    limit = max(int(limit), 1)
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def usas_hint_warning(hint, distribution, facts_text):
    """A warning when the guest agent's OS hint doesn't name the distribution the facts found; "" otherwise."""
    hint, distribution = usas_clean_text(hint, 80), usas_clean_text(distribution, 40)
    if not hint or distribution.lower() in hint.lower():
        return ""
    return f"the guest reported {hint}, the facts say {usas_clean_text(facts_text, 80)}"


def _lines(text):
    return str(text or "").splitlines()


def _suites(origins):
    """The suites ("jammy-security") of an Inst line's origins ("Ubuntu:22.04/jammy-security, USAS-Test:x")."""
    found = []
    for origin in str(origins).split(","):
        origin = origin.strip()
        if not origin:
            continue
        suite = origin.rsplit("/", 1)[1] if "/" in origin else origin.split(":", 1)[-1]
        if SUITE.match(suite) and suite not in found:
            found.append(suite)
    return found[:8]


def usas_apt_simulation(stdout, limit=MAX_ENTRIES):
    """What apt-get -s (upgrade, dist-upgrade or install) would do, from its output in the C locale:
    {"upgrades": [{"name", "current", "candidate", "suites", "security", "new"}], "removals": [names],
    "kept_back": [names], "phased": [names], "truncated": bool, "skipped": int}. A package counts as a security
    update when its candidate comes from a *-security suite (Ubuntu's and Debian's security pockets, Ubuntu Pro's
    ESM). Entries whose name or version doesn't pass the checks are counted in skipped and left out."""
    limit = max(min(int(limit), MAX_ENTRIES), 0)
    result = {"upgrades": [], "removals": [], "kept_back": [], "phased": [], "truncated": False, "skipped": 0}
    seen = set()
    section = None
    for line in _lines(stdout):
        if section and line.startswith(" "):
            for name in line.split():
                if PACKAGE.match(name) and name not in result[section]:
                    if len(result[section]) < limit:
                        result[section].append(name)
                    else:
                        result["truncated"] = True
            continue
        section = SECTIONS.get(line.strip())
        if section:
            continue
        match = INST.match(line)
        if match:
            name, current, candidate, origins = match.groups()
            if not PACKAGE.match(name) or not VERSION.match(candidate) or (current and not VERSION.match(current)):
                result["skipped"] += 1
                continue
            if name in seen:
                continue
            if len(result["upgrades"]) >= limit:
                result["truncated"] = True
                continue
            seen.add(name)
            suites = _suites(origins)
            result["upgrades"].append({"name": name, "current": current or "", "candidate": candidate,
                                       "suites": suites, "security": any(s.endswith("-security") for s in suites),
                                       "new": not current})
            continue
        match = REMV.match(line)
        if match:
            if not PACKAGE.match(match.group(1)):
                result["skipped"] += 1
            elif match.group(1) not in result["removals"]:
                if len(result["removals"]) < limit:
                    result["removals"].append(match.group(1))
                else:
                    result["truncated"] = True
    return result


def usas_package_names(stdout, limit=MAX_ENTRIES):
    """Package names, one per line (apt-mark showhold, reboot-required.pkgs): those that pass the check, once
    each, at most limit."""
    names = []
    limit = max(min(int(limit), MAX_ENTRIES), 0)
    for line in _lines(stdout):
        name = line.strip()
        if len(names) >= limit:
            break
        if PACKAGE.match(name) and name not in names:
            names.append(name)
    return names


def usas_glob_any(name, patterns):
    """Whether a package name matches one of the shell globs (case-sensitive, as package names are)."""
    return any(fnmatch.fnmatchcase(str(name), str(p)) for p in patterns or [])


def usas_matching(names, patterns):
    """The names that match one of the shell globs, in their order."""
    return [n for n in names or [] if usas_glob_any(n, patterns)]


def usas_select_updates(upgrades, scope="all", excludes=(), held=()):
    """Which pending updates to install: {"install": [names], "excluded": [names], "not_security": [names]}.
    Packages apt would newly install (dependencies) aren't named; installing what needs them brings them in.
    security keeps only updates whose candidate comes from a security suite; excludes are shell globs."""
    install, excluded, not_security = [], [], []
    for entry in upgrades or []:
        if not isinstance(entry, dict) or entry.get("new"):
            continue
        name = str(entry.get("name", ""))
        if not PACKAGE.match(name) or name in held:
            continue
        if usas_glob_any(name, excludes):
            excluded.append(name)
        elif scope == "security" and not entry.get("security"):
            not_security.append(name)
        else:
            install.append(name)
    return {"install": install, "excluded": excluded, "not_security": not_security}


def usas_needrestart(stdout):
    """needrestart -b's batch output: {"services": [units], "kernel_status": 0-3 or None, "kernel_expected": str}.
    KSTA is 1 when the newest kernel runs, 2 or 3 when a newer one is installed (an ABI-compatible or a
    version upgrade), 0 when unknown."""
    services, status, expected = [], None, ""
    for line in _lines(stdout):
        key, _, value = line.partition(":")
        value = value.strip()
        if key == "NEEDRESTART-SVC" and SERVICE.match(value) and value not in services and len(services) < 500:
            services.append(value)
        elif key == "NEEDRESTART-KSTA" and value in ("0", "1", "2", "3"):
            status = int(value)
        elif key == "NEEDRESTART-KEXP":
            expected = usas_clean_text(value, 128)
    return {"services": services, "kernel_status": status, "kernel_expected": expected}


def _apt_config(stdout):
    """apt-config dump: {key: [values]} (a list item "Key:: value" is added to Key)."""
    found = {}
    for line in _lines(stdout)[:20000]:
        match = re.match(r'^([A-Za-z0-9:_-]+) "(.*)";$', line.strip())
        if match:
            key = match.group(1).rstrip(":")
            found.setdefault(key, [])
            if match.group(2):
                found[key].append(match.group(2))
    return found


def _periodic(value):
    """An APT::Periodic interval in days ("1", "7", "1d", "always"), or 0 when off."""
    value = str(value).strip().lower()
    if value == "always":
        return 1
    match = re.match(r"^([0-9]{1,4})d?\Z", value)
    return int(match.group(1)) if match else 0


def usas_apt_auto_updates(dump, timer_enabled="", installed=False):
    """What unattended-upgrades does on a host, from apt-config dump, whether apt-daily-upgrade.timer is enabled
    and whether the package is installed: {"enabled", "every_days", "scope", "reboot", "reboot_time", "line"}."""
    config = _apt_config(dump)

    def last(key, default=""):
        values = config.get(key) or [default]
        return values[-1]

    every = _periodic(last("APT::Periodic::Unattended-Upgrade", "0"))
    timer = str(timer_enabled).strip() in ("enabled", "enabled-runtime", "static", "indirect", "alias")
    enabled = bool(installed) and every > 0 and timer
    origins = (config.get("Unattended-Upgrade::Allowed-Origins", [])
               + config.get("Unattended-Upgrade::Origins-Pattern", []))
    scope = "security and updates" if any("-updates" in o for o in origins) else "security"
    reboot = last("Unattended-Upgrade::Automatic-Reboot", "false").lower() in ("1", "true", "yes", "on")
    reboot_time = usas_clean_text(last("Unattended-Upgrade::Automatic-Reboot-Time", ""), 16)
    if not enabled:
        why = ("not installed" if not installed else "timer off" if not timer else "turned off")
        line = f"Automatic: off ({why})"
    else:
        when = "daily" if every == 1 else f"every {every} days"
        line = f"Automatic: {scope} {when}, " + (f"reboot at {reboot_time or 'once done'}" if reboot else "no reboot")
    return {"enabled": enabled, "every_days": every if enabled else 0, "scope": scope if enabled else "",
            "reboot": enabled and reboot, "reboot_time": reboot_time if enabled and reboot else "", "line": line}


def usas_package_changes(before, after):
    """What changed between two package_facts results ({name: [{"version": ...}, ...]}): a list of
    {"name", "from", "to"}, where from is "" for a package that is new."""
    changes = []

    def versions(facts, name):
        entries = (facts or {}).get(name) or []
        return sorted({str(e.get("version", "")) for e in entries if isinstance(e, dict)})

    for name in sorted(after or {}):
        if not PACKAGE.match(str(name)):
            continue
        old, new = versions(before, name), versions(after, name)
        if old != new and new:
            changes.append({"name": name, "from": ", ".join(old), "to": ", ".join(new)})
        if len(changes) >= MAX_ENTRIES:
            break
    return changes


def usas_running_services(services):
    """The enabled services that run, from service_facts' ansible_facts.services: sorted unit names. Services
    started on demand (D-Bus, sockets) are "static", not "enabled", so their stopping isn't a problem."""
    return sorted(name for name, info in (services or {}).items()
                  if isinstance(info, dict) and info.get("state") == "running" and info.get("status") == "enabled"
                  and SERVICE.match(str(name)))


def usas_health_problems(running_before, services_after, required=(), ignore=()):
    """The services that ran before and don't now, and the required ones that don't run: sorted unit names,
    without those that match the ignore globs."""
    after = services_after or {}

    def runs(name):
        info = after.get(name) or after.get(f"{name}.service") or {}
        return isinstance(info, dict) and info.get("state") == "running"

    wanted = list(running_before or []) + [str(r) for r in required or []]
    return sorted({str(n) for n in wanted if not runs(n) and not usas_glob_any(n, ignore)})


def usas_failed_units(stdout, limit=50):
    """The unit names of systemctl list-units --failed --plain --no-legend (the first word of each line), those
    that pass the unit-name check, once each, at most limit."""
    units = []
    for line in _lines(stdout):
        words = line.split()
        name = words[0] if words and words[0] != "●" else (words[1] if len(words) > 1 else "")
        if SERVICE.match(name) and name not in units:
            units.append(name)
        if len(units) >= limit:
            break
    return units


SYSTEM_STATES = {"running": 0, "degraded": 1}


def usas_system_state_worse(before, after):
    """Whether systemctl is-system-running reports something worse after than before (running, then degraded,
    then anything else: maintenance, starting, stopping, offline, unknown)."""
    def rank(state):
        return SYSTEM_STATES.get(str(state).strip(), 2)
    return rank(after) > rank(before)


def _duration(value):
    match = DURATION.match(str(value or ""))
    if not match or not any(match.groups()):
        return None
    hours, minutes = (int(g) if g else 0 for g in match.groups())
    return datetime.timedelta(hours=hours, minutes=minutes)


def usas_window_problems(window, name="updates_window"):
    """What is wrong with a maintenance window ({"days": [...], "start": "HH:MM", "duration": "3h",
    "timezone": "Europe/Berlin"}), as messages; [] when it is fine or empty (no window: any time)."""
    if not window:
        return []
    if not isinstance(window, dict):
        return [f"{name} must be a mapping with days, start, duration and timezone"]
    problems = []
    unknown = sorted(str(k) for k in window if k not in ("days", "start", "duration", "timezone"))
    if unknown:
        problems.append(f"{name} has keys it doesn't know: {', '.join(usas_clean_text(k, 32) for k in unknown)}")
    days = window.get("days", list(DAYS))
    if not isinstance(days, list) or not days or any(str(d) not in DAYS for d in days):
        problems.append(f"{name}.days must be a list of {', '.join(DAYS)}")
    if not TIME.match(str(window.get("start", ""))):
        problems.append(f"{name}.start must be a time, HH:MM")
    duration = _duration(window.get("duration"))
    if duration is None or duration > MAX_WINDOW:
        problems.append(f"{name}.duration must be like 3h, 90m or 1h30m, at most 7 days")
    timezone = str(window.get("timezone", "UTC"))
    if not TIMEZONE.match(timezone) or _zone(timezone) is None:
        problems.append(f"{name}.timezone must be a time zone name such as Europe/Berlin or UTC")
    return problems


def _zone(name):
    if not TIMEZONE.match(str(name)):
        return None
    try:
        return zoneinfo.ZoneInfo(str(name))
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return None


def _now(now):
    if now in (None, ""):
        return datetime.datetime.now(datetime.UTC)
    if isinstance(now, datetime.datetime):
        value = now
    else:
        value = datetime.datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=datetime.UTC)


def _opening(day, hour, minute, zone):
    """The first moment, in UTC, at which the clocks in zone show day hour:minute or later. A time that happens
    twice (clocks going back) opens the first time; one that doesn't exist (clocks skipping ahead) opens when
    the clocks jump past it."""
    wall = datetime.datetime(day.year, day.month, day.day, hour, minute)
    opens = wall.replace(tzinfo=zone).astimezone(datetime.UTC)
    if opens.astimezone(zone).replace(tzinfo=None) == wall:
        return opens
    moment = wall.replace(tzinfo=zone, fold=1).astimezone(datetime.UTC)  # before the jump
    for _ in range(24 * 60):
        if moment.astimezone(zone).replace(tzinfo=None) >= wall:
            break
        moment += datetime.timedelta(minutes=1)
    return moment


def usas_window_openings(window, now=None):
    """The window's openings around now, as (start, end) in UTC: one for each listed day from 8 days before now
    to the day after. Each opens at its start time on that day in its time zone (a start time that doesn't
    exist that day, in the hour clocks skip, opens when the clocks jump past it; one that happens twice opens
    the first time) and stays open for its duration."""
    zone = _zone(window.get("timezone", "UTC"))
    duration = _duration(window.get("duration"))
    start = TIME.match(str(window.get("start", "")))
    days = window.get("days", list(DAYS))
    if zone is None or duration is None or start is None or not isinstance(days, list):
        return []
    local_now = _now(now).astimezone(zone)
    openings = []
    for offset in range(-8, 2):
        day = local_now.date() + datetime.timedelta(days=offset)
        if DAYS[day.weekday()] not in [str(d) for d in days]:
            continue
        opens = _opening(day, int(start.group(1)), int(start.group(2)), zone)
        openings.append((opens, opens + duration))
    return openings


def usas_in_window(window, now=None):
    """Whether now (default: the control node's clock) is inside the maintenance window. No window (empty or
    None) means any time; a window that isn't valid is never open (usas_window_problems says why)."""
    if not window:
        return True
    if not isinstance(window, dict) or usas_window_problems(window):
        return False
    moment = _now(now)
    return any(opens <= moment < closes for opens, closes in usas_window_openings(window, moment))


def usas_window_text(window):
    """A window as words, for the report: "sun 02:00 for 3h (Europe/Berlin)", or "any time"."""
    if not window or not isinstance(window, dict):
        return "any time"
    days = window.get("days", list(DAYS))
    days_text = "every day" if isinstance(days, list) and sorted(set(map(str, days))) == sorted(DAYS) else \
        ",".join(usas_clean_text(d, 3) for d in (days if isinstance(days, list) else []))
    return usas_clean_text(f"{days_text} {window.get('start', '')} for {window.get('duration', '')} "
                           f"({window.get('timezone', 'UTC')})", 80)


STATUSES = ("updated", "nothing", "pending", "busy", "skipped", "unsupported", "unreachable", "failed")


def usas_finish_results(results, mode="apply"):
    """The final results of a run: a server still "applying" stopped during its update, one still "pending" that
    wasn't checked stopped before its check finished (both failed, with the error in the play's log), and one
    checked but not started in an apply wasn't started."""
    finished = []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        status = r.get("status")
        if status == "applying":
            r = {**r, "status": "failed", "reason": "stopped during the update; see the log"}
        elif status == "pending" and not r.get("checked"):
            r = {**r, "status": "failed", "reason": "stopped before its check finished; see the log"}
        elif status == "pending" and mode == "apply":
            r = {**r, "status": "skipped", "reason": "not started"}
        finished.append(r)
    return finished


def usas_updates_summary(results):
    """Counts and one text line per host, for the report and notifications. results is a list of
    usas_updates_result dicts."""
    counts = {s: 0 for s in STATUSES}
    lines = []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        status = r.get("status") if r.get("status") in STATUSES else "failed"
        counts[status] += 1
        parts = [status]
        upgraded, pending = r.get("upgraded") or [], r.get("planned") or []
        if upgraded:
            parts.append(f"{len(upgraded)} upgraded")
        elif pending:
            parts.append(f"{len(pending)} to install")
        security = r.get("security_count", 0)
        if security:
            parts.append(f"{security} security")
        reboot = r.get("reboot") or {}
        if reboot.get("done"):
            parts.append("rebooted")
        elif reboot.get("held"):
            parts.append("reboot held: " + usas_clean_text(reboot.get("why"), 80))
        elif reboot.get("required"):
            parts.append("reboot pending")
        health = (r.get("health") or {}).get("status")
        if health and health != "skipped":
            parts.append(f"health {health}")
        if r.get("reason"):
            parts.append(usas_clean_text(r.get("reason"), 160))
        lines.append(f"{usas_clean_text(r.get('host'), 64)}: " + "; ".join(parts))
    failed = counts["failed"] + counts["unreachable"]
    return {"counts": counts, "lines": lines, "failed": failed, "total": sum(counts.values())}


NOTIFY_POLICIES = ("always", "only-on-error", "on-change")


def usas_notify_due(summary, mode="apply", policy="on-change"):
    """Whether a run's summary calls for a notification: always; only-on-error when a server failed or couldn't
    be reached; on-change also when an apply updated or rebooted a server, or a check found updates waiting."""
    counts = (summary or {}).get("counts") or {}
    errors = counts.get("failed", 0) + counts.get("unreachable", 0)
    if policy == "always":
        return True
    if policy == "only-on-error":
        return errors > 0
    if policy == "on-change":
        return errors > 0 or counts.get("pending" if mode == "check" else "updated", 0) > 0
    return False


def usas_notify_message(summary, run, host_name=""):
    """The notification: {"subject", "body"}. A summary only: counts and one line per server, no package lists."""
    counts = (summary or {}).get("counts") or {}
    run = run or {}
    mode = "check" if run.get("mode") == "check" else "apply"
    shown = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
    errors = (summary or {}).get("failed", 0)
    subject = usas_clean_text(f"USAS updates {mode}{' FAILED' if errors else ''}: {shown or 'no servers'}", 150)
    started = usas_clean_text(run.get("started"), 30)
    lines = [f"Updates run {usas_clean_text(run.get('id'), 40)} ({mode}), started {started}"]
    if host_name:
        lines.append(f"Control node: {usas_clean_text(host_name, 64)}")
    lines += [""] + [usas_clean_text(line, 300) for line in (summary or {}).get("lines", [])[:500]]
    return {"subject": subject, "body": "\n".join(lines) + "\n"}


class FilterModule:
    def filters(self):
        return {
            "usas_clean_text": usas_clean_text,
            "usas_hint_warning": usas_hint_warning,
            "usas_apt_simulation": usas_apt_simulation,
            "usas_package_names": usas_package_names,
            "usas_glob_any": usas_glob_any,
            "usas_matching": usas_matching,
            "usas_select_updates": usas_select_updates,
            "usas_needrestart": usas_needrestart,
            "usas_apt_auto_updates": usas_apt_auto_updates,
            "usas_package_changes": usas_package_changes,
            "usas_running_services": usas_running_services,
            "usas_health_problems": usas_health_problems,
            "usas_system_state_worse": usas_system_state_worse,
            "usas_failed_units": usas_failed_units,
            "usas_window_problems": usas_window_problems,
            "usas_in_window": usas_in_window,
            "usas_window_text": usas_window_text,
            "usas_finish_results": usas_finish_results,
            "usas_updates_summary": usas_updates_summary,
            "usas_notify_due": usas_notify_due,
            "usas_notify_message": usas_notify_message,
        }
