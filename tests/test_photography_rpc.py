"""Thumbnails RPC integration, immutable assets and student-list App selection."""

import base64
import io
import json
import threading
import time
from pathlib import Path
from dataclasses import replace

import pytest
from PIL import Image

from bmpt.core.errors import ServiceError
from bmpt.library.records import Pin, ThumbnailApplyResult
from bmpt.webui.server import MAX_BODY
from test_webui import app, call, rpc, services


@pytest.fixture
def photography_env(services, tmp_path):
    library, game = services.library, services.game
    library.thumbnails = services.thumbnail_store
    pin = Pin("install", tmp_path / "game", (tmp_path / "game",), "global")
    library.pins = [pin]
    services.catalog.v.official_thumbnail = "Student_Portrait_Test_Collection"
    descriptor = {
        "name": services.catalog.v.official_thumbnail,
        "bundle": "uis-01_common-14_charactercollect-_mxload-2024-01-01_000_assets_all_1.bundle",
        "cab": "CAB-test",
        "pid": 1,
    }
    game.collection_target = lambda name: dict(descriptor)
    chosen = {"id": None, "known": True, "enabled": True}
    library.thumbnail_choice = lambda target, selected: dict(chosen)
    library.thumbnail_removal_info = lambda alt, key: {"official": key == "official", "used": chosen["id"] == key}

    def delete(alt, key, confirm=False, log=None):
        if key == chosen["id"]:
            if not confirm:
                raise ServiceError("asset_delete_confirm")
            chosen["id"] = None
        return services.thumbnail_store.delete(alt, key)

    library.delete_thumbnail = delete

    def apply(alt, key, target, pin=None, log=None, progress=None):
        assert pin.key == "install" and target["name"] == descriptor["name"]
        chosen["id"] = key
        return ThumbnailApplyResult(
            alt, "official" if key is None else "mod:photo", [], deferred=not library.enabled, cores=["other:test"]
        )

    library.apply_thumbnail = apply
    stream = io.BytesIO()
    Image.new("RGBA", (202, 228), (10, 130, 200, 240)).save(stream, format="PNG")
    return {
        "pin": pin,
        "descriptor": descriptor,
        "png": "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode(),
        "raw": stream.getvalue(),
        "chosen": chosen,
    }


def done(services, key):
    deadline = time.monotonic() + 3
    while services.jobs.get(key).pending and time.monotonic() < deadline:
        time.sleep(0.01)
    job = services.jobs.get(key)
    assert job.state == "done", job.error
    return job.result


def test_save_select_asset_and_student_list_thumbnail(app, services, photography_env):
    env = photography_env
    saved = rpc(app, "thumbnails.save", alt="hoshino", png=env["png"], mode="portrait")["result"]
    key = saved["saved"]
    selected = rpc(app, "thumbnails.select", alt="hoshino", id=key)["result"]
    url = selected["thumbnails"][0]["url"]
    assert call(app, url)[2] == env["raw"]
    student_list = rpc(app, "students.list")["result"]
    assert student_list["students"][0]["alts"][0]["thumbnail"] == url
    assert rpc(app, "student.get", key="hoshino")["result"]["alts"][0]["thumbnail"] == url
    current = rpc(app, "thumbnails.list", alt="hoshino")["result"]
    assert current["default"].startswith("/asset/thumbnails/default/")
    assert current["game"]["id"] == "official"


@pytest.mark.parametrize("enabled", [True, False])
def test_applied_is_scoped_to_the_shown_skin_but_deletion_checks_every_skin(app, services, photography_env, enabled):
    original = services.catalog.v
    alternative = replace(
        original, key="hoshino_swimsuit", official_thumbnail="Student_Portrait_Test_swimsuit_Collection"
    )
    services.catalog.f.alts.append(alternative)
    keys = [
        rpc(app, "thumbnails.save", alt=v.key, png=photography_env["png"], mode="portrait")["result"]["saved"]
        for v in (original, alternative)
    ]
    choices = dict(zip([v.official_thumbnail for v in (original, alternative)], keys))
    services.game.collection_target = lambda name: {**photography_env["descriptor"], "name": name}
    services.library.thumbnail_choice = lambda descriptor, pin: {
        "id": choices[descriptor["name"]],
        "known": True,
        "enabled": enabled,
        "pending": not enabled,
    }

    def removal_info(alt, key):
        targets = sorted(v.key for v in (original, alternative) if choices[v.official_thumbnail] == key)
        return {
            "official": False,
            "used": bool(targets),
            "used_here": alt in targets,
            "used_elsewhere": any(target != alt for target in targets),
            "targets": targets,
        }

    services.library.thumbnail_removal_info = removal_info
    for alt, own, other in [(original, keys[0], keys[1]), (alternative, keys[1], keys[0])]:
        result = rpc(app, "thumbnails.list", alt=alt.key)["result"]
        assert {entry["alt"] for entry in result["thumbnails"]} == {original.key, alternative.key}
        assert {entry["id"] for entry in result["thumbnails"] if entry["applied"]} == {own}
        assert result["game"]["id"] == own and result["game"]["enabled"] == enabled
        for key in (own, other):
            assert rpc(app, "thumbnails.removalInfo", alt=alt.key, id=key)["result"] == removal_info(alt.key, key)
    # Returning to official must not mark the other skin's persisted photo Applied.
    choices[original.official_thumbnail] = None
    result = rpc(app, "thumbnails.list", alt=original.key)["result"]
    assert result["game"]["id"] == "official" and not any(entry["applied"] for entry in result["thumbnails"])
    assert rpc(app, "thumbnails.removalInfo", alt=original.key, id=keys[1])["result"]["used"]


@pytest.mark.parametrize("unavailable", ["game", "target", "unknown"])
def test_unavailable_or_unknown_target_has_no_applied_photo(app, services, photography_env, unavailable):
    key = rpc(app, "thumbnails.save", alt="hoshino", png=photography_env["png"], mode="portrait")["result"]["saved"]
    photography_env["chosen"].update(id=key, known=unavailable != "unknown")
    if unavailable == "game":
        services.library.pins = []
    elif unavailable == "target":
        services.catalog.v.official_thumbnail = None
    result = rpc(app, "thumbnails.list", alt="hoshino")["result"]
    assert not any(entry["applied"] for entry in result["thumbnails"])


def test_apply_waits_for_real_job_and_restore_resets_app_choice(app, services, photography_env):
    key = rpc(app, "thumbnails.save", alt="hoshino", png=photography_env["png"], mode="memorialLobby")["result"][
        "saved"
    ]
    rpc(app, "thumbnails.select", alt="hoshino", id=key)
    done(services, rpc(app, "thumbnails.apply", alt="hoshino", id=key)["result"])
    assert rpc(app, "thumbnails.list", alt="hoshino")["result"]["game"]["id"] == key
    failed = rpc(app, "thumbnails.delete", alt="hoshino", id=key)["result"]
    deadline = time.monotonic() + 3
    while services.jobs.get(failed).pending and time.monotonic() < deadline:
        time.sleep(0.01)
    assert services.jobs.get(failed).error["code"] == "asset_delete_confirm"
    done(services, rpc(app, "thumbnails.restore", alt="hoshino")["result"])
    result = rpc(app, "thumbnails.list", alt="hoshino")["result"]
    assert result["app"] is None and result["game"]["id"] == "official"
    assert len(result["thumbnails"]) == 1
    assert "thumbnail" not in rpc(app, "students.list")["result"]["students"][0]["alts"][0]


def test_capture_upload_has_a_bounded_larger_limit_without_widening_other_rpcs(app, services, photography_env):
    large = "x" * (MAX_BODY + 100)
    status, _, data = call(
        app,
        "/api/rpc",
        "POST",
        {"method": "thumbnails.save", "params": {"alt": "hoshino", "png": large, "mode": "portrait"}},
    )
    assert status == "200 OK" and json.loads(data)["error"]["code"] == "thumbnail_invalid"
    assert call(app, "/api/rpc", "POST", {"method": "app.info", "params": {"value": large}})[0] == "400 Bad Request"


def test_missing_original_keeps_app_photography_and_default_restore_available(app, services, photography_env):
    services.catalog.v.official_thumbnail = None
    saved = rpc(app, "thumbnails.save", alt="hoshino", png=photography_env["png"], mode="portrait")["result"]
    current = rpc(app, "thumbnails.list", alt="hoshino")["result"]
    assert not current["applicable"] and current["default"] is None
    rpc(app, "thumbnails.select", alt="hoshino", id=saved["saved"])
    done(services, rpc(app, "thumbnails.restore", alt="hoshino")["result"])
    assert services.thumbnail_store.list("hoshino")["app"] is None
    assert (
        rpc(app, "thumbnails.apply", alt="hoshino", id=saved["saved"])["error"]["code"]
        == "thumbnail_target_unavailable"
    )


def test_default_asset_is_pinned_and_reads_only_official_bundles(app, services, photography_env):
    env = photography_env
    official = Path("/verified/" + env["descriptor"]["bundle"])
    services.library.official_bundles = lambda core, pin: [official]

    def original(name, paths):
        assert name == env["descriptor"]["name"] and paths == [official]
        return env["raw"]

    services.game.collection_portrait_from = original
    default = rpc(app, "thumbnails.list", alt="hoshino")["result"]["default"]
    assert call(app, default)[2] == env["raw"]
    assert call(app, default.replace(env["pin"].id, "another-install"))[0] == "404 Not Found"


def test_saving_does_not_wait_for_an_unrelated_game_status_read(app, services, photography_env):
    def blocked(*args):
        raise AssertionError("A capture must not request game status")

    services.library.thumbnail_choice = blocked
    saved = rpc(app, "thumbnails.save", alt="hoshino", png=photography_env["png"], mode="portrait")["result"]
    assert services.thumbnail_store.image("hoshino", saved["saved"]) == photography_env["raw"]


def test_photo_only_library_moves_with_images_and_app_choice(app, services, photography_env, tmp_path):
    saved = rpc(app, "thumbnails.save", alt="hoshino", png=photography_env["png"], mode="portrait")["result"]
    key = saved["saved"]
    rpc(app, "thumbnails.select", alt="hoshino", id=key)
    old = services.library_dir()
    assert not (old / "state.json").exists() and not (old / "profiles.json").exists()
    target = services.move_library(tmp_path / "moved-library")
    assert target != old and (target / "photographs" / "index.json").is_file()
    assert services.thumbnail_store.list("hoshino")["app"] == key
    assert services.thumbnail_store.image("hoshino", key) == photography_env["raw"]
    assert not (old / "photographs").exists()


def test_preview_cache_clear_does_not_remove_thumbnails(app, services, photography_env):
    from types import SimpleNamespace

    saved = rpc(app, "thumbnails.save", alt="hoshino", png=photography_env["png"], mode="portrait")["result"]
    services._cache = SimpleNamespace(clear=lambda: None)
    services.clear_cache()
    assert services.thumbnail_store.image("hoshino", saved["saved"]) == photography_env["raw"]


def test_export_preserves_pixels_and_avoids_collisions(services, photography_env, tmp_path, monkeypatch):
    from bmpt.webui import thumbnails

    destination = tmp_path / "exports"
    services.settings.set("output_dir", str(destination))
    opened = []
    monkeypatch.setattr(thumbnails, "reveal", opened.append)
    key = services.thumbnails.save("hoshino", photography_env["png"], "portrait", "", "", "")["saved"]
    first = services.thumbnails.export("hoshino", key)
    second = services.thumbnails.export("hoshino", key)
    assert first["path"] != second["path"]
    assert Path(first["path"]).read_bytes() == Path(second["path"]).read_bytes() == photography_env["raw"]
    assert opened == [destination, destination]


def test_export_copies_exclusively_where_hard_links_fail(services, photography_env, tmp_path, monkeypatch):
    import errno
    from bmpt.webui import thumbnails

    destination = tmp_path / "exports"
    services.settings.set("output_dir", str(destination))
    monkeypatch.setattr(thumbnails, "reveal", lambda folder: None)

    def unsupported(source, target):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(thumbnails.os, "link", unsupported)
    key = services.thumbnails.save("hoshino", photography_env["png"], "portrait", "", "", "")["saved"]
    first = services.thumbnails.export("hoshino", key)
    second = services.thumbnails.export("hoshino", key)
    assert first["path"] != second["path"]
    assert Path(first["path"]).read_bytes() == Path(second["path"]).read_bytes() == photography_env["raw"]
    assert sorted(path.name for path in destination.iterdir()) == sorted(
        Path(result["path"]).name for result in (first, second)
    )


def test_export_removes_a_partial_copy(services, photography_env, tmp_path, monkeypatch):
    from bmpt.webui import thumbnails

    destination = tmp_path / "exports"
    services.settings.set("output_dir", str(destination))
    monkeypatch.setattr(thumbnails, "reveal", lambda folder: None)
    monkeypatch.setattr(thumbnails.os, "link", lambda source, target: (_ for _ in ()).throw(OSError("no links")))
    key = services.thumbnails.save("hoshino", photography_env["png"], "portrait", "", "", "")["saved"]

    def partial(source, out, *args):
        out.write(source.read(8))
        raise OSError("device full")

    monkeypatch.setattr(thumbnails.shutil, "copyfileobj", partial)
    with pytest.raises(OSError, match="device full"):
        services.thumbnails.export("hoshino", key)
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("deferred", [False, True])
def test_restore_reuses_or_creates_one_default_and_reports_the_deletion_identity(
    app, services, photography_env, existing, deferred
):
    env = photography_env
    saved = services.thumbnail_store.save("hoshino", env["png"], "portrait")["saved"]
    services.thumbnail_store.select("hoshino", saved)
    generated = (
        services.thumbnail_store.save("hoshino", env["png"], "portrait", builtin=True)["saved"] if existing else None
    )
    services.library.enabled = not deferred
    env["chosen"].update(
        id=saved, enabled=not deferred, pending=deferred, error={"code": "thumbnail_apply_failed"} if deferred else None
    )
    stream = io.BytesIO()
    Image.new("RGBA", (202, 228), (200, 30, 10, 255)).save(stream, format="PNG")
    changed_png = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
    done(services, rpc(app, "thumbnails.restore", alt="hoshino", png=changed_png)["result"])
    result = rpc(app, "thumbnails.list", alt="hoshino")["result"]
    defaults = [entry for entry in result["thumbnails"] if entry.get("builtin")]
    assert len(defaults) == 1
    key = defaults[0]["id"]
    assert not existing or key == generated
    assert result["app"] == result["game"]["id"] == key
    for entry in result["thumbnails"]:
        info = rpc(app, "thumbnails.removalInfo", alt="hoshino", id=entry["id"])["result"]
        assert entry["applied"] == info["used"] == (entry["id"] == key)
    assert services.thumbnail_store.image("hoshino", key) == (env["raw"] if existing else stream.getvalue())
    done(services, rpc(app, "thumbnails.restore", alt="hoshino", png=env["png"])["result"])
    assert len(services.thumbnail_store.list("hoshino")["thumbnails"]) == 2
    done(services, rpc(app, "thumbnails.delete", alt="hoshino", id=saved)["result"])
    failed = rpc(app, "thumbnails.delete", alt="hoshino", id=key)["result"]
    deadline = time.monotonic() + 3
    while services.jobs.get(failed).pending and time.monotonic() < deadline:
        time.sleep(0.01)
    assert services.jobs.get(failed).error["code"] == "asset_delete_confirm"


# --- lock order: the photograph lock never waits for the services lock ---


def _run(target, name, results):
    def work():
        try:
            results[name] = target()
        except BaseException as error:  # reported by the test
            results[name] = error

    thread = threading.Thread(target=work, name=name, daemon=True)
    thread.start()
    return thread


def _finished(*threads, timeout=10):
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0, deadline - time.monotonic()))
    return [thread.name for thread in threads if thread.is_alive()]


@pytest.mark.parametrize("writer", ["save", "select"])
def test_a_save_or_selection_arriving_while_the_manifest_is_parsed_does_not_deadlock(
    app, services, photography_env, monkeypatch, writer
):
    """The reader holds the photograph lock and looks families up; the writer arrives meanwhile. The writer must not
    hold the services lock while it waits for the photograph lock: the reader would then wait for the services lock."""
    from bmpt.library.thumbnails import Thumbnails

    env = photography_env
    key = rpc(app, "thumbnails.save", alt="hoshino", png=env["png"], mode="portrait")["result"]["saved"]
    rpc(app, "thumbnails.select", alt="hoshino", id=key)
    reading, writing, release = threading.Event(), threading.Event(), threading.Event()
    real_chosen, real_save, real_select = Thumbnails._chosen, Thumbnails.save, Thumbnails.select

    def chosen(self, data, alt):
        if threading.current_thread().name == "reader" and not reading.is_set():
            reading.set()  # inside the photograph lock, before the family lookup
            assert writing.wait(10) and release.wait(10)
        return real_chosen(self, data, alt)

    def entering(real):
        def wrapper(self, *args, **kwargs):
            if threading.current_thread().name == "writer":
                writing.set()
            return real(self, *args, **kwargs)

        return wrapper

    monkeypatch.setattr(Thumbnails, "_chosen", chosen)
    monkeypatch.setattr(Thumbnails, "save", entering(real_save))
    monkeypatch.setattr(Thumbnails, "select", entering(real_select))
    results = {}
    reader = _run(lambda: services.thumbnail_store.list("hoshino"), "reader", results)
    assert reading.wait(10)
    call_writer = (
        (lambda: services.thumbnails.save("hoshino", env["png"], "portrait", "", "", ""))
        if writer == "save"
        else (lambda: services.thumbnails.select("hoshino", None))
    )
    thread = _run(call_writer, "writer", results)
    assert writing.wait(10)
    release.set()
    stuck = _finished(reader, thread)
    assert stuck == [], f"deadlock: {stuck} still waiting"
    assert not isinstance(results["reader"], BaseException) and not isinstance(results["writer"], BaseException), (
        results
    )
    assert results["reader"]["app"] == key


def test_no_manifest_call_waits_for_the_services_lock(services, photography_env):
    """Whatever the store does under its own lock, it does with the services lock held by someone else."""
    env = photography_env
    store = services.thumbnail_store
    holding, release = threading.Event(), threading.Event()

    def hold():
        with services._lock:
            holding.set()
            release.wait(30)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert holding.wait(10)
    results = {}

    def calls():
        key = store.save("hoshino", env["png"], "portrait")["saved"]
        store.select("hoshino", key)
        assert store.list("hoshino")["app"] == key
        assert store.app_url("hoshino").endswith(f"/{key}.png") and store.selection("hoshino") == key
        assert store.has_selection("hoshino") and store.image("hoshino", key) == env["raw"]
        store.select("hoshino", "official")
        store.delete("hoshino", key)
        return "done"

    worker = _run(calls, "worker", results)
    try:
        stuck = _finished(worker)
    finally:
        release.set()
        holder.join(10)
    assert stuck == [] and results["worker"] == "done", results


def test_a_library_move_waits_for_a_thumbnail_save_and_the_photo_lands_in_the_new_folder(
    app, services, photography_env, monkeypatch, tmp_path
):
    """The one thing the services lock around a save kept out: the move that leaves the folder the save writes to."""
    from bmpt.library.thumbnails import Thumbnails

    env = photography_env
    rpc(app, "thumbnails.save", alt="hoshino", png=env["png"], mode="portrait")  # a library worth moving
    old = services.library_dir()
    saving, release = threading.Event(), threading.Event()
    real_save = Thumbnails.save

    def save(self, *args, **kwargs):
        if threading.current_thread().name == "saver":
            saving.set()  # the store (of the old folder) is resolved, the photograph lock is about to be taken
            assert release.wait(10)
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(Thumbnails, "save", save)
    results = {}
    saver = _run(lambda: services.thumbnails.save("hoshino", env["png"], "portrait", "", "", ""), "saver", results)
    assert saving.wait(10)
    target = tmp_path / "moved-library"
    mover = _run(lambda: services.move_library(target), "mover", results)
    mover.join(0.5)
    assert mover.is_alive(), "the move went ahead under a photograph save"
    release.set()
    assert _finished(saver, mover) == []
    assert not isinstance(results["saver"], BaseException) and not isinstance(results["mover"], BaseException), results
    key = results["saver"]["saved"]
    assert (target / "photographs" / "images" / f"{key}.png").read_bytes() == env["raw"]
    assert not (old / "photographs").exists(), "the save landed in the folder the move left"
    assert len(services.thumbnail_store.list("hoshino")["thumbnails"]) == 2
