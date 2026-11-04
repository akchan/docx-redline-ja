#!/usr/bin/env python3
"""Apply an old_text/new_text findings file to a .docx as Word tracked changes.

    python scripts/apply_redline.py \\
        --docx source.docx --findings findings.jsonl --out redlined.docx [--author "AI"]
    python scripts/apply_redline.py locate --docx source.docx --query "phrase"
    python scripts/apply_redline.py read --docx source.docx [--paragraphs p10-20] [--text]

The source is never written to.  The redline is written only after every
verification check has passed.  A JSON report is written next to ``--out``
and summarized on stdout.

``locate`` reports where a phrase occurs and ``read`` prints paragraph text,
both over the same accepted-view text and paragraph numbers the applier uses.
"""

from __future__ import annotations

import argparse
import copy
import itertools
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from checks import (  # noqa: E402
    added_revision_problems,
    insertions_independent,
    is_reversible,
    revision_fingerprint,
)
from docx_text import SUBSCRIPT, SUPERSCRIPT, build_paragraphs, fallback_mirrors, locator  # noqa: E402
from findings import Scope, ScopeError, load_findings  # noqa: E402
from package import (  # noqa: E402
    PackageError,
    entry_names,
    package_bytes,
    read_package,
    write_atomically,
)
from planner import find_occurrences, occurrence_entry, plan_records  # noqa: E402
from report import build_result, render, summarize_for_stdout  # noqa: E402
from revisions import apply_plan, make_context  # noqa: E402

DEFAULT_AUTHOR = "AI"
COMMANDS = ("apply", "locate", "read")

#: How many ``matches[]`` entries ``locate`` prints per query.  For a common
#: phrase, ``count`` and ``scope`` already answer the question.
MAX_MATCHES = 20

#: ``w:date`` is an ``xsd:dateTime``; this is the shape Word itself writes.
DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: Tag for each vertical alignment, spelled as in ``new_text``.
TAG_OF = {SUPERSCRIPT: "sup", SUBSCRIPT: "sub"}


class StartupError(Exception):
    """A problem found before anything is written.  Exit code 2."""


# --- arguments ---------------------------------------------------------------


def iso8601(value: str) -> str:
    """Parse ``--date`` and normalize it to the UTC form Word writes."""
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not an ISO 8601 timestamp (expected e.g. 2026-08-17T00:00:00Z)"
        ) from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime(DATE_FORMAT)


DESCRIPTIONS = {
    "apply": "Apply a findings JSONL to a .docx as tracked changes",
    "locate": "Report where a phrase occurs in the accepted-view text",
    "read": "Print the accepted-view text of a .docx, one entry per paragraph",
}


def parse_args(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # No subcommand means "apply", so the plain --docx/--findings/--out form works.
    command = argv.pop(0) if argv and argv[0] in COMMANDS else "apply"
    parser = argparse.ArgumentParser(
        prog="apply_redline.py" if command == "apply" else f"apply_redline.py {command}",
        description=DESCRIPTIONS[command],
        epilog="other commands: apply_redline.py locate -h, apply_redline.py read -h"
        if command == "apply"
        else None,
    )
    parser.add_argument("--docx", required=True, help="source .docx (never modified)")
    if command == "apply":
        parser.add_argument("--findings", required=True, help="findings JSONL (never modified)")
        parser.add_argument(
            "--out", required=True, help="output .docx; the report goes to <out>.result.json"
        )
        parser.add_argument(
            "--author", default=DEFAULT_AUTHOR, help=f"revision author (default: {DEFAULT_AUTHOR})"
        )
        parser.add_argument(
            "--date",
            default=None,
            type=iso8601,
            metavar="ISO8601",
            help="revision timestamp as w:date (default: now, UTC). Set for reproducible output.",
        )
    elif command == "locate":
        parser.add_argument(
            "--query",
            required=True,
            action="append",
            metavar="PHRASE",
            help="phrase to locate; repeat for several phrases",
        )
        parser.add_argument(
            "--no-matches",
            action="store_true",
            help="omit matches[]; report only count, paragraphs and scope",
        )
    else:
        parser.add_argument(
            "--paragraphs",
            default=None,
            metavar="SCOPE",
            help=(
                'list only these paragraphs, in the grammar of the `scope` key ("p12", '
                '"p10-12", "p3,p10-12", "p120-"); paragraph_count still counts the '
                "whole document"
            ),
        )
        parser.add_argument(
            "--text",
            action="store_true",
            help="print one p{index}<TAB>text line per paragraph instead of JSON",
        )
    args = parser.parse_args(argv)
    args.command = command
    return args


# --- inputs ------------------------------------------------------------------


def source_path(value: str) -> Path:
    source = Path(value).resolve()
    if not source.is_file():
        raise StartupError(f"{source} does not exist")
    if source.suffix.lower() != ".docx":
        raise StartupError(f"{source} is not a .docx")
    return source


def read_source(source: Path):
    try:
        return read_package(source)
    except PackageError as exc:
        raise StartupError(str(exc)) from exc


def same_file(left: Path, right: Path) -> bool:
    """True if the two paths name the same file on disk.

    When both exist, ``os.path.samefile`` decides, which sees through symlinks,
    hard links and case-insensitive filesystems.  Otherwise the resolved paths
    are compared with case folded.  On a case-sensitive filesystem that also
    refuses two distinct files differing only in case, which is the safe side.
    """
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.realpath(left).casefold() == os.path.realpath(right).casefold()


def colliding_paths(paths: dict[str, Path]) -> str | None:
    """Describe the first pair of paths that name the same file, or ``None``.

    Every pair is checked, inputs and outputs alike: two names for one file is
    always a mistake, and any pair left out would be a silent overwrite.
    """
    named = list(paths.items())
    for index, (flag, path) in enumerate(named):
        for other_flag, other in named[index + 1 :]:
            if same_file(path, other):
                return (
                    f"{flag} ({path}) and {other_flag} ({other}) are the same file; "
                    "each of them must name a file of its own -- inputs are never "
                    "written to, and one output must not overwrite the other"
                )
    return None


# --- apply -------------------------------------------------------------------


def ignored_key_notes(plans) -> list[str]:
    """One note naming the keys the findings carried but this tool never reads.

    Unknown keys are allowed, since a producer may add its own bookkeeping.
    Naming them makes a misspelled key such as ``replace_al`` visible.
    """
    seen: dict[str, list[int]] = {}
    for plan in plans:
        for key in plan.ignored_keys:
            seen.setdefault(key, []).append(plan.index)
    if not seen:
        return []
    return [
        "key(s) not in the contract were carried through unread: "
        + "; ".join(
            f"{key!r} on record(s) " + ", ".join(str(i) for i in indexes)
            for key, indexes in sorted(seen.items())
        )
    ]


def apply(args) -> int:
    source = source_path(args.docx)
    findings = Path(args.findings).resolve()
    out = Path(args.out).resolve()
    result_path = out.with_suffix(".result.json")
    if not findings.is_file():
        raise StartupError(f"{findings} does not exist")
    collision = colliding_paths(
        {"--docx": source, "--findings": findings, "--out": out, "the report": result_path}
    )
    if collision:
        raise StartupError(collision)
    if out.suffix.lower() != ".docx":
        raise StartupError(
            f"--out ({out}) must end in .docx: the output is a Word package, and "
            "writing one under another extension has meant clobbering an input"
        )

    date = args.date or datetime.now(timezone.utc).strftime(DATE_FORMAT)
    package = read_source(source)
    root = package.root
    paragraphs = build_paragraphs(root)
    plans = plan_records(paragraphs, load_findings(findings))

    source_root = copy.deepcopy(root)
    # The reversibility check cannot see another author's revision being
    # rewritten, so their revisions are fingerprinted before the first edit.
    revisions_before = revision_fingerprint(root, args.author)
    ctx = make_context(root, args.author, date)
    _, mirror_notes = apply_plan(paragraphs, plans, ctx, mirrors=fallback_mirrors(root, paragraphs))
    notes = ignored_key_notes(plans) + mirror_notes

    data = package_bytes(package)
    # Faults the source already had are not counted.
    added = added_revision_problems(source_root, root)
    notes.extend(f"revision markup fault added by this run: {line}" for line in added)
    verification = {
        "reversible": is_reversible(source_root, root, args.author),
        "insertions_independent": insertions_independent(root, ctx.created_insertion_ids()),
        "other_revisions_preserved": revision_fingerprint(root, args.author) == revisions_before,
        "revisions_wellformed": not added,
        "media_preserved": entry_names(data) == sorted(package.names()),
    }
    ok = all(verification.values())
    if ok:
        write_atomically(data, out)
    else:
        notes.append("verification failed: no .docx was written")

    result = build_result(
        source=source,
        out=out if ok else None,
        findings=findings,
        author=args.author,
        date=date,
        plans=plans,
        verification=verification,
        notes=notes,
    )
    write_atomically((render(result) + "\n").encode("utf-8"), result_path)
    print(render(summarize_for_stdout(result, result_path)))
    return 0 if ok else 1


# --- locate ------------------------------------------------------------------


def locate(args) -> int:
    """Report where each ``--query`` occurs, as ``p{paragraph}:{start}-{end}``."""
    source = source_path(args.docx)
    paragraphs = build_paragraphs(read_source(source).root)
    queries = []
    for query in args.query:
        found = find_occurrences(paragraphs, query)
        hit = sorted({index for index, _ in found})
        entry = {
            "query": query,
            # count, paragraphs and scope cover every match, even when
            # matches[] is cut short.
            "count": len(found),
            "paragraphs": [locator(index) for index in hit],
            "scope": str(Scope.from_paragraphs(hit)) if hit else "",
        }
        if not args.no_matches:
            entry["matches"] = [
                occurrence_entry(paragraphs[index], start, start + len(query))
                for index, start in found[:MAX_MATCHES]
            ]
            if len(found) > MAX_MATCHES:
                entry["matches_truncated"] = len(found) - MAX_MATCHES
        queries.append(entry)
    print(render({"source": str(source), "queries": queries}))
    return 0


# --- read --------------------------------------------------------------------


def tagged_text(text: str, formats: list[str]) -> str | None:
    """``text`` with ``<sup>``/``<sub>`` around its raised and lowered runs, or ``None``."""
    if not any(fmt in TAG_OF for fmt in formats):
        return None
    parts = []
    for fmt, group in itertools.groupby(zip(text, formats), key=lambda pair: pair[1]):
        chunk = "".join(char for char, _ in group)
        tag = TAG_OF.get(fmt)
        parts.append(f"<{tag}>{chunk}</{tag}>" if tag else chunk)
    return "".join(parts)


def read(args) -> int:
    source = source_path(args.docx)
    scope = None
    if args.paragraphs is not None:
        try:
            scope = Scope.parse(args.paragraphs)
        except ScopeError as exc:
            raise StartupError(f"--paragraphs: {exc}") from exc
    paragraphs = build_paragraphs(read_source(source).root)

    entries = []
    for paragraph in paragraphs:
        if scope is not None and not scope.contains(paragraph.index):
            continue
        entry = {"paragraph": paragraph.index, "text": paragraph.text}
        tagged = tagged_text(paragraph.text, paragraph.formats)
        if tagged is not None:
            entry["text_tagged"] = tagged
        entries.append(entry)

    if args.text:
        lines = []
        for entry in entries:
            text = entry.get("text_tagged", entry["text"])
            # Tabs and line breaks would break the one-paragraph-per-line shape.
            for char in "\t\n\r":
                text = text.replace(char, " ")
            lines.append(f"{locator(entry['paragraph'])}\t{text}")
        print("\n".join(lines))
        return 0

    payload: dict = {"source": str(source), "paragraph_count": len(paragraphs)}
    if scope is not None:
        payload["scope"] = str(scope)
        payload["listed"] = len(entries)
    payload["paragraphs"] = entries
    print(render(payload))
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return {"apply": apply, "locate": locate, "read": read}[args.command](args)
    except StartupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
