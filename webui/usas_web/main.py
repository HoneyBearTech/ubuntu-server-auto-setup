"""The web UI: a JSON API that writes the inventory and the Vault and runs the playbooks, and the pages of the
React app (webui/frontend) that uses it.

Every page URL answers with the same small HTML shell, after the same sign-in checks as the API; the app then
asks /api/<page> for the page's data and posts its forms there, with the same field names as the forms had.
The server checks everything it is sent: the app only shows what the server says.
"""

import asyncio
import hashlib
import json
import os
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

from . import __version__
from .auth import Auth, csrf_ok, password_problem
from .config import LOOPBACK_HOSTS, Settings, from_env
from .remote import RemoteProblem, keys_url, preview_keys, test_proxmox
from .runner import PLAYBOOKS, Run, RunError, Runner
from .schema import Field, Invalid, Schema, display_value, parse_value
from .sshkeys import KeyProblem, Keys, check_host, describe_public_key, remove_ca, save_ca, saved_ca
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
THEMES = ("dark", "light", "system")  # dark is the default
THEME_COOKIE = "usas_theme"
FRONTEND_ENTRY = "src/main.tsx"


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
    runner = Runner(settings)
    keys = Keys(settings)
    store.vault_password()  # creates it on the first run
    try:
        timezones = sorted(zoneinfo.available_timezones())
    except OSError:  # pragma: no cover (no tz database)
        timezones = []

    app = FastAPI(title="ubuntu-server-auto-setup web UI", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.settings, app.state.auth, app.state.store, app.state.runner = settings, auth, store, runner
    app.state.schema, app.state.keys = schema, keys
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

    @app.get("/api/session")
    async def session_info(request: Request):
        session = request.state.session
        return reply(configured=auth.is_configured(), signed_in=session.authenticated, csrf=session.csrf,
                     version=__version__, theme=theme(request))

    @app.post("/api/theme")
    async def set_theme(request: Request):
        form = await checked_form(request)
        value = str(form.get("theme", ""))
        if value not in THEMES:
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
        wait = auth.wait_seconds(client(request))
        if wait:
            return reply(429, error=f"Too many failed attempts. Try again in {wait} seconds.")
        if not auth.verify_password(str(form.get("password", ""))):
            auth.record_failure(client(request))
            return reply(401, error="That password is wrong.")
        auth.clear_failures(client(request))
        request.state.session = auth.log_in(request.state.session)
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

    # --- Overview ----------------------------------------------------------------------------------------

    def readiness(config: Config) -> list[dict[str, Any]]:
        v = config.values
        node = config.node_address
        steps = [
            ("Proxmox API host, token and node", bool(v.get("pve_api_host") and v.get("pve_node")
                                                      and config.secret_is_set("vault_pve_api_token_id")
                                                      and config.secret_is_set("vault_pve_api_token_secret")),
             "/proxmox"),
            ("SSH key", bool(keys.public_key()), "/ssh"),
            ("Proxmox node's SSH address and trusted host key", bool(node and keys.trusted(node)), "/ssh"),
            ("Admin password, gateway and SSH keys for the servers",
             bool(config.secret_is_set("vault_admin_password") and v.get("net_gateway")
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
                errors[f.name] = str(exc)
                continue
            if f.name == "timezone" and timezones and value not in timezones:
                errors[f.name] = "Pick a time zone from the tz database, such as Europe/Berlin or Etc/UTC."
                continue
            if f.vault:
                if value:
                    config.secrets[f.vault] = value
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
                pem = str(form.get("ca_pem", "")) if action == "save-ca" else keys.fetch_ca(config.node_address)
                certs = save_ca(settings, pem)
                return reply(message="Saved the CA certificate " + ", ".join(c["subject"] for c in certs) + ".")
            except KeyProblem as exc:
                return reply(422, ca_error=str(exc))
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
                return reply(test_error=str(exc))
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
                    preview_error = str(exc)
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
            if not any(str(v).strip() for v in raw.values()):
                continue
            row: dict[str, Any] = {}
            for c in schema.server_columns:
                if not str(raw[c.name]).strip() and not c.required:
                    row[c.name] = [] if c.kind == "list" else None  # the role default applies
                    continue
                try:
                    value = parse_value(c, raw[c.name])
                except Invalid as exc:
                    errors[f"servers-{i}-{c.name}"] = str(exc)
                    continue
                row[c.name] = value
            row["_index"] = i
            rows.append(row)
        seen: dict[str, dict[Any, int]] = {"name": {}, "ansible_host": {}, "pve_vmid": {}}
        node = config.values.get("pve_node")
        for row in rows:
            i = row["_index"]
            for key in seen:
                value = row.get(key)
                if value in (None, ""):
                    continue
                if value in seen[key]:
                    errors[f"servers-{i}-{key}"] = "Another server already uses this."
                seen[key][value] = i
            if row.get("name") and row.get("name") == node:
                errors[f"servers-{i}-name"] = "That is the Proxmox node's name."
        return rows, errors

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
        return reply(columns=[field_json(c) for c in schema.server_columns], rows=server_rows(config))

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

    @app.get("/api/ssh")
    async def ssh_data(request: Request):
        session = require_login(request)
        config = store.load()
        node = config.node_address
        pub = keys.public_key()
        hostkey = lambda k: {"keytype": k.keytype, "fingerprint": k.fingerprint}  # noqa: E731
        return reply(public_key=pub or "", key_info=describe_public_key(pub) if pub else None,
                     node_address=node or "", trusted=[hostkey(k) for k in (keys.trusted(node) if node else [])],
                     pending=[hostkey(k) for k in session.pending_hostkeys] if session.pending_hostkeys else None,
                     pve_node=config.values.get("pve_node") or "")

    @app.post("/api/ssh")
    async def ssh_post(request: Request):
        session = require_login(request)
        form = await checked_form(request)
        action = str(form.get("action", ""))
        config = store.load()
        try:
            if action == "generate":
                if keys.public_key() and form.get("replace") is None:
                    return reply(422, area="key", error="Tick the box to confirm replacing the current key.")
                keys.generate()
                store.save(config)  # puts the new public key into admin_extra_ssh_keys
                return reply(message="Created a new Ed25519 key. Add its public key to root's authorized_keys on "
                                     "the node.")
            if action == "upload":
                upload = form.get("key_file")
                data = await upload.read(MAX_PART) if upload is not None and hasattr(upload, "read") else b""
                if not data:
                    data = str(form.get("key_text", "")).encode()
                keys.upload(data, str(form.get("passphrase", "")))
                store.save(config)
                return reply(message="Saved the uploaded key.")
            if action == "address":
                address = check_host(str(form.get("node_address", "")))
                if address != config.node_address:
                    session.pending_hostkeys = None
                config.node_address = address
                if not config.values.get("pve_node"):
                    return reply(422, area="address", error="Set the node name on the Proxmox page first.")
                store.save(config)
                return reply(message=f"Saved the node's SSH address {address}.")
            if action == "scan":
                if not config.node_address:
                    return reply(422, area="address", error="Save the node's SSH address first.")
                session.pending_hostkeys = keys.scan(config.node_address)
                return reply()
            if action == "trust":
                pending, session.pending_hostkeys = session.pending_hostkeys, None
                if not pending or pending[0].host != config.node_address or form.get("confirm") is None:
                    return reply(422, area="hostkey", error="Scan again, compare the fingerprints and tick the box.")
                keys.trust(config.node_address, pending)
                return reply(message=f"Trusted {config.node_address}'s host keys.")
            if action == "forget":
                keys.forget(config.node_address)
                return reply(message=f"Forgot {config.node_address}'s host keys.")
            if action == "test":
                return reply(message=keys.test_login(config.node_address))
        except KeyProblem as exc:
            area = {"generate": "key", "upload": "key", "address": "address"}.get(action, "hostkey")
            return reply(422, area=area, error=str(exc))
        raise Forbidden()

    @app.get("/ssh/id_ed25519.pub")
    async def public_key_file(request: Request):
        require_login(request, api=False)
        if not keys.public_key():
            return PlainTextResponse("No key yet.", status_code=404)
        return PlainTextResponse(keys.public_key() + "\n", headers={
            "Content-Disposition": 'attachment; filename="ubuntu-server-auto-setup.pub"'})

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
                     current_run=run_json(runner.current()))

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
            return reply(422, error=str(exc))
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
        return reply(run=run_json(run), log=log.decode("utf-8", "replace"), offset=len(log))

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
