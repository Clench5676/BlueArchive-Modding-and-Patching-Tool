"""Translate library records at the web API boundary."""

from __future__ import annotations

from typing import Any

from ..host.store_formats import IMPORT_OPTIONS, RESOURCE_TYPES, ZOOM_MODES, source_value

LIBRARY_TYPES = {value: key for key, value in RESOURCE_TYPES.items()}
SPINE_TYPES = {
    "portrait": "spr",
    "memorialLobby": "lobby",
    "official-portrait": "official-spr",
    "official-memorialLobby": "official-lobby",
    "mod": "mod",
    "import": "import",
}

_KEYS = {
    "variant": "alt",
    "variants": "alts",
    "variantKey": "altId",
    "variant_key": "alt_id",
    "family": "student",
    "families": "students",
    "familyNames": "studentNames",
    "familyAliases": "studentAliases",
    "recruitable": "releasedOnServer",
    "released": "releasedOnServer",
    "fieldModel": "fieldChibi",
    "field_model": "field_chibi",
    "modelCores": "chibiCores",
    "modelFiles": "chibiFiles",
    "models": "chibis",
    "assignment": "assignedAlt",
    "label_base": "baseName",
    "repaired_from": "repairSourceId",
    "repaired_id": "repairedCopyId",
    "grafted": "addedAnimations",
    "bg": "background",
    "memorials": "memorialLobbies",
    "mirror": "flipHorizontal",
    "photo": "thumbnail",
    "photos": "thumbnails",
    "originals": "officials",
    "originalsSize": "officialsSize",
    "localKinds": "localResourceTypes",
    "expressionCues": "animationCues",
}
_OPAQUE = {
    "aliases",
    "customAliases",
    "names",
    "studentNames",
    "studentAliases",
    "familyNames",
    "geometry",
    "report",
    "payload",
    "sourceInfo",
}
_RESOURCE_MAPS = {"modCounts", "configured", "previewSources", "selection", "estimates"}
_SOURCES = {"source", "applied", "optimized_from", "configured", "carried", "replaced", "previewSource"}
_SETTINGS = {
    "lobbyZoom": "memorialLobbyZoomOut",
    "lobbyZoomCustom": "memorialLobbyZoomOutCustomLimit",
    "lobbyPost": "memorialLobbyPostProcessing",
    "lobbyIntro": "memorialLobbyIntro",
    "roster": "studentList",
}
_SETTING_INPUT = {value: key for key, value in _SETTINGS.items()}


def resource_type(kind: Any) -> Any:
    """Return the wire resource type for a library classification."""
    return RESOURCE_TYPES.get(kind, kind) if isinstance(kind, str) else kind


def library_kind(value: Any) -> Any:
    """Return the library classification for a wire resource type."""
    if isinstance(value, str):
        return LIBRARY_TYPES.get(value, value)
    return value


def _source_out(value: Any) -> Any:
    return source_value(value)


def wire_resource_map(value: dict) -> dict:
    """Encode values in a map indexed by resource type."""
    return {
        RESOURCE_TYPES.get(key, key): _source_out(item) if isinstance(item, str) else wire_value(item)
        for key, item in value.items()
    }


def import_options_from_storage(options: Any) -> dict:
    """Expose saved option names to the batch import service."""
    if not isinstance(options, dict):
        return {}
    options = {
        key: item
        for key, item in options.items()
        if key in IMPORT_OPTIONS
        or key in ("compression", "extraBytes", "enableAfterImport", "previewAfterImport", "skipUnchanged")
    }
    return {
        IMPORT_OPTIONS.get(key, key): "containerNameType" if key == "strategy" and item == "cont_name_type" else item
        for key, item in options.items()
    }


def stored_import_options(options: Any) -> Any:
    """Encode batch options for the UI-state storage adapter."""
    if not isinstance(options, dict):
        return options
    renamed = {new: old for old, new in IMPORT_OPTIONS.items()}
    return {
        renamed.get(key, key): "cont_name_type" if key == "matchStrategy" and item == "containerNameType" else item
        for key, item in options.items()
    }


def _student_list_out(value: dict) -> dict:
    return {
        "groupAlts" if key == "merged" else "releaseStatus" if key == "recruit" else key: (
            {{"lobby": "memorialLobby", "model": "chibi"}.get(group, group): modes for group, modes in item.items()}
            if key == "filters" and isinstance(item, dict)
            else item
        )
        for key, item in value.items()
    }


def _student_list_in(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {
        "merged" if key == "groupAlts" else "recruit" if key == "releaseStatus" else key: (
            {{"memorialLobby": "lobby", "chibi": "model"}.get(group, group): modes for group, modes in item.items()}
            if key == "filters" and isinstance(item, dict)
            else item
        )
        for key, item in value.items()
    }


def _hall_input(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if result.get("mode") == "memorialLobby":
        result["mode"] = "memorial"
    if "memorialLobbies" in result:
        result["memorials"] = result.pop("memorialLobbies")
    for group in ("portraits", "memorials"):
        if isinstance(result.get(group), list):
            result[group] = [
                {
                    "variant" if key == "alt" else "mirror" if key == "flipHorizontal" else key: source_value(
                        item, forward=False
                    )
                    if key == "source"
                    else item
                    for key, item in row.items()
                }
                if isinstance(row, dict)
                else row
                for row in result[group]
            ]
    return result


def _input_node(value: Any) -> Any:
    if isinstance(value, list):
        return [_input_node(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "resourceType":
                result["kind"] = library_kind(item)
            elif key == "alt":
                result["variant"] = item
            elif key == "source":
                result[key] = source_value(item, forward=False)
            else:
                result[key] = _input_node(item)
        return result
    return value


def rpc_params(method: str, params: dict) -> dict:
    """Decode the current API request for a handler that operates on library values."""
    result = dict(params)
    if method == "classic.queue" and isinstance(result.get("params"), dict):
        nested = _input_node(result["params"])
        result["params"] = nested
    if "memorialLobby" in result:
        result["memorial_lobby"] = result.pop("memorialLobby")
    if "resourceType" in result:
        result["resource_type"] = library_kind(result.pop("resourceType"))
    if "kind" in result and method in ("actions.apply", "actions.restore", "ui.preview", "export.start"):
        result["resource_type"] = library_kind(result.pop("kind"))
    if "variant" in result and "alt" not in result:
        result["alt"] = result.pop("variant")
    if "family" in result and "student" not in result:
        result["student"] = result.pop("family")
    if "lobby" in result and method == "memorialLobby.camera.save" and "memorial_lobby" not in result:
        result["memorial_lobby"] = result.pop("lobby")
    if "source" in result:
        result["source"] = source_value(result["source"], forward=False)
    if "target" in result:
        result["target"] = source_value(result["target"], forward=False)

    if method == "settings.set":
        key = result.get("key")
        if isinstance(key, str):
            result["key"] = _SETTING_INPUT.get(key, key)
        if key == "studentList":
            result["value"] = _student_list_in(result.get("value"))
        elif key == "memorialLobbyZoomOut" and isinstance(result.get("value"), str):
            result["value"] = {new: old for old, new in ZOOM_MODES.items()}.get(result["value"], result["value"])
    if method == "studentHall.update" and "value" in result:
        result["value"] = _hall_input(result["value"])
    if method == "thumbnails.save" and result.get("mode") == "memorialLobby":
        result["mode"] = "lobby"
    if method in ("mods.adopt", "mods.restoreDrift", "mods.dismiss") and "entries" in result:
        result["entries"] = _input_node(result["entries"])
    if method == "mods.sizes" and result.get("kind") == "officials":
        result["kind"] = "originals"
    return result


def asset_url(value: str) -> str:
    """Rename a known app asset route, keeping basenames and query data."""
    for old, new in (
        ("art/portrait/", "art/officialThumbnail/"),
        ("art/bg/", "art/background/"),
        ("spine/official-spr/", "spine/official-portrait/"),
        ("spine/official-lobby/", "spine/official-memorialLobby/"),
        ("spine/spr/", "spine/portrait/"),
        ("spine/lobby/", "spine/memorialLobby/"),
        ("photography/", "thumbnails/"),
        ("model/", "chibi/"),
    ):
        prefix = f"/asset/{old}"
        if value.startswith(prefix):
            return f"/asset/{new}" + value[len(prefix) :]
    return value


def wire_value(value: Any) -> Any:
    """Encode the known API fields in an already JSON-ready service result."""
    if isinstance(value, list):
        return [wire_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "skipUnchanged" in value and ("strategy" in value or "types" in value):
        return import_options_from_storage(value)
    if "merged" in value and "sort" in value:
        return _student_list_out(value)
    mod_target = isinstance(value.get("target"), str) and any(key in value for key in ("kind", "label", "label_base"))
    mod_name = (
        "label_base" in value
        or mod_target
        or (isinstance(value.get("source"), str) and "variant" in value and "kind" in value)
    )
    student = "variants" in value
    catalog_alt = "family" in value and ("names" in value or "key" in value)
    hall_option = "variant" in value and "portrait" in value and "source" in value
    line = any(key in value for key in ("ja", "jp", "ko", "en", "zh", "tw", "th", "time", "speaker", "clip"))
    out = {}
    for key, item in value.items():
        target = _KEYS.get(key, _SETTINGS.get(key, key))
        if key == "kind" and (
            isinstance(item, str) and item in RESOURCE_TYPES or item is None and ("variant" in value or "alt" in value)
        ):
            target, item = "resourceType", resource_type(item)
        elif key in ("kinds", "localKinds", "storedKinds") and isinstance(item, list):
            if key == "kinds":
                target = "resourceTypes"
            item = [resource_type(entry) for entry in item]
        elif key in ("spr", "lobby", "model") and (catalog_alt or key in ("spr", "lobby") and ("spr" in value)):
            target = RESOURCE_TYPES[key]
        elif key == "base" and student and isinstance(item, dict):
            target = "studentNames"
        elif key == "custom" and (catalog_alt or "aliases" in value):
            target = "customAliases"
        elif key == "portrait" and (catalog_alt or hall_option):
            target = "officialThumbnail"
        elif key == "target" and mod_target:
            target = "genericTarget" if item.startswith("other:") else "targetAlt"
        elif key == "label" and mod_name:
            target = "name"
        elif key == "original" and isinstance(item, bool) and ("applied" in value or "restorable" in value):
            target = "hasKeptOfficial"
        elif key == "expression" and line and "mode" not in value:
            target = "animationName"
        elif key == "mode" and item in ("memorial", "lobby"):
            item = "memorialLobby"
        elif key == "mode" and isinstance(item, str) and "custom" in value:
            item = ZOOM_MODES.get(item, item)
        elif key == "slot" and "source" in value and "core" in value:
            target = "configurationEntry"
        elif key == "lobby" and "variant" in value:
            target = "memorialLobby"
        if key in _RESOURCE_MAPS and isinstance(item, dict):
            out[target] = wire_resource_map(item)
        elif key in _SOURCES:
            out[target] = _source_out(item) if isinstance(item, str) else wire_value(item)
        elif key in ("url", "portrait", "bg", "thumbnail") and isinstance(item, str) and item.startswith("/asset/"):
            out[target] = asset_url(item)
        elif target in _OPAQUE:
            out[target] = item
        else:
            out[target] = wire_value(item)
    return out


def wire_error(error: dict) -> dict:
    """Use API names for typed error parameters and retain game and native terms."""
    params = error.get("params")
    if not isinstance(params, dict):
        return error
    value = wire_value(params)
    if "key" in value and isinstance(value["key"], str):
        value["key"] = {
            "variant": "alt",
            "family": "student",
            "lobby": "memorialLobby",
            "mirror": "flipHorizontal",
            "memorials": "memorialLobbies",
            "label": "alias",
        }.get(value["key"], value["key"])
    return {**error, "params": value}


def wire_event(event: str, data: Any) -> Any:
    """Encode an event, keeping the independent job kind field."""
    if event in ("job.started", "job.done", "job.failed", "job.cancelled") and isinstance(data, dict):
        outcome = {key: item if key == "kind" else wire_value(item) for key, item in data.items()}
        return wire_error(outcome) if event == "job.failed" else outcome
    return wire_value(data)
