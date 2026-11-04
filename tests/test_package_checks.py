"""The skill's own package handling and post-condition checks, tested directly."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from lxml import etree as ET

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/docx-redline-ja/skills/docx-redline-ja/scripts"
sys.path.insert(0, str(SCRIPTS))

from checks import added_revision_problems, is_reversible, revision_problems  # noqa: E402
from package import PackageError, package_bytes, read_package  # noqa: E402
from synthetic_docx import W, build_public_domain_docx  # noqa: E402


def body(xml: str):
    return ET.fromstring(f'<w:document xmlns:w="{W}"><w:body>{xml}</w:body></w:document>')


def run(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def ins(rid: int, text: str, author: str = "A") -> str:
    return f'<w:ins w:id="{rid}" w:author="{author}" w:date="2026-01-01T00:00:00Z">{run(text)}</w:ins>'


def dele(rid: int, text: str, author: str = "A") -> str:
    return (
        f'<w:del w:id="{rid}" w:author="{author}" w:date="2026-01-01T00:00:00Z">'
        f'<w:r><w:delText xml:space="preserve">{text}</w:delText></w:r></w:del>'
    )


# ------------------------------------------------------------ package


def test_round_trip_keeps_untouched_parts_and_entry_order(tmp_path: Path):
    source = tmp_path / "in.docx"
    with zipfile.ZipFile(build_public_domain_docx(tmp_path / "base.docx", "ja")) as base, zipfile.ZipFile(
        source, "w", zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr(zipfile.ZipInfo("word/media/image1.png", (2001, 2, 3, 4, 5, 6)), b"\x89PNG")
        for info in base.infolist():
            archive.writestr(info, base.read(info))
    package = read_package(source)
    package.root.find(f".//{{{W}}}t").text = "edited"
    output = tmp_path / "out.docx"
    output.write_bytes(package_bytes(package))

    with zipfile.ZipFile(source) as before, zipfile.ZipFile(output) as after:
        names = before.namelist()
        names.remove("[Content_Types].xml")
        assert after.namelist() == ["[Content_Types].xml", *names]
        for info in before.infolist():
            if info.filename != "word/document.xml":
                assert after.read(info.filename) == before.read(info)
                assert after.getinfo(info.filename).date_time == info.date_time
        assert b"edited" in after.read("word/document.xml")


def test_identical_input_gives_byte_identical_output(tmp_path: Path):
    source = build_public_domain_docx(tmp_path / "in.docx", "ja")
    findings = tmp_path / "f.jsonl"
    findings.write_text(
        json.dumps({"old_text": "名前はまだ無い", "new_text": "名前はまだない"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    outputs = []
    for name in ("a.docx", "b.docx"):
        out = tmp_path / name
        done = subprocess.run(
            [sys.executable, str(SCRIPTS / "apply_redline.py"), "--docx", str(source),
             "--findings", str(findings), "--out", str(out), "--date", "2026-01-01T00:00:00Z"],
            capture_output=True, text=True,
        )
        assert done.returncode == 0, done.stderr
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1]
    # Only the requested output and its report are written.
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "a.docx", "a.result.json", "b.docx", "b.result.json", "f.jsonl", "in.docx",
    ]


@pytest.mark.parametrize("name", ["../evil.xml", "/abs/evil.xml", "word/../../evil.xml"])
def test_entries_that_escape_the_package_are_refused(tmp_path: Path, name: str):
    package = tmp_path / "bad.docx"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("word/document.xml", "<w:document/>")
        archive.writestr(name, "<x/>")
    with pytest.raises(PackageError, match="unsafe"):
        read_package(package)


def test_a_symbolic_link_entry_is_copied_as_a_plain_file(tmp_path: Path):
    package = tmp_path / "link.docx"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}"/>')
        link = zipfile.ZipInfo("word/link.xml")
        link.external_attr = 0o120777 << 16
        archive.writestr(link, "/etc/passwd")
    output = tmp_path / "out.docx"
    output.write_bytes(package_bytes(read_package(package)))
    with zipfile.ZipFile(output) as archive:
        mode = archive.getinfo("word/link.xml").external_attr >> 16
    assert not stat.S_ISLNK(mode)


def test_names_that_differ_only_in_case_are_refused(tmp_path: Path):
    package = tmp_path / "dup.docx"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}"/>')
        archive.writestr("word/a.xml", "<a/>")
        archive.writestr("word/A.xml", "<a/>")
    with pytest.raises(PackageError, match="duplicate"):
        read_package(package)


def test_a_corrupt_entry_is_refused(tmp_path: Path):
    package = tmp_path / "corrupt.docx"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}"/>')
        archive.writestr("word/payload.bin", b"UNIQUE-PAYLOAD")
    package.write_bytes(package.read_bytes().replace(b"UNIQUE-PAYLOAD", b"UNIQUE-PAYLOAX"))
    with pytest.raises(PackageError, match="corrupt"):
        read_package(package)


def test_a_file_that_is_not_a_zip_is_refused(tmp_path: Path):
    package = tmp_path / "plain.docx"
    package.write_text("not a zip", encoding="utf-8")
    with pytest.raises(PackageError):
        read_package(package)


# ------------------------------------------------------------ reversibility


def test_rejecting_our_revisions_restores_the_source_text():
    source = body(f"<w:p>{run('吾輩は猫である')}</w:p>")
    output = body(f"<w:p>{run('吾輩は')}{ins(1, '黒')}{run('猫である')}</w:p>")
    assert is_reversible(source, output, "A")


def test_text_changed_outside_a_revision_is_not_reversible():
    source = body(f"<w:p>{run('吾輩は猫である')}</w:p>")
    output = body(f"<w:p>{run('吾輩は犬')}{ins(1, '黒')}{run('である')}</w:p>")
    assert not is_reversible(source, output, "A")


def test_other_authors_revisions_are_left_alone_on_both_sides():
    source = body(f"<w:p>{run('吾輩は')}{ins(1, '猫', author='B')}{run('である')}</w:p>")
    output = body(f"<w:p>{run('吾輩は')}{ins(1, '猫', author='B')}{dele(2, 'である')}{ins(3, 'だ')}</w:p>")
    assert is_reversible(source, output, "A")


# ------------------------------------------------------------ revision markup


def test_revision_problems_counts_each_fault_kind():
    root = body(
        "<w:p>"
        + ins(1, "a")
        + ins(1, "b")  # duplicate id
        + '<w:ins w:id="2">' + run("c") + "</w:ins>"  # no author, no date
        + '<w:del w:id="3" w:author="A" w:date="2026-01-01T00:00:00Z">' + run("d") + "</w:del>"  # w:t in a deletion
        + '<w:r><w:delText>e</w:delText></w:r>'  # delText outside a deletion
        + "</w:p>"
    )
    assert revision_problems(root) == {
        "duplicate revision w:id": 1,
        "revision without w:author": 1,
        "revision without w:date": 1,
        "w:t inside a deletion": 1,
        "w:delText outside a deletion": 1,
    }


def test_faults_the_source_already_had_are_not_blamed_on_the_run():
    inherited = ins(1, "a") + ins(1, "b")
    source = body(f"<w:p>{inherited}</w:p>")
    clean_edit = body(f"<w:p>{inherited}{ins(2, 'c')}</w:p>")
    bad_edit = body(f"<w:p>{inherited}{ins(2, 'c')}{ins(2, 'd')}</w:p>")
    assert added_revision_problems(source, clean_edit) == []
    assert added_revision_problems(source, bad_edit) == ["1 x duplicate revision w:id"]


def test_paragraph_mark_revisions_are_not_counted_as_run_revisions():
    root = body(
        '<w:p><w:pPr><w:rPr><w:ins w:id="1" w:author="A" w:date="2026-01-01T00:00:00Z"/>'
        "</w:rPr></w:pPr>" + ins(1, "a") + "</w:p>"
    )
    assert revision_problems(root) == {}
