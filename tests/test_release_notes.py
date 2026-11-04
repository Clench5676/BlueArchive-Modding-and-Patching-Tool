"""A build's own release notes (assets/release-notes/<X.Y.Z>.md): the App reads them for Settings' update preview, and the
same text is the GitHub release's. The page's Markdown reader is tested in tests/js/release_notes.test.mjs."""
import re

from bmpt.webui.api import RELEASE_NOTES_DIR, release_notes

MARK = re.compile(r"^<!--\s*lang:\s*([A-Za-z-]+)\s*-->$")


def test_a_build_reads_its_versions_notes_and_none_without_a_file(tmp_path):
    (tmp_path / "0.7.0.md").write_text("<!-- lang: en -->\n## What's New\n", encoding="utf-8")
    for version in ("0.7.0", "0.7.0-alpha.1", "0.7.0a1"):
        assert release_notes(version, tmp_path) == {"version": "0.7.0", "notes": "<!-- lang: en -->\n## What's New\n"}
    assert release_notes("0.7.1-alpha", tmp_path) == {"version": "0.7.1", "notes": ""}
    assert release_notes("", tmp_path) == {"version": "", "notes": ""}
    assert release_notes("../../0.7.0", tmp_path)["notes"] == "", "a version names no other path"


def test_every_shipped_notes_file_has_an_english_and_a_chinese_section():
    files = sorted(RELEASE_NOTES_DIR.glob("*.md"))
    assert files, "the coming release's notes ship with the build"
    for path in files:
        assert re.fullmatch(r"\d+\.\d+\.\d+\.md", path.name)
        lines = path.read_text(encoding="utf-8").splitlines()
        marks = [m.group(1) for line in lines if (m := MARK.match(line.strip()))]
        assert marks[:2] == ["en", "zh-CN"], path.name
        # GitHub shows the release text as written: no HTML but the language marks
        assert not [line for line in lines if re.search(r"<(?!!--)[A-Za-z/]", line)], path.name
        assert lines.count("---") == len(marks) - 1, "one rule between the language sections"
