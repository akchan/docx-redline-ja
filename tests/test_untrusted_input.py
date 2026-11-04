"""Inputs that come from someone else: hostile or odd packages and findings."""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/docx-redline-ja/skills/docx-redline-ja/scripts"
sys.path.insert(0, str(SCRIPTS))

import package as package_module  # noqa: E402
from package import PackageError, read_package  # noqa: E402

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
DATE = 'w:date="2026-01-01T00:00:00Z"'
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/></Types>'
)


def run_(text: str, rpr: str = "") -> str:
    return f'<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r>'


def make_docx(path: Path, body: str | None = None, document: str | None = None) -> Path:
    if document is None:
        document = f'<w:document xmlns:w="{W}" xmlns:mc="{MC}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", CONTENT_TYPES)
        package.writestr("word/document.xml", document)
    return path


def apply(tmp_path: Path, docx: Path, findings: bytes) -> tuple[subprocess.CompletedProcess, dict | None, str | None]:
    records = tmp_path / "findings.jsonl"
    records.write_bytes(findings)
    out = tmp_path / "out.docx"
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / "apply_redline.py"), "--docx", str(docx),
         "--findings", str(records), "--out", str(out), "--author", "AI",
         "--date", "2026-02-02T00:00:00Z"],
        capture_output=True, text=True,
    )
    result = out.with_suffix(".result.json")
    report = json.loads(result.read_text(encoding="utf-8")) if result.exists() else None
    xml = zipfile.ZipFile(out).read("word/document.xml").decode() if out.exists() else None
    return done, report, xml


def line(record: dict) -> bytes:
    return (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")


# ------------------------------------------------------------ the package


def test_too_many_entries_are_refused(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(package_module, "MAX_ENTRIES", 2)
    docx = make_docx(tmp_path / "in.docx", run_("a"))
    with zipfile.ZipFile(docx, "a") as archive:
        archive.writestr("word/extra.xml", "<x/>")
    with pytest.raises(PackageError, match="entries"):
        read_package(docx)


def test_a_package_that_expands_too_far_is_refused(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(package_module, "MAX_TOTAL_BYTES", 1000)
    docx = make_docx(tmp_path / "in.docx", run_("a"))
    with zipfile.ZipFile(docx, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/media/zeros.bin", b"\0" * 100_000)
    with pytest.raises(PackageError, match="expands"):
        read_package(docx)


def test_an_oversized_document_part_is_refused(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(package_module, "MAX_DOCUMENT_BYTES", 100)
    docx = make_docx(tmp_path / "in.docx", f"<w:p>{run_('a' * 200)}</w:p>")
    with pytest.raises(PackageError, match="limit"):
        read_package(docx)


def test_a_drive_letter_in_an_entry_name_is_refused(tmp_path: Path):
    docx = make_docx(tmp_path / "in.docx", run_("a"))
    with zipfile.ZipFile(docx, "a") as archive:
        archive.writestr("C:evil.xml", "<x/>")
    with pytest.raises(PackageError, match="unsafe"):
        read_package(docx)


def test_malformed_document_xml_is_a_startup_error(tmp_path: Path):
    docx = make_docx(tmp_path / "in.docx", document=f'<w:document xmlns:w="{W}"><w:body>')
    (tmp_path / "f.jsonl").write_bytes(line({"old_text": "a", "new_text": "b"}))
    for args in (
        ["--docx", str(docx), "--findings", str(tmp_path / "f.jsonl"), "--out", str(tmp_path / "o.docx")],
        ["locate", "--docx", str(docx), "--query", "a"],
        ["read", "--docx", str(docx)],
    ):
        done = subprocess.run(
            [sys.executable, str(SCRIPTS / "apply_redline.py"), *args], capture_output=True, text=True
        )
        assert done.returncode == 2, done.stderr
        assert done.stderr.startswith("Error: word/document.xml is not well-formed XML")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["f.jsonl", "in.docx"]


# ------------------------------------------------------------ the findings file


@pytest.mark.parametrize("bad", ["\ud800", "￾"])
def test_characters_xml_cannot_hold_are_an_invalid_record(tmp_path: Path, bad: str):
    docx = make_docx(tmp_path / "in.docx", f"<w:p>{run_('abc')}</w:p>")
    raw = json.dumps({"old_text": "abc", "new_text": "a" + bad}).encode("ascii")
    done, report, _ = apply(tmp_path, docx, raw + b"\n")
    assert done.returncode == 0, done.stderr
    assert report["counts"]["by_reason"] == {"invalid_record": 1}
    assert "not allowed in XML" in report["records"][0]["detail"]


def test_a_byte_order_mark_does_not_cost_the_first_record(tmp_path: Path):
    docx = make_docx(tmp_path / "in.docx", f"<w:p>{run_('abc')}</w:p>")
    _, report, _ = apply(tmp_path, docx, b"\xef\xbb\xbf" + line({"old_text": "abc", "new_text": "abd"}))
    assert report["counts"]["applied"] == 1


def test_a_line_separator_inside_a_string_does_not_split_the_record(tmp_path: Path):
    docx = make_docx(tmp_path / "in.docx", f"<w:p>{run_('abc')}</w:p>")
    _, report, _ = apply(tmp_path, docx, line({"old_text": "abc", "new_text": "a c"}))
    assert report["counts"]["records"] == 1


# ------------------------------------------------------------ revision structure


def test_inserted_text_does_not_inherit_a_tracked_formatting_change(tmp_path: Path):
    rpr = f'<w:rPr><w:b/><w:rPrChange w:id="500" w:author="Rev" {DATE}><w:rPr/></w:rPrChange></w:rPr>'
    docx = make_docx(tmp_path / "in.docx", f"<w:p>{run_('吾輩は猫である。', rpr)}</w:p>")
    done, report, xml = apply(tmp_path, docx, line({"old_text": "猫である", "new_text": "黒猫だ"}))
    assert done.returncode == 0, done.stderr
    inserted = [chunk.split("</w:ins>")[0] for chunk in xml.split("<w:ins ")[1:]]
    assert inserted and all("<w:b/>" in chunk and "rPrChange" not in chunk for chunk in inserted)


def test_fallback_copy_is_not_nested_inside_an_existing_revision(tmp_path: Path):
    """The mc:Choice paragraph is plain; its mc:Fallback copy sits in an older
    same-author w:ins behind an unsplittable w:fldSimple."""
    choice = f"<w:p>{run_('abcdef')}</w:p>"
    fallback = (
        f'<w:p><w:ins w:id="7" w:author="AI" {DATE}><w:fldSimple w:instr="X">'
        f"{run_('abcdef')}</w:fldSimple></w:ins></w:p>"
    )
    body = (
        '<w:p><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><w:txbxContent>'
        f"{choice}</w:txbxContent></w:drawing></mc:Choice><mc:Fallback><w:pict><w:txbxContent>"
        f"{fallback}</w:txbxContent></w:pict></mc:Fallback></mc:AlternateContent></w:r></w:p>"
    )
    docx = make_docx(tmp_path / "in.docx", body)
    done, report, xml = apply(tmp_path, docx, line({"old_text": "abcdef", "new_text": "abcXdef"}))
    assert done.returncode == 0, done.stderr
    assert any("mc:Fallback copy was left unchanged" in note for note in report["notes"])
    assert xml.split("<mc:Fallback>")[1].count("<w:ins ") == 1  # only the old one


def test_independence_check_rejects_older_revisions_under_the_same_author():
    from lxml import etree as ET

    from checks import insertions_independent

    root = ET.fromstring(
        f'<w:document xmlns:w="{W}"><w:body><w:p>'
        f'<w:ins w:id="7" w:author="A" {DATE}><w:r><w:t>old</w:t></w:r>'
        f'<w:ins w:id="9" w:author="A" {DATE}><w:r><w:t>new</w:t></w:r></w:ins></w:ins>'
        "</w:p></w:body></w:document>"
    )
    assert not insertions_independent(root, {"9"})
