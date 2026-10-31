"""The updates schedules (webui/usas_web/scheduler.py): their form, windows in time zones, the store, and the
thread's decisions with an injected clock."""

import datetime
import zoneinfo

import pytest
from usas_web import scheduler as sc

ZONES = {"Europe/Berlin", "Etc/UTC", "America/New_York"}
FORM = {"name": "Nightly web", "kind": "apply", "day-mon": "on", "day-thu": "on", "start": "02:30",
        "minutes": "120", "timezone": "Europe/Berlin"}


def at(text, zone="Europe/Berlin"):
    return datetime.datetime.fromisoformat(text).replace(tzinfo=zoneinfo.ZoneInfo(zone)).timestamp()


def schedule(**kw):
    values = {**sc.parse(FORM, ZONES), **kw}
    return sc.Schedule(id="a1b2c3d4e5f6", targets=[{"vmid": 101, "name": "vm-101"}], options={}, created=0.0,
                       **values)


def test_the_form():
    assert sc.parse(FORM, ZONES) == {"name": "Nightly web", "kind": "apply", "days": ["mon", "thu"], "start": "02:30",
                                     "minutes": 120, "timezone": "Europe/Berlin"}
    bad = {"name": "{{ x }}", "kind": "reboot", "start": "24:00", "minutes": "5", "timezone": "../../etc/passwd"}
    with pytest.raises(sc.ScheduleProblem) as caught:
        sc.parse(bad, ZONES)
    assert set(caught.value.errors) == {"name", "kind", "day-mon", "start", "minutes", "timezone"}
    for zone in ("Mars/Olympus", "Etc/UTC\n"):
        with pytest.raises(sc.ScheduleProblem):
            sc.parse({**FORM, "timezone": zone}, ZONES)
    assert sc.parse({**FORM, "name": "  a   b "}, ZONES)["name"] == "a b"


def test_windows_follow_the_time_zone_and_the_days():
    s = schedule()
    # Thursday 2026-10-29, 03:00 in Berlin: inside the window that opened at 02:30.
    now = at("2026-10-29T03:00:00")
    start, end = sc.last_window(s, now)
    assert start == at("2026-10-29T02:30:00") and end == start + 7200
    assert sc.window_key(start) == "2026-10-29T01:30:00Z"
    # The next opens on Monday.
    assert sc.next_start(s, now) == at("2026-11-02T02:30:00")
    # Wednesday: the last window was Monday's.
    assert sc.last_window(s, at("2026-10-28T12:00:00"))[0] == at("2026-10-26T02:30:00")
    # Across the end of summer time (25 October 2026 in Berlin) the local time stays 02:30.
    sunday = schedule(days=["sun"])
    assert sc.last_window(sunday, at("2026-10-25T12:00:00"))[0] == at("2026-10-25T02:30:00")
    new_york = schedule(timezone="America/New_York", days=["thu"])
    assert sc.last_window(new_york, at("2026-10-29T03:00:00", "America/New_York"))[0] == at(
        "2026-10-29T02:30:00", "America/New_York")


def test_surprises():
    assert sc.surprises(None) == ["the check reported no server"]
    assert sc.surprises({"hosts": {"a": {"status": "pending"}, "b": {"status": "nothing"}}}) == []
    found = sc.surprises({"hosts": {"a": {"status": "unreachable"}, "b": {"status": "pending", "removals": ["x"]}}})
    assert found == ["a unreachable", "b would remove packages (x)"]


def test_the_store(tmp_path):
    store = sc.ScheduleStore(tmp_path / "schedules.json")
    assert store.load() == []
    made = store.add(sc.parse(FORM, ZONES), [{"vmid": 101}], {"updates_scope": "all"}, "abc", "run1", 5.0)
    assert sc.ID.match(made.id) and store.load() == [made]
    assert (tmp_path / "schedules.json").stat().st_mode & 0o777 == 0o600
    store.update(made.id, last_outcome="done")
    assert store.load()[0].last_outcome == "done"
    for _ in range(sc.MAX_SCHEDULES - 1):
        store.add(sc.parse(FORM, ZONES), [], {}, "", "", 5.0)
    with pytest.raises(sc.ScheduleProblem):
        store.add(sc.parse(FORM, ZONES), [], {}, "", "", 5.0)
    assert store.remove(made.id).id == made.id and store.remove("nope") is None
    assert all(s.id != made.id for s in store.load())
    (tmp_path / "schedules.json").write_text('{"schedules": [{"id": "x"}, 3]}')
    assert store.load() == []


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def test_a_window_runs_once_waits_for_another_run_and_is_skipped_when_it_closes(tmp_path):
    store = sc.ScheduleStore(tmp_path / "schedules.json")
    made = store.add(sc.parse(FORM, ZONES), [], {}, "", "", at("2026-10-28T12:00:00"))
    clock = Clock(at("2026-10-28T23:00:00"))
    fired, events, busy = [], [], [False]
    sched = sc.Scheduler(store, lambda s: (fired.append(s.id) or "r1", "Check run r1 started."), lambda: busy[0],
                         lambda event, **f: events.append((event, f)), clock)
    sched.tick()
    assert fired == []  # no window yet since it was made
    clock.now = at("2026-10-29T02:31:00")
    busy[0] = True
    sched.tick()
    assert fired == []  # another run is going: it waits
    busy[0] = False
    sched.tick()
    sched.tick()
    assert fired == [made.id]  # once per window
    saved = store.load()[0]
    assert (saved.last_window, saved.last_run, saved.last_outcome) == ("2026-10-29T01:30:00Z", "r1",
                                                                       "Check run r1 started.")
    assert events[-1][0] == "schedule.run" and events[-1][1]["run"] == "r1"

    # Monday's window: another run keeps going until it closes, so it is skipped and says why.
    clock.now = at("2026-11-02T02:40:00")
    busy[0] = True
    sched.tick()
    clock.now = at("2026-11-02T04:31:00")
    sched.tick()
    saved = store.load()[0]
    assert saved.last_outcome == "Skipped: another run was going until the window closed."
    assert events[-1][0] == "schedule.skipped" and fired == [made.id]
    # Thursday's passed while the UI was down.
    clock.now = at("2026-11-05T12:00:00")
    busy[0] = False
    sched.tick()
    assert store.load()[0].last_outcome == "Skipped: the web UI wasn't running during the window."


def test_a_failing_start_is_recorded(tmp_path):
    store = sc.ScheduleStore(tmp_path / "schedules.json")
    made = store.add(sc.parse(FORM, ZONES), [], {}, "", "", 0.0)

    def broken(s):
        raise RuntimeError("boom")
    sched = sc.Scheduler(store, broken, lambda: False, lambda *a, **k: None, Clock(at("2026-10-29T02:31:00")))
    assert sched.run(made, "manual") == ""
    assert store.load()[0].last_outcome == "Couldn't start: boom"
    sched.start()
    sched.stop()


def test_the_command_line_names_an_unknown_schedule(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    from usas_web import __main__ as cli

    monkeypatch.setenv("USAS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("USAS_PROJECT_DIR", str(Path(__file__).resolve().parents[2]))
    assert cli.updates(["--schedule", "a1b2c3d4e5f6"]) == 2
    assert "no schedule" in capsys.readouterr().err
