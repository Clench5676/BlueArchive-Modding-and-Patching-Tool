"""The archive of official portrait rigs (BL-166): masks, the match rule and the store."""

from __future__ import annotations

import json

import numpy as np
from PIL import Image

from bmpt.library import rig_archive as archive


def page(boxes, size=64):
    image = Image.new("RGBA", (size, size))
    for box in boxes:
        image.paste((200, 100, 50, 255), box)
    return image


def test_a_mask_packs_and_unpacks_to_itself():
    mask = archive.alpha_mask(page([(0, 0, 32, 16), (40, 40, 64, 64)]))
    assert mask.shape == (archive.MASK, archive.MASK) and mask.any() and not mask.all()
    assert np.array_equal(archive.unpack(archive.pack(mask)), mask)
    assert archive.unpack("not base64!") is None and archive.unpack(archive.pack(mask)[:20]) is None
    assert archive.iou(mask, mask) == 1.0 and archive.iou(mask, ~mask) == 0.0


def rig(layout, boxes, name="Test"):
    return archive.Rig("test_spr", f"{name}.atlas", f"{name}.skel", layout * 10, layout * 10, layout,
                       {"page": archive.pack(archive.alpha_mask(page(boxes)))})


def test_the_release_a_mod_follows_is_the_closest_and_clearly_closer_than_the_installed_one():
    old, new = rig("a", [(0, 0, 32, 32)]), rig("b", [(32, 32, 64, 64)])
    drawn_old = {"page": archive.alpha_mask(page([(0, 0, 32, 32), (30, 30, 34, 34)]))}  # a little paint of its own
    installed = new.unpacked()
    found = archive.best([old], drawn_old, installed)
    assert found and found[0] is old and found[1] > 0.9
    # a mod made for the installed release: no earlier one
    assert archive.best([old], installed, installed) is None
    # not clearly closer than the installed one, or too far from any
    near = rig("c", [(0, 0, 40, 40)])
    assert archive.best([near], {"page": archive.alpha_mask(page([(0, 0, 36, 36)]))},
                        {"page": archive.alpha_mask(page([(0, 0, 33, 33)]))}) is None
    assert archive.best([old], {"page": archive.alpha_mask(page([(40, 0, 64, 20)]))}, installed) is None
    # pages the mod does not have tell nothing
    assert archive.best([old], {"other": drawn_old["page"]}, installed) is None


def test_the_archive_keeps_each_rig_once_and_the_newest_few(tmp_path):
    store = archive.RigArchive(tmp_path / "rigs")
    mask = {"page": archive.alpha_mask(page([(0, 0, 32, 32)]))}
    assert store.add("Test_spr", "Test.atlas", b"atlas 0", "Test.skel", b"skel 0", "layout 0", mask)
    assert not store.add("test_spr", "Test.atlas", b"atlas 0", "Test.skel", b"skel 0", "layout 0", mask)
    [kept] = store.rigs("TEST_SPR")
    assert (kept.atlas, kept.skeleton, kept.layout) == ("Test.atlas", "Test.skel", "layout 0")
    assert store.read(kept.atlas_sha1) == b"atlas 0" and store.read(kept.skeleton_sha1) == b"skel 0"
    assert store.read("../index.json") is None
    generation = store.generation()
    for i in range(1, archive.KEEP + 2):
        store.add("test_spr", "Test.atlas", f"atlas {i}".encode(), "Test.skel", f"skel {i}".encode(), f"layout {i}", mask)
    assert store.generation() > generation
    rigs = store.rigs("test_spr")
    assert len(rigs) == archive.KEEP and rigs[0].layout == "layout 2" and rigs[-1].layout == f"layout {archive.KEEP + 1}"
    assert store.read(kept.atlas_sha1) is None  # the oldest went with its objects
    assert len(list((tmp_path / "rigs" / "objects").iterdir())) == 2 * archive.KEEP
    store.set_stamp("install", "stamp")
    again = archive.RigArchive(tmp_path / "rigs")  # from disk
    assert again.stamp("install") == "stamp" and len(again.rigs("test_spr")) == archive.KEEP
    assert np.array_equal(again.rigs("test_spr")[0].unpacked()["page"], mask["page"])


def test_an_unreadable_index_is_an_empty_archive(tmp_path):
    (tmp_path / "rigs").mkdir()
    (tmp_path / "rigs" / "index.json").write_text("{not json")
    store = archive.RigArchive(tmp_path / "rigs")
    assert store.rigs("test_spr") == [] and store.stamp("install") is None
    (tmp_path / "rigs" / "index.json").write_text(json.dumps({"version": 1, "rigs": {"test_spr": [{"atlas": "x"}, 5]},
                                                              "stamps": {}}))
    assert archive.RigArchive(tmp_path / "rigs").rigs("test_spr") == []


def test_a_deferred_block_writes_the_index_once_at_its_end(tmp_path, monkeypatch):
    store = archive.RigArchive(tmp_path / "rigs")
    mask = {"page": archive.alpha_mask(page([(0, 0, 32, 32)]))}
    writes = []
    real = archive.atomic_json
    monkeypatch.setattr(archive, "atomic_json", lambda path, data: (writes.append(path), real(path, data)))
    with store.deferred():
        for i in range(3):
            assert store.add(f"c{i}_spr", "Test.atlas", f"atlas {i}".encode(), "Test.skel", f"skel {i}".encode(),
                             "layout", mask)
        store.set_stamp("install", "stamp")
        assert writes == [] and len(store.rigs("c2_spr")) == 1  # kept in memory meanwhile
    assert len(writes) == 1
    again = archive.RigArchive(tmp_path / "rigs")
    assert again.stamp("install") == "stamp" and all(len(again.rigs(f"c{i}_spr")) == 1 for i in range(3))
    with store.deferred():
        pass
    assert len(writes) == 1  # nothing kept, nothing written
