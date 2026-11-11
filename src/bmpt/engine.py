"""The App's one door to the upstream engine, ``ba_modding_toolkit``.

App modules import engine names from here and never from ``ba_modding_toolkit`` itself, so everything the App
depends on in the engine is listed in ``_EXPORTS`` below. ``tests/test_engine_contract.py`` checks that list and the
engine behaviour behind it (file names, match keys, CRC fix, load and save); an upstream sync is judged by those
tests.

Names resolve lazily (PEP 562): importing this module imports nothing from the engine, an App module still loads
only the engine modules whose names it imports, at the moment it imports them, and every access reads the engine
module's current attribute, so engine module state (``utils.EXE_DIR``) and monkeypatched functions behave exactly as
with a direct import. Modules are exported as modules where the App calls through them (``core.process_mod_update``)
so that a replacement set on the engine module is the one the App calls.

Adding a name: add it to ``_EXPORTS`` and to the ``TYPE_CHECKING`` imports; the contract tests keep both in step.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

ENGINE_PACKAGE = "ba_modding_toolkit"
"""The engine's package; ``python -m ba_modding_toolkit`` starts the program from source."""

# Exported name -> (engine module, attribute in it); attribute None exports the module itself.
_EXPORTS: dict[str, tuple[str, str | None]] = {
    # Processing pipelines, called through their modules
    "core": ("core", None),
    "mod_install": ("core.mod_install", None),
    "format_skel_conflicts": ("core.mod_update", "_format_skel_conflicts"),
    # Bundle I/O and the update scope
    "Bundle": ("bundle", "Bundle"),
    "UpdateScope": ("bundle", "UpdateScope"),
    "collect_stream_companions": ("asset", "collect_stream_companions"),
    # Data types and options
    "AnimCheckOptions": ("models", "AnimCheckOptions"),
    "AnimDiffMap": ("models", "AnimDiffMap"),
    "AssetKey": ("models", "AssetKey"),
    "AssetType": ("models", "AssetType"),
    "ContNameTypeKey": ("models", "ContNameTypeKey"),
    "FilePair": ("models", "FilePair"),
    "LogFunc": ("models", "LogFunc"),
    "MatchStrategy": ("models", "MatchStrategy"),
    "Obj": ("models", "Obj"),
    "Patch": ("models", "Patch"),
    "ProgressCallback": ("models", "ProgressCallback"),
    "REPLACEABLE_ASSET_TYPES": ("models", "REPLACEABLE_ASSET_TYPES"),
    "ReplaceAssetType": ("models", "ReplaceAssetType"),
    "SaveOptions": ("models", "SaveOptions"),
    "SkelConvertOptions": ("models", "SkelConvertOptions"),
    "SkelVersionConflict": ("models", "SkelVersionConflict"),
    # File naming
    "CharacterInternalIDMap": ("naming", "CharacterInternalIDMap"),
    "PRELOAD_PREFIX": ("naming", "PRELOAD_PREFIX"),
    "ParsedFilename": ("naming", "ParsedFilename"),
    "parse_filename": ("naming", "parse_filename"),
    "spine_kind": ("naming", "spine_kind"),
    # Game folder layout
    "SEARCH_DIR_SUFFIXES": ("searching", "SEARCH_DIR_SUFFIXES"),
    "get_search_dirs": ("searching", "get_search_dirs"),
    "is_game_install": ("searching", "is_game_install"),
    # Spine
    "get_skel_version": ("spine", "get_skel_version"),
    "get_spine_backend": ("spine", "get_spine_backend"),
    # Strings and languages
    "UI_LANGUAGES": ("i18n", "UI_LANGUAGES"),
    "fallback_language": ("i18n", "fallback_language"),
    "i18n_manager": ("i18n", "i18n_manager"),
    "offered_languages": ("i18n", "offered_languages"),
    "register_locale_dir": ("i18n", "register_locale_dir"),
    "t": ("i18n", "t"),
    # Utilities; ``utils`` itself for the state the App reads at call time (DATA_DIR_OVERRIDE, EXE_DIR)
    "utils": ("utils", None),
    "CRCUtils": ("utils", "CRCUtils"),
    "EXE_DIR": ("utils", "EXE_DIR"),
    "get_BA_path": ("utils", "get_BA_path"),
    "get_version_info": ("utils", "get_version_info"),
    "is_safe_file_name": ("utils.common", "is_safe_file_name"),
    "no_log": ("utils", "no_log"),
    "parse_hex_bytes": ("utils", "parse_hex_bytes"),
    # The classic Tk UI
    "classic_main": ("gui.main", "main"),
}

__all__ = ["ENGINE_PACKAGE", *_EXPORTS]

if TYPE_CHECKING:
    from ba_modding_toolkit import core, utils
    from ba_modding_toolkit.asset import collect_stream_companions
    from ba_modding_toolkit.bundle import Bundle, UpdateScope
    from ba_modding_toolkit.core import mod_install
    from ba_modding_toolkit.core.mod_update import _format_skel_conflicts as format_skel_conflicts
    from ba_modding_toolkit.gui.main import main as classic_main
    from ba_modding_toolkit.i18n import (UI_LANGUAGES, fallback_language, i18n_manager, offered_languages,
                                         register_locale_dir, t)
    from ba_modding_toolkit.models import (REPLACEABLE_ASSET_TYPES, AnimCheckOptions, AnimDiffMap, AssetKey,
                                           AssetType, ContNameTypeKey, FilePair, LogFunc, MatchStrategy, Obj, Patch,
                                           ProgressCallback, ReplaceAssetType, SaveOptions, SkelConvertOptions,
                                           SkelVersionConflict)
    from ba_modding_toolkit.naming import (PRELOAD_PREFIX, CharacterInternalIDMap, ParsedFilename, parse_filename,
                                           spine_kind)
    from ba_modding_toolkit.searching import SEARCH_DIR_SUFFIXES, get_search_dirs, is_game_install
    from ba_modding_toolkit.spine import get_skel_version, get_spine_backend
    from ba_modding_toolkit.utils import CRCUtils, EXE_DIR, get_BA_path, get_version_info, no_log, parse_hex_bytes
    from ba_modding_toolkit.utils.common import is_safe_file_name


def source_of(name: str) -> tuple[str, str | None]:
    """The engine module (full dotted name) and attribute an exported name comes from."""
    module, attribute = _EXPORTS[name]
    return f"{ENGINE_PACKAGE}.{module}", attribute


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = source_of(name)
    module = importlib.import_module(module_name)
    return module if attribute is None else getattr(module, attribute)


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})
