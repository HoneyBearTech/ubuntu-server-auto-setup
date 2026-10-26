#!/usr/bin/env python3
"""Fuzz target: what tools/update_os_catalog.py reads from the vendors and from the catalog file.

Properties: from any directory listing, only plain file and folder names come out (no path, not "." or "..");
from any checksum file, only lines naming such a file with a hex checksum of the right length, exactly as they
appear; and any catalog text either fails with CatalogError or reads as entries that pass validation only with
safe values (HTTPS URLs on the vendors' hosts, plain file names, a sha256 or sha512 checksum), which write back
to the same text.
"""

import importlib.util
import pathlib
import re
import sys

import atheris

with atheris.instrument_imports():
    import roles

    _path = pathlib.Path(roles.ROOT) / "tools" / "update_os_catalog.py"
    _spec = importlib.util.spec_from_file_location("update_os_catalog", _path)
    uoc = importlib.util.module_from_spec(_spec)
    sys.modules["update_os_catalog"] = uoc
    _spec.loader.exec_module(uoc)

NAME = re.compile(r"^[A-Za-z0-9_+-][A-Za-z0-9._+-]*\Z")
LISTING_CHARS = 'href="/.?#%&;:ab12-_ \n<>\'HREF=release'
SUM_CHARS = "0123456789abcdefABCDEF *()=SHA256 \r\n./x-"
CATALOG_CHARS = ' -:"\n#abcdeilmnopstuvwy_0123456789.\\/'
FIELDS = ["id", "family", "name", "version", "codename", "eol", "methods", "autoinstall", "cloud_image", "url",
          "checksum", "file", "build", "signed"]
VALUES = ['"ubuntu-26.04"', '"ubuntu"', '"Ubuntu"', '"26.04"', '"resolute"', '"2031-05-29"', "true", "false",
          '"https://releases.ubuntu.com/26.04/x.iso"', '"https://example.com/x"', '"http://releases.ubuntu.com/x"',
          '"sha256:' + "a" * 64 + '"', '"x.qcow2"', '"x.iso"', '"../x"', '".."', '"26.04.1"', "1", "[]", '"a\\nb"']


def catalog_text(fdp):
    """Lines in (or near) the layout render writes."""
    lines = ["---", "os_catalog:"]
    for _ in range(fdp.ConsumeIntInRange(0, 24)):
        kind = fdp.ConsumeIntInRange(0, 3)
        if kind == 3:
            lines.append(roles.from_alphabet(fdp, CATALOG_CHARS, 60))
            continue
        indent = fdp.PickValueInList([2, 4, 6, 8, 3])
        dash = "- " if fdp.ConsumeBool() else ""
        field = fdp.PickValueInList(FIELDS)
        value = "" if kind == 2 else " " + fdp.PickValueInList(VALUES)
        lines.append(f"{' ' * indent}{dash}{field}:{value}")
    return "\n".join(lines) + "\n"


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    which = fdp.ConsumeIntInRange(0, 2)

    if which == 0:
        text = roles.from_alphabet(fdp, LISTING_CHARS, 200)
        for name in uoc.parse_listing(text):
            assert NAME.match(name.removesuffix("/")), name

    elif which == 1:
        text = roles.from_alphabet(fdp, SUM_CHARS, 300)
        for parse, length in ((uoc.parse_sha256sums, 64), (uoc.parse_sha512sums, 128)):
            for name, digest in parse(text).items():
                assert NAME.match(name) and re.match(rf"^[0-9a-f]{{{length}}}\Z", digest), (name, digest)
                assert re.search(rf"(?im)^{digest} [ *]{re.escape(name)}\r?$", text), (name, digest)
        for name, digest in uoc.parse_bsd_sums(text, "SHA256").items():
            assert NAME.match(name) and re.match(r"^[0-9a-f]{64}\Z", digest), (name, digest)
            assert re.search(rf"(?im)^SHA256 \({re.escape(name)}\) = {digest}\r?$", text), (name, digest)

    else:
        text = catalog_text(fdp)
        try:
            entries = uoc.read_catalog(text)
            uoc.validate(entries)
        except uoc.CatalogError:
            return
        for entry in entries:
            for image in entry["methods"].values():
                uoc.check_url(image["url"])
                assert NAME.match(image["file"]) and uoc.CHECKSUM.match(image["checksum"]), image
        rendered = uoc.render(entries)
        assert uoc.read_catalog(rendered) == entries


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
