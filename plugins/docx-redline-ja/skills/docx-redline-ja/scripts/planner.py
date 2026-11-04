"""Match resolution, minimal-diff hunks and conflict detection.

The planner never touches XML.  It turns (paragraphs, records) into
:class:`Hunk` objects, which ``revisions.py`` writes as ``w:ins`` / ``w:del``.
Records arrive already validated by ``findings.py``.

Skip reasons reported in ``result.json``:

``invalid_record``          the record is not a usable old_text/new_text pair
``crosses_paragraph``       ``old_text`` contains a line break, so it can never sit
                            inside one paragraph
``no_change``               the document already matches ``new_text``, text
                            and formatting
``not_found``               no occurrence in the accepted view (or none
                            inside the record's ``scope``)
``ambiguous``               more than one occurrence and ``replace_all`` was not
                            set
``spans_inline_object``     a match covers a tab / break / image / field
``unsplittable_container``  the edit sits inside an existing ``w:ins`` /
                            ``w:moveTo`` (any author) behind a container that
                            must not be cut in two
``conflict``                two records edit overlapping text, or one record's
                            matches overlap each other

Diffing compares ``(character, vertical alignment)`` pairs, so turning ``mm3``
into ``mm<sup>3</sup>`` is an edit even though the characters are identical.
Untagged characters keep the alignment the document has at that place (see
:func:`resolve_target_formats`), so formatting changes only where a record
asks for it.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from docx_text import BASELINE, Paragraph, insertion_blockers, insertion_format, locator
from findings import InvalidRecord, Scope

#: Characters of context quoted on each side of a match.  Enough to tell two
#: occurrences of a short Japanese phrase apart.
CONTEXT_CHARS = 20

REASON_NO_CHANGE = "no_change"
REASON_NOT_FOUND = "not_found"
REASON_AMBIGUOUS = "ambiguous"
REASON_SPANS_OBJECT = "spans_inline_object"
REASON_UNSPLITTABLE = "unsplittable_container"
REASON_CONFLICT = "conflict"


def position(paragraph: int, start: int, end: int) -> dict:
    """A span as the report spells it.  Hunks, occurrences and ``locate`` share it."""
    return {
        "paragraph": paragraph,
        "locator": locator(paragraph, start, end),
        "start": start,
        "end": end,
    }


@dataclass
class Hunk:
    """One minimal edit in ``paragraph``: delete ``[start, end)`` and insert ``insert_text``.

    ``insert_spans`` gives every inserted character its vertical alignment.
    """

    paragraph: int
    start: int
    end: int
    delete_text: str
    insert_text: str
    insert_spans: list[tuple[int, int, str]] = field(default_factory=list)

    def to_json(self) -> dict:
        out = {
            **position(self.paragraph, self.start, self.end),
            "delete": self.delete_text,
            "insert": self.insert_text,
        }
        if self.insert_spans:
            out["insert_format"] = [
                {"start": s, "end": e, "kind": k} for s, e, k in self.insert_spans
            ]
        return out


@dataclass
class RecordPlan:
    index: int
    old_text: str
    new_text: str
    replace_all: bool = False
    #: Paragraphs the record may touch, or ``None`` for the whole document.
    #: Uniqueness is decided inside the scope.
    scope: Scope | None = None
    #: Occurrences outside ``scope``, so "not found" can be told from
    #: "not found in scope".
    out_of_scope: int = 0
    #: Keys the record carried that this tool does not act on.
    ignored_keys: tuple[str, ...] = ()
    status: str = "applied"  # "applied" | "skipped"
    reason: str | None = None
    detail: str | None = None
    matches: int = 0
    hunks: list[Hunk] = field(default_factory=list)
    occurrences: list[dict] = field(default_factory=list)

    def skip(self, reason: str, detail: str | None = None) -> "RecordPlan":
        self.status = "skipped"
        self.reason = reason
        self.detail = detail
        self.hunks = []
        return self

    def to_json(self) -> dict:
        out: dict = {
            "index": self.index,
            "old_text": self.old_text,
            "new_text": self.new_text,
            "status": self.status,
        }
        if self.replace_all:
            out["replace_all"] = True
        if self.scope is not None:
            out["scope"] = str(self.scope)
        if self.out_of_scope:
            out["out_of_scope"] = self.out_of_scope
        if self.ignored_keys:
            out["ignored_keys"] = list(self.ignored_keys)
        if self.reason:
            out["reason"] = self.reason
        if self.detail:
            out["detail"] = self.detail
        out["matches"] = self.matches
        if self.hunks:
            out["hunks"] = [h.to_json() for h in self.hunks]
        if self.occurrences:
            out["occurrences"] = self.occurrences
        return out


# --- match resolution --------------------------------------------------------


def find_occurrences(paragraphs: list[Paragraph], needle: str) -> list[tuple[int, int]]:
    """Every occurrence as ``(paragraph_index, start)``, overlaps included.

    ``aa`` occurs twice in ``aaa``.  Counting only non-overlapping hits would
    let such an ``old_text`` pass as unique.
    """
    found = []
    for para in paragraphs:
        start = 0
        while True:
            hit = para.text.find(needle, start)
            if hit < 0:
                break
            found.append((para.index, hit))
            start = hit + 1
    return found


def occurrence_entry(para: Paragraph, start: int, end: int) -> dict:
    """Where a match is, plus the match and its surrounding text."""
    entry = position(para.index, start, end)
    entry.update(
        before=para.text[max(0, start - CONTEXT_CHARS) : start],
        match=para.text[start:end],
        after=para.text[end : end + CONTEXT_CHARS],
    )
    return entry


def resolve_target_formats(
    formats: list[str],
    offset: int,
    old_text: str,
    new_visible: str,
    tagged: list[str | None],
) -> list[str]:
    """Give every character of ``new_visible`` a concrete vertical alignment.

    ``formats`` is the whole paragraph's per-character alignment and ``offset``
    is where ``old_text`` starts in it, so an insertion can look past the edges
    of the match.

    A ``<sup>``/``<sub>``/``<plain>`` tag always wins.  An untagged character
    is placed by a character-level diff of ``old_text`` against ``new_visible``:

    * ``equal``: the alignment of the character it lines up with.
    * ``replace``: the alignment of the replaced stretch if it is uniform,
      otherwise plain.  So ``）`` -> ``)`` inside a superscript reference marker
      stays superscript, while rewriting mixed text does not spread the first
      character's alignment over the whole replacement.
    * ``insert``: :func:`docx_text.insertion_format`, which inherits only
      between two characters that already agree.
    """
    source_formats = formats[offset : offset + len(old_text)]
    resolved: list[str] = [BASELINE] * len(new_visible)

    alignment = difflib.SequenceMatcher(None, old_text, new_visible, autojunk=False)
    for op, i1, i2, j1, j2 in alignment.get_opcodes():
        if op == "equal":
            for step in range(j2 - j1):
                resolved[j1 + step] = source_formats[i1 + step]
        elif op == "replace":
            covered = source_formats[i1:i2]
            uniform = covered[0] if covered and len(set(covered)) == 1 else BASELINE
            for target in range(j1, j2):
                resolved[target] = uniform
        elif op == "insert":
            inherited = insertion_format(formats, offset + i1)
            for target in range(j1, j2):
                resolved[target] = inherited

    for target, kind in enumerate(tagged):
        if kind is not None:
            resolved[target] = kind
    return resolved


def explicit_formats(length: int, spans) -> list[str | None]:
    """Per-character alignment a tag asks for, or ``None`` where ``new_text`` has no tag."""
    out: list[str | None] = [None] * length
    for start, end, kind in spans:
        out[start:end] = [kind] * (end - start)
    return out


def spans_from_formats(formats: list[str]) -> list[tuple[int, int, str]]:
    """Collapse a per-character alignment list into ``(start, end, kind)`` runs."""
    spans: list[tuple[int, int, str]] = []
    for index, kind in enumerate(formats):
        if spans and spans[-1][2] == kind:
            spans[-1] = (spans[-1][0], index + 1, kind)
        else:
            spans.append((index, index + 1, kind))
    return spans


def make_hunks(
    paragraph: int,
    offset: int,
    old_text: str,
    new_visible: str,
    spans: list[tuple[int, int, str]],
    formats: list[str],
) -> list[Hunk]:
    """Reduce an old_text/new_text pair to the smallest set of edits.

    ``paragraph`` is the paragraph's index and ``formats`` its per-character
    alignment.  The diff runs over ``(character, vertical alignment)`` pairs,
    so a one-character change shows up in Word as one changed character.
    ``autojunk=False`` matters: with it on, difflib treats characters that are
    frequent in Japanese text as junk and the hunks stop being minimal.
    """
    tagged = explicit_formats(len(new_visible), spans)
    target_formats = resolve_target_formats(formats, offset, old_text, new_visible, tagged)
    source_formats = formats[offset : offset + len(old_text)]

    source_seq = list(zip(old_text, source_formats))
    target_seq = list(zip(new_visible, target_formats))

    matcher = difflib.SequenceMatcher(None, source_seq, target_seq, autojunk=False)
    hunks = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        hunks.append(
            Hunk(
                paragraph=paragraph,
                start=offset + i1,
                end=offset + i2,
                delete_text=old_text[i1:i2],
                insert_text=new_visible[j1:j2],
                insert_spans=spans_from_formats(target_formats[j1:j2]),
            )
        )
    return hunks


# --- conflict detection ------------------------------------------------------


def _overlaps(a: Hunk, b: Hunk) -> bool:
    """True if the two hunks compete for the same characters.

    Two replacements that only touch (``[4,5)`` and ``[5,6)``) share no
    character and do not conflict.  Counting them would skip most edits in a
    manuscript full of one-character fixes (full-width digits, ``，``/``、``).

    A pure insertion is the interval ``[p, p]``.  Whether it lands before or
    after an edit that starts or ends at ``p`` depends on order, so for an
    insertion touching counts as overlap.
    """
    if a.paragraph != b.paragraph:
        return False
    if a.start == a.end or b.start == b.end:
        return a.start <= b.end and b.start <= a.end
    return a.start < b.end and b.start < a.end


def detect_conflicts(plans: list[RecordPlan]) -> None:
    """Mark every record involved in an overlap as ``conflict``; none wins.

    Only hunks of different records are compared.  The hunks of one record are
    applied right-to-left in a fixed order, and a record whose matches overlap
    each other is caught earlier by :func:`overlapping_matches`.
    """
    live = [p for p in plans if p.status == "applied"]
    conflicted: dict[int, set[int]] = {}
    for i, first in enumerate(live):
        for second in live[i + 1 :]:
            if any(_overlaps(x, y) for x in first.hunks for y in second.hunks):
                conflicted.setdefault(first.index, set()).add(second.index)
                conflicted.setdefault(second.index, set()).add(first.index)
    for plan in live:
        others = conflicted.get(plan.index)
        if others:
            plan.skip(
                REASON_CONFLICT,
                "overlaps record(s) " + ", ".join(str(i) for i in sorted(others)),
            )


def overlapping_matches(occurrences: list[tuple[int, int]], length: int) -> bool:
    """True if two occurrences of ``old_text`` share a character.

    ``aa`` matches ``aaa`` twice.  With ``replace_all`` the second edit would
    land on text the first one rewrote, so the record is refused.  Adjacent
    occurrences are fine.
    """
    for (first_para, first_start), (next_para, next_start) in zip(
        occurrences, occurrences[1:]
    ):
        if first_para == next_para and next_start - first_start < length:
            return True
    return False


# --- entry point -------------------------------------------------------------


def plan_records(paragraphs: list[Paragraph], records) -> list[RecordPlan]:
    """Turn validated findings records into an order-independent edit plan."""
    plans: list[RecordPlan] = []
    by_index = {p.index: p for p in paragraphs}

    def quote(found, length: int) -> list[dict]:
        return [occurrence_entry(by_index[pi], st, st + length) for pi, st in found]

    for index, record in enumerate(records):
        plan = RecordPlan(
            index=index,
            old_text=record.old_text,
            new_text=record.new_text,
            ignored_keys=record.ignored_keys,
        )
        plans.append(plan)
        if isinstance(record, InvalidRecord):
            plan.skip(record.reason, record.detail)
            continue
        plan.replace_all = record.replace_all
        plan.scope = record.scope
        new_visible, spans = record.visible, record.spans
        length = len(plan.old_text)
        occurrences = find_occurrences(paragraphs, plan.old_text)

        if plan.scope is not None:
            in_scope = [(pi, st) for pi, st in occurrences if plan.scope.contains(pi)]
            plan.out_of_scope = len(occurrences) - len(in_scope)
            occurrences = in_scope

        plan.matches = len(occurrences)

        if not occurrences:
            plan.skip(
                REASON_NOT_FOUND,
                f"{plan.out_of_scope} occurrence(s) in the document, none inside "
                f"scope {plan.scope}"
                if plan.out_of_scope
                else "no occurrence in the accepted-view text",
            )
            continue

        if len(occurrences) > 1 and not plan.replace_all:
            plan.occurrences = quote(occurrences, length)
            where = f" inside scope {plan.scope}" if plan.scope is not None else ""
            plan.skip(
                REASON_AMBIGUOUS,
                f"{len(occurrences)} occurrences{where}; extend 'old_text' with "
                "surrounding text to make it unique, narrow 'scope', or set "
                '"replace_all": true',
            )
            continue

        if overlapping_matches(occurrences, length):
            plan.occurrences = quote(occurrences, length)
            plan.skip(
                REASON_CONFLICT,
                f"the {len(occurrences)} matches of 'old_text' overlap each other; "
                "extend 'old_text' so its occurrences are disjoint",
            )
            continue

        blocked = [
            (pi, st) for pi, st in occurrences if by_index[pi].covers_object(st, st + length)
        ]
        if blocked:
            plan.occurrences = quote(blocked, length)
            plan.skip(
                REASON_SPANS_OBJECT,
                "match spans a tab, line break, image, field or footnote mark",
            )
            continue

        for pi, st in occurrences:
            para = by_index[pi]
            plan.hunks.extend(
                make_hunks(pi, st, plan.old_text, new_visible, spans, para.formats)
            )

        blockers = insertion_blockers(plan.hunks, by_index.__getitem__)
        if blockers:
            plan.occurrences = quote(occurrences, length)
            plan.skip(
                REASON_UNSPLITTABLE,
                "the insertion point sits inside an existing revision (w:ins or "
                "w:moveTo, by any author -- an earlier revision under our own "
                "author name is just as rejectable) behind "
                + ", ".join(blockers)
                + ", which cannot be split without changing the document structure",
            )
            continue

        if not plan.hunks:
            plan.occurrences = quote(occurrences, length)
            plan.skip(
                REASON_NO_CHANGE,
                "the text and its formatting already match 'new_text'",
            )

    detect_conflicts(plans)
    return plans
