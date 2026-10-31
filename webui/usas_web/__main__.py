"""Starts the web UI: python -m usas_web (the container's command).

python -m usas_web updates --schedule <id> runs an updates schedule now, for host cron or a systemd timer instead
of the web UI's own scheduler (docker exec usas python -m usas_web updates --schedule <id>): the same code, and
the same lock, so it never runs next to another run. It waits for the run (and an apply schedule's update) to
end and exits 0 if it succeeded.
"""

import argparse
import logging
import sys
import time
from dataclasses import replace

import uvicorn

from .config import from_env
from .main import create_app


class _QuietHealthChecks(logging.Filter):
    """Keeps the container's health checks (every 30 seconds) out of the access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/healthz" not in record.getMessage()


def updates(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m usas_web updates", description="Run an updates schedule now.")
    parser.add_argument("--schedule", required=True, help="the schedule's ID (the Updates page shows it)")
    args = parser.parse_args(argv)
    app = create_app(replace(from_env(), scheduler=False), background=False)
    state = app.state
    schedule = next((s for s in state.schedules.load() if s.id == args.schedule), None)
    if schedule is None:
        print(f"There is no schedule {args.schedule}.", file=sys.stderr)
        return 2
    run_id = state.scheduler.run(schedule, "manual-" + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    final = None
    while run_id:
        while not (run := state.runner.get(run_id)) or not run.done:
            time.sleep(2)
        final = run
        # An apply schedule's update starts when its check ends.
        time.sleep(1)
        later = [r for r in state.runner.history() if r.applied_from == run_id]
        run_id = later[0].id if later else ""
    outcome = next(s for s in state.schedules.load() if s.id == schedule.id).last_outcome
    print(outcome)
    return 0 if final is not None and final.status == "succeeded" else 1


def main() -> None:
    if sys.argv[1:2] == ["updates"]:
        sys.exit(updates(sys.argv[2:]))
    # Sign-ins (and failed ones), passkey changes and what create_app does at start-up (such as rewriting
    # the Vault), next to uvicorn's own lines; set up first, so the start-up messages are kept too.
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    ui_log = logging.getLogger("usas_web")
    ui_log.addHandler(handler)
    ui_log.setLevel(logging.INFO)
    settings = from_env()
    app = create_app(settings)
    auth = app.state.auth
    if auth.setup_code:
        print(
            f"First run: open http://localhost:{settings.port} and set the password with this setup code:\n"
            f"  Setup code: {auth.setup_code}\n"
            "(A new code is printed each time the container starts until the password is set.)",
            flush=True,
        )
    logging.getLogger("uvicorn.access").addFilter(_QuietHealthChecks())
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        proxy_headers=bool(settings.trusted_proxies),
        forwarded_allow_ips=settings.trusted_proxies or None,
        server_header=False,
        date_header=False,
        log_level="info",
    )


if __name__ == "__main__":
    sys.exit(main())
