"""Keep the App/engine boundary tied to the accepted upstream ownership inventory."""

import ast
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "package_split"
OWNERSHIP = json.loads((FIXTURES / "ownership.json").read_text())
ENGINE = ROOT / "src" / "ba_modding_toolkit"
APP = ROOT / "src" / "bmpt"


class MaskImports(ast.NodeTransformer):
    def visit_Import(self, node):
        return ast.Pass()

    def visit_ImportFrom(self, node):
        return ast.Pass()


def test_only_inventory_imports_cross_from_engine_to_app():
    expected = {(row["file"], row["line"]): row["import"] for row in OWNERSHIP["allowed_app_imports"]}
    found = {}
    files = {p.relative_to(ROOT).as_posix() for p in ENGINE.rglob("*.py")}
    assert files == set(OWNERSHIP["engine_modules"])
    for relative in sorted(files):
        source = (ROOT / relative).read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                targets = [node.module or ""]
            elif isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                continue
            if any(name == "bmpt" or name.startswith("bmpt.") for name in targets):
                found[relative, node.lineno] = ast.unparse(node)
        if relative != "src/ba_modding_toolkit/i18n.py":
            allowed_lines = {line for file, line in expected if file == relative}
            text = "".join("IMPORT\n" if number in allowed_lines else line
                           for number, line in enumerate(source.splitlines(keepends=True), 1))
            assert hashlib.sha256(text.encode()).hexdigest() == OWNERSHIP["engine_modules"][relative]["masked_import_lines_sha256"], relative
            masked = ast.dump(MaskImports().visit(tree), include_attributes=False)
            assert hashlib.sha256(masked.encode()).hexdigest() == OWNERSHIP["engine_modules"][relative]["non_import_ast_sha256"], relative
    assert found == expected
    assert "bmpt" not in (ENGINE / "i18n.py").read_text()


def test_engine_never_aliases_or_reexports_app_modules():
    for path in ENGINE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        bindings = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module == "bmpt" or (node.module or "").startswith("bmpt.")):
                assert path.name != "__init__.py", path
                bindings.update(alias.asname or alias.name for alias in node.names)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "bmpt" or alias.name.startswith("bmpt."):
                        assert path.name != "__init__.py", path
                        bindings.add(alias.asname or alias.name.split(".")[0])
            # Reject dynamic module aliases or lazy imports that bypass the static import allowlist.
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not (node.value == "bmpt" or node.value.startswith("bmpt.")), path
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                value = node.value
                assert not (isinstance(value, ast.Name) and value.id in bindings), (path, node.lineno)
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
                    exported = {item.value for item in ast.walk(value) if isinstance(item, ast.Constant)}
                    assert not exported & bindings, path
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattr__":
                assert not {item.id for item in ast.walk(node) if isinstance(item, ast.Name)} & bindings, path


def test_app_package_initialization_is_empty():
    assert (APP / "__init__.py").read_bytes() == b""


def python_check(code):
    result = subprocess.run([sys.executable, "-c", code], env=os.environ.copy(), text=True,
                            capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("order", list(itertools.permutations([
    "bmpt.webui.main", "ba_modding_toolkit.gui.main", "ba_modding_toolkit.cli.main",
])))
def test_entry_import_orders_have_no_cycles(order):
    python_check("import importlib\n" + "\n".join(f"importlib.import_module({module!r})" for module in order))


def test_engine_leaf_imports_keep_app_initialization_light():
    python_check("""
import sys
import ba_modding_toolkit.utils
assert 'bmpt.devmode' in sys.modules
assert 'bmpt.webui' not in sys.modules
assert 'bmpt.localization' not in sys.modules
from ba_modding_toolkit import i18n
assert i18n._locale_dirs == []
assert i18n.I18n('en-US').t('webui.error.required_asset_stream_missing') == 'webui.error.required_asset_stream_missing'
""")


@pytest.mark.parametrize("entry", ["bmpt", "bmpt.launcher", "bmpt.webui", "ba_modding_toolkit"])
@pytest.mark.parametrize("classic", [False, True])
def test_entries_register_before_dispatch(entry, classic):
    # Dispatch only to a stub: no native window or server is ever started.
    python_check(f"""
import runpy, sys, types
from ba_modding_toolkit import i18n
seen = []
def check(*args):
    assert i18n.i18n_manager.t('webui.error.required_asset_stream_missing') != 'webui.error.required_asset_stream_missing'
    seen.append(True)
for name in ('bmpt.webui.main', 'ba_modding_toolkit.gui.main'):
    module = types.ModuleType(name)
    module.main = check
    sys.modules[name] = module
sys.argv = [{entry!r}] + (['--classic'] if {classic!r} else [])
runpy.run_module({entry!r}, run_name='__main__')
assert seen == [True]
""")


def test_package_data_and_builds_cover_every_shipped_resource():
    import glob
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    package_data = project["tool"]["setuptools"]["package-data"]
    for package in ("bmpt", "ba_modding_toolkit"):
        folder = ROOT / "src" / package
        resources = {path for path in folder.rglob("*")
                     if path.is_file() and path.suffix != ".py" and "__pycache__" not in path.parts}
        included = {Path(match) for pattern in package_data[package]
                    for match in glob.glob(str(folder / pattern)) if Path(match).is_file()}
        assert resources <= included, sorted(str(path.relative_to(ROOT)) for path in resources - included)
    for filename in ("build_macos.sh", "build_windows.ps1"):
        source = (ROOT / "scripts" / filename).read_text()
        for folder in ("bmpt/assets", "bmpt/locales", "bmpt/web", "bmpt/catalog/data",
                       "ba_modding_toolkit/assets", "ba_modding_toolkit/locales"):
            assert f"--include-data-dir={folder}={folder}" in source, (filename, folder)
        assert "--include-package-data=bmpt" in source
        assert "--include-package-data=ba_modding_toolkit" in source
    assert project["project"]["scripts"]["bamt"] == "bmpt.launcher:main"
