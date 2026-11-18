"""Field spelling for JSON records and Python keyword arguments."""

from __future__ import annotations

from typing import Any

_JSON_FIELDS = {
    "resource_type": "resourceType",
    "resource_types": "resourceTypes",
    "assigned_alt": "assignedAlt",
    "base_name": "baseName",
    "repair_source_id": "repairSourceId",
    "repaired_copy_id": "repairedCopyId",
    "add_animations": "addAnimations",
    "added_animations": "addedAnimations",
    "officials_size": "officialsSize",
    "configuration_entry": "configurationEntry",
    "group_alts": "groupAlts",
    "has_kept_official": "hasKeptOfficial",
    "released_on_server": "releasedOnServer",
}
_OPAQUE = {
    "aliases",
    "customAliases",
    "names",
    "studentNames",
    "studentAliases",
    "geometry",
    "payload",
    "sourceInfo",
}


def _keyword_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_keyword_fields(item) for item in value]
    if isinstance(value, dict):
        if "resource_type" in value:
            raise TypeError("got an unexpected keyword argument 'resource_type'")
        return {"resource_type" if key == "resourceType" else key: _keyword_fields(item) for key, item in value.items()}
    return value


def rpc_params(method: str, params: dict) -> dict:
    """Spell wire parameter names as the handler's Python keywords."""
    fields = {"resourceType": "resource_type", "memorialLobby": "memorial_lobby"}
    for key in params:
        if key in fields.values():
            raise TypeError(f"got an unexpected keyword argument {key!r}")
    result = {fields.get(key, key): value for key, value in params.items()}
    if method == "classic.queue" and "params" in result:
        result["params"] = _keyword_fields(result["params"])
    if method in ("mods.adopt", "mods.restoreDrift", "mods.dismiss") and "entries" in result:
        result["entries"] = _keyword_fields(result["entries"])
    return result


def wire_value(value: Any) -> Any:
    """Encode the JSON spelling of fields in a service result."""
    if isinstance(value, list):
        return [wire_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    target = value.get("target")
    mod_target = isinstance(target, str) and (
        "resource_type" in value or "base_name" in value or all(key in value for key in ("id", "name", "selected"))
    )
    result = {}
    for key, item in value.items():
        name = _JSON_FIELDS.get(key, key)
        if key == "target" and mod_target:
            name = "genericTarget" if target.startswith("other:") else "targetAlt"
        if name == "report" and isinstance(item, dict):
            result[name] = {_JSON_FIELDS.get(field, field): member for field, member in item.items()}
        elif name in _OPAQUE:
            result[name] = item
        else:
            result[name] = wire_value(item)
    return result
