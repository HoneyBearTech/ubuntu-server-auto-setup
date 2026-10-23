"""The status API for dashboards (GET /api/status): a short, read-only summary for a program such as Heimdall,
with its own token instead of a sign-in.

It is off until a token is made on the account page. The token is 32 random bytes, shown once; the data
directory keeps only an HMAC-SHA256 of it (status-token.json), keyed with a key derived from the Vault
password, so the file alone can't confirm a guess. The token is accepted only as "Authorization: Bearer", is
compared in constant time, opens this one read-only route, and never makes a session. Wrong tokens are slowed
down per client address (in a bucket of their own, so a dashboard with an old token can't lock the sign-in
out), and right ones are limited per minute. The summary holds no host names, addresses, node or cluster
names, VM IDs, run limits, logs or secrets.

Everything here is pure or works on files (no FastAPI), so the tests and the fuzzer can call it directly.
"""

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .store import write_private

PREFIX = "usas_st_"
TOKEN_PATTERN = re.compile(r"^usas_st_[A-Za-z0-9_-]{43}\Z")
MAX_HEADER = 256  # longer Authorization headers aren't looked at
HINT = 4  # characters after the prefix that the account page shows
SCHEMA = 1  # goes up only when a key changes meaning or goes away
FREE_ATTEMPTS = 5
MAX_DELAY = 15 * 60
PER_MINUTE = 60  # successful calls per minute: Heimdall asks every 5 s per open tab while a run is going
SAVE_EVERY = 5 * 60  # how often the last use is written to the file
LOG_EVERY = 3600  # how often a successful use from the same address is logged
CLUSTER_TTL = 60  # how long the VM counts are kept (failures too)
RESULTS = ("succeeded", "failed", "stopped", "interrupted")


def parse_bearer(header: str | None) -> str | None:
    """The token of an "Authorization: Bearer <token>" header, or None if it isn't one of ours."""
    if not header or len(header) > MAX_HEADER:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    value = value.strip(" \t")
    return value if TOKEN_PATTERN.match(value) else None


def new_token() -> str:
    return PREFIX + secrets.token_urlsafe(32)


class StatusTokens:
    """The one status token: made, replaced and revoked on the account page, checked on every call."""

    def __init__(self, path: Path, vault_password: Callable[[], bytes], clock: Callable[[], float] = time.time):
        self.path = path
        self.vault_password = vault_password
        self.clock = clock
        self._lock = threading.Lock()
        self._used: tuple[float, str] | None = None  # the last use not written to the file yet
        self._saved = 0.0

    def _digest(self, token: str) -> str:
        key = hashlib.sha256(b"usas status token\0" + self.vault_password()).digest()
        return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()

    def _read(self) -> dict[str, Any] | None:
        """The record, None if there is no token, or {"broken": True} if the file can't be read."""
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or not isinstance(data.get("hash"), str):
                raise ValueError("not a status token file")
            return data
        except (OSError, ValueError):
            return {"broken": True}  # fails closed: no token matches

    def exists(self) -> bool:
        return self.path.exists()

    def create(self) -> str:
        """A new token, replacing the old one at once. The token itself is returned once and never stored."""
        token = new_token()
        record = {"hash": self._digest(token), "hint": token[len(PREFIX):len(PREFIX) + HINT],
                  "created": int(self.clock()), "last_used": None, "last_from": ""}
        with self._lock:
            write_private(self.path, json.dumps(record, indent=2) + "\n")
            self._used, self._saved = None, 0.0
        return token

    def revoke(self) -> bool:
        with self._lock:
            existed = self.path.exists()
            self.path.unlink(missing_ok=True)
            self._used = None
        return existed

    def verify(self, token: str) -> bool:
        record = self._read()
        if not record or record.get("broken"):
            return False
        return hmac.compare_digest(self._digest(token), str(record["hash"]))

    def used(self, client: str) -> None:
        """Notes a successful call; writes it to the file at most every SAVE_EVERY seconds."""
        now = self.clock()
        with self._lock:
            self._used = (now, client)
            if now - self._saved < SAVE_EVERY:
                return
            record = self._read()
            if not record or record.get("broken"):
                return
            record["last_used"], record["last_from"] = int(now), client
            write_private(self.path, json.dumps(record, indent=2) + "\n")
            self._saved = now

    def info(self) -> dict[str, Any]:
        """What the account page shows (never the token or its hash)."""
        record = self._read()
        if record is None:
            return {"exists": False, "broken": False, "hint": "", "created": None, "last_used": None,
                    "last_from": ""}
        if record.get("broken"):
            return {"exists": True, "broken": True, "hint": "", "created": None, "last_used": None,
                    "last_from": ""}
        last_used, last_from = record.get("last_used"), str(record.get("last_from") or "")
        with self._lock:
            if self._used:
                last_used, last_from = int(self._used[0]), self._used[1]
        return {"exists": True, "broken": False, "hint": str(record.get("hint") or ""),
                "created": record.get("created"), "last_used": last_used, "last_from": last_from}


class Throttle:
    """Slows down wrong tokens per client address (5 free, then the wait doubles up to 15 minutes), and limits
    right ones to PER_MINUTE calls a minute. In memory: a restart starts over, as the sign-in throttle does."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, tuple[int, float]] = {}
        self._calls: deque[float] = deque()
        self._logged: dict[str, float] = {}

    def wait_seconds(self, client: str) -> int:
        with self._lock:
            count, last = self._failures.get(client, (0, 0.0))
        if count < FREE_ATTEMPTS:
            return 0
        delay = min(MAX_DELAY, 2 ** (count - FREE_ATTEMPTS + 1))
        return max(0, int(last + delay - self.clock()))

    def record_failure(self, client: str) -> None:
        with self._lock:
            count, _ = self._failures.get(client, (0, 0.0))
            self._failures[client] = (count + 1, self.clock())

    def clear_failures(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    def over_limit(self) -> int:
        """0 if this call may go ahead (and counts it), else how many seconds until one may."""
        now = self.clock()
        with self._lock:
            while self._calls and now - self._calls[0] >= 60:
                self._calls.popleft()
            if len(self._calls) >= PER_MINUTE:
                return max(1, int(self._calls[0] + 60 - now + 0.999))
            self._calls.append(now)
            return 0

    def should_log(self, client: str) -> bool:
        """Whether a successful call from client is worth a log line (the first one, then once an hour)."""
        now = self.clock()
        with self._lock:
            if now - self._logged.get(client, float("-inf")) < LOG_EVERY:
                return False
            self._logged[client] = now
            return True


class ClusterCounts:
    """The cluster's VM IDs and how many QEMU VMs run, from one call a minute at most whoever asks; a failure
    is kept as long, so a dashboard can't make the UI call an unreachable cluster again and again."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._lock = threading.Lock()
        self._cache: tuple[str, float, Any] | None = None

    def get(self, identity: str, fetch: Callable[[], list]) -> list | None:
        """fetch's VMs (vms.Vm), cached under identity (the API and token); None if it raised."""
        with self._lock:
            if self._cache and self._cache[0] == identity and self.clock() - self._cache[1] < CLUSTER_TTL:
                return self._cache[2]
            try:
                value = fetch()
            except Exception:  # noqa: BLE001 (any failure means "unknown"; the VMs page shows the reason)
                value = None
            self._cache = (identity, self.clock(), value)
            return value


def run_summary(run: Any) -> dict[str, Any]:
    """A run without its ID, limit or log: the playbook's key, whether it was a check, when, how it ended."""
    data = {"playbook": run.action, "check": bool(run.check), "started": int(run.started)}
    if run.done:
        data.update(status=run.status if run.status in RESULTS else "failed",
                    finished=int(run.finished) if run.finished else None)
    return data


def summary(version: str, current: Any, history: list, steps: list[dict[str, Any]], servers: list[dict[str, Any]],
            vms: list | None, now: float) -> dict[str, Any]:
    """The answer of GET /api/status. vms is the cluster's VMs (vms.Vm), or None if they aren't known."""
    last = next((run for run in history if run.done), None)
    vmids = {int(s["pve_vmid"]) for s in servers if s.get("pve_vmid")}
    data: dict[str, Any] = {
        "schema": SCHEMA,
        "version": version,
        "setup": {"done": sum(1 for s in steps if s["done"]), "total": len(steps)},
        "running": run_summary(current) if current is not None and not current.done else None,
        "last_run": run_summary(last) if last is not None else None,
        "servers": {"total": len(servers), "pending": None},
        "vms": None,
        "fetched": int(now),
    }
    if vms is not None:
        on_cluster = {vm.vmid for vm in vms}
        machines = [vm for vm in vms if vm.type == "qemu" and not vm.template]
        data["servers"]["pending"] = len(vmids - on_cluster)
        data["vms"] = {"running": sum(1 for vm in machines if vm.status == "running"), "total": len(machines)}
    return data
