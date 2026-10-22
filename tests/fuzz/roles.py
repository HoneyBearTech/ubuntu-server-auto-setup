"""Loads the real templates, Jinja expressions and input patterns from the roles, rendered with Ansible's own
filters and tests, so the fuzz targets exercise exactly what the playbooks run."""

import itertools
import pathlib
import re

import jinja2
import jinja2.nativetypes
import yaml
from ansible.plugins.filter.core import FilterModule
from ansible.plugins.filter.mathstuff import FilterModule as MathFilterModule
from ansible.plugins.test.core import TestModule

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _setup(env):
    env.filters.update(FilterModule().filters())
    env.filters.update(MathFilterModule().filters())
    env.tests.update(TestModule().tests())
    return env


def _ansible_concat(values):
    """Like Ansible 2.19+: a single-expression template returns that value unchanged (a string stays a string),
    several parts are joined as text. Plain jinja2 native mode would turn "0000..." into the integer 0."""
    head = list(itertools.islice(values, 2))
    if not head:
        return None
    if len(head) == 1:
        return head[0]
    return "".join(str(v) for v in itertools.chain(head, values))


class _AnsibleNativeTemplate(jinja2.nativetypes.NativeTemplate):
    pass


class _AnsibleNativeEnvironment(jinja2.nativetypes.NativeEnvironment):
    concat = staticmethod(_ansible_concat)
    template_class = _AnsibleNativeTemplate


_AnsibleNativeTemplate.environment_class = _AnsibleNativeEnvironment

# Ansible renders templates with trim_blocks on, and evaluates set_fact expressions to native types.
TEXT = _setup(jinja2.Environment(undefined=jinja2.StrictUndefined, trim_blocks=True, keep_trailing_newline=True))
NATIVE = _setup(_AnsibleNativeEnvironment(undefined=jinja2.StrictUndefined))


def template(role, name):
    return TEXT.from_string((ROOT / "roles" / role / "templates" / name).read_text())


def role_vars(role):
    return yaml.safe_load((ROOT / "roles" / role / "vars" / "main.yml").read_text())


def set_fact_expression(path, var, last=False):
    """The Jinja expression a set_fact task in a role assigns to var (the last such task if last)."""
    return NATIVE.from_string(_set_fact_task(path, var, last)["ansible.builtin.set_fact"][var].strip())


def set_fact_vars(path, var, last=False):
    """The Jinja expressions of the task vars of the set_fact task that assigns var, by name."""
    task_vars = _set_fact_task(path, var, last).get("vars") or {}
    return {name: NATIVE.from_string(str(value).strip()) for name, value in task_vars.items()}


def set_fact_raw_vars(path, var, last=False):
    """The task vars of the set_fact task that assigns var, as written (render them with render())."""
    return _set_fact_task(path, var, last).get("vars") or {}


def render(value, context):
    """Renders a task value the way Ansible does: every string in it is a template, evaluated to a native value."""
    if isinstance(value, str):
        if "{{" not in value and "{%" not in value:  # not a template: Ansible keeps it as it is ("" included)
            return value
        return NATIVE.from_string(value.strip()).render(context)
    if isinstance(value, list):
        return [render(v, context) for v in value]
    if isinstance(value, dict):
        return {k: render(v, context) for k, v in value.items()}
    return value


def assert_conditions(path, name):
    """The conditions of the assert task with this name, compiled. Ansible checks them in order and fails at the
    first one that is false, or that can't be evaluated."""
    for task in _walk(yaml.safe_load((ROOT / path).read_text())):
        if task.get("name") == name:
            that = task["ansible.builtin.assert"]["that"]
            return [NATIVE.compile_expression(c.strip()) for c in ([that] if isinstance(that, str) else that)]
    raise KeyError(f"no assert task {name!r} in {path}")


def accepted(conditions, context):
    """Whether an assert task with these conditions passes."""
    try:
        return all(bool(condition(**context)) for condition in conditions)
    except Exception:  # noqa: BLE001 (Ansible fails the task on any error in a condition)
        return False


def _set_fact_task(path, var, last=False):
    """The (first, or last) set_fact task that assigns var."""
    tasks = [task for task in _walk(yaml.safe_load((ROOT / path).read_text()))
             if var in (task.get("ansible.builtin.set_fact") or {})]
    if not tasks:
        raise KeyError(f"no set_fact for {var} in {path}")
    return tasks[-1] if last else tasks[0]


def _walk(tasks):
    for task in tasks or []:
        yield task
        for section in ("block", "rescue", "always"):
            yield from _walk(task.get(section))


def matches(pattern, value):
    """Ansible's `match` test: re.match from the start of the string."""
    return isinstance(value, str) and re.match(pattern, value) is not None


def text(fdp, max_len=40):
    return fdp.ConsumeUnicodeNoSurrogates(fdp.ConsumeIntInRange(0, max_len))


def from_alphabet(fdp, alphabet, max_len=20):
    """A string built from a given alphabet, so values that pass a role's pattern are generated often."""
    return "".join(alphabet[fdp.ConsumeIntInRange(0, len(alphabet) - 1)] for _ in range(fdp.ConsumeIntInRange(0, max_len)))


def text_list(fdp, max_items=4, max_len=30):
    return [text(fdp, max_len) for _ in range(fdp.ConsumeIntInRange(0, max_items))]


def username(fdp):
    """Mostly valid Linux user names, sometimes with a character the role must refuse."""
    if fdp.ConsumeIntInRange(0, 9) == 0:
        return from_alphabet(fdp, "abz09_-;'\" $`\n\t/.", 12)
    first = "abcdefghijklmnopqrstuvwxyz_"
    rest = "abcdefghijklmnopqrstuvwxyz0123456789_-"
    return first[fdp.ConsumeIntInRange(0, len(first) - 1)] + from_alphabet(fdp, rest, 12)


def hostname(fdp):
    """Mostly valid host names, sometimes with a character the role must refuse."""
    if fdp.ConsumeIntInRange(0, 9) == 0:
        return from_alphabet(fdp, "abZ09-.:# \n'\"", 20)
    edge = "abcdefghijklmnopqrstuvwxyzABCXYZ0123456789"
    name = edge[fdp.ConsumeIntInRange(0, len(edge) - 1)] + from_alphabet(fdp, edge + "-", 20)
    return name if name[-1] != "-" else name + "0"


def ipv4(fdp):
    """Mostly valid dotted-quad addresses, sometimes malformed."""
    if fdp.ConsumeIntInRange(0, 9) == 0:
        return from_alphabet(fdp, "0123456789.\n :", 16)
    return ".".join(str(fdp.ConsumeIntInRange(0, 255)) for _ in range(4))


def mac(fdp):
    """Mostly valid MAC addresses (sometimes multicast), sometimes malformed, sometimes empty."""
    choice = fdp.ConsumeIntInRange(0, 5)
    if choice == 0:
        return ""
    if choice == 1:
        return from_alphabet(fdp, "0123456789abcdefABCDEF:,= \n", 20)
    return ":".join(f"{fdp.ConsumeIntInRange(0, 255):02{'x' if fdp.ConsumeBool() else 'X'}}" for _ in range(6))
