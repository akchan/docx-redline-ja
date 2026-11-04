"""The tool run over documents Microsoft Word actually wrote.

Every other test builds its input with ``tests/synthetic_docx.py``, which has
none of the markup Word adds: ``mc:Ignorable``, ``w14:paraId``, rsids, a styles
part, Word's comment extension parts. The four files in ``tests/fixtures/`` were
made by hand in Word for Mac; see ``tests/fixtures/README.md``.

Each fixture is copied to ``tmp_path`` first, so a bug in the tool cannot damage
the committed file.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from synthetic_docx import W

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
REDLINE = ROOT / "plugins/docx-redline-ja/skills/docx-redline-ja/scripts/apply_redline.py"
NS = {"w": W}

#: The fixtures' own revisions are by this author; ours must be by someone else
#: for "leave other people's revisions alone" to be testable.
FIXTURE_AUTHOR = "Reviewer A"
OUR_AUTHOR = "Test"

PLAIN = ("ja-plain.docx", "en-plain.docx")
ANNOTATED = ("ja-plain-annotated.docx", "en-plain-annotated.docx")
ALL = PLAIN + ANNOTATED

#: Each edit spans the boundary between a bold or italic run and a plain one.
EDITS = {
    "ja-plain.docx": ("吾輩は猫である。名前はまだ無い。", "吾輩は猫である。名前はまだない。"),
    "en-plain.docx": (
        "Alice was beginning to get very tired of sitting by her sister on the bank.",
        "Alice was beginning to get very tired of sitting by her sister on the riverbank.",
    ),
    "ja-plain-annotated.docx": ("どこで生れたかとんと見当がつかぬ。", "どこで生まれたかとんと見当がつかぬ。"),
    "en-plain-annotated.docx": (
        "It had no pictures or conversations in it.",
        "It had no pictures or conversations at all.",
    ),
}


def document_root(package: Path):
    with zipfile.ZipFile(package) as archive:
        return etree.fromstring(archive.read("word/document.xml"))


def revisions_by(root, author: str, kind: str) -> list[str]:
    return [
        "".join(element.itertext())
        for element in root.xpath(f".//w:{kind}", namespaces=NS)
        if element.get(f"{{{W}}}author") == author
    ]


def redline(tmp_path: Path, name: str) -> tuple[Path, Path, dict]:
    source = Path(shutil.copy2(FIXTURES / name, tmp_path / name))
    old, new = EDITS[name]
    findings = tmp_path / "findings.jsonl"
    findings.write_text(json.dumps({"old_text": old, "new_text": new}, ensure_ascii=False) + "\n", encoding="utf-8")
    output = tmp_path / "redlined.docx"
    done = subprocess.run(
        [sys.executable, str(REDLINE), "--docx", str(source), "--findings", str(findings),
         "--out", str(output), "--author", OUR_AUTHOR],
        text=True, capture_output=True,
    )
    assert done.returncode == 0, done.stderr
    report = json.loads(output.with_suffix(".result.json").read_text(encoding="utf-8"))
    return source, output, report


def test_fixtures_carry_no_personal_metadata():
    """Remaking a fixture in Word puts the user's name and dates back in."""
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "strip_docx_metadata.py"), "--check",
         *[str(FIXTURES / name) for name in ALL]],
        text=True, capture_output=True,
    )
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("name", ALL)
def test_fixtures_still_carry_the_markup_they_exist_for(name: str):
    with zipfile.ZipFile(FIXTURES / name) as archive:
        parts = set(archive.namelist())
        root = etree.fromstring(archive.read("word/document.xml"))

    mc = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    w14 = "http://schemas.microsoft.com/office/word/2010/wordml"
    assert root.get(f"{{{mc}}}Ignorable"), "not a document Word wrote"
    assert any(e.get(f"{{{w14}}}paraId") for e in root.iter())
    assert {"word/styles.xml", "word/settings.xml", "docProps/core.xml"} <= parts
    if name in ANNOTATED:
        assert {"word/comments.xml", "word/people.xml", "word/commentsIds.xml"} <= parts
        assert revisions_by(root, FIXTURE_AUTHOR, "ins")
        assert revisions_by(root, FIXTURE_AUTHOR, "del")


@pytest.mark.parametrize("name", ALL)
def test_redline_passes_every_check_on_a_word_document(tmp_path: Path, name: str):
    _, _, report = redline(tmp_path, name)
    assert report["counts"]["applied"] == 1
    assert all(value is True for value in report["verification"].values()), report["verification"]


@pytest.mark.parametrize("name", ANNOTATED)
def test_redline_leaves_the_other_reviewers_work_alone(tmp_path: Path, name: str):
    """Their insertion, deletion and comment come through untouched."""
    source, output, _ = redline(tmp_path, name)
    before, after = document_root(source), document_root(output)
    for kind in ("ins", "del"):
        assert revisions_by(after, FIXTURE_AUTHOR, kind) == revisions_by(before, FIXTURE_AUTHOR, kind)
    assert revisions_by(after, OUR_AUTHOR, "ins") or revisions_by(after, OUR_AUTHOR, "del")
    with zipfile.ZipFile(source) as a, zipfile.ZipFile(output) as b:
        for part in ("word/comments.xml", "word/commentsExtended.xml", "word/commentsIds.xml", "word/people.xml"):
            assert a.read(part) == b.read(part), part
