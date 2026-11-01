#!/usr/bin/env python3
"""Task coverage for the roles: the share of tasks and handlers that the integration tests ran.

Ansible has no statement coverage tool, so this counts each task and handler in roles/*/tasks (and its
subfolders, such as the per-family files) and roles/*/handlers (blocks and static imports are containers, not
tasks; meta tasks are skipped) and checks it against the "path:line" records written by
tests/callback_plugins/task_coverage.py.
Each test suite measures the roles it exercises: tests/run.sh every role but updates, tests/updates.sh updates.

Usage: coverage.py RECORDS_FILE [--min PERCENT] [--roles R,...] [--skip-roles R,...] [--skip-files GLOB,...]
       (exits 1 below the minimum). --skip-files leaves out task files for an OS family the suite doesn't run
       (tests/baseline.sh on Debian skips */RedHat.yml).
"""

import argparse
import fnmatch
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def tasks_in(node, path, out):
    """Collect (path, line, name) for every task mapping in a task list node."""
    if not isinstance(node, yaml.SequenceNode):
        return
    for item in node.value:
        if not isinstance(item, yaml.MappingNode):
            continue
        keys = {k.value: v for k, v in item.value}
        if "block" in keys:
            for section in ("block", "rescue", "always"):
                if section in keys:
                    tasks_in(keys[section], path, out)
            continue
        # Static imports, like blocks, only hold their file's tasks, which are counted there.
        if {"ansible.builtin.meta", "meta", "ansible.builtin.import_tasks", "import_tasks"} & keys.keys():
            continue
        name = keys["name"].value if "name" in keys else "(unnamed)"
        out.append((path, item.start_mark.line + 1, name))


def all_tasks(roles=None, skip=(), skip_files=()):
    out = []
    for path in sorted(ROOT.glob("roles/*/tasks/**/*.yml")) + sorted(ROOT.glob("roles/*/handlers/*.yml")):
        role = path.relative_to(ROOT).parts[1]
        rel = path.relative_to(ROOT).as_posix()
        if (roles and role not in roles) or role in skip or any(fnmatch.fnmatch(rel, g) for g in skip_files):
            continue
        tasks_in(yaml.compose(path.read_text()), path.relative_to(ROOT).as_posix(), out)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records")
    parser.add_argument("--min", type=float, default=80.0)
    parser.add_argument("--roles", default="", help="only these roles (comma-separated)")
    parser.add_argument("--skip-roles", default="", help="every role but these (comma-separated)")
    parser.add_argument("--skip-files", default="", help="leave out task files matching these globs (comma-separated)")
    args = parser.parse_args()
    only = {r for r in args.roles.split(",") if r}
    skip = {r for r in args.skip_roles.split(",") if r}

    ran = set()
    for line in pathlib.Path(args.records).read_text().splitlines():
        path, _, lineno = line.rpartition(":")
        rel = path.split("/roles/", 1)[-1]
        ran.add((f"roles/{rel}", int(lineno)))

    tasks = all_tasks(only, skip, [g for g in args.skip_files.split(",") if g])
    missed = [t for t in tasks if (t[0], t[1]) not in ran]
    covered = len(tasks) - len(missed)
    pct = 100.0 * covered / len(tasks)
    by_role = {}
    for path, line, _ in tasks:
        role = path.split("/")[1]
        total, hit = by_role.get(role, (0, 0))
        by_role[role] = (total + 1, hit + ((path, line) in ran))
    print("Task coverage by role:")
    for role, (total, hit) in sorted(by_role.items()):
        print(f"  {role:16} {hit:3}/{total:<3} {100.0 * hit / total:5.1f}%")
    print(f"Total: {covered}/{len(tasks)} tasks and handlers ran = {pct:.1f}% (minimum {args.min:.0f}%)")
    if missed:
        print("Not run by the tests:")
        for path, line, name in missed:
            print(f"  {path}:{line}  {name}")
    return 0 if pct >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
