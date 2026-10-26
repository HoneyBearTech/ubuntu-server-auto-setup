"""Runs ansible-playbook and keeps the run history.

The command is an argument list (never a shell), one run at a time, and nothing typed into a form is copied
into it: the playbook comes from the fixed table below, the options are fixed flags, the tags come from a fixed
allowlist per playbook, and the limit is rebuilt from the inventory's own host and group names. Its output goes
to runs/<id>.log line by line, with the Vault's secret values replaced by ******** in case a task ever prints
one, and the run's details to runs/<id>.json.

A run may add a second inventory (runs/<id>.inventory.yml, written by the caller from validated values, such as
the VMs page's hosts) and extra variables from a fixed allowlist per playbook (runs/<id>.vars.yml, every value
!unsafe). Both are kept with the run for the audit. The summary callback (callback_plugins/usas_summary.py)
writes each host's result to runs/<id>.hosts.json. One run at a time holds across processes too: a run holds an
exclusive lock on runs/.lock, which a command-line tool can take as well.
"""

import fcntl
import json
import os
import re
import secrets
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .config import Settings
from .problems import Problem
from .store import Unsafe, dump_yaml, load_yaml, write_private

PLAYBOOKS = {
    "build-installer": ("playbooks/build-installer.yml", "Build the installer ISO on the Proxmox node", ()),
    "provision": ("playbooks/provision-vms.yml", "Create and install the VMs that don't exist yet", ()),
    "baseline": ("playbooks/baseline.yml", "Apply the baseline to the servers", ()),
    "site": ("playbooks/site.yml", "All three, in order", ()),
    "keys": ("playbooks/baseline.yml", "Re-sync the admin user's SSH keys only", ("--tags", "ssh_keys")),
}
# The tags a run of a playbook may pass with --tags (the roles' tags in playbooks/baseline.yml).
PLAYBOOK_TAGS = {
    "baseline": ("ssh_keys", "common", "firewall", "ssh", "fail2ban", "hardening"),
}
# The variables a run of a playbook may pass with -e, each with the format its value must match. A value is
# written as !unsafe, so Ansible never templates it. Nothing allows free-form -e.
EXTRA_VARS: dict[str, dict[str, re.Pattern[str]]] = {}
KEEP_RUNS = 200
# What an inventory name must look like to be used in --limit (no leading "-", no spaces or separators).
LIMIT_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")
REDACTED = "********"


class RunError(Problem, RuntimeError):
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
    # What started it: "playbook" (the Run page) or "vms" (the VMs page). Older run files have none of these.
    kind: str = "playbook"
    tags: list[str] = field(default_factory=list)
    # The hosts a VMs page run was planned for (VM ID, name, address and where it came from, host key).
    targets: list[dict[str, Any]] = field(default_factory=list)
    plan: str = ""  # the plan's hash: a real run needs a check run of the same plan first
    inventory: bool = False  # it had its own inventory, runs/<id>.inventory.yml
    extra_vars: bool = False  # it had extra variables, runs/<id>.vars.yml

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


def parse_tags(action: str, tags: list[str] | tuple[str, ...]) -> list[str]:
    """The tags for --tags, each from the playbook's allowlist, in the allowlist's order."""
    allowed = PLAYBOOK_TAGS.get(action, ())
    wanted = set(tags)
    unknown = sorted(t for t in wanted if t not in allowed)
    if unknown:
        raise RunError(f"{action} can't run with the tag(s) {', '.join(repr(t)[:40] for t in unknown)}.")
    return [t for t in allowed if t in wanted]


def extra_vars_yaml(action: str, values: dict[str, Any]) -> str:
    """The extra-variables file for a run: only the playbook's allowed variables, each value checked against its
    format and written as !unsafe."""
    allowed = EXTRA_VARS.get(action, {})
    data = {}
    for key, value in values.items():
        pattern = allowed.get(key)
        if pattern is None:
            raise RunError(f"{action} doesn't take the variable {key[:40]!r}.")
        text = str(value)
        if not pattern.match(text):
            raise RunError(f"{key} isn't in the accepted format.")
        data[key] = Unsafe(text)
    return dump_yaml(data)


def inventory_names(text: str) -> set[str]:
    """The host and group names of a YAML inventory (for --limit)."""
    names: set[str] = set()

    def walk(groups: Any) -> None:
        if not isinstance(groups, dict):
            return
        for group, body in groups.items():
            names.add(str(group))
            body = body if isinstance(body, dict) else {}
            names.update(str(h) for h in (body.get("hosts") or {}))
            walk(body.get("children"))

    walk(load_yaml(text) or {})
    return names


class Runner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dir = settings.runs_dir
        self._lock = threading.Lock()
        self._current: tuple[Run, subprocess.Popen] | None = None
        self._file_lock: int | None = None  # the open runs/.lock while a run holds it
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

    def path(self, run_id: str, suffix: str) -> Path:
        """runs/<id><suffix>, such as .inventory.yml or .hosts.json."""
        self.log_path(run_id)  # checks the ID's format
        return self.dir / f"{run_id}{suffix}"

    def _save(self, run: Run) -> None:
        write_private(self._meta(run.id), json.dumps(asdict(run)) + "\n")

    @staticmethod
    def _load(path: Path) -> Run:
        data = json.loads(path.read_text())
        known = {f.name for f in fields(Run)}
        return Run(**{k: v for k, v in data.items() if k in known})

    def get(self, run_id: str) -> Run | None:
        try:
            self.log_path(run_id)  # checks the ID's format
        except RunError:
            return None
        path = self._meta(run_id)
        if not path.exists():
            return None
        return self._load(path)

    def history(self) -> list[Run]:
        runs = []
        for path in sorted(self.dir.glob("[0-9]*-*-*.json"), reverse=True):
            if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[a-f0-9]{6}\.json", path.name):
                continue  # runs/<id>.hosts.json and the like
            try:
                runs.append(self._load(path))
            except (ValueError, TypeError):
                continue
        return runs

    def hosts(self, run_id: str, redact: list[str] | tuple[str, ...] = ()) -> dict[str, Any] | None:
        """The summary callback's per-host results of a run, or None if it wrote none; the values of redact are
        replaced by ******** as in the log."""
        try:
            data = json.loads(self.path(run_id, ".hosts.json").read_text())
        except (OSError, ValueError, RunError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("hosts"), dict):
            return None
        hide = sorted({v for v in redact if v and len(v) >= 4}, key=len, reverse=True)

        def scrub(value: Any) -> Any:
            if isinstance(value, str):
                for secret in hide:
                    value = value.replace(secret, REDACTED)
                return value
            if isinstance(value, list):
                return [scrub(v) for v in value]
            if isinstance(value, dict):
                return {scrub(k): scrub(v) for k, v in value.items()}
            return value

        return scrub(data)

    def current(self) -> Run | None:
        with self._lock:
            return self._current[0] if self._current else None

    def command(self, action: str, check: bool, diff: bool, limit: str, *, inventories: tuple[Path, ...] = (),
                tags: tuple[str, ...] | list[str] = (), extra_vars: Path | None = None) -> list[str]:
        key = next((k for k in PLAYBOOKS if k == action), None)
        if key is None:
            raise RunError("unknown action")
        playbook, _, extra = PLAYBOOKS[key]
        s = self.settings
        argv = [s.ansible_playbook, "-i", str(s.inventory_file)]
        for path in inventories:
            argv += ["-i", str(path)]
        argv += ["--vault-password-file", str(s.vault_password_file)]
        argv += list(extra)
        tags = parse_tags(key, list(tags))
        if tags:
            argv.append("--tags=" + ",".join(tags))
        if extra_vars is not None:
            argv += ["-e", f"@{extra_vars}"]
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
            # The per-host results for the run's page (callback_plugins/usas_summary.py).
            "ANSIBLE_CALLBACK_PLUGINS": str(s.project_dir / "callback_plugins"),
            "ANSIBLE_CALLBACKS_ENABLED": "usas_summary",
        }
        for name in ("VIRTUAL_ENV", "ANSIBLE_COLLECTIONS_PATH", "TMPDIR"):
            if os.environ.get(name):
                env[name] = os.environ[name]
        if s.ca_bundle.exists():
            env["SSL_CERT_FILE"] = env["REQUESTS_CA_BUNDLE"] = str(s.ca_bundle)
        env.update(s.run_env)
        return env

    def _argv(self, run: Run) -> list[str]:
        return self.command(run.action, run.check, run.diff, run.limit, tags=run.tags,
                            inventories=(self.path(run.id, ".inventory.yml"),) if run.inventory else (),
                            extra_vars=self.path(run.id, ".vars.yml") if run.extra_vars else None)

    def _take_file_lock(self) -> None:
        """Takes runs/.lock, or raises RunError if another process (or run) holds it."""
        fd = os.open(self.dir / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise RunError("Another run is still going (started outside the web UI). Wait for it to finish.") from None
        self._file_lock = fd

    def _release_file_lock(self) -> None:
        if self._file_lock is not None:
            fcntl.flock(self._file_lock, fcntl.LOCK_UN)
            os.close(self._file_lock)
            self._file_lock = None

    def start(self, action: str, check: bool, diff: bool, limit_text: str, names: set[str],
              redact: list[str], *, kind: str = "playbook", inventory: str | None = None,
              tags: tuple[str, ...] | list[str] = (), extra_vars: dict[str, Any] | None = None,
              targets: list[dict[str, Any]] | None = None, plan: str = "") -> Run:
        """Starts a run. limit_text is checked against names, the inventory's host and group names, plus those
        of inventory: YAML text for a second inventory, written by the caller from validated values only."""
        action = next((k for k in PLAYBOOKS if k == action), "")
        if not action:
            raise RunError("unknown action")
        tags = parse_tags(action, list(tags))
        variables = extra_vars_yaml(action, extra_vars) if extra_vars else None
        if inventory is not None:
            names = names | inventory_names(inventory)
        limit = parse_limit(limit_text, names)
        if not self.settings.inventory_file.exists():
            raise RunError("Save the settings first: there is no inventory yet.")
        with self._lock:
            if self._current is not None:
                raise RunError("Another run is still going. Wait for it to finish or stop it.")
            self._take_file_lock()
            try:
                run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
                run = Run(id=run_id, action=action, check=check, diff=diff, limit=limit, status="running",
                          started=time.time(), kind=kind, tags=tags, targets=list(targets or []), plan=plan,
                          inventory=inventory is not None, extra_vars=variables is not None)
                log = self.log_path(run_id)
                write_private(log, "")
                if inventory is not None:
                    write_private(self.path(run_id, ".inventory.yml"), inventory)
                if variables is not None:
                    write_private(self.path(run_id, ".vars.yml"), variables)
                env = self.environment()
                env["USAS_SUMMARY_FILE"] = str(self.path(run_id, ".hosts.json"))
                proc = subprocess.Popen(  # noqa: S603 (an argument list, no shell)
                    self._argv(run), cwd=self.settings.project_dir, env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
                )
            except BaseException:
                self._release_file_lock()
                raise
            self._current = (run, proc)
            self._save(run)
        secrets_to_hide = sorted({v for v in redact if v and len(v) >= 4}, key=len, reverse=True)
        threading.Thread(target=self._pump, args=(run, proc, log, secrets_to_hide), daemon=True).start()
        self._prune()
        return run

    def _pump(self, run: Run, proc: subprocess.Popen, log: Path, hide: list[str]) -> None:
        with log.open("a", encoding="utf-8") as out:
            argv = self._argv(run)
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
            self._release_file_lock()

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
            for suffix in (".log", ".inventory.yml", ".vars.yml", ".hosts.json"):
                self.path(run.id, suffix).unlink(missing_ok=True)
