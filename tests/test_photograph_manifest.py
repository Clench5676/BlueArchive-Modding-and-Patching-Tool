"""One unusable photograph row or selection costs that entry only; a manifest that cannot be read as a whole is
left byte for byte as it is while the photograph features refuse."""

import copy
import json
import logging
from types import SimpleNamespace

import pytest

from bmpt.errors import ServiceError
from bmpt.library.photographs import Photographs
from test_photographs import capture
from test_webui import app, rpc, services  # noqa: F401  (fixtures)

BAD = "b" * 32
DIGEST = "d" * 64
LOG = "bmpt.library.photographs"


def family_of(key):
    """Variants are `<family>:<alt>`; any other key is a variant the catalog does not list, its own family."""
    return key.split(":")[0] if key.startswith(("student", "other")) else key


def store(root, family=family_of):
    return Photographs(root, family)


def manifest(gallery):
    return json.loads(gallery.index.read_text(encoding="utf-8"))


def put(gallery, data):
    gallery.root.mkdir(parents=True, exist_ok=True)
    gallery.index.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return gallery.index.read_bytes()


def row(key, variant="student:a", **extra):
    return {"id": key, "variant": variant, "sha256": DIGEST, "number": 1, **extra}


def base(**changes):
    return {"version": 1, "photos": {}, "selected": {}, "next": 1, **changes}


@pytest.fixture
def gallery(tmp_path):
    return store(tmp_path)


def test_a_bad_photo_record_costs_that_record_only_and_is_written_back_unchanged(gallery, caplog):
    good = gallery.save("student:a", capture()[0], "portrait")["saved"]
    data = manifest(gallery)
    stale = {"id": BAD, "variant": "student:a", "sha256": "short"}
    data["photos"].update({"not-an-id": row("x" * 32), BAD: stale, "c" * 32: "not an object",
                           "e" * 32: row("f" * 32), "a" * 32: {"id": "a" * 32, "sha256": DIGEST}})
    put(gallery, data)
    fresh = store(gallery.root.parent)
    with caplog.at_level(logging.WARNING, logger=LOG):
        for _ in range(3):
            assert [entry["id"] for entry in fresh.list("student:a")["photos"]] == [good]
    held = [record for record in caplog.records if "cannot be used" in record.getMessage()]
    assert len(held) == 5  # once per record, not at every read
    # every write keeps the held rows as they are, in their place
    second = fresh.save("student:a", capture(color=(1, 2, 3, 255))[0], "portrait")["saved"]
    fresh.select("student:a", second)
    fresh.delete("student:a", good)
    after = manifest(fresh)
    for key in data["photos"]:
        if key != good:
            assert after["photos"][key] == data["photos"][key]
    assert list(after["photos"])[:-1] == [key for key in data["photos"] if key != good]
    assert good not in after["photos"] and second in after["photos"]
    assert fresh.image("student:a", second)


def test_a_held_record_cannot_be_used(gallery):
    data = base(photos={BAD: {"id": BAD, "variant": "student:a", "sha256": "bad"}})
    put(gallery, data)
    with pytest.raises(ServiceError, match="photo_missing"):
        gallery.image("student:a", BAD)
    with pytest.raises(ServiceError, match="photo_missing"):
        gallery.select("student:a", BAD)
    with pytest.raises(ServiceError, match="photo_missing"):
        gallery.delete("student:a", BAD)
    assert gallery.list("student:a")["photos"] == []


def test_a_stale_selection_reads_as_the_default_and_stays_in_the_file(tmp_path, caplog):
    families = {"student:a": "student", "gone-alt": "student"}  # the alt shares the family while the catalog lists it
    gallery = store(tmp_path, lambda variant: families.get(variant, variant))
    key = gallery.save("gone-alt", capture()[0], "portrait")["saved"]
    gallery.select("student:a", key)  # the photo of the alt, chosen for student A through the shared gallery
    assert gallery.selection("student:a") == key and gallery.app_url("student:a").endswith(key + ".png")
    del families["gone-alt"]  # the alt leaves the catalog: its family is now itself
    with caplog.at_level(logging.WARNING, logger=LOG):
        for _ in range(3):
            assert gallery.selection("student:a") is None
            assert gallery.app_url("student:a") is None
            assert gallery.list("student:a")["app"] is None and gallery.list("student:a")["photos"] == []
            assert gallery.has_selection("student:a") is False
    assert len([r for r in caplog.records if "cannot be used" in r.getMessage()]) == 1
    # an unrelated write leaves the row as it is
    other = gallery.save("student:b", capture(color=(5, 6, 7, 255))[0], "portrait")["saved"]
    gallery.select("student:b", other)
    assert manifest(gallery)["selected"]["student:a"] == key
    # a new choice for that student replaces it
    mine = gallery.save("student:a", capture(color=(9, 9, 9, 255))[0], "portrait")["saved"]
    assert manifest(gallery)["selected"]["student:a"] == key
    gallery.select("student:a", mine)
    assert gallery.selection("student:a") == mine and manifest(gallery)["selected"]["student:a"] == mine
    # the family returns: the row works again
    families["gone-alt"] = "student"
    gallery.select("student:a", key)
    assert gallery.selection("student:a") == key


def test_a_selection_that_names_no_photo_is_held_and_kept(gallery):
    data = base(selected={"student:a": "e" * 32, "student:b": 7, "student:c": ["x"], "student:d": "official"})
    raw = put(gallery, data)
    assert gallery.selection("student:a") is None and gallery.selection("student:b") is None
    assert gallery.selection("student:c") is None and gallery.selection("student:d") == "official"
    assert gallery.list("student:a")["app"] is None
    key = gallery.save("student:a", capture()[0], "portrait")["saved"]  # the saving does not take the slot
    after = manifest(gallery)
    assert after["selected"] == {**data["selected"]} and key in after["photos"] and raw != gallery.index.read_bytes()
    gallery.select("student:a", None)  # an explicit choice does
    assert manifest(gallery)["selected"]["student:a"] is None
    assert manifest(gallery)["selected"]["student:b"] == 7


def test_deleting_a_photo_a_kept_row_names_is_refused(tmp_path):
    families = {"student:a": "student", "student:b": "student", "student:z": "student"}
    gallery = store(tmp_path, lambda variant: families.get(variant, variant))
    key = gallery.save("student:a", capture()[0], "portrait")["saved"]
    file = gallery.root / "images" / f"{key}.png"
    data = manifest(gallery)
    data["photos"][BAD] = {"id": key, "variant": "student:a"}  # a held record that names this photo's file
    put(gallery, data)
    with pytest.raises(ServiceError, match="photo_in_use"):
        gallery.delete("student:a", key)
    assert file.is_file() and manifest(gallery)["photos"][BAD] == data["photos"][BAD]
    del data["photos"][BAD]
    data["selected"]["gone-student"] = key  # a selection that names no usable photo's family but this file
    put(gallery, data)
    del families["student:z"]
    data["selected"]["student:z"] = key  # a selection that cannot be used now but names it
    put(gallery, data)
    with pytest.raises(ServiceError, match="photo_in_use"):
        gallery.delete("student:a", key)
    assert file.is_file() and manifest(gallery)["selected"]["student:z"] == key
    # protected files stay protected, and ordinary selections are still reset
    del data["selected"]["student:z"], data["selected"]["gone-student"]
    data["selected"]["student:b"] = key
    put(gallery, data)
    with pytest.raises(ServiceError, match="photo_in_use"):
        gallery.delete("student:a", key, {key})
    gallery.delete("student:a", key)
    assert manifest(gallery)["selected"]["student:b"] is None and not file.exists()


@pytest.mark.parametrize("content", [
    b"bad JSON", b"", b"[]", b'{"version": 2, "photos": {}, "selected": {}, "next": 1}',
    b'{"version": 1, "photos": [], "selected": {}, "next": 1}',
    b'{"version": 1, "photos": {}, "selected": [], "next": 1}',
    b'{"version": 1, "photos": {}, "selected": {}, "next": 0}',
    b'{"version": 1, "photos": {}, "selected": {}, "next": 1, "next_by_variant": []}',
    b'{"version": 1, "photos": {}, "selected": {}, "next": 1, "next_by_variant": {"a": 0}}',
    b"\xff\xfe"])
def test_an_unreadable_manifest_is_left_alone_and_every_write_refuses(gallery, content):
    gallery.root.mkdir()
    gallery.index.write_bytes(content)
    with pytest.raises(ServiceError, match="photo_library_invalid"):
        gallery.selection("student:a")
    for call in (lambda: gallery.list("student:a"), lambda: gallery.app_url("student:a"),
                 lambda: gallery.save("student:a", capture()[0], "portrait"),
                 lambda: gallery.save("student:a", capture()[0], "portrait", builtin=True),
                 lambda: gallery.select("student:a", None), lambda: gallery.delete("student:a", "a" * 32)):
        with pytest.raises(ServiceError, match="photo_library_invalid"):
            call()
    assert gallery.index.read_bytes() == content
    assert sorted(path.name for path in gallery.root.iterdir() if path.name != "index.lock") == ["index.json"]
    # a second instance (the next launch) refuses the same way
    with pytest.raises(ServiceError, match="photo_library_invalid"):
        store(gallery.root.parent).save("student:a", capture()[0], "portrait")
    assert gallery.index.read_bytes() == content


def test_a_symlinked_manifest_is_refused_and_not_followed(gallery, tmp_path):
    gallery.root.mkdir()
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(base()))
    gallery.index.symlink_to(target)
    with pytest.raises(ServiceError, match="photo_library_invalid"):
        gallery.save("student:a", capture()[0], "portrait")
    assert gallery.index.is_symlink() and json.loads(target.read_text()) == base()


def test_the_manifest_becomes_usable_again_when_it_is_fixed(gallery):
    gallery.root.mkdir()
    gallery.index.write_text("bad JSON")
    with pytest.raises(ServiceError):
        gallery.list("student:a")
    gallery.index.write_text(json.dumps(base()))
    assert gallery.list("student:a")["photos"] == []
    assert gallery.save("student:a", capture()[0], "portrait")["saved"]


# --- through the real RPC registry ---


def catalog_with_dropped_alt(services):
    # an alt the catalog no longer lists resolves to itself (Catalog.assignment_variant on an unknown key)
    services.catalog.assignment_variant = lambda key: SimpleNamespace(family=key)


def test_a_stale_selection_keeps_family_get_and_roster_list_answering(app, services, caplog):
    catalog_with_dropped_alt(services)
    gallery = services.photographs
    key = gallery.save("dropped-alt", capture()[0], "portrait")["saved"]  # photo of an alt that left the catalog
    data = manifest(gallery)
    data["selected"]["hoshino"] = key  # selected for student A while the alt still resolved
    put(gallery, data)
    raw = gallery.index.read_bytes()
    with caplog.at_level(logging.WARNING):
        family = rpc(app, "family.get", key="hoshino")
        roster = rpc(app, "roster.list")
    assert "error" not in family and "error" not in roster
    entries = [variant for item in roster["result"]["families"] for variant in item["variants"]]
    assert entries and all("thumbnail" not in variant for variant in entries)
    assert all("thumbnail" not in variant for variant in family["result"]["variants"])
    assert gallery.index.read_bytes() == raw
    assert rpc(app, "photography.list", variant="hoshino")["result"]["app"] is None
    assert not [r for r in caplog.records if "Photograph lookup failed" in r.getMessage()]


def test_a_corrupt_manifest_leaves_every_student_list_working_and_the_file_unchanged(app, services, caplog):
    gallery = services.photographs
    gallery.root.mkdir(parents=True, exist_ok=True)
    gallery.index.write_bytes(b"{ not json")
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            family = rpc(app, "family.get", key="hoshino")
            roster = rpc(app, "roster.list")
            assert "error" not in family and "error" not in roster
    assert all("thumbnail" not in variant for item in roster["result"]["families"] for variant in item["variants"])
    assert len([r for r in caplog.records if "Photograph lookup failed" in r.getMessage()]) == 1  # once, not per student
    # the photograph features themselves refuse, and nothing overwrites the file
    assert rpc(app, "photography.list", variant="hoshino")["error"]["code"] == "photo_library_invalid"
    assert rpc(app, "photography.select", variant="hoshino", id=None)["error"]["code"] == "photo_library_invalid"
    png = capture()[0]
    assert rpc(app, "photography.save", variant="hoshino", png=png, mode="portrait")["error"]["code"] == "photo_library_invalid"
    assert gallery.index.read_bytes() == b"{ not json"
    assert sorted(path.name for path in gallery.root.iterdir() if path.name != "index.lock") == ["index.json"]


def test_variant_json_logs_again_only_after_the_lookup_recovered(app, services, caplog):
    gallery = services.photographs
    gallery.root.mkdir(parents=True, exist_ok=True)
    gallery.index.write_bytes(b"bad")
    with caplog.at_level(logging.WARNING):
        rpc(app, "roster.list")
        rpc(app, "roster.list")
        gallery.index.write_bytes(json.dumps(base()).encode())
        assert "error" not in rpc(app, "roster.list")
        gallery.index.write_bytes(b"bad again")
        rpc(app, "roster.list")
    assert len([r for r in caplog.records if "Photograph lookup failed" in r.getMessage()]) == 2


def test_a_valid_selection_still_gives_the_thumbnail(app, services):
    gallery = services.photographs
    key = gallery.save("hoshino", capture()[0], "portrait")["saved"]
    gallery.select("hoshino", key)
    variants = [v for item in rpc(app, "roster.list")["result"]["families"] for v in item["variants"]]
    assert variants[0]["thumbnail"].endswith(f"{key}.png")


def test_deep_copies_of_the_manifest_carry_the_held_rows(gallery):
    put(gallery, base(photos={BAD: {"id": BAD, "variant": "student:a", "sha256": "bad"}}))
    data = gallery._read()
    clone = copy.deepcopy(data)
    assert clone.held_photos == data.held_photos and clone.document() == data.document()
