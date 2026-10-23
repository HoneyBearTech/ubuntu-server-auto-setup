#!/usr/bin/env python3
"""Fuzz target: reading the status API's token from an Authorization header (webui/usas_web/status.py).

Property: for any header, parse_bearer never raises and returns None or a token: the prefix and exactly 43
characters of URL-safe base64, nothing before or after it (no line break, no blank), taken from the header
itself. A header that is "Bearer " and a valid token, in any case and with blanks around it, always gives that
token back, and the token checks against the HMAC it was stored as, while any other text doesn't.
"""

import pathlib
import string
import sys

import atheris

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "webui"))

with atheris.instrument_imports():
    from usas_web import status

ALPHABET = set(string.ascii_letters + string.digits + "-_")
TOKENS = status.StatusTokens(pathlib.Path("/nonexistent"), lambda: b"fuzz vault password")
STORED = "usas_st_" + "A" * 43
DIGEST = TOKENS._digest(STORED)


def valid(token: str) -> bool:
    body = token[len(status.PREFIX):]
    return token.startswith(status.PREFIX) and len(body) == 43 and set(body) <= ALPHABET


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    header = fdp.ConsumeUnicodeNoSurrogates(fdp.ConsumeIntInRange(0, 300))
    token = status.parse_bearer(header)
    if token is not None:
        assert valid(token), repr(token)
        assert token in header and len(header) <= status.MAX_HEADER, repr(header)
        assert header.partition(" ")[0].lower() == "bearer", repr(header)

    # A well-formed header, built from fuzzed parts, is always read back.
    body = "".join(c if c in ALPHABET else "A" for c in fdp.ConsumeUnicodeNoSurrogates(43))[:43].ljust(43, "x")
    candidate = status.PREFIX + body
    scheme = "".join(c.upper() if fdp.ConsumeBool() else c for c in "bearer")
    blanks = fdp.PickValueInList(["", " ", "\t", " \t "])
    assert status.parse_bearer(f"{scheme} {blanks}{candidate}{blanks}") == candidate
    # Only the token that made the digest matches it.
    assert (TOKENS._digest(candidate) == DIGEST) == (candidate == STORED), candidate


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
