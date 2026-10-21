"""Structural accessibility checks on the HTML the server sends for every page (docs/accessibility.md): a
language, a title, one h1, landmarks, a skip link, unique IDs, and a message for browsers without JavaScript;
and nothing inline that the Content-Security-Policy would block. The pages the app draws are checked the same
way, plus axe-core, by the app's own tests (webui/frontend/test/a11y.test.tsx); contrast by
webui/frontend/test/contrast.test.ts. Keyboard use and screen readers are checked in a browser by hand."""

import html.parser
import re
from collections import Counter

import pytest
from usas_web import main as m

from .conftest import configure, post

PAGES = list(m.PAGES)


class Page(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []
        self.ids: list[str] = []
        self.lang = None
        self.title = ""
        self.in_title = False
        self.inline_script = False
        self.in_script = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if tag == "html":
            self.lang = a.get("lang")
        if "id" in a:
            self.ids.append(a["id"])
        self.in_title = tag == "title"
        self.in_script = tag == "script"

    def handle_endtag(self, tag):
        self.in_title = self.in_script = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.in_script and data.strip():
            self.inline_script = True


def check(text: str, path: str) -> None:
    page = Page()
    page.feed(text)
    assert page.lang == "en", path
    assert page.title.endswith(" · USAS") and len(page.title) > len(" · USAS"), (path, page.title)
    names = [t for t, _ in page.tags]
    assert names.count("h1") == 1, path
    assert "main" in names and "header" in names and "footer" in names, path
    assert any(t == "main" and a.get("id") == "main" for t, a in page.tags), path
    assert any(t == "a" and a.get("class") == "skip" and a.get("href") == "#main" for t, a in page.tags), path
    assert "noscript" in names, path
    duplicates = [i for i, n in Counter(page.ids).items() if n > 1]
    assert not duplicates, (path, duplicates)
    # The Content-Security-Policy allows only files from the server: no inline scripts, styles or handlers.
    assert "style" not in names and not page.inline_script, path
    for tag, a in page.tags:
        assert "style" not in a and not any(k.startswith("on") for k in a), (path, tag, a)
        if tag == "script":
            assert a.get("src", "").startswith("/static/") and a.get("type") == "module", (path, a)
        if tag == "link" and a.get("rel") == "stylesheet":
            assert a.get("href", "").startswith("/static/"), (path, a)
    assert not re.search(r"https?://(?!github\.com/)", text), path  # nothing from other sites


@pytest.mark.parametrize("path", ["/setup"])
def test_first_run_page(client, path):
    check(client.get(path).text, path)


def test_sign_in_page(ui):
    post(ui, "/logout")
    check(ui.get("/login").text, "/login")


def test_every_page(ui):
    for path in PAGES:
        check(ui.get(path).text, path)
    configure(ui, servers=2)
    run_id = post(ui, "/run", {"playbook": "site", "check": "on"}).json()["run"]["id"]
    for path in PAGES + [f"/runs/{run_id}"]:
        check(ui.get(path).text, path)


def test_with_the_built_app(ui, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "frontend_assets", lambda app_dir: {"js": "/static/app/assets/main-1.js",
                                                                "css": ["/static/app/assets/main-1.css"]})
    for path in PAGES:
        text = ui.get(path).text
        check(text, path)
        assert '<script type="module" src="/static/app/assets/main-1.js"></script>' in text
