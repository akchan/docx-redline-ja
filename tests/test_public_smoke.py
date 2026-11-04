from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from synthetic_docx import W, build_public_domain_docx

ROOT = Path(__file__).resolve().parents[1]
REDLINE = ROOT / "plugins/docx-redline-ja/skills/docx-redline-ja/scripts/apply_redline.py"
NS = {"w": W}

REDLINE_CASES = (
    ("ja", "吾輩は猫である。名前はまだ無い。", "吾輩は黒猫である。名前はまだ無い。", "黒"),
    ("en", "Alice was beginning to get very tired of sitting by her sister on the bank.",
     "Alice was quietly beginning to get very tired of sitting by her sister on the bank.", " quietly"),
)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], text=True, capture_output=True)


def document_root(path: Path):
    with zipfile.ZipFile(path) as package:
        return etree.fromstring(package.read("word/document.xml"))


@pytest.mark.parametrize(("language", "old", "new", "inserted"), REDLINE_CASES)
def test_insertion_is_a_minimal_revision(tmp_path: Path, language: str, old: str, new: str, inserted: str):
    source = build_public_domain_docx(tmp_path / f"{language}.docx", language)
    findings = tmp_path / f"{language}-findings.jsonl"
    findings.write_text(json.dumps({"old_text": old, "new_text": new}, ensure_ascii=False) + "\n", encoding="utf-8")
    output = tmp_path / f"{language}-redlined.docx"
    completed = run(str(REDLINE), "--docx", str(source), "--findings", str(findings), "--out", str(output), "--author", "Test")
    assert completed.returncode == 0, completed.stderr
    root = document_root(output)
    assert ["".join(node.itertext()) for node in root.xpath(".//w:ins", namespaces=NS)] == [inserted]
    assert root.xpath(".//w:del", namespaces=NS) == []
    report = json.loads(output.with_suffix(".result.json").read_text(encoding="utf-8"))
    assert all(value is True for value in report["verification"].values())
