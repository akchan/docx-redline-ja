"""Derive the edit tasks the benchmark applies, straight from the corpus.

Every task is a real substring of the document, chosen so the intended edit has
an unambiguous minimum: how many characters *must* be deleted and inserted to
turn ``old_text`` into ``new_text``.  That minimum is what the redline is graded
against.  A tool that rewrites a whole sentence to change one character still
produces a correct document, but a much noisier one for a human to review, and
the difference shows up as a ratio rather than a pass/fail.

Tasks are generated rather than hand-written so the set can be regenerated at a
different size, and so the ``expected`` figures cannot drift from the edits.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import corpus


@dataclass(frozen=True)
class Task:
    task_id: str
    kind: str
    language: str
    paragraph: int
    old_text: str
    new_text: str
    #: Characters that a minimal redline deletes and inserts, from a
    #: character-level diff of ``old_text`` against ``new_text``.
    expected_deleted: int
    expected_inserted: int


def minimal_edit_size(old: str, new: str) -> tuple[int, int]:
    """Characters a minimal redline must delete and insert.

    Computed from the longest common subsequence by dynamic programming, not
    from any tool's diff, so the figure is the true minimum.
    """
    previous = [0] * (len(new) + 1)
    for a in old:
        current = [0]
        for j, b in enumerate(new):
            current.append(previous[j] + 1 if a == b else max(previous[j + 1], current[j]))
        previous = current
    common = previous[-1]
    return len(old) - common, len(new) - common


def _window(paragraph: str, index: int, language: str, width: int = 24) -> tuple[int, str]:
    """A unique substring of ``paragraph`` containing offset ``index``.

    Widened until it occurs exactly once, so no task is ambiguous by accident —
    which is common in space-free Japanese, where short phrases repeat.
    """
    while width < len(paragraph):
        start = max(0, index - width // 2)
        candidate = paragraph[start : start + width]
        if paragraph.count(candidate) == 1 and index - start < len(candidate):
            return start, candidate
        width += 12
    return 0, paragraph


def _tasks_for_language(language: str, paragraphs: list[str], per_kind: int) -> list[Task]:
    """Build ``per_kind`` tasks of each kind, each in a different paragraph.

    Paragraphs are scanned rather than indexed, because not every paragraph
    contains every anchor; a fixed index range silently produces fewer tasks
    than asked for.
    """
    tasks: list[Task] = []
    used: set[int] = set()

    def add(kind: str, index: int, old: str, new: str) -> None:
        deleted, inserted = minimal_edit_size(old, new)
        tasks.append(
            Task(
                task_id=f"{language}-{kind}-{sum(1 for t in tasks if t.kind == kind) + 1}",
                kind=kind,
                language=language,
                paragraph=index,
                old_text=old,
                new_text=new,
                expected_deleted=deleted,
                expected_inserted=inserted,
            )
        )
        used.add(index)

    def collect(kind: str, make) -> None:
        """Walk the corpus until ``per_kind`` paragraphs yielded a task."""
        found = 0
        for index, paragraph in enumerate(paragraphs):
            if index in used or found >= per_kind:
                continue
            built = make(paragraph)
            if built is None:
                continue
            old, new = built
            if old == new:
                continue
            add(kind, index, old, new)
            found += 1
        if found < per_kind:
            raise RuntimeError(f"{language}: only built {found} '{kind}' tasks, wanted {per_kind}")

    marker = "、" if language == "ja" else ", "
    inserted_word = "たしかに" if language == "ja" else "certainly "

    def insertion(paragraph: str):
        hit = paragraph.find(marker, 40)
        if hit == -1:
            return None
        at = hit + len(marker)
        start, old = _window(paragraph, at, language, width=30)
        offset = at - start
        return old, old[:offset] + inserted_word + old[offset:]

    def deletion(paragraph: str):
        hit = paragraph.find(marker, 40)
        if hit == -1:
            return None
        start, old = _window(paragraph, hit, language, width=30)
        offset = hit - start
        return old, old[:offset] + old[offset + len(marker) :]

    def replacement(paragraph: str):
        if language == "ja":
            hit = paragraph.find("た。", 30)
            if hit == -1:
                return None
            start, old = _window(paragraph, hit, language, width=30)
            offset = hit - start
            return old, old[:offset] + "たり。" + old[offset + 2 :]
        match = re.search(r"\bvery\b", paragraph)
        if match is None:
            return None
        start, old = _window(paragraph, match.start(), language, width=36)
        offset = match.start() - start
        return old, old[:offset] + "quite" + old[offset + 4 :]

    def rewrite(paragraph: str):
        if language == "ja":
            hit = paragraph.find("である", 20)
            if hit == -1:
                return None
            start, old = _window(paragraph, hit, language, width=34)
            offset = hit - start
            return old, old[:offset] + "であった" + old[offset + 3 :]
        match = re.search(r"\bwas\b", paragraph)
        if match is None:
            return None
        start, old = _window(paragraph, match.start(), language, width=40)
        offset = match.start() - start
        return old, old[:offset] + "had been" + old[offset + 3 :]

    collect("insert", insertion)
    collect("delete", deletion)
    collect("replace", replacement)
    collect("rewrite", rewrite)
    return sorted(tasks, key=lambda task: task.paragraph)


def build(language: str, per_kind: int = 5, paragraph_count: int = 50) -> list[Task]:
    paragraphs = corpus.paragraphs(language, count=paragraph_count)
    return _tasks_for_language(language, paragraphs, per_kind)


def write(language: str, path: Path, per_kind: int = 5) -> list[Task]:
    tasks = build(language, per_kind=per_kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps(asdict(task), ensure_ascii=False) + "\n")
    return tasks


def load(path: Path) -> list[Task]:
    with path.open(encoding="utf-8") as handle:
        return [Task(**json.loads(line)) for line in handle if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-kind", type=int, default=5)
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent / "tasks")
    args = parser.parse_args()

    for language in ("ja", "en"):
        tasks = write(language, args.out_dir / f"{language}.jsonl", per_kind=args.per_kind)
        total = sum(task.expected_deleted + task.expected_inserted for task in tasks)
        print(f"{language}: {len(tasks)} tasks, {total} characters of minimal revision")
        for task in tasks[:2]:
            print(f"  {task.task_id}: -{task.expected_deleted}/+{task.expected_inserted} {task.old_text[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
