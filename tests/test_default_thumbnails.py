"""The generated default thumbnails applied once after an upgrade: eligibility, the batch writes and the marker."""

import base64
import copy
import io
import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from bmpt.core.errors import ServiceError
from bmpt.engine import Bundle
from bmpt.gameassets import AssetCache, GameAssets
from bmpt.gameassets.unity import find_object, open_bundle
from bmpt.host.config import Settings
from bmpt.library import ModLibrary, portrait_edit, safety, storage
from bmpt.library.judge import core_of
from bmpt.webui import thumbnail_defaults
from bmpt.webui.jobs import MAINTENANCE, USER
from bmpt.webui.services import Services
from bmpt.webui.state import UiState
from ba_modding_toolkit.searching import get_search_dirs
from test_library import FakeAlt, FakeCatalog

GAME = Path(os.environ.get("BAMT_TEST_GAME_DIR", "/nonexistent/bamt-verified-game"))
# alts a, b, d, e, f share the first Collection bundle; c is in another one
SHARED = {
    "a": "Student_Portrait_Shiroko_Collection",
    "b": "Student_Portrait_Hoshino_Collection",
    "d": "Student_Portrait_Aru_Collection",
    "e": "Student_Portrait_Hina_Collection",
    "f": "Student_Portrait_Yuuka_Collection",
}
COLORS = {"a": (20, 130, 220, 255), "b": (210, 50, 140, 255), "c": (40, 200, 90, 255)}


def _png(color):
    stream = io.BytesIO()
    Image.new("RGBA", (202, 228), color).save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


@pytest.fixture(scope="module")
def collections(tmp_path_factory):
    """The two official Collection bundles and the texture names the alts use."""
    if not GAME.is_dir():
        pytest.skip("No verified game test assets")
    game = GameAssets(get_search_dirs(GAME), AssetCache(tmp_path_factory.mktemp("defaults-cache"), allowed=False))
    try:
        shared = {alt: game.collection_target(name) for alt, name in SHARED.items()}
    except ServiceError:
        pytest.skip("Collection fixture is unavailable")
    first = {record["bundle"] for record in shared.values()}
    if len(first) != 1:
        pytest.skip("No shared Collection fixture")
    first = first.pop()
    others = sorted(
        (
            path
            for folder in get_search_dirs(GAME)
            for path in Path(folder).glob("*charactercollect*.bundle")
            if path.name != first and "textures" not in path.name
        ),
        key=lambda path: path.stat().st_size,
    )
    for path in others:
        loaded = Bundle.load(path)
        names = sorted(
            obj.peek_name()
            for obj in loaded.env.objects
            if obj.type.name == "Texture2D" and obj.peek_name().endswith("_Collection")
        )
        for name in names:
            try:
                record = game.collection_target(name)
            except ServiceError:
                continue
            if record["bundle"] == path.name:
                return SimpleNamespace(
                    files=[game._bundle_path(first), path], names={**SHARED, "c": name}, bundles=(first, path.name)
                )
    pytest.skip("No second Collection bundle")


@pytest.fixture
def world(tmp_path, monkeypatch, collections):
    """A real library and services over copies of the two bundles; a, b and c qualify, d, e and f do not."""
    folder = tmp_path / "game"
    folder.mkdir()
    for source in collections.files:
        target = folder / source.name
        if not storage.atomic_clone(source, target):
            shutil.copy2(source, target)
        target.chmod(target.stat().st_mode | 0o200)
    alts = {}
    for key, name in collections.names.items():
        alts[key] = FakeAlt(key, f"{key}_spr")
        alts[key].official_thumbnail = name
    settings = Settings(tmp_path / "config.toml")
    settings.set("game_resource_dir", str(folder))
    settings.set("compression_method", "lz4")
    settings.set("enable_crc_correction", "true")
    game = GameAssets([folder], AssetCache(tmp_path / "cache", allowed=False))
    monkeypatch.setattr(safety, "check_game_not_running", lambda: None)
    catalog = FakeCatalog(*alts.values())
    library = ModLibrary(tmp_path / "library", catalog, game, settings)
    pin = library.current_pin()
    store = library.thumbnails
    descriptors = {key: game.collection_target(name) for key, name in collections.names.items()}
    # d: the official art chosen; e: a capture chosen; f: a capture applied to the game
    store.select("d", "official")
    capture = store.save("e", _png((1, 2, 3, 255)), "portrait")["saved"]
    store.select("e", capture)
    applied = store.save("f", _png((4, 5, 6, 255)), "portrait")["saved"]
    library.apply_thumbnail("f", applied, descriptors["f"], pin)
    state = UiState(tmp_path / "webui.json")
    services = Services(settings, tmp_path, state=state, cache=object(), game=game, catalog=catalog, library=library)
    services.set_spine_consent(True)
    yield SimpleNamespace(
        library=library,
        services=services,
        store=store,
        pin=pin,
        descriptors=descriptors,
        files={key: folder / descriptors[key]["bundle"] for key in descriptors},
        captures={"e": capture, "f": applied},
    )
    services.close()


def _pixel(path, descriptor):
    with open_bundle(path) as env:
        return find_object(env, descriptor).read().image.convert("RGBA").getpixel((0, 0))


def _store_defaults(w, alts=("a", "b", "c")):
    for alt in alts:
        w.services.thumbnail_defaults.store(alt, _png(COLORS[alt]))


def _upgrade(w, previous="0.7.0-rc.1", current="0.7.0-rc.2"):
    """A profile that ran ``previous`` before, started on ``current``."""
    w.services.state.update_tours(lambda history: history.update(notes={"highest": previous}))
    thumbnail_defaults.decide(w.services.state, current)


def _finish(services, job):
    deadline = time.monotonic() + 120
    while job is not None:
        while services.jobs.get(job).pending and time.monotonic() < deadline:
            time.sleep(0.02)
        result = services.jobs.get(job)
        assert result.state == "done", result.error
        job = next(
            (j.id for j in services.jobs._jobs.values() if j.kind == thumbnail_defaults.KIND and j.pending), None
        )
    return result.result


def test_eligibility_reads_the_app_choice_and_the_game_choice(world):
    w = world
    groups = w.library.default_thumbnail_groups(list(w.descriptors.items()), w.pin)
    qualified = {alt for items in groups.values() for alt, _descriptor in items}
    # d chose the official art, e a capture, f has a capture in the game
    assert qualified == {"a", "b", "c"}
    assert len(groups) == 2
    # a generated default already chosen still qualifies, until another thumbnail is chosen
    _store_defaults(w, ["a"])
    w.store.select("a", w.store.builtin("a"))
    assert w.library._default_thumbnail("a", w.descriptors["a"], w.pin) == (w.store.builtin("a"), "official")
    w.store.select("a", w.captures["e"])
    assert w.library._default_thumbnail("a", w.descriptors["a"], w.pin) is None


def test_no_collection_target_is_no_candidate(world):
    w = world
    _upgrade(w)
    w.library.catalog.alts["b"].official_thumbnail = "Student_Portrait_Nobody_Collection"
    listed = w.services.thumbnail_defaults.candidates()
    assert listed["due"]
    assert {item["alt"] for item in listed["candidates"]} == {"a", "c"}


def test_batch_applies_one_write_per_shared_bundle(world, monkeypatch):
    w = world
    _upgrade(w)
    listed = w.services.thumbnail_defaults.candidates()
    assert [item["builtin"] for item in listed["candidates"]] == [None, None, None]
    _store_defaults(w)
    # storing selects nothing and writes nothing
    assert w.store.selection("a") is None and w.library.thumbnail_choice(w.descriptors["a"], w.pin)["id"] is None
    writes = []
    compose = portrait_edit.patch_portraits
    monkeypatch.setattr(
        portrait_edit,
        "patch_portraits",
        lambda base, official, output, edits, **options: (
            writes.append(sorted(edits)),
            compose(base, official, output, edits, **options),
        ),
    )
    result = _finish(w.services, w.services.thumbnail_defaults.apply(["a", "b", "c"], True))
    assert sorted(result["alts"]) == ["a", "b", "c"] and not result["failed"]
    # f's capture is in the recipe the first bundle's write starts from
    assert sorted(writes) == sorted([sorted([SHARED["a"], SHARED["b"]]), [w.descriptors["c"]["name"]]])
    for alt in ("a", "b", "c"):
        builtin = w.store.builtin(alt)
        assert w.store.selection(alt) == builtin
        assert w.library.thumbnail_choice(w.descriptors[alt], w.pin)["id"] == builtin
        assert _pixel(w.files[alt], w.descriptors[alt]) == COLORS[alt]
    # the users' own choices stay as they were
    assert w.store.selection("d") == "official" and w.store.selection("e") == w.captures["e"]
    assert w.library.thumbnail_choice(w.descriptors["f"], w.pin)["id"] == w.captures["f"]
    assert w.library.thumbnail_choice(w.descriptors["d"], w.pin)["id"] is None
    assert not thumbnail_defaults.pending(w.services.state)
    # a second start does nothing
    assert w.services.thumbnail_defaults.candidates() == {"due": False, "candidates": []}
    assert w.services.thumbnail_defaults.apply(["a"], True) is None


def test_a_failed_write_leaves_its_bundle_and_the_manifest_and_keeps_the_marker(world, monkeypatch):
    w = world
    _upgrade(w)
    _store_defaults(w)
    second = w.files["c"]
    before, manifest = second.read_bytes(), w.store.index.read_bytes()
    configured = copy.deepcopy(w.library.profiles.read()["folders"][w.pin.key]["configured"])
    store = w.library._store

    def fail_second(files, *args, **kwargs):
        if second.name in files:
            raise OSError("Synthetic store failure")
        return store(files, *args, **kwargs)

    monkeypatch.setattr(w.library, "_store", fail_second)
    groups = list(w.library.default_thumbnail_groups(list(w.descriptors.items()), w.pin))
    assert groups[1][0] == core_of(second.name)  # the failing write comes second
    result = _finish(w.services, w.services.thumbnail_defaults.apply(["a", "b", "c"], True))
    assert sorted(result["alts"]) == ["a", "b"]
    assert [item["alts"] for item in result["failed"]] == [["c"]]
    assert second.read_bytes() == before
    assert w.store.selection("c") is None
    now = w.library.profiles.read()["folders"][w.pin.key]["configured"]
    assert now.get(core_of(second.name)) == configured.get(core_of(second.name))
    assert w.store.index.read_bytes() != manifest  # a and b are chosen
    assert w.store.selection("a") == w.store.builtin("a")
    assert thumbnail_defaults.pending(w.services.state)
    # the next start retries what is left: a and b are done already
    monkeypatch.setattr(w.library, "_store", store)
    listed = w.services.thumbnail_defaults.candidates()
    assert {item["alt"]: bool(item["builtin"]) for item in listed["candidates"]} == {"a": True, "b": True, "c": True}
    result = _finish(w.services, w.services.thumbnail_defaults.apply(["a", "b", "c"], True))
    assert not result["failed"] and _pixel(second, w.descriptors["c"]) == COLORS["c"]
    assert not thumbnail_defaults.pending(w.services.state)


def test_a_failed_write_inside_one_step_rolls_back_its_bundle(world, monkeypatch):
    w = world
    _store_defaults(w)
    target, before = w.files["a"], w.files["a"].read_bytes()
    manifest = w.store.index.read_bytes()
    configured = copy.deepcopy(w.library.profiles.read()["folders"][w.pin.key]["configured"])
    save = w.library._save

    def fail_after_game_write():
        if target.read_bytes() != before:
            raise OSError("Synthetic state-publication failure")
        return save()

    monkeypatch.setattr(w.library, "_save", fail_after_game_write)
    items = [(alt, w.descriptors[alt]) for alt in ("a", "b")]
    with pytest.raises(ServiceError):
        w.library.apply_default_thumbnails(items, w.pin)
    assert target.read_bytes() == before
    assert w.store.index.read_bytes() == manifest
    assert w.library.profiles.read()["folders"][w.pin.key]["configured"] == configured


def test_mods_off_configures_only(world):
    w = world
    w.library.toggle(False, True, [w.pin])
    _upgrade(w)
    _store_defaults(w)
    before = {alt: w.files[alt].read_bytes() for alt in ("a", "c")}
    result = _finish(w.services, w.services.thumbnail_defaults.apply(["a", "b", "c"], True))
    assert sorted(result["alts"]) == ["a", "b", "c"]
    assert all(w.files[alt].read_bytes() == before[alt] for alt in before)
    assert w.library.thumbnail_choice(w.descriptors["a"], w.pin)["id"] == w.store.builtin("a")
    assert w.store.selection("c") == w.store.builtin("c")
    w.library.toggle(True, False, [w.pin])
    assert _pixel(w.files["c"], w.descriptors["c"]) == COLORS["c"]


def test_fresh_data_records_the_marker_and_applies_nothing(world):
    w = world
    thumbnail_defaults.decide(w.services.state, "0.7.0-rc.2")
    assert not thumbnail_defaults.pending(w.services.state)
    assert w.services.thumbnail_defaults.candidates() == {"due": False, "candidates": []}
    # a later upgrade does not make it due again
    _upgrade(w, "0.7.0-rc.2", "0.7.0-rc.3")
    assert not thumbnail_defaults.pending(w.services.state)


def test_the_marker_waits_for_a_complete_run(world):
    w = world
    _upgrade(w)
    assert w.services.thumbnail_defaults.apply([], False) is None
    assert thumbnail_defaults.pending(w.services.state)
    _store_defaults(w, ["a"])
    _finish(w.services, w.services.thumbnail_defaults.apply(["a"], False))
    assert w.store.selection("a") == w.store.builtin("a")
    assert thumbnail_defaults.pending(w.services.state)
    assert w.services.thumbnail_defaults.apply([], True) is None
    assert not thumbnail_defaults.pending(w.services.state)


def test_skipped_quietly_without_spine_or_with_a_classic_window(world, monkeypatch):
    w = world
    _upgrade(w)
    w.services.set_spine_consent(False)
    assert w.services.thumbnail_defaults.candidates() == {"due": False, "candidates": []}
    assert w.services.thumbnail_defaults.apply([], True) is None
    w.services.set_spine_consent(True)
    monkeypatch.setattr(w.services, "classic_open", lambda: True)
    assert w.services.thumbnail_defaults.candidates()["due"] is False
    assert thumbnail_defaults.pending(w.services.state)


def test_a_waiting_user_job_goes_between_two_writes(world, monkeypatch):
    w = world
    _upgrade(w)
    _store_defaults(w)
    order = []
    apply = w.library.apply_default_thumbnails

    def step(items, *args, **kwargs):
        order.append(("step", [alt for alt, _descriptor in items]))
        if len(order) == 1:
            w.services.jobs.submit("user", lambda log, progress: order.append(("user",)), rank=USER)
        return apply(items, *args, **kwargs)

    monkeypatch.setattr(w.library, "apply_default_thumbnails", step)
    first = w.services.thumbnail_defaults.apply(["a", "b", "c"], True)
    deadline = time.monotonic() + 120
    while w.services.jobs.get(first).pending and time.monotonic() < deadline:
        time.sleep(0.02)
    assert w.services.jobs.get(first).result["left"] == ["c"]
    _finish(w.services, first)
    assert [entry[0] for entry in order] == ["step", "user", "step"]
    assert not thumbnail_defaults.pending(w.services.state)
    assert MAINTENANCE > USER
