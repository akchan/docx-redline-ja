"""Independent measurement of a redlined DOCX.

Deliberately does not reuse either tool's own validator: an arm should not be
graded by code it ships.  Everything here reads ``word/document.xml`` directly
and reconstructs the two views Word shows a reviewer.

* **accepted view** — what the document says once every revision is accepted.
  ``w:ins`` text counts, ``w:del`` text does not.
* **rejected view** — what it says once every revision is rejected.  The reverse.

A redline is *correct* when the accepted view equals the intended text and the
rejected view equals the original.  It is *minimal* when the characters inside
``w:ins`` and ``w:del`` are no more than the intended edit required.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}

#: Elements whose text is present only in the accepted view.
INSERT_LIKE = frozenset({f"{{{W}}}ins", f"{{{W}}}moveTo"})
#: Elements whose text is present only in the rejected view.
DELETE_LIKE = frozenset({f"{{{W}}}del", f"{{{W}}}moveFrom"})
#: Run-level elements that carry visible characters.
TEXT_TAGS = frozenset({f"{{{W}}}t", f"{{{W}}}delText"})


@dataclass(frozen=True)
class Measurement:
    """What one output document looks like, independent of how it was made."""

    accepted_text: list[str]
    rejected_text: list[str]
    inserted_chars: int
    deleted_chars: int
    insertions: int
    deletions: int
    paragraphs: int

    @property
    def revision_chars(self) -> int:
        """Total characters a human reviewer sees marked up."""
        return self.inserted_chars + self.deleted_chars

    @property
    def revisions(self) -> int:
        return self.insertions + self.deletions


def document_root(path: Path):
    with zipfile.ZipFile(path) as package:
        return etree.fromstring(package.read("word/document.xml"))


def _revision_state(node) -> str | None:
    """``"ins"``, ``"del"`` or ``None`` for the nearest revision ancestor."""
    current = node
    while current is not None:
        if current.tag in INSERT_LIKE:
            return "ins"
        if current.tag in DELETE_LIKE:
            return "del"
        current = current.getparent()
    return None


def measure(path: Path) -> Measurement:
    root = document_root(path)
    accepted: list[str] = []
    rejected: list[str] = []
    inserted_chars = deleted_chars = 0

    body_paragraphs = root.xpath(".//w:p", namespaces=NS)
    for paragraph in body_paragraphs:
        accepted_parts: list[str] = []
        rejected_parts: list[str] = []
        for node in paragraph.iter():
            if node.tag not in TEXT_TAGS:
                continue
            text = node.text or ""
            state = _revision_state(node)
            if state == "ins":
                accepted_parts.append(text)
                inserted_chars += len(text)
            elif state == "del":
                rejected_parts.append(text)
                deleted_chars += len(text)
            else:
                accepted_parts.append(text)
                rejected_parts.append(text)
        accepted.append("".join(accepted_parts))
        rejected.append("".join(rejected_parts))

    return Measurement(
        accepted_text=accepted,
        rejected_text=rejected,
        inserted_chars=inserted_chars,
        deleted_chars=deleted_chars,
        insertions=len(root.xpath(".//w:ins", namespaces=NS)),
        deletions=len(root.xpath(".//w:del", namespaces=NS)),
        paragraphs=len(body_paragraphs),
    )


def plain_paragraphs(path: Path) -> list[str]:
    """Paragraph text of a document that has no revisions in it."""
    return measure(path).accepted_text


def opens_as_a_package(path: Path) -> bool:
    """Whether the file is at least a readable OOXML package Word could open.

    Not a schema check — a cheap guard that an arm produced a real ``.docx``
    rather than a truncated or non-zip file.
    """
    try:
        with zipfile.ZipFile(path) as package:
            names = set(package.namelist())
            if not {"[Content_Types].xml", "word/document.xml"} <= names:
                return False
            etree.fromstring(package.read("word/document.xml"))
            if package.testzip() is not None:
                return False
    except (zipfile.BadZipFile, etree.XMLSyntaxError, KeyError, OSError):
        return False
    return True
