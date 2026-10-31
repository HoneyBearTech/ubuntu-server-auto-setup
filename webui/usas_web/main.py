"""The web UI: a JSON API that writes the inventory and the Vault and runs the playbooks, and the pages of the
React app (webui/frontend) that uses it.

Every page URL answers with the same small HTML shell, after the same sign-in checks as the API; the app then
asks /api/<page> for the page's data and posts its forms there, with the same field names as the forms had.
The server checks everything it is sent: the app only shows what the server says.
"""

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
import zoneinfo
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, nodes, status, vmplan, vms
from .audit import AuditLog, session_fingerprint
from .auth import Auth, csrf_ok, password_problem
from .config import LOOPBACK_HOSTS, Settings, from_env
from .passkeys import MAX_NAME, MAX_PASSKEYS, PasskeyError, Passkeys, clean_name, loggable
from .remote import PveApi, RemoteProblem, VmLister, keys_url, preview_keys, test_proxmox, tls_verify
from .runner import PLAYBOOKS, Run, RunError, Runner
from .schema import Field, Invalid, Schema, display_value, parse_value
from .sshkeys import (
    KeyProblem,
    Keys,
    check_host,
    describe_public_key,
    remove_ca,
    save_ca,
    saved_ca,
    vm_alias,
)
from .store import Config, Store

HERE = Path(__file__).parent
# The page URLs and their titles (the browser tab shows the title before the app has loaded).
PAGES = {
    "/": "Overview",
    "/proxmox": "Proxmox API",
    "/ssh": "SSH key",
    "/defaults": "Defaults",
    "/servers": "Servers",
    "/hardening": "Hardening",
    "/vms": "VMs",
    "/run": "Run",
    "/runs": "History",
    "/account": "Account",
}
SECTION_TITLES = {"proxmox": "Proxmox API", "defaults": "Defaults for every server", "hardening": "Hardening"}
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
    "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
MAX_PART = 256 * 1024
API_FIELDS = ("pve_api_host", "pve_api_port", "pve_api_user", "pve_api_token_id", "pve_api_token_secret",
              "pve_validate_certs")
ASSETS = ("favicon.svg",)
PUBLIC_PATHS = ("/healthz", "/favicon.ico")
STATUS_PATH = "/api/status"  # for dashboards: its own token, no session (status.py)
THEMES = ("dark", "light", "system")  # dark is the default
THEME_COOKIE = "usas_theme"
FRONTEND_ENTRY = "src/main.tsx"
VM_API_TIMEOUT = 10  # longest a VMs page call to the API may take, whatever pve_api_timeout says
PLAN_TTL = 600  # a reviewed plan can be run for 10 minutes
CHECK_TTL = 1800  # a real run needs a passing check run of the same plan from the last 30 minutes
# Sign-ins and passkey changes, in the container's log (python -m usas_web sets it up).
log = logging.getLogger("usas_web")


def asset_urls(static_dir: Path) -> dict[str, str]:
    """The static files' URLs, each with a hash of its contents: a changed file gets a new URL, so a browser
    never keeps an old file after an upgrade."""
    return {name: f"/static/{name}?v={hashlib.sha256((static_dir / name).read_bytes()).hexdigest()[:12]}"
            for name in ASSETS}


def frontend_assets(app_dir: Path) -> dict[str, Any] | None:
    """The built app's entry script and stylesheets, from Vite's manifest (their names carry a hash of their
    contents already); None if the app hasn't been built (npm run build in webui/frontend)."""
    try:
        entry = json.loads((app_dir / ".vite" / "manifest.json").read_text())[FRONTEND_ENTRY]
        return {"js": f"/static/app/{entry['file']}", "css": [f"/static/app/{c}" for c in entry.get("css", [])]}
    except (OSError, ValueError, KeyError, TypeError):
        return None


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


class SignIn(Exception):
    """An API call that needs a sign-in (or the first-run setup): the app goes to url."""

    def __init__(self, url: str):
        self.url = url


class Forbidden(Exception):
    pass


@dataclass
class Problems:
    """Errors per field name, shown next to the fields and in a summary at the top of the form."""

    errors: dict[str, str]

    def __bool__(self) -> bool:
        return bool(self.errors)


def reply(status: int = 200, **data: Any) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def run_json(run: Run | None) -> dict[str, Any] | None:
    if run is None:
        return None
    data = asdict(run)
    data["done"] = run.done
    data["playbook"] = PLAYBOOKS[run.action][0] if run.action in PLAYBOOKS else None
    return data


def field_json(f: Field, config: Config | None = None) -> dict[str, Any]:
    """What the app needs to draw a field: the same label, help, default and variable name the forms showed."""
    shown_default = None
    if f.default not in (None, "", []) and f.kind != "secret":
        shown_default = ", ".join(str(display_value(f, f.default)).splitlines())
    data = {"name": f.name, "label": f.label, "kind": f.kind, "help": f.help, "required": f.required,
            "advanced": f.advanced, "variable": f.vault or f.name, "default": shown_default,
            "choices": list(f.choices) if f.choices else None}
    if f.choice_labels:
        data["choice_labels"] = dict(f.choice_labels)
    if f.kind == "secret":
        data["saved"] = bool(config and config.secret_is_set(f.vault))
    return data


def value_json(f: Field, value: Any) -> str | bool:
    if f.kind == "bool":
        return bool(value)
    if f.kind == "secret":
        return ""  # a secret is never sent back
    shown = display_value(f, value)
    return "" if shown is None else str(shown)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or from_env()
    os.umask(0o077)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    schema = Schema(settings.project_dir)
    store = Store(settings, schema)
    auth = Auth(settings)
    passkeys = Passkeys(settings)
    audit = AuditLog(settings.data_dir / "audit.jsonl")

    def run_ended(run: Run) -> None:
        if run.kind == "vms":
            hosts = (runner.hosts(run.id) or {}).get("hosts") or {}
            audit.record("run.finished", run=run.id, check=run.check, status=run.status, returncode=run.returncode,
                         hosts={name: {k: r.get(k) for k in ("ok", "changed", "failed", "unreachable")}
                                for name, r in hosts.items() if isinstance(r, dict)})

    runner = Runner(settings, on_finish=run_ended)
    keys = Keys(settings)
    lister = VmLister(releases=vms.catalog_releases(schema.os_catalog))
    tokens = status.StatusTokens(settings.status_token_file, store.vault_password)
    throttle = status.Throttle()
    counts = status.ClusterCounts()
    plan = nodes.load_plan(settings.project_dir)
    store.vault_password()  # creates it on the first run
    if store.repair_secrets():
        log.info("Rewrote the Vault so that values such as an all-digit password load as text")
    try:
        timezones = sorted(zoneinfo.available_timezones())
    except OSError:  # pragma: no cover (no tz database)
        timezones = []

    app = FastAPI(title="ubuntu-server-auto-setup web UI", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.settings, app.state.auth, app.state.store, app.state.runner = settings, auth, store, runner
    app.state.schema, app.state.keys, app.state.vm_lister = schema, keys, lister
    app.state.passkeys = passkeys
    app.state.audit = audit
    app.state.status_tokens, app.state.status_throttle, app.state.cluster_counts = tokens, throttle, counts
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(version=__version__, assets=asset_urls(HERE / "static"))

    def is_https(request: Request) -> bool:
        return request.url.scheme == "https" or settings.secure_cookies == "true"

    def cookie_name(request: Request) -> str:
        return "__Host-usas_session" if is_https(request) else "usas_session"

    # --- Guard: allowed hosts, sessions, Origin checks and security headers -------------------------------

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.url.hostname or "").lower()
        host = f"[{host}]" if ":" in host else host
        if host not in settings.allowed_hosts:
            return PlainTextResponse(
                "This host name isn't allowed. Open the UI at http://localhost:<port>, or add the name to "
                "USAS_ALLOWED_HOSTS (see docs/web-ui.md).", status_code=400)
        if host not in LOOPBACK_HOSTS and request.url.scheme != "https":
            return PlainTextResponse("Beyond localhost the UI only works over HTTPS (see docs/web-ui.md).",
                                     status_code=400)
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/static/"):
            response = await call_next(request)
            return _headers(response, cache=path != "/healthz")
        if path == STATUS_PATH:
            # A dashboard's call: the route checks its token; it gets no session and no cookie.
            return _headers(await call_next(request))
        session = auth.get_session(request.cookies.get(cookie_name(request)))
        fresh = session is None
        if session is None:
            session = auth.new_session()
        request.state.session = session
        request.state.logout = False
        if request.method in UNSAFE_METHODS:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.url.netloc}"
            site = request.headers.get("sec-fetch-site")
            if (origin and origin != expected) or (site and site not in ("same-origin", "none")):
                return PlainTextResponse("Cross-site request refused.", status_code=403)
        response = await call_next(request)
        current = request.state.session
        if request.state.logout:
            response.delete_cookie(cookie_name(request), path="/", secure=is_https(request), httponly=True,
                                   samesite="strict")
        elif fresh or current.sid != session.sid:
            response.set_cookie(cookie_name(request), current.sid, path="/", secure=is_https(request),
                                httponly=True, samesite="strict")
        return _headers(response)

    def _headers(response: Response, cache: bool = False) -> Response:
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # same-origin, not no-referrer: with no-referrer, browsers send "Origin: null" on posts, which the
        # Origin check above would refuse. Nothing is sent to other sites either way.
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        response.headers["Cache-Control"] = "private, max-age=300" if cache else "no-store"
        return response

    @app.exception_handler(Redirect)
    async def _redirect(request: Request, exc: Redirect):
        return RedirectResponse(exc.url, status_code=303)

    @app.exception_handler(SignIn)
    async def _sign_in(request: Request, exc: SignIn):
        return reply(401, error="Sign in first.", redirect=exc.url)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden):
        return reply(403, error="The form expired or didn't come from this page. Reload the page and try again.")

    # --- Helpers -----------------------------------------------------------------------------------------

    def theme(request: Request) -> str:
        value = request.cookies.get(THEME_COOKIE, "")
        return value if value in THEMES else "dark"

    def require_login(request: Request, api: bool = True):
        """The signed-in session. Otherwise a page redirects to the sign-in (or the first-run setup), and an
        API call answers 401 with where the app should go."""
        if not auth.is_configured():
            url = "/setup"
        elif not request.state.session.authenticated:
            target = request.headers.get("x-usas-page", "") if api else request.url.path
            url = "/login" + (f"?next={quote(safe_next(target))}" if safe_next(target) != "/" else "")
        else:
            return request.state.session
        raise SignIn(url) if api else Redirect(url)

    async def checked_form(request: Request):
        """The posted form, if the request carries the session's CSRF token in its X-CSRF-Token header. A page
        on another site can't set that header without the browser asking this server first (it never agrees)."""
        if not csrf_ok(request.state.session, request.headers.get("x-csrf-token")):
            raise Forbidden()
        return await request.form(max_files=2, max_fields=2000, max_part_size=MAX_PART)

    def shell(request: Request, title: str) -> HTMLResponse:
        session = request.state.session
        # The manifest is read each time (it is small), so a rebuilt app shows without a restart.
        return templates.TemplateResponse(request, "app.html", {
            "title": title, "theme": theme(request), "csrf": session.csrf, "signed_in": session.authenticated,
            "frontend": frontend_assets(HERE / "static" / "app")})

    def safe_next(target: str | None) -> str:
        if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
            return "/"
        path = urlsplit(target).path
        if path in PAGES or (path.startswith("/runs/") and "/" not in path[6:] and runner.get(path[6:])):
            return path
        return "/"

    def who(request: Request) -> dict[str, str]:
        """The session and the client, as the audit log names them."""
        return {"session": session_fingerprint(request.state.session.sid), "client": client(request)}

    def client(request: Request) -> str:
        return request.client.host if request.client else "?"

    # --- Health, icon, the app's pages ----------------------------------------------------------------------

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return PlainTextResponse("ok")

    # Browsers and bookmark tools ask for /favicon.ico whatever the page links; answer with the SVG icon.
    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return FileResponse(HERE / "static" / "favicon.svg", media_type="image/svg+xml")

    def page_route(path: str, title: str):
        async def page(request: Request):
            require_login(request, api=False)
            return shell(request, title)

        app.add_api_route(path, page, methods=["GET"], response_class=HTMLResponse, include_in_schema=False)

    for path, title in PAGES.items():
        page_route(path, title)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_page(request: Request, run_id: str):
        require_login(request, api=False)
        if runner.get(run_id) is None:
            raise Redirect("/runs")
        return shell(request, "Run")

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_page(request: Request):
        if auth.is_configured():
            raise Redirect("/login")
        return shell(request, "Set up")

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: str = "/"):
        if not auth.is_configured():
            raise Redirect("/setup")
        if request.state.session.authenticated:
            raise Redirect(safe_next(next))
        return shell(request, "Sign in")

    # --- Session, theme, first run, sign-in -------------------------------------------------------------------

    def passkey_info() -> dict[str, Any]:
        """What the sign-in page needs to offer passkeys: whether any is registered, whether one is required,
        and the host name they belong to (the page hides them where the browser can't use them)."""
        return {"registered": passkeys.count() > 0, "required": passkeys.required(), "rp_id": settings.rp_id,
                "public_url": settings.public_url}

    @app.get("/api/session")
    async def session_info(request: Request):
        session = request.state.session
        return reply(configured=auth.is_configured(), signed_in=session.authenticated, csrf=session.csrf,
                     version=__version__, theme=theme(request), passkeys=passkey_info())

    @app.post("/api/theme")
    async def set_theme(request: Request):
        form = await checked_form(request)
        # The cookie holds one of the fixed names, never the text that was sent.
        value = {name: name for name in THEMES}.get(str(form.get("theme", "")))
        if value is None:
            return reply(422, error="Choose dark, light or system.")
        response = reply(theme=value)
        response.set_cookie(THEME_COOKIE, value, max_age=400 * 24 * 3600, path="/", secure=is_https(request),
                            httponly=True, samesite="strict")
        return response

    @app.post("/api/setup")
    async def setup(request: Request):
        if auth.is_configured():
            return reply(409, error="The password is set already.", redirect="/login")
        form = await checked_form(request)
        wait = auth.wait_seconds(client(request))
        if wait:
            return reply(429, error=f"Too many attempts. Try again in {wait} seconds.")
        if not auth.check_setup_code(str(form.get("setup_code", ""))):
            auth.record_failure(client(request))
            return reply(422, error="That setup code is wrong. It is in the container's log: docker logs usas")
        problem = password_problem(str(form.get("password", "")), str(form.get("confirm", "")))
        if problem:
            return reply(422, error=problem)
        auth.set_password(str(form["password"]))
        auth.clear_failures(client(request))
        request.state.session = auth.log_in(request.state.session)
        return reply(message="Password set. Start with the Proxmox API.", redirect="/proxmox",
                     csrf=request.state.session.csrf)

    @app.post("/api/login")
    async def login(request: Request):
        if not auth.is_configured():
            return reply(409, error="Set the password first.", redirect="/setup")
        form = await checked_form(request)
        target = safe_next(str(form.get("next", "/")))
        if passkeys.required():
            # The password isn't checked at all, so it can't be guessed here either.
            log.warning("Password sign-in refused from %s: a passkey is required", client(request))
            return reply(403, error="Sign in with a passkey: signing in with the password is turned off.")
        wait = auth.wait_seconds(client(request))
        if wait:
            return reply(429, error=f"Too many failed attempts. Try again in {wait} seconds.")
        if not auth.verify_password(str(form.get("password", ""))):
            auth.record_failure(client(request))
            log.warning("Sign-in with a wrong password from %s", client(request))
            return reply(401, error="That password is wrong.")
        auth.clear_failures(client(request))
        request.state.session = auth.log_in(request.state.session)
        log.info("Signed in with the password from %s", client(request))
        return reply(redirect=target, csrf=request.state.session.csrf)

    @app.post("/api/logout")
    async def logout(request: Request):
        await checked_form(request)
        auth.end(request.state.session)
        request.state.logout = True
        return reply(redirect="/login")

    @app.post("/api/account")
    async def account(request: Request):
        session = require_login(request)
        form = await checked_form(request)
        if not auth.verify_password(str(form.get("current", ""))):
            return reply(422, error="The current password is wrong.")
        problem = password_problem(str(form.get("password", "")), str(form.get("confirm", "")))
        if problem:
            return reply(422, error=problem)
        auth.set_password(str(form["password"]))
        auth.end_all_except(session)
        return reply(message="Password changed. Other sessions were signed out.")

    # --- Passkeys ----------------------------------------------------------------------------------------

    def passkey_failed(request: Request, what: str, exc: PasskeyError, status: int = 401) -> JSONResponse:
        log.warning("%s failed from %s: %s", what, client(request), exc.reason)
        return reply(status, error=exc.message)

    @app.get("/api/account")
    async def account_info(request: Request):
        session = require_login(request)
        return reply(passkeys=passkeys.listing(), require_passkey=passkeys.required(), broken=passkeys.broken(),
                     rp_id=settings.rp_id, public_url=settings.public_url, max_passkeys=MAX_PASSKEYS,
                     max_name=MAX_NAME, current=session.signed_in_with, status_token=tokens.info())

    @app.post("/api/passkeys/register-options")
    async def passkey_register_options(request: Request):
        session = require_login(request)
        form = await checked_form(request)
        # Adding a way in needs the password, as changing it does: a session left open isn't enough.
        wait = auth.wait_seconds(client(request))
        if wait:
            return reply(429, error=f"Too many failed attempts. Try again in {wait} seconds.")
        name = clean_name(str(form.get("passkey_name", "")))
        errors = {}
        if not name:
            errors["passkey_name"] = "Name the passkey, such as the device or password manager it is in."
        elif len(name) > MAX_NAME:
            errors["passkey_name"] = f"Use at most {MAX_NAME} characters."
        if not auth.verify_password(str(form.get("passkey_current", ""))):
            auth.record_failure(client(request))
            errors["passkey_current"] = "The current password is wrong."
        if errors:
            return reply(422, errors=errors)
        try:
            options = passkeys.registration_options(session, name)
        except PasskeyError as exc:
            return reply(409, error=exc.message)
        return reply(options=json.loads(options))

    @app.post("/api/passkeys/register")
    async def passkey_register(request: Request):
        session = require_login(request)
        form = await checked_form(request)
        try:
            record = passkeys.register(session, str(form.get("credential", "")))
        except PasskeyError as exc:
            return passkey_failed(request, "Adding a passkey", exc, 422)
        log.info("Passkey \"%s\" added from %s", loggable(record["name"]), client(request))
        return reply(message=f"Added the passkey “{record['name']}”.")

    @app.post("/api/passkeys/remove")
    async def passkey_remove(request: Request):
        session = require_login(request)
        form = await checked_form(request)
        credential_id = str(form.get("id", ""))
        try:
            name = passkeys.remove(credential_id)
        except PasskeyError as exc:
            return reply(409, error=exc.message)
        # Sessions signed in with it end too (this one stays, so the page can say what happened).
        ended = auth.end_all_except(session, lambda s: s.signed_in_with == credential_id)
        log.info("Passkey \"%s\" removed from %s", loggable(name), client(request))
        signed_out = " Other sessions signed in with it were signed out." if ended else ""
        return reply(message=f"Removed the passkey “{name}”.{signed_out}")

    @app.post("/api/passkeys/require")
    async def passkey_require(request: Request):
        session = require_login(request)
        form = await checked_form(request)
        on = form.get("require_passkey") == "on"
        try:
            passkeys.set_required(on)
        except PasskeyError as exc:
            return reply(409, error=exc.message)
        log.info("Passkey requirement turned %s from %s", "on" if on else "off", client(request))
        if not on:
            return reply(message="Signing in with the password works again.")
        ended = auth.end_all_except(session, lambda s: s.signed_in_with == "password")
        signed_out = " Other sessions signed in with the password were signed out." if ended else ""
        return reply(message=f"A passkey is now required to sign in.{signed_out}")

    @app.post("/api/passkeys/login-options")
    async def passkey_login_options(request: Request):
        if not auth.is_configured():
            return reply(409, error="Set the password first.", redirect="/setup")
        await checked_form(request)
        wait = auth.wait_seconds(client(request))
        if wait:
            return reply(429, error=f"Too many failed attempts. Try again in {wait} seconds.")
        try:
            options = passkeys.authentication_options(request.state.session)
        except PasskeyError as exc:
            return reply(409, error=exc.message)
        return reply(options=json.loads(options))

    @app.post("/api/passkeys/login")
    async def passkey_login(request: Request):
        if not auth.is_configured():
            return reply(409, error="Set the password first.", redirect="/setup")
        form = await checked_form(request)
        target = safe_next(str(form.get("next", "/")))
        wait = auth.wait_seconds(client(request))
        if wait:
            return reply(429, error=f"Too many failed attempts. Try again in {wait} seconds.")
        try:
            record = passkeys.authenticate(request.state.session, str(form.get("credential", "")))
        except PasskeyError as exc:
            auth.record_failure(client(request))
            return passkey_failed(request, "Sign-in with a passkey", exc)
        auth.clear_failures(client(request))
        request.state.session = auth.log_in(request.state.session, via=record["id"])
        log.info("Signed in with the passkey \"%s\" from %s", loggable(record["name"]), client(request))
        return reply(redirect=target, csrf=request.state.session.csrf)

    # --- The status API for dashboards (status.py) ----------------------------------------------------------

    @app.post("/api/status-token/create")
    async def status_token_create(request: Request):
        require_login(request)
        form = await checked_form(request)
        # A token reads the status from anywhere the UI can be reached: making one needs the password, as
        # adding a passkey does.
        wait = auth.wait_seconds(client(request))
        if wait:
            return reply(429, error=f"Too many failed attempts. Try again in {wait} seconds.")
        if not auth.verify_password(str(form.get("status_current", ""))):
            auth.record_failure(client(request))
            return reply(422, errors={"status_current": "The current password is wrong."})
        replaced = tokens.exists()
        token = tokens.create()
        log.info("Status API token %s from %s", "replaced" if replaced else "created", client(request))
        what = "Replaced the token: the old one no longer works." if replaced else "Created the token."
        return reply(token=token, message=f"{what} Copy it now: it isn't shown again.")

    @app.post("/api/status-token/revoke")
    async def status_token_revoke(request: Request):
        require_login(request)
        await checked_form(request)
        if not tokens.revoke():
            return reply(message="There is no token: the status API is off.")
        log.info("Status API token revoked from %s", client(request))
        return reply(message="Revoked the token. The status API is off.")

    def refused(status_code: int, error: str, retry: int = 0) -> JSONResponse:
        headers = {"Retry-After": str(retry)} if retry else {"WWW-Authenticate": 'Bearer realm="usas"'}
        return JSONResponse({"error": error}, status_code=status_code, headers=headers)

    def status_data() -> dict[str, Any]:
        """The summary (status.summary); the cluster is asked at most once a minute, in this thread."""
        config = store.load()
        api = pve_api(config)
        found = (counts.get(api.identity, lambda: vms.parse_resources(api.get("/cluster/resources?type=vm")))
                 if api is not None else None)
        return status.summary(__version__, runner.current(), runner.history(), readiness(config), config.servers,
                              found, time.time())

    @app.get(STATUS_PATH)
    async def status_api(request: Request):
        who = client(request)
        if not tokens.exists():
            return reply(404, error="The status API is off: create a token on the Account page.")
        wait = throttle.wait_seconds(who)
        if wait:
            return refused(429, "Too many calls with a wrong token. Try again later.", wait)
        header = request.headers.get("authorization")
        token = status.parse_bearer(header)
        if token is None or not tokens.verify(token):
            throttle.record_failure(who)
            reason = "no token" if not header else "not a status token" if token is None else "wrong token"
            log.warning("Status API call refused from %s: %s", who, reason)
            return refused(401, "The token is missing or wrong. Create one on the Account page.")
        throttle.clear_failures(who)
        wait = throttle.over_limit()
        if wait:
            return refused(429, f"At most {status.PER_MINUTE} calls a minute.", wait)
        tokens.used(who)
        if throttle.should_log(who):
            log.info("Status API used from %s", who)
        return reply(**await asyncio.to_thread(status_data))

    # --- Overview ----------------------------------------------------------------------------------------

    def nodes_ready(config: Config) -> bool:
        """Every node the servers use has an SSH address with a trusted host key."""
        used = config.nodes_in_use()
        return bool(used) and all(config.node_addresses.get(n) and keys.trusted(config.node_addresses[n])
                                  for n in used)

    def gateway_set(config: Config) -> bool:
        """Whether every server has its default route: the Defaults page's gateway, DHCP, or an extra card's."""
        if config.values.get("net_gateway"):
            return True
        return bool(config.servers) and all(
            srv.get("net_mode") == "dhcp" or any(nic.get("gateway") for nic in srv.get("vm_extra_nics") or [])
            for srv in config.servers)

    def readiness(config: Config) -> list[dict[str, Any]]:
        v = config.values
        steps = [
            ("Proxmox API host, token and node", bool(v.get("pve_api_host") and v.get("pve_node")
                                                      and config.secret_is_set("vault_pve_api_token_id")
                                                      and config.secret_is_set("vault_pve_api_token_secret")),
             "/proxmox"),
            ("SSH key", bool(keys.public_key()), "/ssh"),
            ("Each Proxmox node's SSH address and trusted host key", nodes_ready(config), "/ssh"),
            ("Admin password, gateway and SSH keys for the servers",
             bool(config.secret_is_set("vault_admin_password") and gateway_set(config)
                  and (v.get("github_user") or v.get("admin_extra_ssh_keys") or config.authorize_ui_key)),
             "/defaults"),
            ("At least one server", bool(config.servers), "/servers"),
        ]
        return [{"label": label, "done": done, "href": href} for label, done, href in steps]

    @app.get("/api/overview")
    async def overview(request: Request):
        require_login(request)
        config = store.load()
        return reply(steps=readiness(config), runs=[run_json(r) for r in runner.history()[:5]],
                     current_run=run_json(runner.current()), node=config.values.get("pve_node") or "",
                     cluster=store.cluster().get("name", ""))

    # --- Form sections -----------------------------------------------------------------------------------

    def form_values(fields: list[Field], config: Config) -> dict[str, Any]:
        return {f.name: config.values.get(f.name, f.default) if not f.vault else config.secrets.get(f.vault, "")
                for f in fields}

    def apply_section(section: str, form, config: Config,
                      only: tuple[str, ...] = ()) -> Problems:
        """Parses the section's fields (or only some) from form into config; returns the errors."""
        errors: dict[str, str] = {}
        for f in schema.sections[section]:
            if only and f.name not in only:
                continue
            try:
                value = parse_value(f, form.get(f.name))
            except Invalid as exc:
                errors[f.name] = exc.message
                continue
            if f.name == "timezone" and timezones and value not in timezones:
                errors[f.name] = "Pick a time zone from the tz database, such as Europe/Berlin or Etc/UTC."
                continue
            if f.vault:
                if value:
                    config.secrets[f.vault] = value
                elif not f.required and form.get(f"{f.name}__clear") in ("on", "true"):
                    config.secrets.pop(f.vault, None)
                elif f.required and not config.secret_is_set(f.vault):
                    errors[f.name] = f"{f.label} is required."
                continue
            config.values[f.name] = value
        return Problems(errors)

    def section_json(section: str, config: Config) -> dict[str, Any]:
        fields = schema.sections[section]
        values = form_values(fields, config)
        data: dict[str, Any] = {"title": SECTION_TITLES[section], "fields": [field_json(f, config) for f in fields],
                                "values": {f.name: value_json(f, values[f.name]) for f in fields}}
        if section == "proxmox":
            data.update(cluster=store.cluster(), ca=saved_ca(settings))
        elif section == "defaults":
            data.update(ui_key=keys.public_key() or "", authorize_ui_key=config.authorize_ui_key, timezones=timezones)
        return data

    def node_problem(name: str) -> str:
        """Why name can't be the node, if the last connection test listed the cluster's nodes; else ""."""
        nodes = store.cluster().get("nodes") or {}
        if not nodes or not name or name in nodes:
            return ""
        same = [n for n in nodes if n.lower() == name.lower()]
        hint = f" Node names are case-sensitive: did you mean {same[0]}?" if same else ""
        return f"{name} isn't a node in the cluster ({', '.join(sorted(nodes))}).{hint}"

    @app.get("/api/proxmox")
    async def proxmox_data(request: Request):
        require_login(request)
        return reply(**section_json("proxmox", store.load()))

    @app.post("/api/proxmox")
    async def proxmox(request: Request):
        require_login(request)
        form = await checked_form(request)
        action = form.get("action", "save")
        config = store.load()
        if action in ("save-ca", "fetch-ca", "remove-ca"):
            try:
                if action == "remove-ca":
                    remove_ca(settings)
                    return reply(message="The CA certificate was removed.")
                if action == "save-ca":
                    pem = str(form.get("ca_pem", ""))
                else:
                    pem = keys.fetch_ca(config.node_addresses.get(config.default_node, ""), store.node_user(config))
                certs = save_ca(settings, pem)
                return reply(message="Saved the CA certificate " + ", ".join(c["subject"] for c in certs) + ".")
            except KeyProblem as exc:
                return reply(422, ca_error=exc.message)
        only = API_FIELDS if action == "test" else ()
        problems = apply_section("proxmox", form, config, only)
        if problems:
            return reply(422, errors=problems.errors)
        if action == "test":
            v = config.values
            try:
                result = test_proxmox(
                    str(v["pve_api_host"]), int(v.get("pve_api_port") or 8006), str(v["pve_api_user"]),
                    config.secrets.get("vault_pve_api_token_id", ""),
                    config.secrets.get("vault_pve_api_token_secret", ""),
                    bool(v.get("pve_validate_certs")), settings.ca_bundle,
                )
            except RemoteProblem as exc:
                return reply(test_error=exc.message)
            # The node list (names and status only) is kept for the node dropdown; the form isn't saved.
            store.save_cluster(result.cluster, result.status, result.iso_storages)
            message = f"Connected to Proxmox VE {result.version}"
            message += f", cluster {result.cluster}." if result.cluster else "."
            message += f" Nodes: {', '.join(result.nodes)}. The node name is now a list of them"
            if result.iso_storages:
                message += (", and the ISO storage a list of the storages that allow ISO images "
                            f"({', '.join(result.iso_storages)})")
            message += "."
            node = str(form.get("pve_node") or "")
            if node and node not in result.nodes:
                message += " The node name you entered isn't one of them."
            message += " Nothing was saved yet."
            return reply(test_result={"version": result.version, "cluster": result.cluster, "nodes": result.nodes,
                                      "iso_storages": list(result.iso_storages)},
                         message=message, cluster=store.cluster())
        errors = {}
        problem = node_problem(str(config.values.get("pve_node") or ""))
        if problem:
            errors["pve_node"] = problem
        storages = store.cluster().get("iso_storages") or {}
        iso_storage = str(config.values.get("pve_iso_storage") or "")
        if storages and iso_storage and iso_storage not in storages:
            errors["pve_iso_storage"] = (f"{iso_storage} isn't a storage that allows ISO images "
                                         f"({', '.join(storages)}). Allow ISO images on it in Proxmox "
                                         "(Datacenter → Storage → Content), then test the connection again.")
        if errors:
            return reply(422, errors=errors)
        store.save(config)
        return reply(message="Saved the Proxmox settings.")

    def disk_encryption_problems(config: Config, errors: dict[str, str]) -> None:
        """What disk encryption needs that the defaults don't have, as the role's checks would find it."""
        passphrase = "vm_disk_passphrase"  # noqa: S105 (the field's name, not a secret)
        if passphrase in errors:
            errors[passphrase] = ("The disk passphrase must be 12 to 512 printable ASCII characters (letters, "
                                  "digits, spaces and punctuation), without braces.")
        if not setting(config, "vm_disk_encryption") or "vm_disk_encryption" in errors:
            return
        needs = []
        if not setting(config, "vm_secure_boot"):
            needs.append("Secure Boot keys (under Installation, More settings)")
        if setting(config, "autoinstall_storage_layout") != "lvm":
            needs.append("the lvm disk layout")
        if needs:
            errors["vm_disk_encryption"] = "Encrypting the root disk needs " + " and ".join(needs) + "."
        if not config.secret_is_set("vault_disk_passphrase") and passphrase not in errors:
            errors[passphrase] = "Set a disk passphrase: it unlocks the disk when the TPM doesn't."

    @app.get("/api/defaults")
    async def defaults_data(request: Request):
        require_login(request)
        return reply(**section_json("defaults", store.load()))

    @app.post("/api/defaults")
    async def defaults(request: Request):
        require_login(request)
        form = await checked_form(request)
        config = store.load()
        config.authorize_ui_key = form.get("authorize_ui_key") is not None
        preview = form.get("action") == "preview"
        problems = apply_section("defaults", form, config,
                                 ("github_user", "github_keys_url", "admin_extra_ssh_keys") if preview else ())
        if not preview:
            disk_encryption_problems(config, problems.errors)
        if problems:
            return reply(422, errors=problems.errors)
        if preview:
            user = str(config.values.get("github_user") or "")
            found, preview_error = [], None
            if not user:
                preview_error = "Enter a GitHub user first."
            else:
                try:
                    found = preview_keys(keys_url(str(config.values.get("github_keys_url")), user))
                    if not found:
                        preview_error = f"{user} has no public keys on GitHub; the playbooks would refuse to run."
                except RemoteProblem as exc:
                    preview_error = exc.message
            extra = [d for d in (describe_public_key(k) for k in config.values.get("admin_extra_ssh_keys") or []) if d]
            return reply(github_user=user, preview=found, extra_keys=extra if found else [],
                         preview_error=preview_error)
        store.save(config)
        return reply(message="Saved the defaults.")

    @app.get("/api/hardening")
    async def hardening_data(request: Request):
        require_login(request)
        return reply(**section_json("hardening", store.load()))

    @app.post("/api/hardening")
    async def hardening(request: Request):
        require_login(request)
        form = await checked_form(request)
        config = store.load()
        problems = apply_section("hardening", form, config)
        admin = str(config.values.get("admin_user") or schema.by_name["admin_user"].default)
        allow = config.values.get("ssh_allow_users") or []
        if "ssh_allow_users" not in problems.errors and allow and admin not in allow:
            problems.errors["ssh_allow_users"] = f"Add the admin user ({admin}), or Ansible would be locked out."
        if problems:
            return reply(422, errors=problems.errors)
        store.save(config)
        return reply(message="Saved the hardening settings.")

    # --- Servers ------------------------------------------------------------------------------------------

    def parse_servers(form, config: Config) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """The server rows of the form (fields servers-<row>-<column>, rows that are all empty skipped) and the
        errors, keyed by field name."""
        count = min(int(form.get("server_count") or 0) if str(form.get("server_count", "0")).isdigit() else 0, 500)
        rows, errors = [], {}
        for i in range(count):
            raw = {c.name: form.get(f"servers-{i}-{c.name}", "") for c in schema.server_columns}
            # A row whose fields are empty (a choice left at its default) is skipped.
            if not any(str(raw[c.name]).strip() not in ("", str(c.default) if c.kind == "choice" else "")
                       for c in schema.server_columns):
                continue
            row: dict[str, Any] = {}
            for c in schema.server_columns:
                if not str(raw[c.name]).strip() and not c.required:
                    row[c.name] = [] if c.kind == "list" else None  # the role default applies
                    continue
                try:
                    value = parse_value(c, raw[c.name])
                except Invalid as exc:
                    errors[f"servers-{i}-{c.name}"] = exc.message
                    continue
                row[c.name] = value
            row["_index"] = i
            rows.append(row)
        seen: dict[str, dict[Any, int]] = {"name": {}, "ansible_host": {}, "pve_vmid": {}, "net_mac": {}}
        # Servers and nodes are hosts of the same inventory, so their names can't be the same.
        node_names = {*(store.cluster().get("nodes") or {}), *config.node_names(),
                      *(r["pve_node"] for r in rows if r.get("pve_node"))}
        for row in rows:
            i = row["_index"]
            network_problem(row, config, errors)
            for key in seen:
                value = row.get(key)
                if value in (None, ""):
                    continue
                if value in seen[key]:
                    errors[f"servers-{i}-{key}"] = "Another server already uses this."
                seen[key][value] = i
            if row.get("name") and row.get("name") in node_names:
                errors[f"servers-{i}-name"] = "That is the name of a Proxmox node."
            problem = node_problem(str(row.get("pve_node") or ""))
            if problem and f"servers-{i}-pve_node" not in errors:
                errors[f"servers-{i}-pve_node"] = problem
        return rows, errors

    def network_problem(row: dict[str, Any], config: Config, errors: dict[str, str]) -> None:
        """The checks of roles/proxmox_vm/tasks/main.yml that involve more than one of a row's fields."""
        i = row["_index"]
        if row.get("net_mac"):
            row["net_mac"] = str(row["net_mac"]).upper()  # as the role writes it, and to find it twice
        cards = row.get("vm_extra_nics") or []
        dhcp = row.get("net_mode") == "dhcp"
        routes = [card for card in cards if card.get("gateway")]
        if not dhcp and not row.get("ansible_host") and f"servers-{i}-ansible_host" not in errors:
            errors[f"servers-{i}-ansible_host"] = "Enter the static IP address, or choose dhcp as the address."
        if f"servers-{i}-vm_extra_nics" in errors:
            return
        macs = [str(m).upper() for m in [row.get("net_mac")] + [card.get("mac") for card in cards] if m]
        if len(routes) > 1:
            errors[f"servers-{i}-vm_extra_nics"] = "Only one card can have the default route (via)."
        elif dhcp and routes:
            errors[f"servers-{i}-vm_extra_nics"] = "With dhcp, the first card gets the default route: remove via."
        elif not dhcp and not routes and not config.values.get("net_gateway"):
            errors[f"servers-{i}-vm_extra_nics"] = ("A static address needs the gateway on the Defaults page, or an "
                                                   "extra card with via and its gateway.")
        elif len(set(macs)) != len(macs):
            errors[f"servers-{i}-vm_extra_nics"] = "Each card needs its own MAC address."

    def server_rows(config: Config) -> list[dict[str, str]]:
        rows = []
        for srv in config.servers:
            row = {}
            for c in schema.server_columns:
                shown = display_value(c, srv.get(c.name))
                row[c.name] = ", ".join(str(shown).splitlines()) if c.kind == "list" else str(shown)
            rows.append(row)
        return rows

    @app.get("/api/servers")
    async def servers_data(request: Request):
        require_login(request)
        config = store.load()
        return reply(columns=[field_json(c) for c in schema.server_columns], rows=server_rows(config),
                     nodes=sorted(store.cluster().get("nodes") or {}), default_node=config.default_node,
                     default_os=str(setting(config, "vm_os")))

    @app.post("/api/servers")
    async def servers(request: Request):
        require_login(request)
        form = await checked_form(request)
        config = store.load()
        parsed, errors = parse_servers(form, config)
        if errors:
            return reply(422, errors=errors)
        config.servers = [{k: v for k, v in r.items() if not k.startswith("_")} for r in parsed]
        store.save(config)
        return reply(message=f"Saved {len(config.servers)} server(s).")

    # --- SSH key, host key -------------------------------------------------------------------------------

    def hostkey(k) -> dict[str, str]:
        return {"keytype": k.keytype, "fingerprint": k.fingerprint}

    def node_json(config: Config, name: str) -> dict[str, Any]:
        address = config.node_addresses.get(name, "")
        return {"name": name, "address": address, "default": name == config.default_node,
                "in_use": name in config.nodes_in_use(),
                "servers": sorted(s["name"] for s in config.servers if config.server_node(s) == name),
                "trusted": [hostkey(k) for k in (keys.trusted(address) if address else [])]}

    @app.get("/api/ssh")
    async def ssh_data(request: Request):
        session = require_login(request)
        config = store.load()
        pub = keys.public_key()
        pending = session.pending_hostkeys
        return reply(public_key=pub or "", key_info=describe_public_key(pub) if pub else None,
                     nodes=[node_json(config, n) for n in config.node_names()],
                     pending={"node": session.pending_node, "keys": [hostkey(k) for k in pending]} if pending else None,
                     pve_node=config.default_node, node_user=store.node_user(config))

    NODE_ACTIONS = ("address", "scan", "trust", "forget", "test", "remove")

    @app.post("/api/ssh")
    async def ssh_post(request: Request):
        session = require_login(request)
        form = await checked_form(request)
        action = str(form.get("action", ""))
        config = store.load()
        node = str(form.get("node", ""))
        if action in NODE_ACTIONS and node not in config.node_names():
            return reply(422, area="key", error="That isn't one of the nodes. Reload the page.")
        address = config.node_addresses.get(node, "")
        try:
            if action == "generate":
                if keys.public_key() and form.get("replace") is None:
                    return reply(422, area="key", error="Tick the box to confirm replacing the current key.")
                keys.generate()
                store.save(config)  # puts the new public key into admin_extra_ssh_keys
                return reply(message=f"Created a new Ed25519 key. Add its public key to {store.node_user(config)}'s "
                                     "authorized_keys on each node.")
            if action == "upload":
                upload = form.get("key_file")
                data = await upload.read(MAX_PART) if upload is not None and hasattr(upload, "read") else b""
                if not data:
                    data = str(form.get("key_text", "")).encode()
                keys.upload(data, str(form.get("passphrase", "")))
                store.save(config)
                return reply(message="Saved the uploaded key.")
            if action == "address":
                new = check_host(str(form.get("node_address", "")))
                if new != address or session.pending_node != node:
                    session.pending_hostkeys, session.pending_node = None, ""
                config.node_addresses[node] = new
                store.save(config)
                return reply(message=f"Saved node {node}'s SSH address {new}.", node=node)
            if action == "remove":
                if node in config.nodes_in_use():
                    return reply(422, area="address", node=node, error=f"Servers still use {node}.")
                config.node_addresses.pop(node, None)
                store.save(config)
                return reply(message=f"Removed node {node}'s SSH address.", node=node)
            if action in ("scan", "trust", "forget", "test") and not address:
                return reply(422, area="address", node=node, error=f"Save node {node}'s SSH address first.")
            if action == "scan":
                session.pending_hostkeys, session.pending_node = keys.scan(address), node
                return reply(node=node)
            if action == "trust":
                pending, session.pending_hostkeys = session.pending_hostkeys, None
                if (not pending or session.pending_node != node or pending[0].host != address
                        or form.get("confirm") is None):
                    return reply(422, area="hostkey", node=node,
                                 error="Scan again, compare the fingerprints and tick the box.")
                keys.trust(address, pending)
                return reply(message=f"Trusted {address}'s host keys (node {node}).", node=node)
            if action == "forget":
                keys.forget(address)
                return reply(message=f"Forgot {address}'s host keys (node {node}).", node=node)
            if action == "test":
                return reply(message=keys.test_login(address, store.node_user(config)), node=node)
        except KeyProblem as exc:
            area = {"generate": "key", "upload": "key", "address": "address"}.get(action, "hostkey")
            return reply(422, area=area, node=node, error=exc.message)
        raise Forbidden()

    @app.get("/ssh/id_ed25519.pub")
    async def public_key_file(request: Request):
        require_login(request, api=False)
        if not keys.public_key():
            return PlainTextResponse("No key yet.", status_code=404)
        return PlainTextResponse(keys.public_key() + "\n", headers={
            "Content-Disposition": 'attachment; filename="ubuntu-server-auto-setup.pub"'})

    # --- VMs: a read-only list of the cluster's VMs ---------------------------------------------------------

    def setting(config: Config, name: str) -> Any:
        """A saved value, or the role default the inventory leaves it at."""
        value = config.values.get(name)
        return schema.by_name[name].default if value in (None, "") else value

    def pve_api(config: Config) -> PveApi | None:
        """The API with the saved token, or None until the Proxmox page has the host and the token."""
        host = str(config.values.get("pve_api_host") or "")
        token_id = config.secrets.get("vault_pve_api_token_id", "")
        secret = config.secrets.get("vault_pve_api_token_secret", "")
        if not (host and token_id and secret):
            return None
        return PveApi(host, int(setting(config, "pve_api_port")), str(setting(config, "pve_api_user")), token_id,
                      secret, verify=tls_verify(bool(setting(config, "pve_validate_certs")), settings.ca_bundle),
                      timeout=min(int(setting(config, "pve_api_timeout")), VM_API_TIMEOUT))

    def vm_notes(data: dict[str, Any], verified: bool) -> list[dict[str, str]]:
        """Why some data is missing, and what to do about it."""
        notes = []
        if data["forbidden"]:
            major = data["version"].split(".", 1)[0]
            if major.isdigit() and int(major) < 9:
                message = (f"The token may not ask the guest agents of {data['forbidden']} VM(s) for their addresses. "
                           "On Proxmox VE 8 that needs VM.Monitor, which allows much more than reading: adding it "
                           "is optional (see docs/installing.md). Without it, addresses come from the inventory "
                           "or cloud-init.")
            else:
                message = (f"The token may not ask the guest agents of {data['forbidden']} VM(s) for their addresses: "
                           "add VM.GuestAgent.Audit to its role (see docs/installing.md). Until then, addresses "
                           "come from the inventory or cloud-init.")
            notes.append({"kind": "warning", "message": message})
        if not verified:
            notes.append({"kind": "warning", "message": "The API's TLS certificate isn't verified (turned off on the "
                                                        "Proxmox page)."})
        return notes

    def vm_servers(config: Config) -> dict[int, dict[str, str]]:
        return {int(s["pve_vmid"]): {"name": str(s["name"]), "ansible_host": str(s.get("ansible_host") or "")}
                for s in config.servers if s.get("pve_vmid")}

    def alias_of(vmid: int) -> str:
        return vm_alias(str(store.cluster().get("name") or ""), vmid)

    async def vm_listing(config: Config, api: PveApi) -> dict[str, Any]:
        """The cluster listing, with what selecting a VM needs: whether it can be, and its pinned host keys."""
        network = vms.subnet(config.values.get("net_gateway"), setting(config, "net_prefix"))
        # The calls block (and wait for the guest agents), so they run in a thread.
        data = await asyncio.to_thread(lister.list, api, vm_servers(config), network)
        pins = keys.pins()
        for row in data["vms"]:
            ok, why = vmplan.selectable(row)
            alias = alias_of(row["vmid"])
            row.update(selectable=ok, why_not=why, alias=alias,
                       pinned=[hostkey(k) for k in pins.get(alias, [])])
        return data

    def plan_settings(config: Config) -> vmplan.Settings:
        rules = [vmplan.rule_text(r) for r in config.values.get("firewall_allow") or [] if isinstance(r, dict)]
        return vmplan.Settings(
            admin_user=str(setting(config, "admin_user")), ssh_port=int(setting(config, "ssh_port")),
            github_user=str(config.values.get("github_user") or ""),
            # The form doesn't set these: the roles' defaults apply.
            ssh_keys_exclusive=bool(schema.roles.default_for("ssh_keys_exclusive", "common")),
            ui_key_authorized=config.authorize_ui_key and bool(keys.public_key()),
            apt_upgrade=str(schema.roles.default_for("apt_upgrade", "common")),
            ufw_default_incoming=str(schema.roles.default_for("ufw_default_incoming", "firewall")),
            ufw_rules=rules, known_hosts=str(settings.known_hosts))

    @app.get("/api/vms")
    async def vms_data(request: Request):
        require_login(request)
        config = store.load()
        api = pve_api(config)
        if api is None:
            return reply(409, error="Set up the Proxmox API first: the host and the token.", setup="/proxmox")
        try:
            data = await vm_listing(config, api)
        except RemoteProblem as exc:
            return reply(502, error=exc.message)
        return reply(**data, notes=vm_notes(data, api.verify is not False), cluster=store.cluster().get("name", ""),
                     fetched=int(time.time()),
                     run={"presets": [{"key": k, "label": label, "tags": list(tags) if tags is not None else None}
                                      for k, (label, tags) in vmplan.PRESETS.items()],
                          "default_preset": vmplan.DEFAULT_PRESET, "tags": list(vmplan.TAGS),
                          "max_hosts": vmplan.MAX_HOSTS, "user": str(setting(config, "admin_user")),
                          "ssh_port": int(setting(config, "ssh_port")), "busy": runner.current() is not None,
                          "key": bool(keys.public_key())})

    HOSTKEY_ACTIONS = ("scan", "trust", "forget", "test")

    @app.post("/api/vms/hostkey")
    async def vm_hostkey(request: Request):
        """Scans a VM's host keys, pins the ones the user compared (by cluster and VM ID), forgets them, or tests
        the login."""
        session = require_login(request)
        form = await checked_form(request)
        action, vmid_text = str(form.get("action", "")), str(form.get("vmid", ""))
        if action not in HOSTKEY_ACTIONS or not vms.VMID_PATTERN.match(vmid_text):
            raise Forbidden()
        vmid = int(vmid_text)
        alias = alias_of(vmid)
        try:
            if action == "forget":
                forgotten = [k.fingerprint for k in keys.trusted(alias)]
                keys.forget(alias)
                session.pending_vm_keys.pop(vmid, None)
                audit.record("hostkey.forget", **who(request), vmid=vmid, alias=alias, fingerprints=forgotten)
                return reply(message=f"Forgot VM {vmid}'s host keys.", vmid=vmid)
            if action == "trust":
                pending = session.pending_vm_keys.pop(vmid, None)
                if not pending or pending[0].host != alias or form.get("confirm") is None:
                    return reply(422, vmid=vmid, error="Scan again, compare the fingerprints and tick the box.")
                keys.trust(alias, pending)
                audit.record("hostkey.pin", **who(request), vmid=vmid, alias=alias,
                             fingerprints=[k.fingerprint for k in pending], **session.pending_vm_from.pop(vmid, {}))
                return reply(message=f"Pinned VM {vmid}'s host keys.", vmid=vmid,
                             pinned=[hostkey(k) for k in pending])
            address = check_host(str(form.get("address", "")))
            port = vmplan.parse_port(str(form.get("port", ""))) or 22
            if action == "scan":
                found = await asyncio.to_thread(keys.scan, address, port, alias)
                session.pending_vm_keys[vmid] = found
                session.pending_vm_from[vmid] = {"address": address, "port": port}
                return reply(vmid=vmid, scanned=[hostkey(k) for k in found], address=address, port=port)
            user = vmplan.check_user(str(form.get("user", "")),
                                     vmplan.Formats(ipv4="", users=schema.by_name["admin_user"].patterns))
            message = await asyncio.to_thread(keys.test_login, address, user, port, alias)
            return reply(message=message, vmid=vmid)
        except (KeyProblem, Invalid) as exc:
            return reply(422, vmid=vmid, error=exc.message)

    def stored_plan(session, plan_id: str) -> vmplan.Plan | None:
        now = time.time()
        for key in [k for k, (made, _) in session.vm_plans.items() if now - made > PLAN_TTL]:
            del session.vm_plans[key]
        found = session.vm_plans.get(plan_id)
        return found[1] if found else None

    @app.post("/api/vms/plan")
    async def vm_plan(request: Request):
        """Checks the selection and each VM's settings and returns the plan to review: the hosts, what the run
        would change, and a plan ID to run it with."""
        session = require_login(request)
        form = await checked_form(request)
        config = store.load()
        api = pve_api(config)
        if api is None:
            return reply(409, error="Set up the Proxmox API first: the host and the token.", setup="/proxmox")
        selected_text = [str(x) for x in form.getlist("vmid")]
        if not all(vms.VMID_PATTERN.match(x) for x in selected_text):
            raise Forbidden()
        selected = list(dict.fromkeys(int(x) for x in selected_text))
        values = {k: str(val) for k, val in form.items() if isinstance(val, str) and k.startswith("vm-")}
        preset = str(form.get("preset", ""))
        try:
            tags = vmplan.preset_tags(preset, [str(t) for t in form.getlist("tags")])
        except vmplan.PlanProblem as exc:
            return reply(422, errors=exc.errors)
        if len(selected) > vmplan.MAX_HOSTS:
            return reply(422, errors={"selection": f"Select at most {vmplan.MAX_HOSTS} VMs for one run."})
        if not keys.public_key():
            return reply(422, errors={"selection": "Create or upload the web UI's SSH key first (SSH key page): "
                                                   "the runs log in with it."})
        try:
            data = await vm_listing(config, api)
        except RemoteProblem as exc:
            return reply(502, error=exc.message)
        rows = {row["vmid"]: row for row in data["vms"]}
        static = vm_servers(config)
        # A server this project installed was trusted when it was installed: its pin starts from that key.
        for vmid in selected:
            row = rows.get(vmid)
            if vmid in static and row is not None:
                address = static[vmid]["ansible_host"] or str(row.get("ip") or "")
                adopted = keys.adopt(alias_of(vmid), address) if address else []
                if adopted:
                    audit.record("hostkey.adopt", **who(request), vmid=vmid, alias=alias_of(vmid), address=address,
                                 fingerprints=[k.fingerprint for k in adopted])
        pins = {alias: [k.fingerprint for k in found] for alias, found in keys.pins().items()}
        formats = vmplan.Formats(ipv4=str(next(c for c in schema.server_columns if c.name == "ansible_host").pattern),
                                 users=schema.by_name["admin_user"].patterns)
        try:
            plan = vmplan.build(rows, selected, values, servers=config.servers, pins=pins,
                                aliases={vmid: alias_of(vmid) for vmid in selected}, formats=formats,
                                settings=plan_settings(config), tags=tags, preset=preset)
        except vmplan.PlanProblem as exc:
            return reply(422, errors=exc.errors)
        plan_id = secrets.token_urlsafe(16)
        stored_plan(session, "")  # drops expired plans
        session.vm_plans[plan_id] = (time.time(), plan)
        return reply(plan_id=plan_id, plan=vmplan.plan_json(plan), expires=int(time.time()) + PLAN_TTL)

    @app.post("/api/vms/run")
    async def vm_run(request: Request):
        """Runs a reviewed plan in check mode (--check --diff): nothing on the VMs changes."""
        session = require_login(request)
        form = await checked_form(request)
        plan = stored_plan(session, str(form.get("plan_id", "")))
        if plan is None:
            return reply(422, error="That plan has expired (plans last 10 minutes). Review it again.")
        if form.get("mode") != "check":
            return reply(422, error="A plan runs in check mode first; its run's page then offers the real run.")
        try:
            started = runner.start("baseline", True, True, plan.limit, store.host_names(), secret_values(),
                                   kind="vms", inventory=plan.inventory, tags=plan.tags, targets=plan.summary(),
                                   plan=plan.digest)
        except RunError as exc:
            return reply(422, error=exc.message)
        for target in plan.targets:
            if target.override:
                audit.record("vm.override", **who(request), run=started.id, vmid=target.vmid, name=target.name,
                             reason="its OS isn't a release of the OS catalog, or the guest didn't report it; "
                                    "run anyway")
        audit.record("run.check", **who(request), run=started.id, plan=plan.digest, tags=plan.tags,
                     targets=plan.summary())
        return reply(redirect=f"/runs/{started.id}", run=run_json(started))

    def apply_problem(check: Run | None) -> str:
        """Why a check run's plan can't be run for real now, or "" if it can."""
        if check is None or check.kind != "vms" or not check.check or check.applied_from:
            return "Only a check run started from the VMs page can be run for real."
        if check.status != "succeeded":
            return "The check run didn't succeed: fix what failed and run the check again."
        if time.time() - (check.finished or 0) > CHECK_TTL:
            return "The check run is more than 30 minutes old: run the check again."
        if any(r.applied_from == check.id for r in runner.history()):
            return "This check run's plan has been run already: run the check again first."
        if check.settings != runner.settings_digest():
            return "Settings were saved since the check run: run the check again."
        try:
            inventory = runner.path(check.id, ".inventory.yml").read_text()
        except OSError:
            return "The check run's inventory is gone: run the check again."
        pins = {t.get("alias", alias_of(int(t["vmid"]))): [k.fingerprint for k in
                                                           keys.trusted(t.get("alias") or alias_of(int(t["vmid"])))]
                for t in check.targets}
        if vmplan.digest(check.tags, inventory, pins) != check.plan:
            return "A VM's pinned host keys changed since the check run: run the check again."
        return ""

    def apply_json(run: Run) -> dict[str, Any] | None:
        """For a VMs page check run: whether its plan can be run for real, and until when."""
        if run.kind != "vms" or not run.check:
            return None
        problem = apply_problem(run)
        return {"possible": not problem, "problem": problem,
                "until": int((run.finished or 0) + CHECK_TTL) if run.finished else None}

    @app.post("/api/vms/apply")
    async def vm_apply(request: Request):
        """Runs a passing check run's plan for real: the same hosts, inventory, tags and pinned keys, within 30
        minutes of the check, once, after the user confirmed it."""
        require_login(request)
        form = await checked_form(request)
        check = runner.get(str(form.get("run_id", "")))
        problem = apply_problem(check)
        if not problem and form.get("confirm") is None:
            problem = "Tick the box to confirm the real run."
        if problem:
            audit.record("run.refused", **who(request), check=check.id if check else None, reason=problem)
            return reply(422, error=problem)
        inventory = runner.path(check.id, ".inventory.yml").read_text()
        try:
            started = runner.start("baseline", False, True, check.limit, store.host_names(), secret_values(),
                                   kind="vms", inventory=inventory, tags=check.tags, targets=check.targets,
                                   plan=check.plan, applied_from=check.id)
        except RunError as exc:
            audit.record("run.refused", **who(request), check=check.id, reason=exc.message)
            return reply(422, error=exc.message)
        audit.record("run.apply", **who(request), run=started.id, check=check.id, plan=check.plan, tags=check.tags,
                     targets=check.targets, confirmed=True)
        return reply(redirect=f"/runs/{started.id}", run=run_json(started))

    @app.get("/audit.jsonl")
    async def audit_download(request: Request):
        """The audit log, oldest entry first (the rotated files, then the current one)."""
        require_login(request, api=False)
        return Response(audit.read(), media_type="application/x-ndjson", headers={
            "Content-Disposition": 'attachment; filename="usas-audit.jsonl"'})

    # --- Runs ---------------------------------------------------------------------------------------------

    def secret_values() -> list[str]:
        return list(store.read_secrets().values())

    @app.get("/api/run")
    async def run_data(request: Request):
        require_login(request)
        config = store.load()
        return reply(playbooks=[{"key": k, "playbook": p, "description": d, "extra": list(x)}
                                for k, (p, d, x) in PLAYBOOKS.items()],
                     names=sorted(store.host_names()), steps=readiness(config),
                     current_run=run_json(runner.current()),
                     iso_plan=nodes.iso_plan(plan, config, str(setting(config, "pve_iso_storage")), store.cluster(),
                                             schema.installer_isos, str(setting(config, "vm_os"))))

    @app.post("/api/run")
    async def run(request: Request):
        require_login(request)
        form = await checked_form(request)
        action = str(form.get("playbook", ""))
        try:
            if action not in PLAYBOOKS:
                raise RunError("Choose what to run.")
            started = runner.start(action, form.get("check") is not None, form.get("diff") is not None,
                                   str(form.get("limit", "")), store.host_names(), secret_values())
        except RunError as exc:
            return reply(422, error=exc.message)
        return reply(redirect=f"/runs/{started.id}", run=run_json(started))

    @app.get("/api/runs")
    async def runs_data(request: Request):
        require_login(request)
        return reply(runs=[run_json(r) for r in runner.history()])

    @app.get("/api/runs/{run_id}")
    async def run_detail(request: Request, run_id: str):
        require_login(request)
        run = runner.get(run_id)
        if run is None:
            return reply(404, error="There is no such run.", redirect="/runs")
        path = runner.log_path(run.id)
        log = path.read_bytes() if path.exists() else b""
        return reply(run=run_json(run), log=log.decode("utf-8", "replace"), offset=len(log),
                     hosts=runner.hosts(run.id, secret_values()), apply=apply_json(run))

    @app.get("/api/runs/{run_id}/hosts")
    async def run_hosts(request: Request, run_id: str):
        """The per-host results so far (the run's page asks again while the run goes)."""
        require_login(request)
        run = runner.get(run_id)
        if run is None:
            return reply(404, error="There is no such run.")
        return reply(hosts=runner.hosts(run.id, secret_values()), done=run.done)

    def get_run(run_id: str):
        run = runner.get(run_id)
        if run is None:
            raise Redirect("/runs")
        return run

    @app.get("/runs/{run_id}/log")
    async def run_log(request: Request, run_id: str):
        require_login(request, api=False)
        run = get_run(run_id)
        return FileResponse(runner.log_path(run.id), media_type="text/plain; charset=utf-8",
                            filename=f"run-{run.id}.log")

    @app.get("/runs/{run_id}/stream")
    async def run_stream(request: Request, run_id: str, offset: int = 0):
        require_login(request, api=False)
        run = get_run(run_id)
        last = request.headers.get("last-event-id")
        start = int(last) if last and last.isdigit() else max(0, offset)
        path = runner.log_path(run.id)

        async def events():
            position = start
            buffer = b""
            while True:
                with path.open("rb") as fh:
                    fh.seek(position)
                    chunk = fh.read(64 * 1024)
                if chunk:
                    buffer += chunk
                    position += len(chunk)
                    *lines, buffer = buffer.split(b"\n")
                    for line in lines:
                        yield f"data: {line.decode('utf-8', 'replace').replace(chr(13), '')}\n\n"
                    yield f"id: {position - len(buffer)}\n\n"
                    continue
                current = runner.get(run.id)
                if current is None or current.done:
                    if buffer:
                        yield f"data: {buffer.decode('utf-8', 'replace').replace(chr(13), '')}\n\n"
                    yield f"event: end\ndata: {current.status if current else 'gone'}\n\n"
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/api/runs/{run_id}/stop")
    async def run_stop(request: Request, run_id: str):
        require_login(request)
        await checked_form(request)
        if runner.stop(run_id):
            return reply(kind="warning", message="Stopping the run (like Ctrl-C). A provisioning run stopped now "
                                                 "can leave the seed ISO on the node; see "
                                                 "docs/installing.md#troubleshooting.")
        return reply(message="The run isn't running any more.")

    return app
