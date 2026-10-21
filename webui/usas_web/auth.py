"""The sign-in: one password, set on first run, stored as an argon2id hash; server-side sessions; CSRF tokens.

The first run needs a one-time setup code, printed to the container log, so that nobody else who can reach
the port (or a web page using DNS rebinding) can set the password first.
"""

import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .config import Settings
from .store import write_private

MIN_PASSWORD = 12
MAX_PASSWORD = 1024
IDLE_SECONDS = 2 * 3600
FREE_ATTEMPTS = 5
MAX_DELAY = 15 * 60

# argon2-cffi's defaults are RFC 9106's second recommendation: argon2id, 64 MiB, 3 passes, 4 lanes.
_hasher = PasswordHasher()


def password_problem(password: str, confirm: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if len(password) > MAX_PASSWORD:
        return f"Use at most {MAX_PASSWORD} characters."
    if password != confirm:
        return "The two passwords don't match."
    return None


@dataclass
class Session:
    sid: str
    csrf: str
    created: float
    last_seen: float
    authenticated: bool = False
    # Host keys scanned but not trusted yet: the ones the user compared are the ones that get trusted.
    pending_hostkeys: list | None = None


class Auth:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._failures: dict[str, tuple[int, float]] = {}
        self.setup_code = "" if self.is_configured() else self._new_setup_code()

    @staticmethod
    def _new_setup_code() -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        raw = "".join(secrets.choice(alphabet) for _ in range(12))
        return "-".join(raw[i : i + 4] for i in range(0, 12, 4))

    # --- The password ------------------------------------------------------------------------------------

    def is_configured(self) -> bool:
        return self.settings.auth_file.exists()

    def _hash(self) -> str:
        return json.loads(self.settings.auth_file.read_text())["password_hash"]

    def set_password(self, password: str) -> None:
        record = {"password_hash": _hasher.hash(password), "changed": int(time.time())}
        write_private(self.settings.auth_file, json.dumps(record) + "\n")
        self.setup_code = ""

    def check_setup_code(self, code: str) -> bool:
        given = code.strip().upper().replace(" ", "")
        return bool(self.setup_code) and hmac.compare_digest(given, self.setup_code)

    def verify_password(self, password: str) -> bool:
        try:
            stored = self._hash()
            _hasher.verify(stored, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError, KeyError, ValueError):
            return False
        if _hasher.check_needs_rehash(stored):
            self.set_password(password)
        return True

    # --- Throttling -------------------------------------------------------------------------------------

    def wait_seconds(self, client: str) -> int:
        """How long this client must wait before its next attempt (0: it may try now)."""
        with self._lock:
            count, last = self._failures.get(client, (0, 0.0))
        if count < FREE_ATTEMPTS:
            return 0
        delay = min(MAX_DELAY, 2 ** (count - FREE_ATTEMPTS + 1))
        return max(0, int(last + delay - time.time()))

    def record_failure(self, client: str) -> None:
        with self._lock:
            count, _ = self._failures.get(client, (0, 0.0))
            self._failures[client] = (count + 1, time.time())

    def clear_failures(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    # --- Sessions ----------------------------------------------------------------------------------------

    def new_session(self) -> Session:
        now = time.time()
        session = Session(sid=secrets.token_urlsafe(32), csrf=secrets.token_urlsafe(32), created=now, last_seen=now)
        with self._lock:
            self._expire(now)
            self._sessions[session.sid] = session
        return session

    def get_session(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        now = time.time()
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            if self._expired(session, now):
                del self._sessions[sid]
                return None
            session.last_seen = now
            return session

    def _expired(self, session: Session, now: float) -> bool:
        return now - session.last_seen > IDLE_SECONDS or now - session.created > self.settings.session_hours * 3600

    def _expire(self, now: float) -> None:
        for sid in [sid for sid, s in self._sessions.items() if self._expired(s, now)]:
            del self._sessions[sid]

    def log_in(self, old: Session | None) -> Session:
        """A new, signed-in session (a new ID, so a session fixed before sign-in is worthless)."""
        if old is not None:
            self.end(old)
        session = self.new_session()
        session.authenticated = True
        return session

    def end(self, session: Session) -> None:
        with self._lock:
            self._sessions.pop(session.sid, None)

    def end_all_except(self, keep: Session) -> None:
        with self._lock:
            for sid in [sid for sid in self._sessions if sid != keep.sid]:
                del self._sessions[sid]


def csrf_ok(session: Session | None, token: str | None) -> bool:
    return session is not None and bool(token) and hmac.compare_digest(session.csrf, token or "")
