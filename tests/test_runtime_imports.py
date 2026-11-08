"""Shipped code imports only what the builds ship: the project's runtime dependencies and theirs.

Builds install the runtime dependencies alone (no dependency groups), so a module the development environment has only
through a group fails in the App, not in the tests (numpy in 0.7.0-alpha.17).
"""

import ast
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = [ROOT / "src" / "bmpt", ROOT / "src" / "ba_modding_toolkit"]


def _name(requirement: str) -> str:
    return re.split(r"[\s<>=!~;\[(]", requirement, maxsplit=1)[0].lower().replace("_", "-")


def _closure() -> set[str]:
    """The runtime dependencies of pyproject.toml and, through their installed metadata, theirs."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    todo = [_name(r) for r in project["dependencies"]]
    seen: set[str] = set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            requires = metadata.requires(name) or []
        except metadata.PackageNotFoundError:
            continue
        todo += [_name(r) for r in requires if "extra ==" not in r]
    return seen


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def test_shipped_modules_import_only_runtime_dependencies():
    allowed = _closure()
    providers = metadata.packages_distributions()
    own = {"bmpt", "ba_modding_toolkit", "bamt_dev"}
    missing = {}
    for package in SHIPPED:
        for path in package.rglob("*.py"):
            for module in _imports(path) - own - set(sys.stdlib_module_names):
                distributions = {d.lower().replace("_", "-") for d in providers.get(module, [])}
                if not distributions & allowed:
                    missing.setdefault(module, []).append(str(path.relative_to(ROOT)))
    # platform modules of optional back ends, imported only where they exist (pyobjc's, pythonnet's .NET namespaces)
    missing = {m: files for m, files in missing.items() if m not in {"objc", "AppKit", "Foundation", "WebKit",
                                                                    "Quartz", "UniformTypeIdentifiers", "win32api",
                                                                    "win32con", "win32gui", "clr", "System", "winreg", "_winapi"}}
    assert not missing, missing
