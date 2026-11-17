"""The optional converter route of the graft (bmpt.spine_converter): fetching the pinned release, running it safely,
and the graft in JSON."""

import hashlib
import io
import json

import pytest

from bmpt import spine_converter as sc


def _official():
    return {
        "skeleton": {"spine": "4.2.33"},
        "bones": [
            {"name": "root"},
            {"name": "face", "parent": "root"},
            {"name": "tear", "parent": "face"},
            {"name": "tear_tip", "parent": "tear"},
        ],
        "slots": [
            {"name": "body", "bone": "root"},
            {"name": "face", "bone": "face"},
            {"name": "tear", "bone": "tear_tip", "attachment": None},
            {"name": "glow", "bone": "root"},
        ],
        "events": {"sob": {"int": 1}},
        "ik": [{"name": "tear_ik", "bones": ["tear"], "target": "tear_tip"}],
        "skins": [
            {
                "name": "default",
                "attachments": {
                    "face": {"face_00": {"type": "mesh", "vertices": [0, 0, 1, 1]}, "face_09": {"type": "region"}},
                    "tear": {"drop": {"type": "region"}},
                },
            }
        ],
        "animations": {
            "00": {"slots": {"face": {"attachment": [{"name": "face_00"}]}}},
            "09": {
                "slots": {"face": {"attachment": [{"name": "face_09"}]}, "tear": {"attachment": [{"name": "drop"}]}},
                "bones": {"tear": {"rotate": [{"value": 10}]}},
                "ik": {"tear_ik": [{"mix": 1}]},
                "events": [{"name": "sob"}],
            },
            "10": {"attachments": {"default": {"face": {"face_00": {"deform": [{"vertices": [0.1]}]}}}}},
            "11": {"attachments": {"blush": {"face": {"face_00": {"deform": [{}]}}}}},
        },
    }


def _mod():
    return {
        "skeleton": {"spine": "4.2.33"},
        "bones": [{"name": "root"}, {"name": "face", "parent": "root"}],
        "slots": [{"name": "body", "bone": "root"}, {"name": "face", "bone": "face"}, {"name": "glow", "bone": "root"}],
        "skins": [
            {"name": "default", "attachments": {"face": {"face_00": {"type": "mesh", "vertices": [0, 0, 1, 1, 2, 2]}}}}
        ],
        "animations": {"00": {"slots": {"face": {"attachment": [{"name": "face_00"}]}}}},
    }


def test_an_animation_the_built_in_graft_refuses_is_added_in_json_with_what_it_needs():
    mod = _mod()
    result = sc.graft_json(mod, _official())
    assert result.added == ["09"]
    assert result.bones == ["tear", "tear_tip"] and result.slots == ["tear"]
    assert [b["name"] for b in mod["bones"]] == ["root", "face", "tear", "tear_tip"]
    # the new slot sits where the official order has it: after the face, before the glow
    assert [s["name"] for s in mod["slots"]] == ["body", "face", "tear", "glow"]
    assert mod["events"] == {"sob": {"int": 1}}
    assert [c["name"] for c in mod["ik"]] == ["tear_ik"]
    attachments = mod["skins"][0]["attachments"]
    assert set(attachments["face"]) == {"face_00", "face_09"} and set(attachments["tear"]) == {"drop"}
    assert attachments["face"]["face_00"]["vertices"] == [0, 0, 1, 1, 2, 2]  # the mod's own stays as it is
    assert result.regions == ["face_09", "drop"]  # what the atlas must gain
    # refused, with nothing of them added: a deform of a face the mod redrew, a skin the mod lacks
    assert set(result.refused) == {"10", "11"}
    assert "redrawn" in result.refused["10"] and result.refused["11"] == "skin blush"
    assert set(mod["animations"]) == {"00", "09"}


def test_the_release_is_used_only_at_its_pinned_size_and_hash(tmp_path, monkeypatch):
    payload = b"MZ" + b"\0" * 100
    monkeypatch.setattr(
        sc,
        "RELEASE",
        sc.Release(
            "v9", "Tool.exe", "https://example.invalid/Tool.exe", hashlib.sha256(payload).hexdigest(), len(payload)
        ),
    )

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    asked = []

    def opener(request, timeout):
        asked.append(request.full_url)
        return Response(payload)

    with pytest.raises(sc.ConverterError, match="converter_consent"):
        sc.download(tmp_path, consented=False, platform="win32", opener=opener)
    with pytest.raises(sc.ConverterError, match="converter_unavailable"):
        sc.download(tmp_path, consented=True, platform="darwin", opener=opener)
    assert asked == []
    path = sc.download(tmp_path, consented=True, platform="win32", opener=opener)
    assert asked == ["https://example.invalid/Tool.exe"] and path.read_bytes() == payload
    assert sc.located(tmp_path, "win32") == path
    path.write_bytes(payload[:-1] + b"x")  # changed since: never run
    assert sc.located(tmp_path, "win32") is None
    monkeypatch.setattr(
        sc, "RELEASE", sc.Release("v9", "Tool.exe", "https://example.invalid/Tool.exe", "0" * 64, len(payload))
    )
    with pytest.raises(sc.ConverterError, match="converter_mismatch"):
        sc.download(tmp_path, consented=True, platform="win32", opener=opener)
    assert not list(sc.tool_dir(tmp_path).glob("*.part"))
    status = sc.status(tmp_path, "darwin")
    assert status["platform"] == "macos" and not status["downloadable"] and status["licence"] == sc.LICENCE


def test_a_macos_build_the_user_compiled_is_found_in_the_tools_folder(tmp_path):
    assert sc.located(tmp_path, "darwin") is None
    exe = sc.tool_dir(tmp_path) / sc.MAC_NAME
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"#!/bin/sh\n")
    assert sc.located(tmp_path, "darwin") is None  # not executable
    exe.chmod(0o755)
    assert sc.located(tmp_path, "darwin") == exe
    assert sc.located(tmp_path, "linux") is None


def test_the_converter_runs_on_a_temporary_copy_with_an_argument_list(tmp_path, monkeypatch):
    calls = []

    def run(exe, source, target):
        calls.append((exe, source, target))
        assert source.parent == target.parent and source.read_bytes() == b'{"skeleton": {}}'
        target.write_bytes(b"converted")
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setattr(sc, "_run", run)
    assert sc.convert(tmp_path / "tool", b'{"skeleton": {}}', ".json", ".skel") == b"converted"
    ((exe, source, target),) = calls
    assert not source.exists() and source.suffix == ".json" and target.suffix == ".skel"
    monkeypatch.setattr(sc, "_run", lambda exe, source, target: type("Done", (), {"returncode": 3})())
    with pytest.raises(sc.ConverterError, match="converter_failed"):
        sc.convert(tmp_path / "tool", b"x", ".skel", ".json")


def test_a_skeleton_the_apps_reader_cannot_walk_never_reaches_the_converter(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_run", lambda *args: pytest.fail("the converter ran"))
    with pytest.raises(sc.ConverterError, match="converter_unreadable"):
        sc.to_json(tmp_path / "tool", b"\x00\x01not a skeleton")


def test_drift_is_the_largest_move_of_the_mods_own_poses():
    before = {"00/face": [0, 0, 1, 1], "01/face": [2, 2]}
    assert sc.drift(before, {"00/face": [0, 0.01, 1, 1], "01/face": [2, 2]}) == pytest.approx(0.01)
    assert sc.drift(before, {"00/face": [0, 0, 1, 1]}) == float("inf")
    assert json.dumps(sc.DRIFT)
