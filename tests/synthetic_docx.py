from __future__ import annotations

import zipfile
from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import escape

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

PUBLIC_DOMAIN_TEXT = {
    "ja": ("吾輩は猫である。名前はまだ無い。", "どこで生れたかとんと見当がつかぬ。"),
    "en": (
        "Alice was beginning to get very tired of sitting by her sister on the bank.",
        "Once or twice she had peeped into the book her sister was reading.",
    ),
}


def build_docx(path: Path, paragraphs: Sequence[str]) -> Path:
    """Create a minimal DOCX whose body is one ``w:p`` per given paragraph.

    The package is deliberately small (four parts, no styles, no settings) so a
    test or benchmark reads only what it put in.  Callers own the text; see
    :func:`build_public_domain_docx` for the attributed excerpts the test suite
    uses.
    """
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    root_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    document_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>"""
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>' for text in paragraphs
    )
    document = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{W}"><w:body>{body}<w:sectPr/></w:body></w:document>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", root_rels)
        package.writestr("word/_rels/document.xml.rels", document_rels)
        package.writestr("word/document.xml", document)
    return path


def build_public_domain_docx(path: Path, language: str) -> Path:
    """Create a DOCX from a short, attributed public-domain excerpt.

    Sources and their redistribution terms are recorded in ``TEST_DATA.md``.
    """
    return build_docx(path, PUBLIC_DOMAIN_TEXT[language])
