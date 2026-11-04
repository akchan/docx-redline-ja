"""Checks run on the edited document before it may be written.

``reversible``
    Rejecting every revision by our author restores the paragraph text.  Both
    the source and the output are rejected, so revisions an earlier run left
    under the same name do not count against this one.
``insertions_independent``
    Rejecting every other revision, older ones under our name included, keeps
    the insertions this run wrote.  An insertion nested inside another
    ``w:ins`` or ``w:moveTo`` would disappear with it.
``other_revisions_preserved``
    The text held by other authors' revisions is unchanged.
``revisions_wellformed``
    The run added no revision markup fault that Word reports as damage.
"""

from __future__ import annotations

import copy
from collections import Counter

from docx_text import REJECTABLE_CONTAINERS, REJECTED_CONTAINERS, localname, q

#: Every revision element that holds run content.
REVISIONS = (*REJECTABLE_CONTAINERS, *REJECTED_CONTAINERS)

#: Every element that carries characters, whatever revision state it is in.
_TEXT_TAGS = ("t", "delText", "instrText", "delInstrText")


def reject(root, rejected):
    """A copy of ``root`` with every revision for which ``rejected(elem)`` holds rejected.

    A rejected ``w:ins`` / ``w:moveTo`` is removed with its content.  A rejected
    ``w:del`` / ``w:moveFrom`` is unwrapped, its ``w:delText`` back to ``w:t``.
    """
    root = copy.deepcopy(root)
    for tag in REVISIONS:
        for elem in list(root.iter(q(tag))):
            parent = elem.getparent()
            if parent is None or not rejected(elem):
                continue
            if tag in REJECTED_CONTAINERS:
                for text in elem.iter(q("delText")):
                    text.tag = q("t")
                index = parent.index(elem)
                for child in reversed(list(elem)):
                    parent.insert(index, child)
            parent.remove(elem)
    return root


def _paragraph_texts(root) -> list[str]:
    texts = []
    for paragraph in root.iter(q("p")):
        text = "".join(node.text or "" for node in paragraph.iter(q("t")))
        if text:
            texts.append(text)
    return texts


def is_reversible(source_root, output_root, author: str) -> bool:
    def ours(elem) -> bool:
        return elem.get(q("author")) == author

    return _paragraph_texts(reject(source_root, ours)) == _paragraph_texts(reject(output_root, ours))


def insertions_independent(root, created_ids) -> bool:
    """True if rejecting every revision but ``created_ids`` keeps all of them."""

    def created(elem) -> bool:
        return localname(elem) == "ins" and elem.get(q("id")) in created_ids

    def present(node) -> set[str]:
        return {ins.get(q("id")) for ins in node.iter(q("ins")) if created(ins)}

    return present(root) <= present(reject(root, lambda elem: not created(elem)))


def _owning_revision(node, author: str) -> tuple[str, str] | None:
    """``(kind, author)`` of the innermost revision around ``node`` that is not ours.

    Our own wrappers are skipped, so text inside ``their ins > our del`` still
    counts as their insertion.
    """
    for ancestor in node.iterancestors():
        name = localname(ancestor)
        if name == "p":
            return None
        if name in REVISIONS:
            owner = ancestor.get(q("author")) or ""
            if owner != author:
                return name, owner
    return None


def revision_fingerprint(root, author: str) -> dict[str, str]:
    """Text held by every revision not ours, keyed by ``kind|author``.

    Each character is counted once, under the innermost such revision, in
    document order.  The two changes we make inside another author's revision
    leave it unchanged: splitting their ``w:ins`` (the halves spell the same
    string) and nesting our ``w:del`` in it (``w:t`` becomes ``w:delText``).
    Losing a character, moving one across a revision boundary, or changing whose
    revision holds it all change the fingerprint.

    It does not see formatting changes inside their revision, their ``w:id`` /
    ``w:date``, or text reordered within one key.
    """
    out: dict[str, str] = {}
    for node in root.iter(*[q(tag) for tag in _TEXT_TAGS]):
        owner = _owning_revision(node, author)
        if owner is None:
            continue
        key = f"{owner[0]}|{owner[1]}"
        out[key] = out.get(key, "") + (node.text or "")
    return out


def revision_problems(root) -> Counter:
    """Faults in the revision markup that Word reports as damage or mishandles, by kind.

    - two revisions sharing a ``w:id``
    - a revision without ``w:author`` or ``w:date``
    - deleted text stored as ``w:t``, or ``w:delText`` outside a deletion

    Counts, not a yes/no, so the caller can subtract the faults the source
    already had.
    """
    problems: Counter = Counter()
    ids: Counter = Counter()
    for tag in REVISIONS:
        for elem in root.iter(q(tag)):
            if elem.getparent() is not None and localname(elem.getparent()) == "rPr":
                continue  # a revision mark on the paragraph or run properties
            ids[elem.get(q("id"))] += 1
            if not elem.get(q("author")):
                problems["revision without w:author"] += 1
            if not elem.get(q("date")):
                problems["revision without w:date"] += 1
    problems["duplicate revision w:id"] += sum(n - 1 for n in ids.values() if n > 1)

    deleted = tuple(q(tag) for tag in REJECTED_CONTAINERS)
    for node in root.iter(q("t")):
        if any(a.tag in deleted for a in node.iterancestors()):
            problems["w:t inside a deletion"] += 1
    for node in root.iter(q("delText")):
        if not any(a.tag in deleted for a in node.iterancestors()):
            problems["w:delText outside a deletion"] += 1
    return +problems


def added_revision_problems(source_root, output_root) -> list[str]:
    """Faults present in the output but not in the source, as readable lines."""
    added = revision_problems(output_root) - revision_problems(source_root)
    return [f"{count} x {kind}" for kind, count in sorted(added.items())]
