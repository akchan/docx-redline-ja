#!/usr/bin/env python3
"""Remove personal metadata from the Word-made test fixtures.

    python3 tools/strip_docx_metadata.py tests/fixtures/*.docx
    python3 tools/strip_docx_metadata.py --check tests/fixtures/*.docx

Word records who made a document and when, in more places than one would look.
This blanks the creator and last editor, fixes every timestamp to one date,
zeroes the editing time and drops the document IDs. Revision and comment
authors must already be the placeholder below: they are part of what the
fixtures test, so they are checked, not rewritten.

``--check`` changes nothing and exits 1 if any file still needs stripping. The
test suite runs it.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

from lxml import etree as ET

PLACEHOLDER_AUTHOR = "Reviewer A"
FIXED_DATE = "2026-01-01T00:00:00Z"
PARSER = ET.XMLParser(resolve_entities=False, no_network=True)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "w": W,
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
}


def _set_text(root, path: str, value: str) -> None:
    for node in root.xpath(path, namespaces=NS):
        node.text = value or None


def _strip_part(name: str, root) -> None:
    if name == "docProps/core.xml":
        _set_text(root, "dc:creator | cp:lastModifiedBy", "")
        _set_text(root, "cp:revision", "1")
        _set_text(root, "dcterms:created | dcterms:modified", FIXED_DATE)
    elif name == "docProps/app.xml":
        _set_text(root, "ep:TotalTime", "0")
        _set_text(root, "ep:Company | ep:Manager", "")
    elif name == "word/settings.xml":
        for node in root.xpath("w14:docId | w15:docId", namespaces=NS):
            node.getparent().remove(node)
    # w:date, w16cex:dateUtc, w16du:dateUtc and whatever Word adds next.
    for node in root.iter():
        for attr in node.attrib:
            if ET.QName(attr).localname in ("date", "dateUtc"):
                node.set(attr, FIXED_DATE)


def _foreign_authors(root) -> set[str]:
    names = {node.get(f"{{{W}}}author") for node in root.iter() if node.get(f"{{{W}}}author")}
    names |= {node.get(f"{{{NS['w15']}}}author") for node in root.iter() if node.get(f"{{{NS['w15']}}}author")}
    names |= {node.get(f"{{{NS['w15']}}}userId") for node in root.iter() if node.get(f"{{{NS['w15']}}}userId")}
    return names - {PLACEHOLDER_AUTHOR}


def strip(path: Path, check: bool) -> list[str]:
    """Strip ``path`` in place, or with ``check`` only report what would change."""
    problems: list[str] = []
    rewritten: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        contents = {info.filename: archive.read(info) for info in infos}
    for name, data in contents.items():
        if not name.endswith(".xml"):
            continue
        root = ET.fromstring(data, PARSER)
        for author in sorted(_foreign_authors(root)):
            problems.append(f"{path.name}: {name} names {author!r}; remake it under {PLACEHOLDER_AUTHOR!r}")
        before = ET.tostring(root)
        _strip_part(name, root)
        if ET.tostring(root) != before:
            problems.append(f"{path.name}: {name} carries metadata")
            rewritten[name] = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    if rewritten and not check:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for info in infos:
                archive.writestr(info, rewritten.get(info.filename, contents[info.filename]))
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--check", action="store_true", help="report only; exit 1 if anything needs stripping")
    args = parser.parse_args(argv)
    problems = [line for path in args.files for line in strip(path, args.check)]
    for line in problems:
        print(line, file=sys.stderr)
    if args.check:
        return 1 if problems else 0
    return 1 if any("remake it" in line for line in problems) else 0


if __name__ == "__main__":
    raise SystemExit(main())
