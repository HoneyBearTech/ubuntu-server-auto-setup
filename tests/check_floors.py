#!/usr/bin/env python3
"""Fail if a lower bound in a requirements file allows a version with a known vulnerability.

osv-scanner resolves a range such as `foo>=1.0` to a current version, so it doesn't report that 1.0 itself
is vulnerable; OpenSSF Scorecard does, and `pip install` would accept an affected copy already in the
environment. This queries OSV (https://osv.dev) for the version each `>=` bound names.

Usage: check_floors.py FILE [FILE...]
"""

import json
import re
import sys
import urllib.request

FLOOR = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*>=\s*([0-9][^,\s;#]*)")


def floors(paths):
    found = {}
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                match = FLOOR.match(line)
                if match:
                    found[(match.group(1), match.group(2))] = path
    return found


def main(paths):
    found = floors(paths)
    queries = [{"package": {"ecosystem": "PyPI", "name": name}, "version": version} for name, version in found]
    request = urllib.request.Request(
        "https://api.osv.dev/v1/querybatch",
        data=json.dumps({"queries": queries}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 (fixed https URL)
        results = json.load(response)["results"]
    bad = 0
    for (name, version), result in zip(found, results, strict=True):
        ids = sorted(v["id"] for v in result.get("vulns", []))
        if ids:
            bad += 1
            print(f"{found[(name, version)]}: {name}>={version} allows known vulnerabilities: {', '.join(ids)}")
    print(f"Checked {len(found)} lower bounds; {bad} allow a vulnerable version.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
