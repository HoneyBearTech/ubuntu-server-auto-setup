"""The cluster's VMs, as the VMs page shows them.

The parsing and classification live in plugins/module_utils/usas_vms.py, shared with the playbooks' plugins so
the page can't drift from what they do. This module loads that file from the project (as nodes.py loads the
node planner) and offers its names here, so the rest of the web UI keeps using vms.parse_resources and so on.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SHARED = Path(__file__).resolve().parents[2] / "plugins" / "module_utils" / "usas_vms.py"
_NAME = "usas_vms"


def _load():
    if _NAME in sys.modules:
        return sys.modules[_NAME]
    spec = importlib.util.spec_from_file_location(_NAME, SHARED)
    if spec is None or spec.loader is None:  # pragma: no cover (a broken checkout)
        raise RuntimeError(f"can't load {SHARED}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_NAME] = module  # dataclasses look their module up while the file runs
    try:
        spec.loader.exec_module(module)
    except BaseException:  # pragma: no cover (a broken checkout)
        del sys.modules[_NAME]
        raise
    return module


def _own(name: str, value: object) -> bool:
    """A public name the shared file defines itself, not one it imported (re, ipaddress, dataclass, ...)."""
    if name.startswith("_") or isinstance(value, ModuleType):
        return False
    return value.__module__ == _NAME if isinstance(value, type) or callable(value) else True


_shared = _load()
__all__ = [name for name, value in vars(_shared).items() if _own(name, value)]
globals().update({name: getattr(_shared, name) for name in __all__})
