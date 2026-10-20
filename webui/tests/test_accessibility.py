"""Structural accessibility checks on every page (docs/accessibility.md): a language, one h1, landmarks, a
label for every control, unique IDs, and references (for, aria-describedby, aria-labelledby, error-summary
links) that point at something. Contrast and keyboard use are checked in a browser by hand."""

import html.parser
from collections import Counter

import pytest

from .conftest import configure, post

PAGES = ["/", "/proxmox", "/ssh", "/defaults", "/servers", "/hardening", "/run", "/runs", "/account"]


class Page(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []
        self.ids: list[str] = []
        self.text_by_id: dict[str, str] = {}
        self.stack: list[str] = []
        self.button_text: list[str] = []
        self.label_for: set[str] = set()
        self.wrapped: set[str] = set()
        self.in_label = 0
        self.lang = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if tag == "html":
            self.lang = a.get("lang")
        if "id" in a:
            self.ids.append(a["id"])
        if tag == "label":
            self.in_label += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        if tag in ("input", "select", "textarea") and self.in_label and "id" in a:
            self.wrapped.add(a["id"])
        if tag == "button":
            self.button_text.append(a.get("aria-label", ""))

    def handle_endtag(self, tag):
        if tag == "label":
            self.in_label -= 1

    def handle_data(self, data):
        if self.button_text is not None and self.tags and self.tags[-1][0] == "button":
            self.button_text[-1] += data.strip()


def check(text: str, path: str) -> None:
    page = Page()
    page.feed(text)
    assert page.lang == "en", path
    names = [t for t, _ in page.tags]
    assert names.count("h1") == 1, path
    assert "main" in names and "header" in names and "footer" in names, path
    duplicates = [i for i, n in Counter(page.ids).items() if n > 1]
    assert not duplicates, (path, duplicates)
    ids = set(page.ids)
    for tag, a in page.tags:
        if tag in ("input", "select", "textarea") and a.get("type") not in ("hidden", "submit"):
            labelled = a.get("id") in page.label_for or a.get("id") in page.wrapped or a.get("aria-label")
            assert labelled, (path, tag, a)
        for attr in ("aria-describedby", "aria-labelledby"):
            for ref in (a.get(attr) or "").split():
                assert ref in ids, (path, attr, ref)
        if tag == "label" and a.get("for"):
            assert a["for"] in ids, (path, a)
        if tag == "a" and (a.get("href") or "").startswith("#"):
            assert a["href"][1:] in ids, (path, a)
        if tag == "img":
            assert "alt" in a, path
    assert all(page.button_text), (path, page.button_text)
    assert any(a.get("class") == "skip" and a.get("href") == "#main" for t, a in page.tags if t == "a"), path


@pytest.mark.parametrize("path", ["/setup"])
def test_first_run_page(client, path):
    check(client.get(path).text, path)


def test_sign_in_page(ui):
    post(ui, "/logout", {})
    check(ui.get("/login").text, "/login")


def test_every_page(ui):
    for path in PAGES:
        check(ui.get(path).text, path)
    configure(ui, servers=2)
    for path in PAGES:
        check(ui.get(path).text, path)


def test_pages_with_errors(ui):
    from .conftest import DEFAULTS, PROXMOX

    pages = [
        post(ui, "/proxmox", {"action": "save", **PROXMOX, "pve_api_host": "", "pve_node": "x y"}),
        post(ui, "/defaults", {"action": "save", **DEFAULTS, "net_gateway": "nope", "admin_password": ""}),
        post(ui, "/servers", {"action": "save", "server_count": "1", "servers-0-name": "bad name"}),
        post(ui, "/run", {"playbook": "site"}),
    ]
    for r in pages:
        assert r.status_code == 422
        check(r.text, r.url.path)
        assert 'id="error-summary"' in r.text and 'tabindex="-1"' in r.text
