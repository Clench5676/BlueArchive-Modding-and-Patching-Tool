"""Saved character ownership reaches API views without creating local game targets."""

from types import SimpleNamespace

import pytest

from bmpt.core.errors import ServiceError

from test_webui import app, services, rpc, FakeStudent, FakeMod, FakeAlt


@pytest.fixture
def assigned(services, monkeypatch):
    future = FakeAlt(
        "hoshino_future",
        "hoshino",
        "hoshino_future",
        names={"en": {"full": "Hoshino (Future)", "short": "Future"}},
        released_on_server=False,
        background=None,
    )
    future.page_id, future.schale_id = "12345", None
    unused = FakeAlt("unassigned", "unassigned", "unassigned", released_on_server=False, background=None)
    local = services.catalog.v
    students = [
        FakeStudent("hoshino", services.catalog.f.names, "Abydos", [local, future]),
        FakeStudent("unassigned", {"en": "Unassigned"}, None, [unused]),
    ]
    alts = {alt.key: alt for student in students for alt in student.alts}

    def assignment_alt(key):
        if key not in alts:
            raise ServiceError("catalog_alt_not_found", key=key)
        return alts[key]

    monkeypatch.setattr(services.catalog, "assignment_students", lambda: students, raising=False)
    monkeypatch.setattr(services.catalog, "assignment_alt", assignment_alt, raising=False)
    targets = {future.key}
    mods = [
        FakeMod("own-spr", future.key),
        FakeMod("own-lobby", future.key, resource_type="memorialLobby"),
        FakeMod("own-model", future.key, resource_type="chibi"),
        FakeMod("sibling", local.key),
    ]
    monkeypatch.setattr(services.library, "assigned_targets", lambda: set(targets))
    monkeypatch.setattr(services.library, "mods", lambda: list(mods))
    services.library.pins = [SimpleNamespace(key="current")]
    calls = []

    def snapshot(keys):
        calls.append(list(keys))
        return {
            "install": "current",
            "mods": list(mods),
            "favorites": set(),
            "modified": {},
            "assigned": sorted(targets),
            "configured": {key: {"portrait": None, "memorialLobby": None, "chibi": None} for key in [*keys, *targets]},
        }

    monkeypatch.setattr(services.library, "student_list_snapshot", snapshot)
    return SimpleNamespace(future=future, unused=unused, local=local, targets=targets, mods=mods, calls=calls)


def test_student_list_and_student_include_saved_assignment_with_no_local_handles(app, services, assigned):
    student_list = rpc(app, "students.list")
    assert "error" not in student_list, student_list
    alts = [alt for student in student_list["result"]["students"] for alt in student["alts"]]
    assert {alt["key"] for alt in alts} == {assigned.local.key, assigned.future.key}
    student = rpc(app, "student.get", key=assigned.future.student)
    assert "error" not in student, student
    for source in (alts, student["result"]["alts"]):
        future = next(alt for alt in source if alt["key"] == assigned.future.key)
        assert all(future[resource_type] is None for resource_type in ("portrait", "memorialLobby", "chibi"))
        assert future["localResourceTypes"] == []
        assert future["storedResourceTypes"] == ["memorialLobby", "chibi", "portrait"]
        assert future["mods"] == 3 and not future["releasedOnServer"]
        assert not any(future["configured"].values())
    assigned.targets.clear()
    assigned.mods[:] = [mod for mod in assigned.mods if mod.target != assigned.future.key]
    remaining = rpc(app, "students.list")["result"]
    assert [alt["key"] for student in remaining["students"] for alt in student["alts"]] == [assigned.local.key]
    assert rpc(app, "student.get", key=assigned.unused.student)["error"]["code"] == "student_not_found"


def test_assigned_student_list_keeps_one_snapshot_and_filters_saved_preview_choices(
    app, services, assigned, monkeypatch
):
    services.state.set_preview_source("current", assigned.future.key, "portrait", "mod:own-spr")
    services.state.set_preview_source("current", assigned.future.key, "memorialLobby", "official")
    services.state.set_preview_source("current", assigned.future.key, "chibi", "mod:own-spr")

    def forbidden(*args, **kwargs):
        pytest.fail("Assigned roster must reuse its single library snapshot")

    for name in ("mods", "assigned_targets", "current_pin", "configured", "selection", "modified", "favorites"):
        monkeypatch.setattr(services.library, name, forbidden)
    monkeypatch.setattr(services.state, "preview_sources", forbidden)
    result = rpc(app, "students.list")
    assert "error" not in result, result
    future = next(
        alt for student in result["result"]["students"] for alt in student["alts"] if alt["key"] == assigned.future.key
    )
    assert assigned.calls == [[assigned.local.key]]
    assert future["previewSources"] == {"portrait": "mod:own-spr"}


@pytest.mark.parametrize(
    ("mod_suffix", "resource_type"), [("spr", "portrait"), ("lobby", "memorialLobby"), ("model", "chibi")]
)
def test_own_mod_preview_is_saved_without_an_install_target(app, services, assigned, mod_suffix, resource_type):
    result = rpc(app, "ui.preview", alt=assigned.future.key, resourceType=resource_type, source=f"mod:own-{mod_suffix}")
    assert result == {"result": {resource_type: f"mod:own-{mod_suffix}"}}
    assert services.state.preview_sources("current", assigned.future.key) == {resource_type: f"mod:own-{mod_suffix}"}
    assert services.library.applied == []


@pytest.mark.parametrize(
    "resource_type,source",
    [
        ("portrait", "official"),
        ("memorialLobby", "official"),
        ("chibi", "official"),
        ("portrait", "alt:hoshino"),
        ("portrait", "mod:sibling"),
        ("portrait", "mod:own-lobby"),
        ("portrait", "mod:missing"),
    ],
)
def test_absent_target_rejects_official_cross_alt_and_wrong_kind_previews(
    app, services, assigned, resource_type, source
):
    result = rpc(app, "ui.preview", alt=assigned.future.key, resourceType=resource_type, source=source)
    assert result["error"]["code"] == "bad_params"
    assert services.state.preview_sources("current", assigned.future.key) == {}
    assert services.library.applied == []


def test_assignment_metadata_supports_aliases_and_memorial_dialogue(app, services, assigned, monkeypatch):
    assert rpc(app, "aliases.set", alt=assigned.future.key, aliases=["Future nickname"])["result"] == [
        "Future nickname"
    ]
    future = next(
        alt for alt in rpc(app, "student.get", key="hoshino")["result"]["alts"] if alt["key"] == assigned.future.key
    )
    assert future["customAliases"] == ["Future nickname"]
    pages = []

    def lines(page):
        pages.append(page)
        return {"future_memoriallobby_1": {"ja": "Future dialogue", "zh-CN": "Dialogue", "audio": ""}}

    monkeypatch.setattr(services.kivo, "lines", lines)
    result = rpc(app, "memorialLobby.lines", alt=assigned.future.key, lang="zh-CN")
    assert "error" not in result, result
    assert result["result"]["future_memoriallobby_1"]["ja"] == "Future dialogue"
    assert pages == [assigned.future.page_id]
    assert (
        rpc(app, "aliases.set", alt=assigned.unused.key, aliases=["Unowned"])["error"]["code"]
        == "catalog_alt_not_found"
    )
    assert (
        rpc(app, "memorialLobby.lines", alt=assigned.unused.key, lang="zh-CN")["error"]["code"]
        == "catalog_alt_not_found"
    )
