"""Updates on a schedule: the schedules, their windows, and the thread that starts their runs.

A schedule is made from a passing updates check run of the VMs page (its VMs, how each is reached and its
choices) and runs the updates playbook in a weekly window: on the chosen weekdays, from a start time for a
number of minutes, in a time zone. A "check" schedule runs the check; an "apply" schedule runs the check first
and installs only if it passed and found nothing surprising (a server that failed, couldn't be reached or would
lose packages). The plan is built again from the cluster's listing and the pinned host keys each time, so a VM
whose key isn't pinned any more, or that is gone, stops the run before anything is contacted.

The schedules live in /data/schedules.json (0600). The thread looks every TICK seconds (an injected clock in the
tests). A window's run starts once; while another run is going it waits, and when the window closes first it is
skipped, as is a window that passed while the web UI wasn't running. Only the standard library is used
(zoneinfo for the time zones).
"""

import datetime
import json
import logging
import re
import secrets
import threading
import time
import zoneinfo
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .problems import Problem
from .store import write_private

TICK = 30
MAX_SCHEDULES = 20
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
KINDS = {"check": "List the pending updates (check only)", "apply": "Install the updates (after a passing check)"}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,47}\Z")
START = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]\Z")
MINUTES = re.compile(r"^[0-9]{1,4}\Z")
ZONE = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}\Z")
ID = re.compile(r"^[a-f0-9]{12}\Z")
MIN_MINUTES, MAX_MINUTES = 30, 720
log = logging.getLogger("usas_web")


class ScheduleProblem(Problem, ValueError):
    """A schedule that can't be saved; errors are keyed by the form field they belong to."""

    def __init__(self, errors: dict[str, str]):
        super().__init__(next(iter(errors.values())))
        self.errors = errors


@dataclass
class Schedule:
    id: str
    name: str
    kind: str  # check or apply
    days: list[str]
    start: str  # HH:MM
    minutes: int  # how long the window stays open
    timezone: str
    # The VMs and how each is reached (vmplan.Plan.summary of the check run it was made from).
    targets: list[dict[str, Any]]
    options: dict[str, str]  # the updates choices (updates_scope, updates_reboot, updates_snapshot)
    created: float
    session: str = ""  # the audit log's fingerprint of the session that made it
    made_from: str = ""  # the check run
    last_window: str = ""  # the start of the last window it ran (or was skipped) in, as ISO 8601
    last_outcome: str = ""
    last_time: float = 0.0
    last_run: str = ""


def parse(form: dict[str, Any], zones: set[str] | frozenset[str]) -> dict[str, Any]:
    """A schedule's own settings from the form (name, kind, day-<day>, start, minutes, timezone), checked.
    Raises ScheduleProblem with every error."""
    errors = {}
    name = " ".join(str(form.get("name", "")).split())
    if not NAME.match(name):
        errors["name"] = "Name the schedule: letters, digits, spaces, '.', '_' or '-', up to 48."
    kind = str(form.get("kind", ""))
    if kind not in KINDS:
        errors["kind"] = "Choose what the schedule does."
    days = [d for d in DAYS if form.get(f"day-{d}") == "on"]
    if not days:
        errors["day-mon"] = "Choose at least one day."
    start = str(form.get("start", ""))
    if not START.match(start):
        errors["start"] = "Enter the start time as HH:MM (24 hours)."
    text = str(form.get("minutes", ""))
    minutes = int(text) if MINUTES.match(text) else 0
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        errors["minutes"] = f"The window lasts from {MIN_MINUTES} to {MAX_MINUTES} minutes."
    zone = str(form.get("timezone", ""))
    if not ZONE.match(zone) or zone not in zones:
        errors["timezone"] = "Enter a time zone such as Europe/Berlin or Etc/UTC."
    if errors:
        raise ScheduleProblem(errors)
    return {"name": name, "kind": kind, "days": days, "start": start, "minutes": minutes, "timezone": zone}


def _starts(s: Schedule, now: float, back: bool) -> list[datetime.datetime]:
    """Window starts around now: the last eight days' (back) or the next eight days' in the schedule's zone."""
    zone = zoneinfo.ZoneInfo(s.timezone)
    today = datetime.datetime.fromtimestamp(now, zone).date()
    hour, minute = (int(x) for x in s.start.split(":"))
    found = []
    for offset in range(8):
        day = today - datetime.timedelta(days=offset) if back else today + datetime.timedelta(days=offset)
        if DAYS[day.weekday()] in s.days:
            found.append(datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone))
    return found


def last_window(s: Schedule, now: float) -> tuple[float, float] | None:
    """The latest window that has started by now: its start and end as timestamps."""
    for start in sorted(_starts(s, now, back=True), reverse=True):
        if start.timestamp() <= now:
            return start.timestamp(), start.timestamp() + s.minutes * 60
    return None


def next_start(s: Schedule, now: float) -> float | None:
    """When the next window opens, after now."""
    later = [t.timestamp() for t in _starts(s, now, back=False) if t.timestamp() > now]
    return min(later) if later else None


def window_key(start: float) -> str:
    return datetime.datetime.fromtimestamp(start, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def surprises(updates: dict[str, Any] | None) -> list[str]:
    """Why a scheduled check's results stop its apply: a server that failed, couldn't be reached or would lose
    packages, or no result at all."""
    hosts = (updates or {}).get("hosts")
    if not isinstance(hosts, dict) or not hosts:
        return ["the check reported no server"]
    found = []
    for name, r in sorted(hosts.items()):
        if not isinstance(r, dict):
            continue
        if r.get("status") in ("failed", "unreachable"):
            found.append(f"{name} {r.get('status')}")
        if r.get("removals"):
            found.append(f"{name} would remove packages ({', '.join(map(str, r['removals'][:5]))})")
    return found


class ScheduleStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> list[Schedule]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        known = {f.name for f in fields(Schedule)}
        found = []
        for item in data.get("schedules", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            try:
                found.append(Schedule(**{k: v for k, v in item.items() if k in known}))
            except TypeError:
                continue
        return found

    def _write(self, schedules: list[Schedule]) -> None:
        write_private(self.path, json.dumps({"schedules": [asdict(s) for s in schedules]}, indent=1) + "\n")

    def add(self, values: dict[str, Any], targets: list[dict[str, Any]], options: dict[str, str], session: str,
            made_from: str, now: float) -> Schedule:
        with self._lock:
            schedules = self.load()
            if len(schedules) >= MAX_SCHEDULES:
                raise ScheduleProblem({"name": f"At most {MAX_SCHEDULES} schedules: delete one first."})
            schedule = Schedule(id=secrets.token_hex(6), targets=targets, options=options, created=now,
                                session=session, made_from=made_from, **values)
            self._write([*schedules, schedule])
            return schedule

    def remove(self, schedule_id: str) -> Schedule | None:
        with self._lock:
            schedules = self.load()
            gone = next((s for s in schedules if s.id == schedule_id), None)
            if gone is not None:
                self._write([s for s in schedules if s.id != schedule_id])
            return gone

    def update(self, schedule_id: str, **changes: Any) -> None:
        with self._lock:
            schedules = self.load()
            for s in schedules:
                if s.id == schedule_id:
                    for key, value in changes.items():
                        setattr(s, key, value)
            self._write(schedules)


class Scheduler:
    """Starts each schedule's run once per window. fire(schedule) starts it and says what happened; busy() says
    whether another run is going; record(event, **fields) writes the audit log."""

    def __init__(self, store: ScheduleStore, fire: Callable[[Schedule], tuple[str, str]], busy: Callable[[], bool],
                 record: Callable[..., Any], clock: Callable[[], float] = time.time):
        self.store = store
        self.fire = fire
        self.busy = busy
        self.record = record
        self.clock = clock
        self._waited: set[tuple[str, str]] = set()  # (schedule, window) that found another run going
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        now = self.clock()
        for s in self.store.load():
            window = last_window(s, now)
            if window is None or window[1] <= s.created:
                continue  # no window since it was made
            start, end = window
            key = window_key(start)
            if s.last_window == key:
                continue
            if now < end:
                if self.busy():
                    self._waited.add((s.id, key))
                    continue
                self.run(s, key)
            else:
                why = ("another run was going until the window closed" if (s.id, key) in self._waited
                       else "the web UI wasn't running during the window")
                self.store.update(s.id, last_window=key, last_outcome=f"Skipped: {why}.", last_time=now,
                                  last_run="")
                self.record("schedule.skipped", schedule=s.id, name=s.name, window=key, reason=why)
                log.warning("Updates schedule %s skipped its window %s: %s", s.name, key, why)
                self._waited.discard((s.id, key))

    def run(self, s: Schedule, key: str) -> str:
        """Starts the schedule's run now (key names the window, or the manual run); returns the run ID or ""."""
        try:
            run_id, outcome = self.fire(s)
        except Exception as exc:  # a schedule's failure must not stop the scheduler
            log.exception("Updates schedule %s couldn't start", s.name)
            run_id, outcome = "", f"Couldn't start: {exc}"
        self.store.update(s.id, last_window=key, last_outcome=outcome, last_time=self.clock(), last_run=run_id)
        self.record("schedule.run", schedule=s.id, name=s.name, kind=s.kind, window=key, run=run_id or None,
                    outcome=outcome)
        self._waited.discard((s.id, key))
        return run_id

    def _loop(self) -> None:
        while not self._stop.wait(TICK):
            try:
                self.tick()
            except Exception:  # pragma: no cover (logged; the next tick tries again)
                log.exception("The updates scheduler's check failed")

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="usas-scheduler", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
