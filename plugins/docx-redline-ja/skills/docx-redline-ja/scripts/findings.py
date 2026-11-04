"""The findings JSONL contract: reading records, the ``scope`` grammar and inline tags.

``references/findings_schema.md`` describes the contract in prose; this module
implements it.  Everything that decides whether a line is usable lives here:
key names, types, the ban on line breaks and control characters, the
``scope`` grammar and the ``<sup>``/``<sub>``/``<plain>`` markup.

Only ``old_text``, ``new_text``, ``replace_all`` and ``scope`` are read.  The
names of any other keys are kept so the report can list them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import NamedTuple

from docx_text import BASELINE, SUBSCRIPT, SUPERSCRIPT

#: Every key the applier acts on.
KEYS = ("old_text", "new_text", "replace_all", "scope")

REASON_INVALID = "invalid_record"
REASON_CROSSES_PARAGRAPH = "crosses_paragraph"

#: Control characters not allowed in ``old_text``/``new_text``.  ``\n`` and
#: ``\r`` get the ``crosses_paragraph`` reason instead, and ``\t`` is allowed.
_FORBIDDEN_CONTROL = {chr(c) for c in range(0x20)} - {"\n", "\r", "\t"}


def _not_xml_char(ch: str) -> bool:
    """True for a character XML 1.0 cannot hold, other than C0 controls.

    Surrogates (a lone one survives ``json.loads``) and U+FFFE/U+FFFF.
    """
    code = ord(ch)
    return 0xD800 <= code <= 0xDFFF or code in (0xFFFE, 0xFFFF)


# --- scope -------------------------------------------------------------------


class ScopeError(ValueError):
    """A ``scope`` value that does not parse."""


#: ``p12``, ``p10-12`` or ``p120-`` (to the end of the document).
_RANGE = re.compile(r"\Ap(?P<first>\d+)(?:(?P<dash>-)(?P<last>\d+)?)?\Z")


class ParagraphRange(NamedTuple):
    """An inclusive run of paragraphs; ``last is None`` means "to the end"."""

    first: int
    last: int | None

    @classmethod
    def parse(cls, text: str) -> "ParagraphRange":
        match = _RANGE.match(text)
        if match is None:
            raise ScopeError(
                f"{text!r} is not a paragraph range; write e.g. \"p12\", "
                '"p10-12" or "p120-" (p120 to the end)'
            )
        first = int(match.group("first"))
        if match.group("dash") is None:
            last: int | None = first
        elif match.group("last") is None:
            last = None
        else:
            last = int(match.group("last"))
        if last is not None and last < first:
            raise ScopeError(f"{text!r} ends before it starts")
        return cls(first, last)

    def contains(self, paragraph: int) -> bool:
        return self.first <= paragraph and (self.last is None or paragraph <= self.last)

    @property
    def text(self) -> str:
        if self.last is None:
            return f"p{self.first}-"
        return f"p{self.first}" if self.last == self.first else f"p{self.first}-{self.last}"


@dataclass(frozen=True)
class Scope:
    """The paragraphs a record may touch, in the numbering ``locate`` reports.

    ``ranges`` is sorted, with touching or overlapping ranges merged, so two
    scopes that mean the same thing compare equal and print the same way.
    """

    ranges: tuple[ParagraphRange, ...]

    @staticmethod
    def _normalize(ranges) -> tuple[ParagraphRange, ...]:
        ordered = sorted(ranges, key=lambda r: (r[0], r[1] is None, r[1] or 0))
        merged: list[ParagraphRange] = []
        for start, end in ordered:
            if not merged:
                merged.append(ParagraphRange(start, end))
                continue
            last_start, last_end = merged[-1]
            if last_end is None:  # already runs to the end of the document
                continue
            if start > last_end + 1:  # a real gap: keep them apart
                merged.append(ParagraphRange(start, end))
                continue
            merged[-1] = ParagraphRange(last_start, None if end is None else max(last_end, end))
        return tuple(merged)

    @classmethod
    def parse(cls, text) -> "Scope":
        """``"p3, p10-12"`` -> :class:`Scope`.  Raises :class:`ScopeError`."""
        if not isinstance(text, str):
            raise ScopeError("'scope' must be a string such as \"p12\" or \"p10-12\"")
        tokens = [token.strip() for token in text.split(",")]
        if any(not token for token in tokens):
            raise ScopeError(
                f"{text!r} is not a paragraph range; write e.g. \"p12\", \"p10-12\", "
                '"p3,p10-12" or "p120-" (p120 to the end)'
            )
        return cls(cls._normalize(ParagraphRange.parse(token) for token in tokens))

    @classmethod
    def from_paragraphs(cls, indexes) -> "Scope":
        """The tightest scope covering ``indexes`` (consecutive runs collapsed)."""
        return cls(cls._normalize(ParagraphRange(index, index) for index in set(indexes)))

    def contains(self, paragraph: int) -> bool:
        return any(entry.contains(paragraph) for entry in self.ranges)

    def __str__(self) -> str:
        return ",".join(entry.text for entry in self.ranges)


# --- inline tags in new_text --------------------------------------------------
#
# ``new_text`` is visible text plus three optional tags.  Any other ``<`` is an
# ordinary character.  Untagged text inherits the alignment the document has
# there, so forgetting to re-tag a superscript inside a longer ``old_text``
# does not strip it; removing one takes ``<plain>``.

_TAG = re.compile(r"</?(sup|sub|plain)>")
_KIND = {"sup": SUPERSCRIPT, "sub": SUBSCRIPT, "plain": BASELINE}


def parse_inline_format(source: str) -> tuple[str, list[tuple[int, int, str]]]:
    """Split ``source`` into visible text and ``(start, end, kind)`` spans over it.

    Raises ``ValueError`` for nested, unclosed or unmatched tags.
    """
    parts: list[str] = []
    spans: list[tuple[int, int, str]] = []
    visible_len = 0
    open_tag: tuple[str, int] | None = None
    pos = 0

    for match in _TAG.finditer(source):
        chunk = source[pos : match.start()]
        parts.append(chunk)
        visible_len += len(chunk)
        pos = match.end()

        name = match.group(1)
        if match.group(0).startswith("</"):
            if open_tag is None or open_tag[0] != name:
                raise ValueError(f"unmatched closing tag </{name}>")
            if visible_len > open_tag[1]:
                spans.append((open_tag[1], visible_len, _KIND[name]))
            open_tag = None
        else:
            if open_tag is not None:
                raise ValueError(
                    f"<{name}> opened inside <{open_tag[0]}> (nesting is not supported)"
                )
            open_tag = (name, visible_len)

    if open_tag is not None:
        raise ValueError(f"unclosed tag <{open_tag[0]}>")
    parts.append(source[pos:])
    return "".join(parts), spans


# --- records -----------------------------------------------------------------


@dataclass(frozen=True)
class FindingRecord:
    """One usable old_text/new_text instruction.

    ``visible`` and ``spans`` are ``new_text`` with its tags parsed out.
    ``ignored_keys`` names the keys this tool does not act on, sorted.
    """

    old_text: str
    new_text: str
    visible: str
    spans: tuple[tuple[int, int, str], ...] = ()
    replace_all: bool = False
    scope: Scope | None = None
    ignored_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class InvalidRecord:
    """A line that cannot become a :class:`FindingRecord`, and why.

    ``old_text``/``new_text`` hold whatever the line had, rendered for display.
    """

    reason: str
    detail: str
    old_text: str = ""
    new_text: str = ""
    ignored_keys: tuple[str, ...] = ()


Record = FindingRecord | InvalidRecord


def _display(value) -> str:
    """Render a raw JSON value for the report without pretending it is text."""
    return value if isinstance(value, str) else repr(value)


def parse_record(obj, line: int | None = None) -> Record:
    """Validate one decoded JSON object against the contract."""
    prefix = f"line {line}: " if line else ""

    if not isinstance(obj, dict):
        return InvalidRecord(REASON_INVALID, f"{prefix}record is not an object")

    old_text = obj.get("old_text")
    new_text = obj.get("new_text")
    ignored = tuple(sorted(key for key in obj if key not in KEYS))

    def bad(reason: str, detail: str) -> InvalidRecord:
        return InvalidRecord(reason, prefix + detail, _display(old_text), _display(new_text), ignored)

    if not isinstance(old_text, str):
        return bad(REASON_INVALID, "'old_text' is missing or not a string")
    if not isinstance(new_text, str):
        return bad(REASON_INVALID, "'new_text' is missing or not a string")
    if old_text == "":
        return bad(REASON_INVALID, "'old_text' must not be empty")

    replace_all = obj.get("replace_all", False)
    if not isinstance(replace_all, bool):
        return bad(REASON_INVALID, "'replace_all' must be a boolean")

    scope = None
    if obj.get("scope") is not None:
        try:
            scope = Scope.parse(obj["scope"])
        except ScopeError as exc:
            return bad(REASON_INVALID, f"'scope': {exc}")

    if any(ch in old_text or ch in new_text for ch in ("\n", "\r")):
        return bad(
            REASON_CROSSES_PARAGRAPH,
            "old_text/new_text must stay inside a single paragraph (line break found)",
        )
    forbidden = sorted({ch for ch in old_text + new_text if ch in _FORBIDDEN_CONTROL})
    if forbidden:
        return bad(
            REASON_INVALID,
            "control character(s) not allowed: " + ", ".join(f"U+{ord(ch):04X}" for ch in forbidden),
        )
    unencodable = sorted({ch for ch in old_text + new_text if _not_xml_char(ch)})
    if unencodable:
        return bad(
            REASON_INVALID,
            "character(s) not allowed in XML: " + ", ".join(f"U+{ord(ch):04X}" for ch in unencodable),
        )

    try:
        visible, spans = parse_inline_format(new_text)
    except ValueError as exc:
        return bad(REASON_INVALID, str(exc))

    return FindingRecord(old_text, new_text, visible, tuple(spans), replace_all, scope, ignored)


def load_findings(path) -> list[Record]:
    """Parse a findings file.  Blank lines and ``#`` comments are skipped.

    A line that is not valid JSON becomes an :class:`InvalidRecord`, so one
    broken line does not cost the other findings.
    """
    # utf-8-sig: editors on Windows often save JSONL with a BOM.
    with open(path, encoding="utf-8-sig") as handle:
        text = handle.read()
    records: list[Record] = []
    # Split on "\n" only: str.splitlines() would also break on U+2028 and other
    # separators that are legal inside a JSON string.
    for lineno, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            records.append(InvalidRecord(REASON_INVALID, f"line {lineno}: invalid JSON ({exc.msg})"))
            continue
        records.append(parse_record(obj, lineno))
    return records
