"""Accepted-view text of ``word/document.xml``, mapped back to its XML nodes.

The accepted view is the text with every tracked change accepted: ``<w:del>``
content is left out and ``<w:ins>`` content is kept, whoever the author.  This
is what the proofreader read, so ``old_text`` is matched against it.

Each :class:`Paragraph` has its ``text`` and a list of :class:`Piece` objects
mapping slices of that text to the XML nodes that produced them.  Non-text
inline content (tabs, breaks, drawings, fields, footnote marks) takes one
character, so offsets match what a reader sees and a match covering it can be
refused as ``spans_inline_object``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import lxml.etree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
XML_NS = "http://www.w3.org/XML/1998/namespace"

#: U+0000 is forbidden in XML 1.0, so it never occurs in document text and no
#: ``old_text`` can match across an inline object.
SENTINEL = "\x00"

#: Vertical alignments, as ``w:vertAlign/@w:val`` spells them.
SUPERSCRIPT = "superscript"
SUBSCRIPT = "subscript"
BASELINE = "baseline"


def q(tag: str) -> str:
    """Qualify a bare WordprocessingML tag name."""
    return f"{{{W}}}{tag}"


def locator(paragraph: int, start: int | None = None, end: int | None = None) -> str:
    """``p16`` for a paragraph, ``p16:99-100`` for the half-open span ``[99, 100)`` in it."""
    return f"p{paragraph}" if start is None else f"p{paragraph}:{start}-{end}"


def localname(elem) -> str:
    tag = elem.tag
    if not isinstance(tag, str):  # comment / PI
        return ""
    return tag.rsplit("}", 1)[-1]


def namespace(elem) -> str:
    tag = elem.tag
    if not isinstance(tag, str) or not tag.startswith("{"):
        return ""
    return tag[1:].split("}", 1)[0]


# --- classification tables ---------------------------------------------------

#: Revisions whose rejection removes their content.  An insertion of ours must
#: not sit inside one.  Author is not checked: a revision from an earlier run
#: under our own name can be rejected just the same.
REJECTABLE_CONTAINERS = ("ins", "moveTo")

#: Revisions whose rejection restores their content.  It is left out of the
#: accepted view.
REJECTED_CONTAINERS = ("del", "moveFrom")

#: Run-level containers that may be cut in two so a new ``w:ins`` can sit
#: outside an enclosing revision.  Each only wraps, so two halves read like the
#: original.  ``w:sdt`` (it carries an identity and a data binding) and fields
#: are not splittable; see :func:`unsplittable_ancestor`.
SPLITTABLE_CONTAINERS = (*REJECTABLE_CONTAINERS, "hyperlink", "smartTag", "bdo", "dir", "customXml")

#: Containers whose children are read as run-level content.
TRANSPARENT_CONTAINERS = (*SPLITTABLE_CONTAINERS, "fldSimple", "sdtContent")

#: Elements that occupy no visible position and carry no text.
IGNORED_ELEMENTS = {
    "pPr",
    "rPr",
    "proofErr",
    "bookmarkStart",
    "bookmarkEnd",
    "commentRangeStart",
    "commentRangeEnd",
    "permStart",
    "permEnd",
    "moveFromRangeStart",
    "moveFromRangeEnd",
    "moveToRangeStart",
    "moveToRangeEnd",
    "lastRenderedPageBreak",
    "annotationRef",
    "commentReference",
    "rPrChange",
    "pPrChange",
    "sectPr",
    "del",  # only reachable as a run child in malformed input
    "delText",
    "delInstrText",
}

#: Inline objects that read as an ordinary character.  They get that character
#: so ``old_text`` lines up with them, but stay object pieces, so a match that
#: covers one is refused instead of deleting a tab stop or a no-break hyphen.
OBJECT_CHARS = {
    "tab": "\t",
    "ptab": "\t",
    "noBreakHyphen": "-",
}

# Anything else inside a run (<w:br>, <w:drawing>, <w:pict>, <w:object>,
# <w:fldChar>, <w:instrText>, <w:sym>, <w:footnoteReference>, or foreign content
# such as <m:oMath>) becomes one SENTINEL character.


@dataclass
class Piece:
    """A contiguous slice of a paragraph's accepted text and its XML origin."""

    start: int
    end: int
    kind: str  # "text" | "object"
    run: ET._Element  # the enclosing <w:r>
    node: ET._Element  # the <w:t> (text) or the object element itself


def anchor_index(pos: int) -> int | None:
    """Index of the character an insertion at ``pos`` attaches to, or ``None``.

    An insertion goes after the character before it, whose run supplies the
    ``w:rPr``.  At the start of a paragraph there is none.  The planner (for
    alignment) and ``revisions.resolve_insert_point`` (for the run to clone)
    must agree on this, so both call it.
    """
    return pos - 1 if pos > 0 else None


def insertion_format(formats: list[str] | None, pos: int) -> str:
    """Vertical alignment an untagged pure insertion at ``pos`` takes.

    Inherited only when the characters on both sides agree; otherwise plain.
    Inserting into a superscript reference marker stays superscript, while a
    period appended after one stays plain.
    """
    if not formats:
        return BASELINE
    before = formats[pos - 1] if 0 < pos <= len(formats) else None
    after = formats[pos] if 0 <= pos < len(formats) else None
    return before if before is not None and before == after else BASELINE


def display_name(elem) -> str:
    """``prefix:localname`` as the document spells it, e.g. ``w:sdt``, ``mc:Choice``."""
    prefix = elem.prefix
    name = localname(elem)
    return f"{prefix}:{name}" if prefix else name


def unsplittable_ancestor(run) -> str | None:
    """Name of the container that stops ``run`` being lifted out of a revision.

    Lifting our ``w:ins`` out of an enclosing ``w:ins`` or ``w:moveTo`` means
    splitting every container in between.  Returns ``None`` when there is no
    enclosing revision or the whole path is splittable; otherwise the prefixed
    name of the first container that must not be cut.
    """
    node, blocker = run, None
    while True:
        parent = node.getparent()
        if parent is None:
            return None
        name = localname(parent)
        if name == "p":
            return None
        if name in REJECTABLE_CONTAINERS:
            if blocker is not None:
                return blocker
        elif name not in SPLITTABLE_CONTAINERS and blocker is None:
            blocker = display_name(parent)
        node = parent


@dataclass
class Paragraph:
    index: int
    element: ET._Element
    text: str
    pieces: list[Piece] = field(default_factory=list)
    #: One vertical alignment per character of ``text``.
    formats: list[str] = field(default_factory=list)

    def piece_at(self, offset: int) -> Piece | None:
        """The piece covering the character at ``offset``."""
        for piece in self.pieces:
            if piece.start <= offset < piece.end:
                return piece
        return None

    def pieces_overlapping(self, start: int, end: int) -> list[Piece]:
        return [p for p in self.pieces if p.start < end and start < p.end]

    def covers_object(self, start: int, end: int) -> bool:
        """True if ``[start, end)`` touches any non-text inline content."""
        return any(p.kind == "object" for p in self.pieces_overlapping(start, end))

    def insert_anchor_piece(self, pos: int) -> Piece | None:
        """The piece an insertion at ``pos`` hangs from, or ``None`` if empty."""
        if not self.pieces:
            return None
        index = anchor_index(pos)
        if index is None:
            return self.pieces[0]
        return self.piece_at(index) or self.pieces[-1]

    def insertion_blocker(self, pos: int) -> str | None:
        """Container that would trap an insertion at ``pos`` inside a revision."""
        piece = self.insert_anchor_piece(pos)
        return None if piece is None else unsplittable_ancestor(piece.run)


def insertion_blockers(hunks, paragraph_for) -> list[str]:
    """Sorted names of the containers that would trap any of the hunks' insertions.

    ``paragraph_for`` maps a hunk's paragraph index to the :class:`Paragraph`
    to check, which for an ``mc:Fallback`` copy is the copy.
    """
    names = {paragraph_for(h.paragraph).insertion_blocker(h.end) for h in hunks if h.insert_text}
    names.discard(None)
    return sorted(names)


def _is_fallback_content(elem) -> bool:
    """True if the element is inside an ``mc:Fallback`` branch.

    ``mc:AlternateContent`` states the same content in ``mc:Choice`` and
    ``mc:Fallback``.  Counting both would double the paragraphs inside.
    """
    for ancestor in elem.iterancestors():
        if ancestor.tag == f"{{{MC}}}Fallback":
            return True
    return False


def iter_paragraphs(root) -> list:
    """All ``w:p`` elements in document order, minus ``mc:Fallback`` duplicates."""
    return [p for p in root.iter(q("p")) if not _is_fallback_content(p)]


def run_format(run) -> str:
    """Vertical alignment set directly in a run's own ``w:rPr``.

    Alignment from a character or paragraph style is not resolved.  That would
    mean reading styles.xml, and a wrong guess would rewrite formatting nobody
    asked to change.
    """
    rpr = run.find(q("rPr"))
    if rpr is None:
        return BASELINE
    node = rpr.find(q("vertAlign"))
    if node is None:
        return BASELINE
    return node.get(q("val")) or BASELINE


def build_paragraph(element, index: int) -> Paragraph:
    chunks: list[str] = []
    pieces: list[Piece] = []
    formats: list[str] = []
    pos = 0

    def emit_text(text: str, run, node) -> None:
        nonlocal pos
        if not text:
            return
        fmt = run_format(run)
        chunks.append(text)
        pieces.append(Piece(pos, pos + len(text), "text", run, node))
        formats.extend([fmt] * len(text))
        pos += len(text)

    def emit_object(run, node, char: str = SENTINEL) -> None:
        nonlocal pos
        chunks.append(char)
        pieces.append(Piece(pos, pos + 1, "object", run, node))
        formats.append(run_format(run))
        pos += 1

    def walk_run(run) -> None:
        for child in run:
            name = localname(child)
            if not name:
                continue
            if namespace(child) != W:
                # e.g. <m:oMath> inside a run, or an mc:AlternateContent picture
                emit_object(run, child)
                continue
            if name == "t":
                emit_text(child.text or "", run, child)
            elif name in IGNORED_ELEMENTS:
                continue
            else:
                emit_object(run, child, OBJECT_CHARS.get(name, SENTINEL))

    def walk_container(container) -> None:
        for child in container:
            name = localname(child)
            if not name:
                continue
            if child.tag == f"{{{MC}}}AlternateContent":
                # Read the Choice branch only, or every branch if there is none.
                for branch in child:
                    if localname(branch) == "Choice":
                        walk_container(branch)
                        break
                else:
                    for branch in child:
                        walk_container(branch)
                continue
            if namespace(child) != W:
                continue
            if name in REJECTED_CONTAINERS:
                continue
            if name == "r":
                walk_run(child)
            elif name == "sdt":
                for sub in child:
                    if localname(sub) == "sdtContent":
                        walk_container(sub)
            elif name in TRANSPARENT_CONTAINERS:
                walk_container(child)

    walk_container(element)
    return Paragraph(
        index=index,
        element=element,
        text="".join(chunks),
        pieces=pieces,
        formats=formats,
    )


def build_paragraphs(root) -> list[Paragraph]:
    return [build_paragraph(p, i) for i, p in enumerate(iter_paragraphs(root))]


def fallback_mirrors(root, paragraphs: list[Paragraph]) -> dict[int, list]:
    """Map a paragraph index to the ``mc:Fallback`` paragraphs that copy it.

    A text box is often stored twice: a DrawingML version in ``mc:Choice`` and
    a VML version in ``mc:Fallback``.  Only ``Choice`` is read, so without this
    an edit would leave the fallback copy saying the old thing.  Paragraphs are
    paired by position, and only when both branches have the same number.
    """
    index_of = {id(p.element): p.index for p in paragraphs}
    mirrors: dict[int, list] = {}
    for alternate in root.iter(f"{{{MC}}}AlternateContent"):
        branches = {localname(child): child for child in alternate}
        choice, fallback = branches.get("Choice"), branches.get("Fallback")
        if choice is None or fallback is None:
            continue
        chosen = list(choice.iter(q("p")))
        spare = list(fallback.iter(q("p")))
        if len(chosen) != len(spare):
            continue
        for original, copy_of in zip(chosen, spare):
            index = index_of.get(id(original))
            if index is not None:
                mirrors.setdefault(index, []).append(copy_of)
    return mirrors

