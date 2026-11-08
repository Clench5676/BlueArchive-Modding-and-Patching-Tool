"""Special scenes (catalog/special.py) and the skeleton a Spine preview stands for (webui/services.skeleton_file), BL-125."""

from bmpt.catalog import Catalog
from bmpt.catalog.special import describe
from bmpt.webui.services import skeleton_file


def _metadata():
    entries, students, _ = Catalog(None, None)._metadata()
    return entries, students


def test_a_two_letter_name_never_claims_a_scene_by_prefix():
    entries, students = _metadata()
    # ui_set is the raid and world boss banners, not Ui's: it keeps an entry of its own under its core
    family, _, names = describe("ui_set", entries, students)
    assert family == "special" and names["zh-CN"]["short"] == "ui_set"
    # a core that is the two-letter name itself, and longer names as prefixes, still find their student
    assert describe("ui", entries, students)[0] != "special"
    assert describe("rei_background", entries, students)[0] != "special"


def test_a_preview_stands_for_the_named_the_only_or_else_the_first_skeleton():
    files = {"ch0100_home.skel": b"", "ch0100_home.atlas": b"", "ch0100_home_bg.skel": b"", "ch0100_home_bg.atlas": b""}
    assert [skeleton_file(files, "ch0100_home", s) for s in (".skel", ".atlas")] == ["ch0100_home.skel", "ch0100_home.atlas"]
    single = {"test_spr.skel": b"", "mod.atlas": b""}
    assert [skeleton_file(single, "other", s) for s in (".skel", ".atlas")] == ["test_spr.skel", "mod.atlas"]
    banners = {f"Ui_RaidBoss_{n}.{ext}": b"" for n in ("Goz", "Binah", "Hod") for ext in ("skel", "atlas")}
    assert [skeleton_file(banners, "ui_set", s) for s in (".skel", ".atlas")] == [
        "Ui_RaidBoss_Binah.skel", "Ui_RaidBoss_Binah.atlas"]
    assert skeleton_file({}, "ui_set", ".skel") is None
