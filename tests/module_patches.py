"""Patch a module-level name in every module of a split module or package that reads it."""

import importlib
import pkgutil
from types import ModuleType


def modules_of(*modules: ModuleType) -> list[ModuleType]:
    """The modules given and, for a package, its submodules."""
    found = []
    for module in modules:
        found.append(module)
        if hasattr(module, "__path__"):
            found += [
                importlib.import_module(f"{module.__name__}.{info.name}")
                for info in pkgutil.iter_modules(module.__path__)
            ]
    return found


def patch_everywhere(patch, modules, name: str, value) -> None:
    """Set ``name`` to ``value`` in every one of ``modules`` (packages with their submodules) that holds it.

    A module holds a name it defines or imports; the split modules import nothing they do not read.
    """
    holders = [module for module in modules_of(*modules) if name in vars(module)]
    assert holders, name
    for module in holders:
        patch.setattr(module, name, value)
