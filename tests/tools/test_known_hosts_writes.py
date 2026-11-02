"""Every task that writes the control node's known_hosts runs for one host at a time. ansible.builtin.known_hosts
reads the whole file and replaces it, so two hosts writing at once (forks, delegated to localhost) lose one's entry:
provisioning five VMs at once left one without its host key. Run with `make catalog-test`."""

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WRITERS = {"ansible.builtin.known_hosts", "known_hosts"}


def tasks(items, inherited=None):
    """Yield (task, throttle) for every task in a task list, blocks included; a block's throttle applies inside."""
    for item in items or []:
        throttle = item.get("throttle", inherited)
        for key in ("block", "rescue", "always"):
            if key in item:
                yield from tasks(item[key], throttle)
        yield item, throttle


def known_hosts_writes():
    files = sorted((ROOT / "roles").glob("*/tasks/*.yml")) + sorted((ROOT / "playbooks").glob("**/*.yml"))
    for path in files:
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, list):
            continue
        plays = [p for p in data if isinstance(p, dict) and ("hosts" in p or "import_playbook" in p)]
        lists = [p.get(k) for p in plays for k in ("pre_tasks", "tasks", "post_tasks", "handlers")] if plays else [data]
        for task_list in lists:
            for task, throttle in tasks(task_list):
                if WRITERS & task.keys():
                    yield path.relative_to(ROOT), task.get("name"), throttle


def test_the_writes_are_found():
    names = {name for _, name, _ in known_hosts_writes()}
    assert {"Remove the stale host key", "Trust the new host key", "Add a known_hosts entry for the new port"} <= names


def test_each_write_runs_one_host_at_a_time():
    unthrottled = [(str(path), name) for path, name, throttle in known_hosts_writes() if throttle != 1]
    assert not unthrottled, f"known_hosts writes without throttle: 1: {unthrottled}"
