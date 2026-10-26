#!/usr/bin/env python3
"""Fuzz target: a server's OS settings (vm_os, vm_os_image_url, vm_os_image_sha256), as roles/os_catalog checks them.

Properties: every id in the OS catalog passes os_catalog_id_pattern; an id that passes is a plain, lowercase name
that can't be read as a path or an option and, with a SHA-256 that passes its pattern, makes a file name Proxmox's
Import content accepts; a URL that passes vm_os_image_url_pattern is HTTPS, has a host, no user, query, fragment,
spaces or control characters, ends in a disk image's name, and parses to exactly what was given.
"""

import re
import sys
import urllib.parse

import atheris
import yaml

with atheris.instrument_imports():
    import roles

VARS = roles.role_vars("os_catalog")
ID = VARS["os_catalog_id_pattern"]
URL = VARS["vm_os_image_url_pattern"]
SHA = VARS["vm_os_image_sha256_pattern"]
CATALOG = yaml.safe_load((roles.ROOT / "roles" / "os_catalog" / "vars" / "catalog.yml").read_text())["os_catalog"]
IMPORT_FILE = re.compile(r"^[A-Za-z0-9_+-][A-Za-z0-9._+-]*\.(qcow2|raw|vmdk)\Z")
ID_CHARS = "abcdeuntrocky-0123456789./ _A\n"
URL_CHARS = "https:/example.com0123456789-_~%+.@?#qcowimgraw \n\t:"
HEX = "0123456789abcdefABCDEF \n"

for entry in CATALOG:
    assert roles.matches(ID, entry["id"]), entry["id"]


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    vm_os = roles.from_alphabet(fdp, ID_CHARS, 24)
    sha = roles.from_alphabet(fdp, HEX, 66)
    if roles.matches(ID, vm_os):
        assert re.match(r"^[a-z][a-z0-9.-]*\Z", vm_os) and ".." not in vm_os, vm_os
        if roles.matches(SHA, sha):
            assert IMPORT_FILE.match(f"{vm_os}-{sha[:16]}.qcow2"), (vm_os, sha)
    if roles.matches(SHA, sha):
        assert re.match(r"^[0-9a-f]{64}\Z", sha), sha

    url = fdp.PickValueInList(["https://", "https://example.com/", "http://example.com/", ""]) + roles.from_alphabet(
        fdp, URL_CHARS, 60)
    if roles.matches(URL, url):
        parts = urllib.parse.urlsplit(url)
        assert parts.scheme == "https" and parts.hostname, url
        assert not parts.query and not parts.fragment and "@" not in parts.netloc, url
        assert not any(c.isspace() or ord(c) < 32 for c in url), url
        assert re.search(r"\.(qcow2|img|raw)\Z", parts.path), url
        assert urllib.parse.urlunsplit(parts) == url, url


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
