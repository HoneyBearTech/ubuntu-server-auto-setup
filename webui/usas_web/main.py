"""The web UI: server-rendered pages that write the inventory and the Vault, and run the playbooks.

Every page works without JavaScript; static/app.js only adds the live run log and small conveniences.
"""

import asyncio
import os
import zoneinfo
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
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
from .runner import PLAYBOOKS, RunError, Runner
from .schema import Field, Invalid, Schema, display_value, parse_value
from .sshkeys import KeyProblem, Keys, check_host, describe_public_key, remove_ca, save_ca, saved_ca
from .store import Config, Store

HERE = Path(__file__).parent
NAV = (
    ("/", "Overview"),
    ("/proxmox", "Proxmox"),
    ("/ssh", "SSH key"),
    ("/defaults", "Defaults"),
    ("/servers", "Servers"),
    ("/hardening", "Hardening"),
    ("/run", "Run"),
    ("/runs", "History"),
)
SECTION_TITLES = {"proxmox": "Proxmox API", "defaults": "Defaults for every server", "hardening": "Hardening"}
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
MAX_PART = 256 * 1024
API_FIELDS = ("pve_api_host", "pve_api_port", "pve_api_user", "pve_api_token_id", "pve_api_token_secret",
              "pve_validate_certs")


class Redirect(Exception):
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
    templates.env.globals.update(nav=NAV, version=__version__, display_value=display_value)

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
        if path == "/healthz" or path.startswith("/static/"):
            response = await call_next(request)
            return _headers(response, cache=path.startswith("/static/"))
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
        # same-origin, not no-referrer: with no-referrer, browsers send "Origin: null" on form posts, which the
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

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden):
        return PlainTextResponse("The form expired or didn't come from this page. Go back, reload and try again.",
                                 status_code=403)

    # --- Helpers -----------------------------------------------------------------------------------------

    def require_login(request: Request):
        if not auth.is_configured():
            raise Redirect("/setup")
        if not request.state.session.authenticated:
            target = request.url.path
            raise Redirect("/login" + (f"?next={quote(target)}" if target != "/" else ""))
        return request.state.session

    async def checked_form(request: Request):
        form = await request.form(max_files=2, max_fields=2000, max_part_size=MAX_PART)
        if not csrf_ok(request.state.session, form.get("csrf_token")):
            raise Forbidden()
        return form

    def flash(request: Request, message: str, kind: str = "ok") -> None:
        request.state.session.flash.append((kind, message))

    def render(request: Request, template: str, status: int = 200, **ctx) -> HTMLResponse:
        session = request.state.session
        messages, session.flash = list(session.flash), []
        ctx.update(csrf=session.csrf, signed_in=session.authenticated, current=request.url.path, flashes=messages)
        return templates.TemplateResponse(request, template, ctx, status_code=status)

    def safe_next(target: str | None) -> str:
        if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
            return "/"
        return target if urlsplit(target).path in {p for p, _ in NAV} else "/"

    def client(request: Request) -> str:
        return request.client.host if request.client else "?"

    # --- Health, first run, sign-in ----------------------------------------------------------------------

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return PlainTextResponse("ok")

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_page(request: Request):
        if auth.is_configured():
            raise Redirect("/login")
        return render(request, "setup.html", error=None)

    @app.post("/setup", response_class=HTMLResponse)
    async def setup(request: Request):
        if auth.is_configured():
            raise Redirect("/login")
        form = await checked_form(request)
        wait = auth.wait_seconds(client(request))
        if wait:
            return render(request, "setup.html", 429, error=f"Too many attempts. Try again in {wait} seconds.")
        if not auth.check_setup_code(str(form.get("setup_code", ""))):
            auth.record_failure(client(request))
            return render(request, "setup.html", 422,
                          error="That setup code is wrong. It is in the container's log: docker logs usas")
        problem = password_problem(str(form.get("password", "")), str(form.get("confirm", "")))
        if problem:
            return render(request, "setup.html", 422, error=problem)
        auth.set_password(str(form["password"]))
        auth.clear_failures(client(request))
        request.state.session = auth.log_in(request.state.session)
        flash(request, "Password set. Start with the Proxmox API.")
        raise Redirect("/proxmox")

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: str = "/"):
        if not auth.is_configured():
            raise Redirect("/setup")
        if request.state.session.authenticated:
            raise Redirect(safe_next(next))
        return render(request, "login.html", error=None, next=safe_next(next))

    @app.post("/login", response_class=HTMLResponse)
    async def login(request: Request):
        if not auth.is_configured():
            raise Redirect("/setup")
        form = await checked_form(request)
        target = safe_next(str(form.get("next", "/")))
        wait = auth.wait_seconds(client(request))
        if wait:
            return render(request, "login.html", 429, next=target,
                          error=f"Too many failed attempts. Try again in {wait} seconds.")
        if not auth.verify_password(str(form.get("password", ""))):
            auth.record_failure(client(request))
            return render(request, "login.html", 401, error="That password is wrong.", next=target)
        auth.clear_failures(client(request))
        request.state.session = auth.log_in(request.state.session)
        raise Redirect(target)

    @app.post("/logout")
    async def logout(request: Request):
        await checked_form(request)
        auth.end(request.state.session)
        request.state.logout = True
        raise Redirect("/login")

    @app.get("/account", response_class=HTMLResponse)
    async def account_page(request: Request):
        require_login(request)
        return render(request, "account.html", error=None)

    @app.post("/account", response_class=HTMLResponse)
    async def account(request: Request):
        session = require_login(request)
        form = await checked_form(request)
        if not auth.verify_password(str(form.get("current", ""))):
            return render(request, "account.html", 422, error="The current password is wrong.")
        problem = password_problem(str(form.get("password", "")), str(form.get("confirm", "")))
        if problem:
            return render(request, "account.html", 422, error=problem)
        auth.set_password(str(form["password"]))
        auth.end_all_except(session)
        flash(request, "Password changed. Other sessions were signed out.")
        raise Redirect("/account")

    # --- Overview ----------------------------------------------------------------------------------------

    def readiness(config: Config) -> list[tuple[str, bool, str]]:
        v = config.values
        node = config.node_address
        return [
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

    @app.get("/", response_class=HTMLResponse)
    async def overview(request: Request):
        require_login(request)
        config = store.load()
        return render(request, "overview.html", steps=readiness(config), runs=runner.history()[:5],
                      current_run=runner.current())

    # --- Form sections -----------------------------------------------------------------------------------

    def form_values(fields: list[Field], config: Config) -> dict[str, Any]:
        return {f.name: config.values.get(f.name, f.default) if not f.vault else config.secrets.get(f.vault, "")
                for f in fields}

    def apply_section(section: str, form, config: Config,
                      only: tuple[str, ...] = ()) -> tuple[dict[str, Any], Problems]:
        """Parses the section's fields (or only some) from form into config; returns the values and the errors."""
        errors: dict[str, str] = {}
        submitted: dict[str, Any] = {}
        for f in schema.sections[section]:
            if only and f.name not in only:
                submitted[f.name] = form.get(f.name) if f.kind != "secret" else ""
                continue
            raw = form.get(f.name)
            submitted[f.name] = raw if f.kind != "bool" else raw is not None
            try:
                value = parse_value(f, raw)
            except Invalid as exc:
                errors[f.name] = str(exc)
                continue
            if f.kind == "list" and f.pattern == "ufw_rule":
                submitted[f.name] = value
            if f.name == "timezone" and timezones and value not in timezones:
                errors[f.name] = "Pick a time zone from the tz database, such as Europe/Berlin or Etc/UTC."
                continue
            if f.vault:
                if value:
                    config.secrets[f.vault] = value
                elif f.required and not config.secret_is_set(f.vault):
                    errors[f.name] = f"{f.label} is required."
                if f.kind == "secret":
                    submitted[f.name] = ""
                continue
            config.values[f.name] = value
        return submitted, Problems(errors)

    def section_page(request: Request, section: str, config: Config, submitted=None, problems=None, status=200,
                     **extra):
        fields = schema.sections[section]
        values = form_values(fields, config)
        if submitted:
            values.update({k: v for k, v in submitted.items() if not (k in schema.by_name
                                                                      and schema.by_name[k].kind == "secret")})
        if section == "proxmox":
            extra.setdefault("cluster", store.cluster())
        return render(request, f"{section}.html", status, title=SECTION_TITLES[section], fields=fields,
                      values=values, errors=(problems.errors if problems else {}), config=config,
                      timezones=timezones, **extra)

    def node_problem(name: str) -> str:
        """Why name can't be the node, if the last connection test listed the cluster's nodes; else ""."""
        nodes = store.cluster().get("nodes") or {}
        if not nodes or not name or name in nodes:
            return ""
        same = [n for n in nodes if n.lower() == name.lower()]
        hint = f" Node names are case-sensitive: did you mean {same[0]}?" if same else ""
        return f"{name} isn't a node in the cluster ({', '.join(sorted(nodes))}).{hint}"

    @app.get("/proxmox", response_class=HTMLResponse)
    async def proxmox_page(request: Request):
        require_login(request)
        return section_page(request, "proxmox", store.load(), ca=saved_ca(settings))

    @app.post("/proxmox", response_class=HTMLResponse)
    async def proxmox(request: Request):
        require_login(request)
        form = await checked_form(request)
        action = form.get("action", "save")
        config = store.load()
        if action in ("save-ca", "fetch-ca", "remove-ca"):
            try:
                if action == "remove-ca":
                    remove_ca(settings)
                    flash(request, "The CA certificate was removed.")
                else:
                    pem = str(form.get("ca_pem", "")) if action == "save-ca" else keys.fetch_ca(config.node_address)
                    certs = save_ca(settings, pem)
                    flash(request, "Saved the CA certificate " + ", ".join(c["subject"] for c in certs) + ".")
            except KeyProblem as exc:
                return section_page(request, "proxmox", config, status=422, ca=saved_ca(settings), ca_error=str(exc))
            raise Redirect("/proxmox")
        only = API_FIELDS if action == "test" else ()
        submitted, problems = apply_section("proxmox", form, config, only)
        if problems:
            return section_page(request, "proxmox", config, submitted, problems, 422, ca=saved_ca(settings))
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
                return section_page(request, "proxmox", config, submitted, status=200, ca=saved_ca(settings),
                                    test_error=str(exc))
            # The node list (names and status only) is kept for the node dropdown; the form isn't saved.
            store.save_cluster(result.cluster, result.status)
            return section_page(request, "proxmox", config, submitted, ca=saved_ca(settings), test_result=result)
        problem = node_problem(str(config.values.get("pve_node") or ""))
        if problem:
            return section_page(request, "proxmox", config, submitted, Problems({"pve_node": problem}), 422,
                                ca=saved_ca(settings))
        store.save(config)
        flash(request, "Saved the Proxmox settings.")
        raise Redirect("/proxmox")

    @app.get("/defaults", response_class=HTMLResponse)
    async def defaults_page(request: Request):
        require_login(request)
        return section_page(request, "defaults", store.load(), ui_key=keys.public_key())

    @app.post("/defaults", response_class=HTMLResponse)
    async def defaults(request: Request):
        require_login(request)
        form = await checked_form(request)
        config = store.load()
        config.authorize_ui_key = form.get("authorize_ui_key") is not None
        only = ("github_user", "github_keys_url", "admin_extra_ssh_keys") if form.get("action") == "preview" else ()
        submitted, problems = apply_section("defaults", form, config, only)
        if problems:
            return section_page(request, "defaults", config, submitted, problems, 422, ui_key=keys.public_key())
        if form.get("action") == "preview":
            user = str(config.values.get("github_user") or "")
            preview, preview_error = [], None
            if not user:
                preview_error = "Enter a GitHub user first."
            else:
                try:
                    preview = preview_keys(keys_url(str(config.values.get("github_keys_url")), user))
                    if not preview:
                        preview_error = f"{user} has no public keys on GitHub; the playbooks would refuse to run."
                except RemoteProblem as exc:
                    preview_error = str(exc)
            extra = [d for d in (describe_public_key(k) for k in config.values.get("admin_extra_ssh_keys") or []) if d]
            return section_page(request, "defaults", config, submitted, ui_key=keys.public_key(), preview=preview,
                                preview_error=preview_error, extra_keys=extra)
        store.save(config)
        flash(request, "Saved the defaults.")
        raise Redirect("/defaults")

    @app.get("/hardening", response_class=HTMLResponse)
    async def hardening_page(request: Request):
        require_login(request)
        return section_page(request, "hardening", store.load())

    @app.post("/hardening", response_class=HTMLResponse)
    async def hardening(request: Request):
        require_login(request)
        form = await checked_form(request)
        config = store.load()
        submitted, problems = apply_section("hardening", form, config)
        admin = str(config.values.get("admin_user") or schema.by_name["admin_user"].default)
        allow = config.values.get("ssh_allow_users") or []
        if "ssh_allow_users" not in problems.errors and allow and admin not in allow:
            problems.errors["ssh_allow_users"] = f"Add the admin user ({admin}), or Ansible would be locked out."
        if problems:
            return section_page(request, "hardening", config, submitted, problems, 422)
        store.save(config)
        flash(request, "Saved the hardening settings.")
        raise Redirect("/hardening")

    # --- Servers ------------------------------------------------------------------------------------------

    def parse_servers(form, config: Config) -> tuple[list[dict[str, Any]], dict[str, str]]:
        count = min(int(form.get("server_count") or 0) if str(form.get("server_count", "0")).isdigit() else 0, 500)
        rows, errors = [], {}
        for i in range(count):
            raw = {c.name: form.get(f"servers-{i}-{c.name}", "") for c in schema.server_columns}
            if not any(str(v).strip() for v in raw.values()):
                continue
            row: dict[str, Any] = {"_raw": raw}
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
                row[c.name] = ", ".join(shown.splitlines()) if c.kind == "list" else shown
            rows.append(row)
        return rows

    @app.get("/servers", response_class=HTMLResponse)
    async def servers_page(request: Request):
        require_login(request)
        config = store.load()
        rows = server_rows(config) or [{c.name: "" for c in schema.server_columns}]
        return render(request, "servers.html", columns=schema.server_columns, rows=rows, errors={}, config=config)

    @app.post("/servers", response_class=HTMLResponse)
    async def servers(request: Request):
        require_login(request)
        form = await checked_form(request)
        config = store.load()
        action = str(form.get("action", "save"))
        parsed, errors = parse_servers(form, config)
        rows = [{k: str(v) for k, v in r["_raw"].items()} for r in parsed]
        if action == "add":
            rows.append({c.name: "" for c in schema.server_columns})
            return render(request, "servers.html", columns=schema.server_columns, rows=rows, errors={},
                          config=config, focus_last=True)
        if action.startswith("remove-") and action[7:].isdigit():
            drop = int(action[7:])
            kept = [r for r in parsed if r["_index"] != drop]
            rows = [{k: str(v) for k, v in r["_raw"].items()} for r in kept] or [
                {c.name: "" for c in schema.server_columns}]
            return render(request, "servers.html", columns=schema.server_columns, rows=rows, errors={},
                          config=config, notice="Row removed. Save to keep the change.")
        if errors:
            # Errors are keyed by the submitted row index; renumber them for the rows shown.
            renumbered = {}
            for pos, r in enumerate(parsed):
                for c in schema.server_columns:
                    key = f"servers-{r['_index']}-{c.name}"
                    if key in errors:
                        renumbered[f"servers-{pos}-{c.name}"] = errors[key]
            return render(request, "servers.html", 422, columns=schema.server_columns, rows=rows,
                          errors=renumbered, config=config)
        config.servers = [{k: v for k, v in r.items() if not k.startswith("_")} for r in parsed]
        store.save(config)
        flash(request, f"Saved {len(config.servers)} server(s).")
        raise Redirect("/servers")

    # --- SSH key, host key -------------------------------------------------------------------------------

    def ssh_page(request: Request, status: int = 200, **extra) -> HTMLResponse:
        config = store.load()
        node = config.node_address
        pub = keys.public_key()
        return render(request, "ssh.html", status, public_key=pub, key_info=describe_public_key(pub) if pub else None,
                      node_address=node, trusted=keys.trusted(node) if node else [],
                      pending=request.state.session.pending_hostkeys, config=config, **extra)

    @app.get("/ssh", response_class=HTMLResponse)
    async def ssh_get(request: Request):
        require_login(request)
        return ssh_page(request)

    @app.post("/ssh", response_class=HTMLResponse)
    async def ssh_post(request: Request):
        session = require_login(request)
        form = await checked_form(request)
        action = str(form.get("action", ""))
        config = store.load()
        try:
            if action == "generate":
                if keys.public_key() and form.get("replace") is None:
                    return ssh_page(request, 422, key_error="Tick the box to confirm replacing the current key.")
                keys.generate()
                store.save(config)  # puts the new public key into admin_extra_ssh_keys
                flash(request, "Created a new Ed25519 key. Add its public key to root's authorized_keys on the node.")
            elif action == "upload":
                upload = form.get("key_file")
                data = await upload.read(MAX_PART) if upload is not None and hasattr(upload, "read") else b""
                if not data:
                    data = str(form.get("key_text", "")).encode()
                keys.upload(data, str(form.get("passphrase", "")))
                store.save(config)
                flash(request, "Saved the uploaded key.")
            elif action == "address":
                address = check_host(str(form.get("node_address", "")))
                if address != config.node_address:
                    session.pending_hostkeys = None
                config.node_address = address
                if not config.values.get("pve_node"):
                    return ssh_page(request, 422, address_error="Set the node name on the Proxmox page first.")
                store.save(config)
                flash(request, f"Saved the node's SSH address {address}.")
            elif action == "scan":
                if not config.node_address:
                    return ssh_page(request, 422, address_error="Save the node's SSH address first.")
                session.pending_hostkeys = keys.scan(config.node_address)
                return ssh_page(request)
            elif action == "trust":
                pending, session.pending_hostkeys = session.pending_hostkeys, None
                if not pending or pending[0].host != config.node_address or form.get("confirm") is None:
                    return ssh_page(request, 422,
                                    hostkey_error="Scan again, compare the fingerprints and tick the box.")
                keys.trust(config.node_address, pending)
                flash(request, f"Trusted {config.node_address}'s host keys.")
            elif action == "forget":
                keys.forget(config.node_address)
                flash(request, f"Forgot {config.node_address}'s host keys.")
            elif action == "test":
                flash(request, keys.test_login(config.node_address))
            else:
                raise Forbidden()
        except KeyProblem as exc:
            return ssh_page(request, 422, ssh_error=str(exc))
        raise Redirect("/ssh")

    @app.get("/ssh/id_ed25519.pub")
    async def public_key_file(request: Request):
        require_login(request)
        if not keys.public_key():
            return PlainTextResponse("No key yet.", status_code=404)
        return PlainTextResponse(keys.public_key() + "\n", headers={
            "Content-Disposition": 'attachment; filename="ubuntu-server-auto-setup.pub"'})

    # --- Runs ---------------------------------------------------------------------------------------------

    def secret_values() -> list[str]:
        return list(store.read_secrets().values())

    @app.get("/run", response_class=HTMLResponse)
    async def run_page(request: Request):
        require_login(request)
        config = store.load()
        return render(request, "run.html", playbooks=PLAYBOOKS, names=sorted(store.host_names()),
                      steps=readiness(config), current_run=runner.current(), error=None, form={})

    @app.post("/run", response_class=HTMLResponse)
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
            config = store.load()
            return render(request, "run.html", 422, playbooks=PLAYBOOKS, names=sorted(store.host_names()),
                          steps=readiness(config), current_run=runner.current(), error=str(exc), form=form)
        raise Redirect(f"/runs/{started.id}")

    @app.get("/runs", response_class=HTMLResponse)
    async def runs_page(request: Request):
        require_login(request)
        return render(request, "runs.html", runs=runner.history(), playbooks=PLAYBOOKS)

    def get_run(run_id: str):
        run = runner.get(run_id)
        if run is None:
            raise Redirect("/runs")
        return run

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail(request: Request, run_id: str):
        require_login(request)
        run = get_run(run_id)
        log = runner.log_path(run.id).read_bytes() if runner.log_path(run.id).exists() else b""
        return render(request, "run_detail.html", run=run, log=log.decode("utf-8", "replace"), offset=len(log),
                      playbooks=PLAYBOOKS)

    @app.get("/runs/{run_id}/log")
    async def run_log(request: Request, run_id: str):
        require_login(request)
        run = get_run(run_id)
        return FileResponse(runner.log_path(run.id), media_type="text/plain; charset=utf-8",
                            filename=f"run-{run.id}.log")

    @app.get("/runs/{run_id}/stream")
    async def run_stream(request: Request, run_id: str, offset: int = 0):
        require_login(request)
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

    @app.post("/runs/{run_id}/stop")
    async def run_stop(request: Request, run_id: str):
        require_login(request)
        await checked_form(request)
        if runner.stop(run_id):
            flash(request, "Stopping the run (like Ctrl-C). A provisioning run stopped now can leave the seed ISO "
                           "on the node; see docs/installing.md#troubleshooting.", "warning")
        raise Redirect(f"/runs/{run_id}")

    return app
