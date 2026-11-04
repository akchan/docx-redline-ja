"""Apply the same edits with each tool and compare the redlines they produce.

No model is involved, so this pass costs nothing to run and its numbers are
reproducible.  It answers two questions:

1. **Is the redline correct?**  Accepting every revision must yield the intended
   text; rejecting every revision must yield the original.
2. **Is the redline minimal?**  How many characters does a reviewer see marked
   up, against the fewest that the edit could possibly require?

Question 2 is where a character-level diff separates from a word-level one, and
the gap is largest in Japanese, where a "word" boundary does not exist for a
whitespace tokeniser to find: one changed character marks up a whole clause.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "tests"))

import arms
import corpus
import measure
import tasks as task_module
from synthetic_docx import build_docx

TOKENISERS = ("cl100k_base", "o200k_base")


@dataclass
class ArmReport:
    arm: str
    language: str
    ran: bool
    tasks: int
    #: Redline quality, `None` when the arm could not be executed here.
    applied_correctly: int | None
    reversible: bool | None
    inserted_chars: int | None
    deleted_chars: int | None
    revision_chars: int | None
    revisions: int | None
    opens_as_package: bool | None
    seconds: float | None
    #: Cost of driving the arm: what a model must emit for these edits.
    payload_chars: int
    payload_tokens: dict[str, int]
    #: Minimum characters the edits require, for the ratio.
    minimum_revision_chars: int
    detail: str = ""

    @property
    def noise_ratio(self) -> float | None:
        if self.revision_chars is None or self.minimum_revision_chars == 0:
            return None
        return self.revision_chars / self.minimum_revision_chars


def count_tokens(text: str) -> dict[str, int]:
    """Token counts per encoding, or ``{}`` when tiktoken is not installed."""
    try:
        import tiktoken
    except ImportError:
        return {}
    counts = {}
    for name in TOKENISERS:
        try:
            counts[name] = len(tiktoken.get_encoding(name).encode(text))
        except Exception:
            continue
    return counts


def intended_paragraphs(original: list[str], selected) -> list[str]:
    """The document as it should read once every edit is accepted."""
    out = list(original)
    for task in selected:
        paragraph = out[task.paragraph]
        if task.old_text not in paragraph:
            raise RuntimeError(f"{task.task_id}: old_text not found in paragraph {task.paragraph}")
        out[task.paragraph] = paragraph.replace(task.old_text, task.new_text, 1)
    return out


def grade(result: arms.ArmResult, original: list[str], expected: list[str], selected) -> dict:
    """Turn one arm's output document into comparable numbers."""
    if not result.ok or result.output is None:
        return {
            "applied_correctly": 0,
            "reversible": False,
            "inserted_chars": None,
            "deleted_chars": None,
            "revision_chars": None,
            "revisions": None,
            "opens_as_package": False,
        }

    found = measure.measure(result.output)
    correct = sum(
        1
        for task in selected
        if task.paragraph < len(found.accepted_text)
        and found.accepted_text[task.paragraph] == expected[task.paragraph]
    )
    return {
        "applied_correctly": correct,
        "reversible": found.rejected_text == original,
        "inserted_chars": found.inserted_chars,
        "deleted_chars": found.deleted_chars,
        "revision_chars": found.revision_chars,
        "revisions": found.revisions,
        "opens_as_package": measure.opens_as_a_package(result.output),
    }


def run_language(language: str, workdir: Path, docx_cli: list[str] | None, paragraph_count: int) -> list[ArmReport]:
    paragraphs = corpus.paragraphs(language, count=paragraph_count)
    selected = task_module.build(language, per_kind=5, paragraph_count=paragraph_count)
    expected = intended_paragraphs(paragraphs, selected)
    minimum = sum(task.expected_deleted + task.expected_inserted for task in selected)

    language_dir = workdir / language
    language_dir.mkdir(parents=True, exist_ok=True)
    source = build_docx(language_dir / "source.docx", paragraphs)

    reports: list[ArmReport] = []

    ours_payload = arms.ours_records(selected)
    ours = arms.run_ours(source, selected, language_dir)
    reports.append(
        ArmReport(
            arm="ours",
            language=language,
            ran=True,
            tasks=len(selected),
            seconds=round(ours.seconds, 3),
            payload_chars=len(ours_payload),
            payload_tokens=count_tokens(ours_payload),
            minimum_revision_chars=minimum,
            detail=ours.detail,
            **grade(ours, paragraphs, expected, selected),
        )
    )

    cli_payload = arms.docx_cli_records(selected)
    if docx_cli:
        cli = arms.run_docx_cli(source, selected, language_dir, docx_cli)
        reports.append(
            ArmReport(
                arm="docx-cli",
                language=language,
                ran=True,
                tasks=len(selected),
                seconds=round(cli.seconds, 3),
                payload_chars=len(cli_payload),
                payload_tokens=count_tokens(cli_payload),
                minimum_revision_chars=minimum,
                detail=cli.detail,
                **grade(cli, paragraphs, expected, selected),
            )
        )
    else:
        reports.append(
            ArmReport(
                arm="docx-cli", language=language, ran=False, tasks=len(selected),
                applied_correctly=None, reversible=None, inserted_chars=None,
                deleted_chars=None, revision_chars=None, revisions=None,
                opens_as_package=None, seconds=None,
                payload_chars=len(cli_payload), payload_tokens=count_tokens(cli_payload),
                minimum_revision_chars=minimum,
                detail="docx-cli not found; pass --docx-cli-cmd to include it",
            )
        )

    edit_payload = arms.docx_cli_edit_records(selected, paragraphs)
    if docx_cli:
        cli_edit = arms.run_docx_cli_edit(source, selected, paragraphs, language_dir, docx_cli)
        reports.append(
            ArmReport(
                arm="docx-cli (edit)",
                language=language,
                ran=True,
                tasks=len(selected),
                seconds=round(cli_edit.seconds, 3),
                payload_chars=len(edit_payload),
                payload_tokens=count_tokens(edit_payload),
                minimum_revision_chars=minimum,
                detail=cli_edit.detail,
                **grade(cli_edit, paragraphs, expected, selected),
            )
        )

    skill_payload = arms.docx_skill_payload(selected)
    reports.append(
        ArmReport(
            arm="docx-skill", language=language, ran=False, tasks=len(selected),
            applied_correctly=None, reversible=None, inserted_chars=None,
            deleted_chars=None, revision_chars=None, revisions=None,
            opens_as_package=None, seconds=None,
            payload_chars=len(skill_payload), payload_tokens=count_tokens(skill_payload),
            minimum_revision_chars=minimum,
            detail="hand-written OOXML; no command to run, cost modelled only",
        )
    )
    return reports


def skill_context_cost() -> dict:
    """Bytes and tokens a model reads before it can use each tool at all."""
    def size(paths: list[Path]) -> dict:
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths if path.exists())
        return {"files": len(paths), "chars": len(text), "tokens": count_tokens(text)}

    ours_skill = arms.REPO / "plugins/docx-redline-ja/skills/docx-redline-ja"
    cli_skill = Path.home() / ".claude/plugins/marketplaces/docx-cli/skills/docx-cli"
    docx_skill = arms.docx_skill_dir()

    out = {
        "ours": {
            "skill_only": size([ours_skill / "SKILL.md"]),
            "with_references": size([ours_skill / "SKILL.md", *sorted((ours_skill / "references").glob("*.md"))]),
        }
    }
    if (cli_skill / "SKILL.md").exists():
        out["docx-cli"] = {
            "skill_only": size([cli_skill / "SKILL.md"]),
            "with_references": size([cli_skill / "SKILL.md", *sorted((cli_skill / "references").glob("*.md"))]),
        }
    if docx_skill is not None:
        out["docx-skill"] = {"skill_only": size([docx_skill / "SKILL.md"])}
    return out


def render(reports: list[ArmReport]) -> str:
    lines = [
        "| language | arm | correct | reversible | marked-up chars | vs minimum | revisions | payload tokens | seconds |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for report in reports:
        ratio = report.noise_ratio
        tokens = report.payload_tokens.get("o200k_base") or report.payload_tokens.get("cl100k_base")
        lines.append(
            "| {lang} | {arm} | {correct} | {rev} | {chars} | {ratio} | {revisions} | {tokens} | {seconds} |".format(
                lang=report.language,
                arm=report.arm,
                correct="—" if report.applied_correctly is None else f"{report.applied_correctly}/{report.tasks}",
                rev="—" if report.reversible is None else ("yes" if report.reversible else "no"),
                chars="—" if report.revision_chars is None else report.revision_chars,
                ratio="—" if ratio is None else f"{ratio:.1f}x",
                revisions="—" if report.revisions is None else report.revisions,
                tokens=tokens if tokens is not None else "—",
                seconds="—" if report.seconds is None else f"{report.seconds:.2f}",
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lang", choices=("ja", "en", "both"), default="both")
    parser.add_argument("--paragraphs", type=int, default=50)
    parser.add_argument("--docx-cli-cmd", nargs="+", default=None,
                        help="command that runs docx-cli, e.g. 'bun run /path/src/index.ts'")
    parser.add_argument("--out-dir", type=Path, default=HERE / "out")
    args = parser.parse_args()

    docx_cli = args.docx_cli_cmd or arms.default_docx_cli()
    if docx_cli is None:
        print("note: docx-cli not found, that arm will report cost only", file=sys.stderr)

    languages = ("ja", "en") if args.lang == "both" else (args.lang,)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    reports: list[ArmReport] = []
    for language in languages:
        reports.extend(run_language(language, args.out_dir, docx_cli, args.paragraphs))

    table = render(reports)
    print(table)
    print()
    context = skill_context_cost()
    print("Context a model reads before the first edit:")
    for name, sizes in context.items():
        for variant, info in sizes.items():
            tokens = info["tokens"].get("o200k_base", "—")
            print(f"  {name:<11} {variant:<16} {info['chars']:>7} chars  {tokens:>7} tokens")

    payload = {
        "reports": [asdict(report) for report in reports],
        "context_cost": context,
        "corpus": corpus.attribution_lines(),
    }
    result_path = args.out_dir / "deterministic.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out_dir / "deterministic.md").write_text(table + "\n", encoding="utf-8")
    print(f"\nwrote {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
