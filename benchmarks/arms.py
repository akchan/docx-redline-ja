"""How each tool under comparison is driven, and what an LLM must emit to drive it.

Three arms:

``ours``
    ``apply_redline.py`` from this repository.  The model emits one JSONL record
    per edit — ``old_text`` and ``new_text`` — and nothing else.

``docx-cli``
    ``docx-cli`` (https://github.com/kklimuk/docx-cli, published on npm as
    ``bun-docx``).  Its closest equivalent path is
    ``track-changes on`` followed by ``replace --batch``, whose JSONL uses
    ``pattern``/``replacement``.  The record is the same shape and size as ours,
    so the two are directly comparable on both output quality and input cost.

``docx-skill``
    Anthropic's bundled ``docx`` skill, which has no redline command: the model
    unpacks the package and writes the ``w:del``/``w:ins`` OOXML by hand.  There
    is nothing to execute here in the deterministic pass, so only its token cost
    is modelled — by generating the minimal correct XML a model would have to
    produce for each edit.

Only ``ours`` and ``docx-cli`` can be run mechanically; the third arm's
correctness is measured in the agentic pass instead.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REDLINE_SCRIPT = REPO / "plugins/docx-redline-ja/skills/docx-redline-ja/scripts/apply_redline.py"


def docx_skill_dir() -> Path | None:
    """Directory of Anthropic's ``docx`` skill on this machine, or ``None``.

    The skill moves depending on how it was installed, so look in each place in
    turn rather than hard-coding one:

    1. ``$DOCX_SKILL_DIR``, to pin a specific copy.
    2. ``~/.claude/skills/docx``, a hand-installed copy.
    3. ``~/.claude/skills/synced/<bucket>/docx``, synced from claude.ai.  The
       bucket name is an opaque ID, so it is globbed; the most recently updated
       copy wins if there are several.
    4. ``~/.claude/plugins/marketplaces/*/skills/docx``, from a marketplace
       such as ``anthropics/skills``.
    """
    override = os.environ.get("DOCX_SKILL_DIR")
    if override:
        path = Path(override).expanduser()
        if not (path / "SKILL.md").is_file():
            raise FileNotFoundError(f"DOCX_SKILL_DIR has no SKILL.md: {path}")
        return path.resolve()

    claude = Path.home() / ".claude"
    for pattern in ("skills/docx", "skills/synced/*/docx", "plugins/marketplaces/*/skills/docx"):
        found = [path for path in claude.glob(pattern) if (path / "SKILL.md").is_file()]
        if found:
            return max(found, key=lambda path: (path / "SKILL.md").stat().st_mtime).resolve()
    return None

AUTHOR = "Benchmark"
#: Fixed so two runs of the benchmark produce byte-identical revision metadata.
DATE = "2026-01-01T00:00:00Z"


@dataclass
class ArmResult:
    ok: bool
    output: Path | None
    seconds: float
    detail: str = ""


#: The ``docx-cli`` release the benchmark measures.  Pinned so a published
#: number names the version it came from, and so ``bunx`` never fetches
#: whatever was pushed to npm most recently.  Bump it deliberately and rerun.
DOCX_CLI_PACKAGE = "bun-docx@0.28.0"


def default_docx_cli() -> list[str] | None:
    """Command that runs ``docx-cli``, if one is available on this machine.

    The pinned ``bunx`` package comes first: it brings its own dependencies, so
    the benchmark measures a known release rather than whatever ``docx`` is on
    ``PATH`` or whatever state a local clone happens to be in.
    """
    if shutil.which("bunx"):
        return ["bunx", DOCX_CLI_PACKAGE]
    if shutil.which("docx"):
        return ["docx"]
    checkout = Path.home() / ".claude/plugins/marketplaces/docx-cli/src/index.ts"
    if checkout.exists() and shutil.which("bun"):
        return ["bun", "run", str(checkout)]
    return None


def docx_cli_version(command: list[str]) -> str:
    """The version string the arm actually ran, for the report."""
    try:
        completed = subprocess.run([*command, "--version"], text=True, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else "unknown"


# ---------------------------------------------------------------- our tool


def ours_records(tasks) -> str:
    """The exact bytes an LLM has to emit to drive this repository's tool."""
    return "".join(
        json.dumps({"old_text": task.old_text, "new_text": task.new_text}, ensure_ascii=False) + "\n"
        for task in tasks
    )


def run_ours(source: Path, tasks, workdir: Path) -> ArmResult:
    findings = workdir / "findings.jsonl"
    findings.write_text(ours_records(tasks), encoding="utf-8")
    output = workdir / "ours.docx"

    started = time.perf_counter()
    completed = subprocess.run(
        [
            # `sys.executable`, not "python3": the interpreter running the
            # benchmark is the one that has lxml and defusedxml installed, and
            # on a machine whose system python lacks them "python3" fails.
            sys.executable, str(REDLINE_SCRIPT),
            "--docx", str(source),
            "--findings", str(findings),
            "--out", str(output),
            "--author", AUTHOR,
            "--date", DATE,
        ],
        text=True,
        capture_output=True,
    )
    elapsed = time.perf_counter() - started

    if completed.returncode != 0 or not output.exists():
        return ArmResult(False, None, elapsed, completed.stderr.strip()[:400])
    return ArmResult(True, output, elapsed)


# ------------------------------------------------------------- docx-cli


def docx_cli_records(tasks) -> str:
    """The equivalent ``replace --batch`` script for ``docx-cli``."""
    return "".join(
        json.dumps({"pattern": task.old_text, "replacement": task.new_text}, ensure_ascii=False) + "\n"
        for task in tasks
    )


def run_docx_cli(source: Path, tasks, workdir: Path, command: list[str]) -> ArmResult:
    output = workdir / "docx-cli.docx"
    shutil.copy2(source, output)  # docx-cli mutates in place
    batch = workdir / "batch.jsonl"
    batch.write_text(docx_cli_records(tasks), encoding="utf-8")

    environment = {**os.environ, "DOCX_AUTHOR": AUTHOR}
    started = time.perf_counter()
    enable = subprocess.run(
        [*command, "track-changes", str(output), "on"],
        text=True, capture_output=True, env=environment,
    )
    if enable.returncode != 0:
        return ArmResult(False, None, time.perf_counter() - started, enable.stderr.strip()[:400])

    applied = subprocess.run(
        [*command, "replace", str(output), "--batch", str(batch), "--author", AUTHOR],
        text=True, capture_output=True, env=environment,
    )
    elapsed = time.perf_counter() - started

    # A nonzero exit can still mean "most entries applied and the file was
    # saved".  Grading the document that exists is fairer than scoring zero on
    # the exit code, so the failure detail is recorded and the output is kept.
    if not output.exists():
        return ArmResult(False, None, elapsed, (applied.stderr or applied.stdout).strip()[:400])
    detail = "" if applied.returncode == 0 else (applied.stderr or applied.stdout).strip()[:400]
    return ArmResult(True, output, elapsed, detail)


def docx_cli_edit_records(tasks, paragraphs) -> str:
    """The ``edit --batch`` script: one whole rewritten paragraph per edit.

    ``edit`` is the path that runs docx-cli's word-level diff, so it is the
    fairest test of that algorithm.  The price is in the input: the model must
    address a locator and re-emit the entire paragraph, not just the phrase it
    wants to change.
    """
    rewritten: dict[int, str] = {}
    for task in tasks:
        current = rewritten.get(task.paragraph, paragraphs[task.paragraph])
        rewritten[task.paragraph] = current.replace(task.old_text, task.new_text, 1)
    return "".join(
        # No per-record "track" flag: `track-changes on` already applies to
        # the whole document, and 0.28.0 rejects the field as unknown.
        json.dumps({"at": f"p{index}", "text": text}, ensure_ascii=False) + "\n"
        for index, text in sorted(rewritten.items())
    )


def run_docx_cli_edit(source: Path, tasks, paragraphs, workdir: Path, command: list[str]) -> ArmResult:
    output = workdir / "docx-cli-edit.docx"
    shutil.copy2(source, output)
    batch = workdir / "edit-batch.jsonl"
    batch.write_text(docx_cli_edit_records(tasks, paragraphs), encoding="utf-8")

    environment = {**os.environ, "DOCX_AUTHOR": AUTHOR}
    started = time.perf_counter()
    enable = subprocess.run(
        [*command, "track-changes", str(output), "on"],
        text=True, capture_output=True, env=environment,
    )
    if enable.returncode != 0:
        return ArmResult(False, None, time.perf_counter() - started, enable.stderr.strip()[:400])

    applied = subprocess.run(
        [*command, "edit", str(output), "--batch", str(batch), "--author", AUTHOR],
        text=True, capture_output=True, env=environment,
    )
    elapsed = time.perf_counter() - started

    if not output.exists():
        return ArmResult(False, None, elapsed, (applied.stderr or applied.stdout).strip()[:400])
    detail = "" if applied.returncode == 0 else (applied.stderr or applied.stdout).strip()[:400]
    return ArmResult(True, output, elapsed, detail)


# ----------------------------------------------------------- docx skill


def docx_skill_xml(task) -> str:
    """The smallest correct hand-written OOXML for one edit.

    The bundled ``docx`` skill's documented procedure is to replace the whole
    ``<w:r>`` with ``<w:del>``/``<w:ins>`` siblings, copying the original run's
    ``<w:rPr>``.  Even at its most economical that means emitting both the old
    and the new text plus the revision scaffolding — which is what this models.
    It is a floor, not an estimate of real output: a model also re-emits
    surrounding context it had to read.
    """
    return (
        f'<w:del w:id="1" w:author="{AUTHOR}" w:date="{DATE}">'
        f"<w:r><w:delText>{task.old_text}</w:delText></w:r></w:del>"
        f'<w:ins w:id="2" w:author="{AUTHOR}" w:date="{DATE}">'
        f"<w:r><w:t>{task.new_text}</w:t></w:r></w:ins>"
    )


def docx_skill_payload(tasks) -> str:
    return "\n".join(docx_skill_xml(task) for task in tasks) + "\n"
