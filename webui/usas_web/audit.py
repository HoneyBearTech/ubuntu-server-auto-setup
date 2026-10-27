"""The audit log: what was done to VMs from the VMs page, and from which session.

One JSON object per line in /data/audit.jsonl (0600), appended under a lock: the time, the event, the session's
fingerprint (a hash of its ID, never the ID) and the client's address, then the event's own fields (VM IDs,
addresses and where they came from, host-key fingerprints, tags, the run ID and its outcome). The UI has one
password and no accounts, so "who" is a session, not a person. When the file would pass MAX_BYTES it is rotated
(audit.jsonl.1 to .3; the oldest is dropped). The History page offers the kept files as one download.
"""

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
KEEP = 3


def session_fingerprint(sid: str) -> str:
    """A session's name in the log: 12 hex digits of a hash of its ID."""
    return hashlib.sha256(sid.encode()).hexdigest()[:12]


class AuditLog:
    def __init__(self, path: Path, max_bytes: int = MAX_BYTES, keep: int = KEEP):
        self.path = path
        self.max_bytes = max_bytes
        self.keep = keep
        self._lock = threading.Lock()

    def _rotated(self, n: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{n}")

    def _rotate(self) -> None:
        self._rotated(self.keep).unlink(missing_ok=True)
        for n in range(self.keep - 1, 0, -1):
            if self._rotated(n).exists():
                os.replace(self._rotated(n), self._rotated(n + 1))
        if self.path.exists():
            os.replace(self.path, self._rotated(1))

    def record(self, event: str, session: str = "", client: str = "", **fields: Any) -> dict[str, Any]:
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, "session": session,
                 "client": client, **fields}
        line = (json.dumps(entry, ensure_ascii=True, sort_keys=False, default=str) + "\n").encode()
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            size = self.path.stat().st_size if self.path.exists() else 0
            if size and size + len(line) > self.max_bytes:
                self._rotate()
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, line)
            finally:
                os.close(fd)
        return entry

    def files(self) -> list[Path]:
        """The kept files, oldest first."""
        found = [self._rotated(n) for n in range(self.keep, 0, -1) if self._rotated(n).exists()]
        return found + ([self.path] if self.path.exists() else [])

    def read(self) -> bytes:
        with self._lock:
            return b"".join(p.read_bytes() for p in self.files())

    def entries(self, limit: int = 1000) -> list[dict[str, Any]]:
        """The newest entries, newest first."""
        found = []
        for line in reversed(self.read().decode("utf-8", "replace").splitlines()):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                found.append(entry)
            if len(found) >= limit:
                break
        return found
