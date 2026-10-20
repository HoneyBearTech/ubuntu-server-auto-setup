"""Runs ansible-playbook and keeps the run history.

The command is an argument list (never a shell), one run at a time, and nothing typed into a form is copied
into it: the playbook comes from the fixed table below, the options are fixed flags, and the limit is
rebuilt from the inventory's own host and group names. Its output goes to
runs/<id>.log line by line, with the Vault's secret values replaced by ******** in case a task ever prints
one, and the run's details to runs/<id>.json.
"""

import json
import os
import re
import secrets
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Settings
from .store import write_private

PLAYBOOKS = {
    "build-installer": ("playbooks/build-installer.yml", "Build the installer ISO on the Proxmox node", ()),
    "provision": ("playbooks/provision-vms.yml", "Create and install the VMs that don't exist yet", ()),
    "baseline": ("playbooks/baseline.yml", "Apply the baseline to the servers", ()),
    "site": ("playbooks/site.yml", "All three, in order", ()),
    "keys": ("playbooks/baseline.yml", "Re-sync the admin user's SSH keys only", ("--tags", "ssh_keys")),
}
KEEP_RUNS = 200
# What an inventory name must look like to be used in --limit (no leading "-", no spaces or separators).
LIMIT_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")
REDACTED = "********"


class RunError(RuntimeError):
    pass


@dataclass
class Run:
    id: str
    action: str
    check: bool
    diff: bool
    limit: str
    status: str  # running, succeeded, failed, stopped, interrupted
    started: float
    finished: float | None = None
    returncode: int | None = None

    @property
    def done(self) -> bool:
        return self.status != "running"


def parse_limit(limit: str, names: set[str]) -> str:
    """A --limit value from inventory host and group names, separated by commas or colons, each optionally
    prefixed with ! (exclude) or & (intersect). The result is built from the inventory's names and fixed
    prefixes only, never from the typed text, so it can't carry an option, a separator or a line break."""
    if any(ord(c) < 32 or ord(c) == 127 for c in limit):
        raise RunError("The limit isn't a list of hosts or groups in the inventory: it has a line break or "
                       "control character.")
    limit = limit.strip()
    if not limit:
        return ""
    known_names = sorted(n for n in names if LIMIT_NAME.match(n))
    parts = []
    for item in (part.strip() for part in re.split(r"[,:]", limit)):
        if item.startswith("!"):
            prefix, name = "!", item[1:]
        elif item.startswith("&"):
            prefix, name = "&", item[1:]
        else:
            prefix, name = "", item
        known = next((n for n in known_names if n == name), None)
        if known is None:
            raise RunError(f"'{item}' isn't a host or group in the inventory.")
        parts.append(prefix + known)
    return ",".join(parts)


class Runner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dir = settings.runs_dir
        self._lock = threading.Lock()
        self._current: tuple[Run, subprocess.Popen] | None = None
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for run in self.history():
            if run.status == "running":  # the UI stopped while it ran
                run.status = "interrupted"
                self._save(run)

    def _meta(self, run_id: str) -> Path:
        return self.dir / f"{run_id}.json"

    def log_path(self, run_id: str) -> Path:
        if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[a-f0-9]{6}", run_id):
            raise RunError("no such run")
        return self.dir / f"{run_id}.log"

    def _save(self, run: Run) -> None:
        write_private(self._meta(run.id), json.dumps(asdict(run)) + "\n")

    def get(self, run_id: str) -> Run | None:
        try:
            self.log_path(run_id)  # checks the ID's format
        except RunError:
            return None
        path = self._meta(run_id)
        if not path.exists():
            return None
        return Run(**json.loads(path.read_text()))

    def history(self) -> list[Run]:
        runs = []
        for path in sorted(self.dir.glob("*.json"), reverse=True):
            try:
                runs.append(Run(**json.loads(path.read_text())))
            except (ValueError, TypeError):
                continue
        return runs

    def current(self) -> Run | None:
        with self._lock:
            return self._current[0] if self._current else None

    def command(self, action: str, check: bool, diff: bool, limit: str) -> list[str]:
        key = next((k for k in PLAYBOOKS if k == action), None)
        if key is None:
            raise RunError("unknown action")
        playbook, _, extra = PLAYBOOKS[key]
        s = self.settings
        argv = [s.ansible_playbook, "-i", str(s.inventory_file), "--vault-password-file", str(s.vault_password_file)]
        argv += list(extra)
        if check:
            argv.append("--check")
        if diff:
            argv.append("--diff")
        if limit:
            argv.append(f"--limit={limit}")
        argv.append(playbook)
        return argv

    def environment(self) -> dict[str, str]:
        s = self.settings
        home = os.environ.get("HOME") or tempfile.gettempdir()
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": home,
            "LANG": "C.UTF-8",
            "ANSIBLE_CONFIG": str(s.project_dir / "ansible.cfg"),
            "ANSIBLE_FORCE_COLOR": "0",
            "ANSIBLE_NOCOLOR": "1",
            "ANSIBLE_HOST_KEY_CHECKING": "True",
            "ANSIBLE_RETRY_FILES_ENABLED": "False",
            "PYTHONUNBUFFERED": "1",
        }
        for name in ("VIRTUAL_ENV", "ANSIBLE_COLLECTIONS_PATH", "TMPDIR"):
            if os.environ.get(name):
                env[name] = os.environ[name]
        if s.ca_bundle.exists():
            env["SSL_CERT_FILE"] = env["REQUESTS_CA_BUNDLE"] = str(s.ca_bundle)
        env.update(s.run_env)
        return env

    def start(self, action: str, check: bool, diff: bool, limit_text: str, names: set[str],
              redact: list[str]) -> Run:
        """Starts a run. limit_text is checked against names, the inventory's host and group names."""
        action = next((k for k in PLAYBOOKS if k == action), "")
        if not action:
            raise RunError("unknown action")
        limit = parse_limit(limit_text, names)
        if not self.settings.inventory_file.exists():
            raise RunError("Save the settings first: there is no inventory yet.")
        with self._lock:
            if self._current is not None:
                raise RunError("Another run is still going. Wait for it to finish or stop it.")
            run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
            run = Run(id=run_id, action=action, check=check, diff=diff, limit=limit, status="running",
                      started=time.time())
            log = self.log_path(run_id)
            write_private(log, "")
            argv = self.command(action, check, diff, limit)
            proc = subprocess.Popen(  # noqa: S603 (an argument list, no shell)
                argv, cwd=self.settings.project_dir, env=self.environment(), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
            )
            self._current = (run, proc)
            self._save(run)
        secrets_to_hide = sorted({v for v in redact if v and len(v) >= 4}, key=len, reverse=True)
        threading.Thread(target=self._pump, args=(run, proc, log, secrets_to_hide), daemon=True).start()
        self._prune()
        return run

    def _pump(self, run: Run, proc: subprocess.Popen, log: Path, hide: list[str]) -> None:
        with log.open("a", encoding="utf-8") as out:
            argv = self.command(run.action, run.check, run.diff, run.limit)
            out.write("$ ansible-playbook " + " ".join(argv[1:]) + "\n")
            out.flush()
            for raw in iter(proc.stdout.readline, b""):  # type: ignore[union-attr] (stdout=PIPE)
                line = raw.decode("utf-8", "replace")
                for value in hide:
                    line = line.replace(value, REDACTED)
                out.write(line)
                out.flush()
        returncode = proc.wait()
        with self._lock:
            run.returncode = returncode
            run.finished = time.time()
            if run.status == "running":
                run.status = "succeeded" if returncode == 0 else "failed"
            self._save(run)
            self._current = None

    def stop(self, run_id: str) -> bool:
        """Interrupts the current run like Ctrl-C; terminates it if it hasn't stopped after 15 seconds."""
        with self._lock:
            if not self._current or self._current[0].id != run_id:
                return False
            run, proc = self._current
            run.status = "stopped"
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except ProcessLookupError:
            return True

        def terminate():
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

        threading.Thread(target=terminate, daemon=True).start()
        return True

    def _prune(self) -> None:
        for run in self.history()[KEEP_RUNS:]:
            self._meta(run.id).unlink(missing_ok=True)
            (self.dir / f"{run.id}.log").unlink(missing_ok=True)
