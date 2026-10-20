#!/usr/bin/env python3
"""Fuzz target: picking the ISO's checksum out of Ubuntu's SHA256SUMS.

Property: for any SHA256SUMS content and any file name the role accepts, the result is empty or a 64-digit hex checksum that
appears on a line naming exactly that file ("<sum> <name>" or "<sum> *<name>"), so a look-alike name can
never supply the checksum.
"""

import sys

import atheris

with atheris.instrument_imports():
    import roles

PICK = roles.set_fact_expression("roles/installer_iso/tasks/main.yml", "installer_iso_sha256")
NAME_PATTERN = roles.role_vars("installer_iso")["installer_iso_name_pattern"]
NAME_CHARS = "ubuntu-26.04.1-live-server-amd64.iso*[](){}+?|^$\\ \n"
SUM_CHARS = "0123456789abcdef *\n"


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    name = roles.from_alphabet(fdp, NAME_CHARS, 40)
    if not roles.matches(NAME_PATTERN, name):  # the role refuses other names before this expression runs
        return
    pieces = []
    for _ in range(fdp.ConsumeIntInRange(0, 4)):
        kind = fdp.ConsumeIntInRange(0, 2)
        if kind == 0:
            pieces.append(roles.from_alphabet(fdp, SUM_CHARS, 70) + " " + fdp.PickValueInList(["", "*"]) + name)
        elif kind == 1:
            pieces.append(roles.from_alphabet(fdp, "0123456789abcdef", 64) + " *" + roles.text(fdp, 40))
        else:
            pieces.append(roles.text(fdp, 80))
    content = "\n".join(pieces) + fdp.PickValueInList(["", "\n"])

    picked = PICK.render(installer_sums={"content": content}, ubuntu_iso_name=name)
    assert isinstance(picked, str), repr(picked)
    picked = picked.strip()
    if not picked:
        return
    assert len(picked) == 64 and all(c in "0123456789abcdef" for c in picked), picked
    assert f"{picked} {name}" in content.split("\n") or f"{picked} *{name}" in content.split("\n"), (picked, name)


def main():
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
