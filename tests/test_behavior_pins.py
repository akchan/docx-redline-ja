"""Golden snapshot of the CLI over a document built to reach the uncommon paths.

The regression corpus is mostly plain paragraphs.  This document adds the
paths it misses: superscript and subscript edits, a table cell, text boxes with
an ``mc:Fallback`` copy (one in step, one drifted), container-level
``mc:AlternateContent``, ``scope``, ``replace_all``, other authors' revisions,
and every skip reason.  The snapshot holds the report, the canonical form of
``word/document.xml`` and the output of ``locate`` and ``read``.

After an intended behavior change, regenerate the snapshot with
``python3 tests/test_behavior_pins.py --regen`` and review the diff.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from lxml import etree as ET

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/docx-redline-ja/skills/docx-redline-ja/scripts"
GOLDEN = Path(__file__).parent / "fixtures" / "behavior_pins.json"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
DATE = "2026-01-01T00:00:00Z"


def r(text: str, align: str | None = None) -> str:
    rpr = f'<w:rPr><w:vertAlign w:val="{align}"/></w:rPr>' if align else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r>'


def p(*runs: str, para_id: str | None = None) -> str:
    attr = f' w14:paraId="{para_id}"' if para_id else ""
    return f"<w:p{attr}>{''.join(runs)}</w:p>"


def rev(tag: str, rid: int, author: str, body: str) -> str:
    return f'<w:{tag} w:id="{rid}" w:author="{author}" w:date="{DATE}">{body}</w:{tag}>'


def text_box(choice: str, fallback: str) -> str:
    return (
        "<w:r><mc:AlternateContent>"
        f'<mc:Choice Requires="wps"><w:drawing><w:txbxContent>{p(r(choice))}</w:txbxContent></w:drawing></mc:Choice>'
        f"<mc:Fallback><w:pict><w:txbxContent>{p(r(fallback))}</w:txbxContent></w:pict></mc:Fallback>"
        "</mc:AlternateContent></w:r>"
    )


BODY = "".join(
    [
        p(r("吾輩は猫である。名前はまだ無い。"), para_id="1A2B3C4D"),
        p(r("体積は10mm"), r("3", "superscript"), r("である。")),
        p(r("単位はmm3とする。")),
        p(r("文献"), r("12", "superscript"), r("によると、")),
        p(r("注"), r("a", "superscript"), r("を参照。")),
        p(r("水はH"), r("2", "subscript"), r("Oと書く。")),
        p(r("末尾の文献"), r("7", "superscript"), r("。")),
        "<w:tbl><w:tr><w:tc>" + p(r("表の中の文章です。")) + "</w:tc></w:tr></w:tbl>",
        p(r("図の説明"), text_box("箱の中の文字です。", "箱の中の文字です。")),
        p(r("別図"), text_box("別の箱の文字。", "別の箱の文学。")),
        p(
            "<mc:AlternateContent><mc:Choice>"
            + r("選択肢の文字")
            + "</mc:Choice><mc:Fallback>"
            + r("代替の文字")
            + "</mc:Fallback></mc:AlternateContent>"
        ),
        p(r("繰り返し、繰り返し。")),
        p(r("同じ語と同じ語。")),
        p(r("えええ")),
        p(r("範囲の語。")),
        p(r("範囲の語。")),
        p(r("前"), "<w:r><w:tab/></w:r>", r("後の文。")),
        p(r("衝突する文章。")),
        p(
            r("前文"),
            rev("ins", 901, "他人", r("他人の挿入文")),
            rev("del", 902, "他人", '<w:r><w:delText xml:space="preserve">消された</w:delText></w:r>'),
            r("後文。"),
        ),
        p(rev("ins", 903, "他人", '<w:fldSimple w:instr="PAGE">' + r("欄内の文字") + "</w:fldSimple>")),
        p(r("古い"), rev("ins", 904, "他人", rev("ins", 905, "Bench", r("入れ子")))),
        p(r("無関係な最後の段落。")),
    ]
)

DOCUMENT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:document xmlns:w="{W}" xmlns:mc="{MC}" xmlns:w14="{W14}"><w:body>{BODY}<w:sectPr/></w:body></w:document>'
)

FINDINGS = [
    {"old_text": "名前はまだ無い", "new_text": "名前はもう有る"},
    {"old_text": "10mm3で", "new_text": "20mm3で"},
    {"old_text": "mm3と", "new_text": "mm<sup>3</sup>と"},
    {"old_text": "献12に", "new_text": "献1,2に"},
    {"old_text": "注aを", "new_text": "注<plain>a</plain>を"},
    {"old_text": "H2O", "new_text": "H2O"},
    {"old_text": "献7。", "new_text": "献7a。"},
    {"old_text": "表の中", "new_text": "表中"},
    {"old_text": "箱の中", "new_text": "箱内"},
    {"old_text": "別の箱", "new_text": "他の箱"},
    {"old_text": "選択肢の", "new_text": "選択の"},
    {"old_text": "繰り返し", "new_text": "くりかえし", "replace_all": True},
    {"old_text": "同じ語", "new_text": "同一の語"},
    {"old_text": "ええ", "new_text": "えー", "replace_all": True},
    {"old_text": "範囲の語", "new_text": "範囲内の語", "scope": "p17"},
    {"old_text": "範囲の語", "new_text": "範囲外の語", "scope": "p999"},
    {"old_text": "繰り返し", "new_text": "x", "scope": "p0-2,p20-"},
    {"old_text": "前\t後", "new_text": "前後"},
    {"old_text": "衝突する", "new_text": "ぶつかる"},
    {"old_text": "する文", "new_text": "した文"},
    {"old_text": "の挿", "new_text": "の大挿"},
    {"old_text": "挿入文", "new_text": "挿文"},
    {"old_text": "欄内の", "new_text": "欄内での"},
    {"old_text": "吾輩は", "new_text": "吾輩は"},
    {"old_text": "存在しない語", "new_text": "x"},
    {"old_text": "無関係", "new_text": "<sup>x"},
    {"old_text": "無関係", "new_text": "無関心", "severity": "high", "replace_al": True},
    {"search": "古い", "replace": "新しい"},
    {"old_text": "古い", "new_text": "新しい", "replace_all": "yes"},
    {"old_text": "古い", "new_text": "新\x01しい"},
    {"old_text": "古い\n", "new_text": "新しい"},
    {"old_text": "", "new_text": "x"},
    ["not", "an", "object"],
]


def findings_text() -> str:
    lines = [json.dumps(item, ensure_ascii=False) for item in FINDINGS]
    lines.insert(3, "# a comment line")
    lines.insert(5, "")
    lines.append("{not json")
    return "\n".join(lines) + "\n"


def build(directory: Path) -> tuple[Path, Path]:
    docx = directory / "pins.docx"
    with zipfile.ZipFile(docx, "w", zipfile.ZIP_DEFLATED) as package:

        def add(name: str, data) -> None:
            # A fixed timestamp keeps the source's sha256 stable across runs.
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            package.writestr(info, data, zipfile.ZIP_DEFLATED)

        add(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        add(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        add("word/document.xml", DOCUMENT)
        add("word/media/image1.png", b"\x89PNG not really")
    findings = directory / "pins.jsonl"
    findings.write_text(findings_text(), encoding="utf-8")
    return docx, findings


def run(*args) -> dict:
    done = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True)
    return {"exit": done.returncode, "stdout": done.stdout, "stderr": done.stderr}


def scrub(value, directory: Path):
    if isinstance(value, dict):
        return {k: scrub(v, directory) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, directory) for v in value]
    if isinstance(value, str):
        value = value.replace(str(directory.resolve()), "<TMP>").replace(str(directory), "<TMP>")
        return re.sub(r"/\S*/docx-redline-ja-[^/]+/", "<WORK>/", value)
    return value


def canonical_document(docx: Path) -> str:
    with zipfile.ZipFile(docx) as package:
        root = ET.fromstring(package.read("word/document.xml"))
        names = package.namelist()
    return json.dumps(names) + "\n" + ET.tostring(root, method="c14n").decode()


def snapshot(directory: Path) -> dict:
    docx, findings = build(directory)
    out = directory / "out.docx"
    redline = SCRIPTS / "apply_redline.py"
    common = ["--docx", docx, "--findings", findings, "--author", "Bench", "--date", DATE]

    shot: dict = {}
    shot["apply"] = run(redline, *common, "--out", out)
    shot["apply.result"] = json.loads(out.with_suffix(".result.json").read_text(encoding="utf-8"))
    shot["apply.document"] = canonical_document(out)
    shot["apply.same_out"] = run(redline, *common, "--out", docx)
    shot["apply.bad_out_suffix"] = run(redline, *common, "--out", directory / "x.txt")
    shot["apply.bad_date"] = run(
        redline, "--docx", docx, "--findings", findings, "--out", directory / "y.docx", "--date", "soon"
    )

    shot["locate.one"] = run(redline, "locate", "--docx", docx, "--query", "繰り返し")
    shot["locate.many"] = run(redline, "locate", "--docx", docx, "--query", "範囲の語", "--query", "無い語")
    shot["locate.no_matches"] = run(redline, "locate", "--docx", docx, "--query", "の", "--no-matches")

    shot["read.json"] = run(redline, "read", "--docx", docx)
    shot["read.text"] = run(redline, "read", "--docx", docx, "--text")
    shot["read.paragraphs"] = run(redline, "read", "--docx", docx, "--paragraphs", "p1-3,p20-")
    shot["read.bad_scope"] = run(redline, "read", "--docx", docx, "--paragraphs", "q1")

    broken = directory / "broken.docx"
    broken.write_bytes(b"not a zip")
    shot["broken.apply"] = run(redline, "--docx", broken, "--findings", findings, "--out", directory / "z.docx")
    shot["broken.locate"] = run(redline, "locate", "--docx", broken, "--query", "x")
    shot["broken.read"] = run(redline, "read", "--docx", broken)
    return scrub(shot, directory)


def test_cli_matches_the_golden_snapshot(tmp_path: Path):
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    actual = snapshot(tmp_path)
    assert actual.keys() == expected.keys()
    for key in expected:
        assert actual[key] == expected[key], key


def test_snapshot_reaches_the_paths_it_is_meant_to_pin():
    shot = json.loads(GOLDEN.read_text(encoding="utf-8"))
    result = shot["apply.result"]
    assert all(result["verification"].values())
    reasons = {record.get("reason") for record in result["records"]}
    assert reasons >= {
        None,
        "invalid_record",
        "crosses_paragraph",
        "no_change",
        "not_found",
        "ambiguous",
        "spans_inline_object",
        "unsplittable_container",
        "conflict",
    }
    notes = " ".join(result["notes"])
    assert "mc:Fallback copy" in notes
    assert "superscript" in shot["apply.document"] and "subscript" in shot["apply.document"]


if __name__ == "__main__" and "--regen" in sys.argv:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        GOLDEN.write_text(
            json.dumps(snapshot(Path(tmp)), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    print(f"wrote {GOLDEN}")
