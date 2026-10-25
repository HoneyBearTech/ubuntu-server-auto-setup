"""Starts the web UI: python -m usas_web (the container's command)."""

import logging
import sys

import uvicorn

from .config import from_env
from .main import create_app


class _QuietHealthChecks(logging.Filter):
    """Keeps the container's health checks (every 30 seconds) out of the access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/healthz" not in record.getMessage()


def main() -> None:
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
