"""The audit log: entries, rotation at the size limit, and reading it back."""

import json

from usas_web.audit import AuditLog, session_fingerprint


def test_entries_and_permissions(tmp_path):
    log = AuditLog(tmp_path / "data" / "audit.jsonl")
    assert log.read() == b"" and log.entries() == [] and log.files() == []
    entry = log.record("hostkey.pin", session="abc", client="10.0.0.5", vmid=101, note="line\nbreak\x1b")
    assert entry["event"] == "hostkey.pin" and entry["time"].endswith("Z")
    lines = log.path.read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["note"] == "line\nbreak\x1b"  # escaped: one line per entry
    assert log.path.stat().st_mode & 0o777 == 0o600
    log.record("run.check", run="x")
    assert [e["event"] for e in log.entries()] == ["run.check", "hostkey.pin"]
    log.path.write_bytes(log.path.read_bytes() + b"not json\n[1]\n")
    assert len(log.entries()) == 2 and len(log.entries(limit=1)) == 1


def test_rotation_keeps_three(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl", max_bytes=300, keep=3)
    for i in range(40):
        log.record("event", n=i, pad="x" * 50)
    names = [p.name for p in log.files()]
    assert names == ["audit.jsonl.3", "audit.jsonl.2", "audit.jsonl.1", "audit.jsonl"]
    assert all(p.stat().st_size <= 300 for p in log.files())
    numbers = [json.loads(line)["n"] for line in log.read().decode().splitlines()]
    assert numbers == sorted(numbers) and numbers[-1] == 39 and numbers[0] > 0  # the oldest were dropped
    assert not (tmp_path / "audit.jsonl.4").exists()


def test_session_fingerprint():
    assert session_fingerprint("sid") == session_fingerprint("sid") != session_fingerprint("other")
    assert len(session_fingerprint("sid")) == 12 and "sid" not in session_fingerprint("sid")
