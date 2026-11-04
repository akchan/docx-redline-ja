#!/usr/bin/env python3
"""Regenerate the before/after figure at the top of the READMEs.

    python3 tools/render_readme_example.py            # writes docs/example-*.png
    python3 tools/render_readme_example.py --out-dir /tmp/x

Nothing in the figure is drawn by hand. The script builds a one-paragraph DOCX
from the sentence below, applies the same two findings with this skill and
with docx-cli (``bunx bun-docx@0.28.0``, ``track-changes on`` + ``replace
--batch``), reads the ``w:del``/``w:ins`` markup back out of both outputs and
paints what Word would show: deletions struck through in red, insertions
underlined in blue. Four PNGs come out, light and dark, with Japanese and
English row labels. The sentence is original; it imitates the kind of fix a
proofreader makes to a medical manuscript and is not quoted from anywhere.

Needs Pillow, lxml, ``bunx`` and a CJK font (Hiragino on macOS, Noto Sans CJK
elsewhere, or ``--font``).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from lxml import etree as ET
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
from synthetic_docx import build_docx  # noqa: E402

SCRIPT = REPO / "plugins/docx-redline-ja/skills/docx-redline-ja/scripts/apply_redline.py"
DOCX_CLI = ["bunx", "bun-docx@0.28.0"]

SENTENCE = "MRIは小病変の検出率のが高く、治療後に画像上で残存がなく画像上Complete Responseと判定された症例を解析対象とした。"
FINDINGS = [
    ("検出率のが高く", "検出率が高く"),
    ("画像上で残存がなく画像上Complete Response", "画像上残存がなくcomplete response"),
]

LABELS = {
    "ja": ("原文", "docx-cli", "このスキル"),
    "en": ("Original", "docx-cli", "This skill"),
}
THEMES = {
    # background, text, deletion, insertion, label
    "light": ("#ffffff", "#1f2328", "#d1242f", "#0969da", "#59636e"),
    "dark": ("#0d1117", "#e6edf3", "#ff7b72", "#79c0ff", "#9198a1"),
}
FONT_CANDIDATES = [
    ("/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", 0),
]

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


# ------------------------------------------------------------ produce docx


def run_ours(source: Path, workdir: Path) -> Path:
    findings = workdir / "findings.jsonl"
    findings.write_text(
        "".join(f'{{"old_text":"{old}","new_text":"{new}"}}\n' for old, new in FINDINGS), encoding="utf-8"
    )
    out = workdir / "ours.docx"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--docx", str(source), "--findings", str(findings), "--out", str(out),
         "--author", "Claude", "--date", "2026-01-01T00:00:00Z"],
        check=True, capture_output=True, text=True,
    )
    return out


def run_docx_cli(source: Path, workdir: Path) -> Path:
    if shutil.which(DOCX_CLI[0]) is None:
        sys.exit("bunx is not installed; it is needed to run docx-cli for the comparison row")
    batch = workdir / "batch.jsonl"
    batch.write_text(
        "".join(f'{{"pattern":"{old}","replacement":"{new}"}}\n' for old, new in FINDINGS), encoding="utf-8"
    )
    out = workdir / "docx-cli.docx"
    shutil.copy2(source, out)  # docx-cli edits in place
    env = {**os.environ, "DOCX_AUTHOR": "docx-cli"}
    for args in (["track-changes", str(out), "on"], ["replace", str(out), "--batch", str(batch), "--author", "docx-cli"]):
        subprocess.run([*DOCX_CLI, *args], check=True, capture_output=True, text=True, env=env)
    return out


# --------------------------------------------------------------- read docx


def segments(docx: Path) -> list[tuple[str, str]]:
    """The first paragraph as (kind, text) pieces; kind is plain, del or ins."""
    with zipfile.ZipFile(docx) as package:
        root = ET.fromstring(package.read("word/document.xml"), ET.XMLParser(resolve_entities=False, no_network=True))
    paragraph = root.find(".//w:body/w:p", NS)
    pieces: list[tuple[str, str]] = []
    for child in paragraph:
        tag = ET.QName(child).localname
        if tag == "r":
            pieces.append(("plain", "".join(child.xpath("string(.//w:t)", namespaces=NS))))
        elif tag in ("del", "ins"):
            pieces.append((tag, "".join(child.xpath("string(.//w:delText|.//w:t)", namespaces=NS))))
    return pieces


# ------------------------------------------------------------------- draw

SCALE = 2
WIDTH, LABEL_W, PAD, FONT_PX, LEADING, ROW_GAP = 720, 108, 20, 18, 30, 18
NO_LINE_START = set("、。，．）」]")


def tokens(pieces):
    """Split into drawable units: one CJK character or one Latin word with its trailing space."""
    out = []
    for kind, text in pieces:
        word = ""
        for ch in text:
            if ch.isascii():
                word += ch
                if ch == " ":
                    out.append((kind, word)); word = ""
            else:
                if word:
                    out.append((kind, word)); word = ""
                out.append((kind, ch))
        if word:
            out.append((kind, word))
    return out


def layout(pieces, font, max_w):
    lines, line, x = [], [], 0
    for kind, text in tokens(pieces):
        w = font.getlength(text)
        if line and x + w > max_w and text not in NO_LINE_START:
            lines.append(line); line, x = [], 0
        line.append((kind, text, x)); x += w
    if line:
        lines.append(line)
    return lines


def render(rows, labels, theme, font, out: Path):
    bg, fg, red, blue, grey = theme
    text_w = WIDTH - LABEL_W - 2 * PAD
    laid = [layout(pieces, font, text_w * SCALE) for pieces in rows]
    height = PAD * 2 + sum(len(lines) * LEADING for lines in laid) + ROW_GAP * (len(rows) - 1)
    image = Image.new("RGB", (WIDTH * SCALE, height * SCALE), bg)
    draw = ImageDraw.Draw(image)
    y = PAD * SCALE
    for label, lines in zip(labels, laid):
        draw.text((PAD * SCALE, y), label, font=font, fill=grey)
        for line in lines:
            for i, (kind, text, x) in enumerate(line):
                x0 = (PAD + LABEL_W) * SCALE + x
                color = {"plain": fg, "del": red, "ins": blue}[kind]
                draw.text((x0, y), text, font=font, fill=color)
                w = font.getlength(text.rstrip() if i == len(line) - 1 else text)
                if kind == "del":
                    ym = y + FONT_PX * SCALE * 0.55
                    draw.line([(x0, ym), (x0 + w, ym)], fill=red, width=SCALE)
                elif kind == "ins":
                    yb = y + FONT_PX * SCALE * 1.12
                    draw.line([(x0, yb), (x0 + w, yb)], fill=blue, width=SCALE)
            y += LEADING * SCALE
        y += ROW_GAP * SCALE
    image.save(out, optimize=True)


def load_font(explicit: str | None):
    for path, index in ([(explicit, 0)] if explicit else FONT_CANDIDATES):
        if path and Path(path).exists():
            return ImageFont.truetype(path, FONT_PX * SCALE, index=index)
    sys.exit("no CJK font found; pass --font /path/to/font.ttc")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=REPO / "docs")
    parser.add_argument("--font", help="TrueType/OpenType font with Japanese glyphs")
    args = parser.parse_args()
    font = load_font(args.font)

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        source = build_docx(workdir / "source.docx", [SENTENCE])
        rows = [segments(source), segments(run_docx_cli(source, workdir)), segments(run_ours(source, workdir))]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for lang, labels in LABELS.items():
        for name, theme in THEMES.items():
            out = args.out_dir / f"example-{lang}-{name}.png"
            render(rows, labels, theme, font, out)
            print(out)


if __name__ == "__main__":
    main()
