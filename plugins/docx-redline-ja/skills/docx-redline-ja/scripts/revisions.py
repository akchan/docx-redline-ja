"""Turning planner hunks into ``w:ins`` / ``w:del`` markup.

Two rules:

1. An insertion leaves its neighbors alone.  The ``<w:ins>`` is spliced in as
   a sibling, and the runs around it keep their text.  If the insertion point
   sits inside an existing ``w:ins`` or ``w:moveTo``, that revision is split so
   ours lands beside it: rejecting their revision must not take ours with it.
2. A deletion stays where its text is.  Text inside another author's
   ``<w:ins>`` gets our ``<w:del>`` nested inside that ``<w:ins>``, which is
   how Word records rejecting part of someone else's insertion.

Hunks are applied right-to-left within a paragraph so offsets computed against
the original accepted text stay valid.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass, field

import lxml.etree as ET

from docx_text import (
    BASELINE,
    REJECTABLE_CONTAINERS,
    SPLITTABLE_CONTAINERS,
    XML_NS,
    Paragraph,
    anchor_index,
    build_paragraph,
    insertion_blockers,
    localname,
    locator,
    q,
)

#: Tail of the ``CT_RPr`` sequence; ``w:vertAlign`` must precede these.
_RPR_TAIL = ("rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath", "rPrChange")

_SPACE_CHARS = " \t\n\r"


@dataclass
class RevisionContext:
    """Author, timestamp and the running ``w:id`` counter for new revisions."""

    author: str
    date: str
    next_id_value: int = 1
    created: list[ET._Element] = field(default_factory=list)

    def next_id(self) -> str:
        value = self.next_id_value
        self.next_id_value += 1
        return str(value)

    def stamp(self, element) -> ET._Element:
        element.set(q("id"), self.next_id())
        element.set(q("author"), self.author)
        element.set(q("date"), self.date)
        self.created.append(element)
        return element

    def created_insertion_ids(self) -> set[str]:
        """``w:id`` of the ``w:ins`` elements stamped during this run."""
        return {elem.get(q("id")) for elem in self.created if localname(elem) == "ins"}


def max_revision_id(root) -> int:
    """Highest numeric ``w:id`` already used by a revision element."""
    highest = 0
    id_attr = q("id")
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        value = elem.get(id_attr)
        if value is None:
            continue
        try:
            highest = max(highest, int(value))
        except ValueError:
            continue
    return highest


def make_context(root, author: str, date: str) -> RevisionContext:
    return RevisionContext(author=author, date=date, next_id_value=max_revision_id(root) + 1)


# --- low-level element helpers ----------------------------------------------


def _rpr(run):
    return run.find(q("rPr"))


def _content_children(run) -> list:
    return [child for child in run if localname(child) != "rPr"]


def _preserve_space(node) -> None:
    text = node.text or ""
    if text and (text[0] in _SPACE_CHARS or text[-1] in _SPACE_CHARS):
        node.set(f"{{{XML_NS}}}space", "preserve")


def _new_text_like(template, text: str):
    node = ET.Element(template.tag)
    for name, value in template.attrib.items():
        node.set(name, value)
    node.text = text
    _preserve_space(node)
    return node


def split_run(run, child, char_offset: int | None):
    """Split ``run`` just before ``child``, or ``char_offset`` characters into it.

    Returns ``(left, right)``.  ``left`` is the original ``run`` whenever
    anything remains on the left, so callers' references stay valid.  Either
    side is ``None`` when the split point is at a run boundary.
    """
    kids = _content_children(run)
    index = kids.index(child)
    left_kids = kids[:index]
    right_kids = kids[index + 1 :]

    if char_offset is None:
        right_kids = [child, *right_kids]
    else:
        text = child.text or ""
        if char_offset <= 0:
            right_kids = [child, *right_kids]
        elif char_offset >= len(text):
            left_kids = [*left_kids, child]
        else:
            tail = _new_text_like(child, text[char_offset:])
            child.text = text[:char_offset]
            _preserve_space(child)
            left_kids = [*left_kids, child]
            right_kids = [tail, *right_kids]

    if not left_kids:
        return None, run
    if not right_kids:
        return run, None

    right = ET.Element(run.tag)
    for name, value in run.attrib.items():
        right.set(name, value)
    rpr = _rpr(run)
    if rpr is not None:
        right.append(copy.deepcopy(rpr))
    for kid in right_kids:
        right.append(kid)  # appending an in-tree element moves it

    parent = run.getparent()
    parent.insert(parent.index(run) + 1, right)
    return run, right


def _clone_shell(container, ctx: RevisionContext):
    """An empty copy of ``container`` with a fresh ``w:id``.

    Leading property children such as ``w:smartTagPr`` are copied too, since
    they describe the container and both halves need them.
    """
    shell = ET.Element(container.tag)
    for name, value in container.attrib.items():
        shell.set(name, value)
    if container.get(q("id")) is not None:
        shell.set(q("id"), ctx.next_id())
    for child in container:
        if not localname(child).endswith("Pr"):
            break
        shell.append(copy.deepcopy(child))
    return shell


def _inside_revision(elem) -> bool:
    """True if ``elem`` is, or sits inside, a ``w:ins`` or ``w:moveTo`` in its paragraph."""
    node = elem
    while node is not None and localname(node) != "p":
        if localname(node) in REJECTABLE_CONTAINERS:
            return True
        node = node.getparent()
    return False


def _liftable(parent) -> bool:
    """True if a new sibling of a child of ``parent`` still has to move outward.

    A hyperlink or smart tag with no revision around it is left whole, and the
    insertion goes inside it.
    """
    return localname(parent) in SPLITTABLE_CONTAINERS and _inside_revision(parent)


def position_after(elem, ctx: RevisionContext) -> tuple[ET._Element, int]:
    """Where to splice a new sibling so it reads immediately after ``elem``.

    Every splittable container up to and including the outermost enclosing
    ``w:ins`` / ``w:moveTo`` is cut in two, so our insertion ends up beside that
    revision rather than inside it.
    """
    while True:
        parent = elem.getparent()
        if parent is None:
            raise ValueError("element is detached from the document")
        if not _liftable(parent):
            return parent, parent.index(elem) + 1

        rest = list(parent)[parent.index(elem) + 1 :]
        if rest:
            shell = _clone_shell(parent, ctx)
            for node in rest:
                shell.append(node)
            grandparent = parent.getparent()
            grandparent.insert(grandparent.index(parent) + 1, shell)
        elem = parent


def position_before(elem, ctx: RevisionContext) -> tuple[ET._Element, int]:
    """Where to splice a new sibling so it reads immediately before ``elem``."""
    while True:
        parent = elem.getparent()
        if parent is None:
            raise ValueError("element is detached from the document")
        if not _liftable(parent):
            return parent, parent.index(elem)

        index = parent.index(elem)
        leading = sum(1 for child in parent if localname(child).endswith("Pr"))
        if index <= leading:
            elem = parent
            continue
        shell = _clone_shell(parent, ctx)
        for node in list(parent)[index:]:
            shell.append(node)
        grandparent = parent.getparent()
        grandparent.insert(grandparent.index(parent) + 1, shell)
        elem = shell


def _set_vert_align(rpr, kind: str | None) -> None:
    """Set the alignment the planner resolved on an ``rPr`` cloned from the anchor run.

    ``None`` and ``"baseline"`` both mean plain, written as no ``w:vertAlign``,
    as Word does.  The existing element is always removed first, so plain text
    inserted next to a superscript marker does not inherit it.
    """
    for existing in rpr.findall(q("vertAlign")):
        rpr.remove(existing)
    if not kind or kind == BASELINE:
        return
    node = ET.Element(q("vertAlign"))
    node.set(q("val"), kind)
    index = len(rpr)
    for position, child in enumerate(rpr):
        if localname(child) in _RPR_TAIL:
            index = position
            break
    rpr.insert(index, node)


def make_ins(text: str, spans, rpr_template, ctx: RevisionContext):
    """Build a ``<w:ins>`` holding ``text``, one run per ``(start, end, kind)`` span.

    The planner's spans cover every character of ``text``.
    """
    ins = ctx.stamp(ET.Element(q("ins")))
    for start, end, kind in spans:
        chunk = text[start:end]
        run = ET.Element(q("r"))
        rpr = copy.deepcopy(rpr_template) if rpr_template is not None else ET.Element(q("rPr"))
        # A tracked formatting change belongs to the run it was made on, not
        # to text we insert next to it.
        for mark in rpr.findall(q("rPrChange")):
            rpr.remove(mark)
        _set_vert_align(rpr, kind)
        if len(rpr):
            run.append(rpr)
        node = ET.SubElement(run, q("t"))
        node.text = chunk
        _preserve_space(node)
        ins.append(run)
    return ins


def wrap_in_del(run, ctx: RevisionContext):
    """Move ``run`` into a fresh ``<w:del>`` at the same position."""
    parent = run.getparent()
    marker = ctx.stamp(ET.Element(q("del")))
    parent.insert(parent.index(run), marker)
    marker.append(run)
    for node in run.iter(q("t")):
        node.tag = q("delText")
        _preserve_space(node)
    for node in run.iter(q("instrText")):
        node.tag = q("delInstrText")
    return marker


# --- hunk application --------------------------------------------------------


def resolve_insert_point(para: Paragraph, pos: int, ctx: RevisionContext):
    """``(parent, index, rpr_template)`` for an insertion at ``pos``.

    The anchor run is the one :func:`docx_text.anchor_index` names, the same
    character the planner took the alignment from.  At the start of a
    paragraph the following run supplies the ``w:rPr``.
    """
    piece = para.insert_anchor_piece(pos)
    if anchor_index(pos) is None:
        parent, index = position_before(piece.run, ctx)
        return parent, index, _rpr(piece.run)

    if piece.kind == "text":
        # pos - piece.start is in [1, len]: split inside the text or right after it.
        split_run(piece.run, piece.node, pos - piece.start)
    else:
        kids = _content_children(piece.run)
        following = kids.index(piece.node) + 1
        if following < len(kids):
            split_run(piece.run, kids[following], None)
    parent, index = position_after(piece.run, ctx)
    return parent, index, _rpr(piece.run)


def delete_range(para: Paragraph, start: int, end: int, ctx: RevisionContext) -> None:
    """Wrap the accepted-view range ``[start, end)`` in ``<w:del>``."""
    pieces = para.pieces_overlapping(start, end)
    for piece in reversed(pieces):
        if piece.kind != "text":
            raise ValueError(
                "refusing to delete an inline object; the planner should have "
                "reported spans_inline_object"
            )
        local_start = max(start, piece.start) - piece.start
        local_end = min(end, piece.end) - piece.start

        left, _ = split_run(piece.run, piece.node, local_end)
        target = left if left is not None else piece.run
        _, middle = split_run(target, piece.node, local_start)
        if middle is None:  # pragma: no cover - local_start < local_end guarantees content
            raise ValueError("empty deletion slice")
        wrap_in_del(middle, ctx)


def apply_hunk(para: Paragraph, hunk, ctx: RevisionContext) -> None:
    """Apply one hunk: the ``<w:del>`` is emitted first, the ``<w:ins>`` after it."""
    if hunk.insert_text:
        parent, index, rpr_template = resolve_insert_point(para, hunk.end, ctx)
        parent.insert(index, make_ins(hunk.insert_text, hunk.insert_spans, rpr_template, ctx))
    if hunk.end > hunk.start:
        delete_range(para, hunk.start, hunk.end, ctx)


def apply_plan(
    paragraphs: list[Paragraph],
    plans,
    ctx: RevisionContext,
    mirrors: dict[int, list],
) -> tuple[int, list[str]]:
    """Apply every hunk of every applied record.

    ``mirrors`` maps a paragraph index to its ``mc:Fallback`` copies (see
    :func:`docx_text.fallback_mirrors`).  Each copy gets the same tracked
    change, unless its text differs from the paragraph, in which case it is
    left alone and named in the returned notes.

    Returns ``(hunk_count, notes)``.  Mirrored hunks are not counted.
    """
    by_index = {p.index: p for p in paragraphs}
    grouped: dict[int, list] = defaultdict(list)
    for plan in plans:
        if plan.status != "applied":
            continue
        for hunk in plan.hunks:
            grouped[hunk.paragraph].append(hunk)

    count = 0
    notes: list[str] = []
    for paragraph_index, hunks in grouped.items():
        para = by_index[paragraph_index]
        original_text = para.text
        ordered = sorted(hunks, key=lambda h: (h.start, h.end), reverse=True)
        for hunk in ordered:
            apply_hunk(para, hunk, ctx)
            count += 1
        for element in mirrors.get(paragraph_index, []):
            mirror = build_paragraph(element, paragraph_index)
            if mirror.text != original_text:
                notes.append(
                    f"{locator(paragraph_index)}: the mc:Fallback copy reads "
                    f"{mirror.text!r} rather than {original_text!r} and was left "
                    "unchanged"
                )
                continue
            # The copy can be built differently from the paragraph the planner
            # checked, so an insertion point that is fine there may be trapped
            # inside an existing revision here.
            blockers = insertion_blockers(ordered, lambda _: mirror)
            if blockers:
                notes.append(
                    f"{locator(paragraph_index)}: the mc:Fallback copy was left "
                    "unchanged because an insertion would sit inside an existing "
                    "revision behind " + ", ".join(blockers)
                )
                continue
            for hunk in ordered:
                apply_hunk(mirror, hunk, ctx)
    return count, notes
