"""Student Hall keeps independent entries and rejects lost updates without altering other settings."""

from copy import deepcopy
from uuid import uuid4

import pytest

from bmpt.core.errors import ServiceError
from bmpt.webui.state import UiState
from bmpt.webui.student_hall import (
    empty_student_hall,
    normalize_student_hall,
    portrait_lines,
    save_student_hall,
    validate_student_hall,
)


def entry(**extra):
    return {"id": str(uuid4()), "alt": "hoshino", "source": "mod:example", **extra}


def test_same_character_different_resources_keep_independent_positions(tmp_path):
    state = UiState(tmp_path / "ui.json")
    value = empty_student_hall()
    value["portraits"] = [entry(source="mod:first", x=400), entry(source="mod:second", x=1200)]
    value["memorialLobbies"] = [entry(source="mod:first"), entry(source="mod:second")]
    value["active"] = value["memorialLobbies"][1]["id"]
    saved = save_student_hall(state, value, 0)
    assert saved["portraits"][0]["x"] == 400
    assert saved["portraits"][1]["x"] == 1200
    assert len(saved["memorialLobbies"]) == 2
    assert saved["active"] == value["active"]
    assert UiState(state.path).get("student_hall") == saved


def test_more_than_game_slot_count_is_preserved():
    value = empty_student_hall()
    value["portraits"] = [entry(source=f"mod:source-{i}") for i in range(100)]
    value["memorialLobbies"] = [entry(source=f"mod:source-{i}") for i in range(100)]
    assert len(validate_student_hall(value)["portraits"]) == 100
    assert len(validate_student_hall(value)["memorialLobbies"]) == 100


def test_removing_active_uses_remaining_order():
    value = empty_student_hall()
    value["memorialLobbies"] = [entry(source="mod:first"), entry(source="mod:second")]
    value["active"] = str(uuid4())
    assert validate_student_hall(value)["active"] == value["memorialLobbies"][0]["id"]
    value["memorialLobbies"].clear()
    assert validate_student_hall(value)["active"] is None


@pytest.mark.parametrize(
    "patch", [{"x": float("nan")}, {"scale": 0}, {"y": True}, {"source": "../outside"}, {"id": "bad"}]
)
def test_invalid_entry_is_rejected_without_silent_data_loss(patch):
    value = empty_student_hall()
    value["portraits"] = [entry(**patch)]
    with pytest.raises(ServiceError):
        validate_student_hall(value)


def test_duplicate_uuid_is_rejected_across_modes():
    value = empty_student_hall()
    value["portraits"] = [entry()]
    value["memorialLobbies"] = deepcopy(value["portraits"])
    with pytest.raises(ServiceError):
        validate_student_hall(value)


def test_cas_retains_other_settings_and_prevents_lost_updates(tmp_path):
    first = UiState(tmp_path / "ui.json")
    second = UiState(first.path)
    first.set("voice_volume", 35)
    save_student_hall(first, empty_student_hall(), 0)
    with pytest.raises(ServiceError, match="student_hall_conflict"):
        save_student_hall(second, empty_student_hall(), 0)
    assert second.get("voice_volume") == 35
    assert second.get("student_hall")["revision"] == 1


def test_failed_publication_preserves_complete_previous_snapshot(tmp_path, monkeypatch):
    state = UiState(tmp_path / "ui.json")
    previous = save_student_hall(state, empty_student_hall(), 0)
    value = deepcopy(previous)
    value["portraits"] = [entry()]

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr("bmpt.webui.state._replace", fail)
    with pytest.raises(OSError):
        save_student_hall(state, value, 1)
    assert UiState(state.path).get("student_hall") == previous


def test_portrait_dialogue_excludes_memorial_and_unsafe_voice_is_not_implicitly_trusted():
    rows = portrait_lines(
        {
            "voice": [
                {
                    "description": "Hoshino_Lobby_1.ogg",
                    "text": "Lobby",
                    "text_original": "Original",
                    "file": "//static.kivo.wiki/test.ogg",
                },
                {"description": "Hoshino_MemorialLobby_1.ogg", "text": "Memorial"},
                {"description": "Hoshino_Lobby_2.ogg", "file": "https://evil.invalid/test.ogg"},
            ]
        }
    )
    assert set(rows) == {"hoshino_lobby_1", "hoshino_lobby_2"}
    assert rows["hoshino_lobby_1"]["audio"] == "https://static.kivo.wiki/test.ogg"
    assert rows["hoshino_lobby_1"]["ja"] == "Original"


def test_portrait_composition_and_cancellation_in_node():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    result = subprocess.run(
        [
            node,
            "--test",
            str(Path(__file__).parent / "js" / "student_hall.test.mjs"),
            str(Path(__file__).parent / "js" / "student_hall_scene.test.mjs"),
            str(Path(__file__).parent / "js" / "student_hall_icons.test.mjs"),
            str(Path(__file__).parent / "js" / "student_hall_memorial.test.mjs"),
            str(Path(__file__).parent / "js" / "student_hall_fullscreen.test.mjs"),
            str(Path(__file__).parent / "js" / "student_hall_sources.test.mjs"),
            str(Path(__file__).parent / "js" / "student_hall_navigation.test.mjs"),
            str(Path(__file__).parent / "js" / "student_hall_backgrounds.test.mjs"),
            str(Path(__file__).parent / "js" / "student_hall_model.test.mjs"),
            str(Path(__file__).parent / "js" / "student_hall_ui.test.mjs"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_library_only_cross_server_entries_are_available_without_local_core(tmp_path):
    from types import SimpleNamespace
    from bmpt.webui.rpc import RpcRegistry
    from bmpt.webui.student_hall import register_api

    alt = SimpleNamespace(
        key="unreleased",
        names={"en": {"full": "Unreleased"}},
        portrait=None,
        memorial_lobby=None,
        official_thumbnail=None,
    )
    mod = SimpleNamespace(
        id="future-mod", target="unreleased", resource_type="memorialLobby", alias="", name="Future lobby"
    )
    service = SimpleNamespace(
        game_found=lambda: True,
        state=UiState(tmp_path / "ui.json"),
        catalog=SimpleNamespace(students=lambda: []),
        library=SimpleNamespace(mods=lambda: [mod], alt_metadata=lambda key: alt),
        game=SimpleNamespace(collection_names=lambda: {"background": ["BG_School_Collection"]}),
    )
    registry = RpcRegistry(current=True)
    register_api(registry, service)
    result = registry.call("studentHall.options", {})["result"]
    (choice,) = result["choices"]
    assert {
        key: choice[key] for key in ("alt", "source", "resourceType", "core", "names", "name", "officialThumbnail")
    } == {
        "alt": "unreleased",
        "source": "mod:future-mod",
        "resourceType": "memorialLobby",
        "core": None,
        "names": alt.names,
        "name": "Future lobby",
        "officialThumbnail": None,
    }
    assert choice["localResourceTypes"] == [] and choice["releasedOnServer"] is False
    assert choice["modCounts"] == {"memorialLobby": 1} and choice["mods"] == 1


def test_global_portrait_scale_defaults_read_and_persistence_leave_entries_unchanged(tmp_path):
    from bmpt.webui.student_hall import normalize_student_hall

    state = UiState(tmp_path / "ui.json")
    legacy = empty_student_hall()
    legacy.pop("portraitScale")
    legacy["portraits"] = [entry(x=500, y=950, scale=0.6)]
    assert normalize_student_hall(legacy)["portraitScale"] == 1
    assert "portraitScale" not in legacy
    previous = save_student_hall(state, legacy, 0)
    changed = deepcopy(previous)
    changed["portraitScale"] = 0.73
    saved = save_student_hall(state, changed, previous["revision"])
    assert saved["portraitScale"] == 0.73
    assert saved["portraits"] == previous["portraits"]
    assert saved["background"] == previous["background"]
    assert UiState(state.path).get("student_hall")["portraitScale"] == 0.73
    for size in (0.25, 1, 1.5):
        changed["portraitScale"] = size
        assert validate_student_hall(changed)["portraitScale"] == size


@pytest.mark.parametrize("scale", [0, 0.249, 1.501, float("nan"), float("inf"), True, "75", None])
def test_invalid_global_portrait_scale_cannot_replace_saved_layout(tmp_path, scale):
    state = UiState(tmp_path / "ui.json")
    previous = save_student_hall(state, empty_student_hall(), 0)
    bad = deepcopy(previous)
    bad["portraitScale"] = scale
    with pytest.raises(ServiceError):
        save_student_hall(state, bad, previous["revision"])
    assert UiState(state.path).get("student_hall") == previous


@pytest.mark.parametrize("mirror", [0, 1, "true", None, []])
def test_portrait_mirror_requires_a_boolean(mirror):
    value = empty_student_hall()
    value["portraits"] = [entry(flipHorizontal=mirror)]
    with pytest.raises(ServiceError):
        validate_student_hall(value)


def test_portrait_mirror_and_layer_order_round_trip(tmp_path):
    state = UiState(tmp_path / "ui.json")
    value = empty_student_hall()
    value["portraits"] = [entry(source="mod:front", flipHorizontal=True), entry(source="mod:back")]
    saved = save_student_hall(state, value, 0)
    assert [row["source"] for row in saved["portraits"]] == ["mod:front", "mod:back"]
    assert [row["flipHorizontal"] for row in saved["portraits"]] == [True, False]
    assert UiState(state.path).get("student_hall")["portraits"] == saved["portraits"]


def test_duplicate_memorial_resource_is_rejected_without_replacing_saved_state(tmp_path):
    state = UiState(tmp_path / "ui.json")
    value = empty_student_hall()
    value["memorialLobbies"] = [entry(source="official"), entry(source="mod:alternate")]
    saved = save_student_hall(state, value, 0)
    duplicated = deepcopy(saved)
    duplicated["memorialLobbies"].append(entry(source="official"))
    with pytest.raises(ServiceError, match="student_hall_duplicate_memorial"):
        save_student_hall(state, duplicated, saved["revision"])
    assert UiState(state.path).get("student_hall") == saved


def test_legacy_memorial_normalization_retains_first_order_and_active_uuid_without_writing(tmp_path):
    state = UiState(tmp_path / "ui.json")
    value = empty_student_hall()
    value["revision"] = 12
    value["portraits"] = [entry(source="official")]
    first, other, active = entry(source="official"), entry(source="mod:other"), entry(source="official")
    value["memorialLobbies"] = [first, other, active]
    value["active"] = active["id"]
    state.set("student_hall", value)
    before = state.path.read_bytes()
    normalized = normalize_student_hall(state.get("student_hall"))
    assert [item["source"] for item in normalized["memorialLobbies"]] == ["official", "mod:other"]
    assert normalized["memorialLobbies"][0]["id"] == normalized["active"] == active["id"]
    assert normalized["revision"] == 12
    ids = [item["id"] for group in ("portraits", "memorialLobbies") for item in normalized[group]]
    assert len(ids) == len(set(ids))
    validate_student_hall(normalized)
    assert state.path.read_bytes() == before
    assert value["memorialLobbies"][0]["id"] == first["id"]
    collision = deepcopy(normalized)
    collision["portraits"][0]["id"] = active["id"]
    with pytest.raises(ServiceError):
        validate_student_hall(collision)
