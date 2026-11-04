"""Give a real model the same job three ways and measure what it costs.

The deterministic pass compares the tools once a model has already decided what
to change.  This pass compares the whole job: read the document, work out where
the edits go, drive the tool, and produce a redlined ``.docx``.

Each arm runs in its own scratch directory with only one tool visible, so the
model cannot fall back to another approach:

``ours``          this repository's plugin, loaded with ``--plugin-dir``
``docx-cli``      docx-cli's plugin directory, with ``docx`` on ``PATH``
``docx-skill``    the bundled ``docx`` skill directory, linked in as a plugin

The model runs unattended with ``--permission-mode dontAsk`` and reads text
downloaded from Aozora Bunko and Project Gutenberg, so each run is fenced in:

- Every Bash command runs inside Claude Code's sandbox (Seatbelt on macOS):
  writes are confined to the run's scratch directory and the per-user temp
  directory, secrets such as ``~/.ssh`` are unreadable, and the network is
  closed except for the npm registry the ``docx-cli`` arm needs.  The run
  refuses to start if the sandbox is unavailable, rather than falling back.
- Bash itself is allowed outright.  The sandbox is the boundary; command
  patterns would only add friction that falls unevenly on the arms (a model
  that writes a heredoc, or a stray line continuation, would be refused in one
  arm and not another).  ``python``/``python3`` resolve to this interpreter
  through a shim on ``PATH``.
- Files may be written only inside the run's scratch directory, and read only
  there and in the arm's tool directory.
- The child process gets a short allowlist of environment variables, not this
  process's whole environment.

Grading uses ``measure.py``, the same independent reader the deterministic pass
uses.  Cost comes from the CLI's own ``usage`` block, not from an estimate.

This pass calls the Anthropic API and therefore costs money.  It is never run by
the test suite or by CI; run it by hand when the published numbers need
refreshing.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "tests"))

import arms
import corpus
import measure
import tasks as task_module
from run_deterministic import intended_paragraphs
from synthetic_docx import build_docx

OURS_PLUGIN = arms.REPO / "plugins/docx-redline-ja"
DOCX_CLI_PLUGIN = Path.home() / ".claude/plugins/marketplaces/docx-cli"
DOCX_SKILL = arms.docx_skill_dir()

PROMPT = """\
`{source}` is a Word document. Apply the edits listed in `{instructions}` to it
as Word tracked changes, and write the result to `{output}`.

Requirements:
- Every edit must be recorded as a tracked change, with author "{author}".
- Do not change any text the instruction list does not mention.
- The result must be a valid .docx that Word can open.

Report only the path you wrote when you are done.
"""


@dataclass
class Run:
    arm: str
    language: str
    attempt: int
    ok: bool
    tasks_correct: int
    tasks_total: int
    reversible: bool
    revision_chars: int | None
    minimum_revision_chars: int
    #: The minimum markup for the edits that were applied correctly.  The
    #: ratio divides by this, not by the minimum for every edit requested, or
    #: an arm that applied fewer edits would look tighter than it is.
    landed_minimum_chars: int
    opens_as_package: bool
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    turns: int
    seconds: float
    cost_usd: float | None
    detail: str = ""


@dataclass
class ArmSummary:
    arm: str
    language: str
    attempts: int
    solved: float
    revision_chars: float | None
    noise_ratio: float | None
    input_tokens: float
    output_tokens: float
    seconds: float
    cost_usd: float | None
    runs: list[Run] = field(default_factory=list)


def write_instructions(path: Path, selected) -> None:
    """A plain-language edit list, identical for every arm.

    Deliberately not in either tool's input format: handing an arm its own
    JSONL would measure transcription, not the work.
    """
    lines = ["Replace each quoted phrase on the left with the one on the right.", ""]
    for index, task in enumerate(selected, start=1):
        lines.append(f"{index}. {task.old_text!r}")
        lines.append(f"   -> {task.new_text!r}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_workdir(root: Path, arm: str, language: str, attempt: int, paragraphs, selected) -> tuple[Path, Path, Path]:
    workdir = root / f"{language}-{arm.replace(' ', '_')}-{attempt}"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    source = build_docx(workdir / "source.docx", paragraphs)
    instructions = workdir / "edits.txt"
    write_instructions(instructions, selected)
    return workdir, source, workdir / "reviewed.docx"


#: Tools every arm may use.  ``--restricted`` drops Bash and the file tools by
#: default; naming them here gives all three arms the same capability set.
#: What each tool may *touch* is narrowed per arm by ``settings_for``.
TOOLS = "Bash,Read,Write,Edit,Glob,Grep,Skill,TodoWrite"

#: Environment variables passed to ``claude -p``.  ``HOME`` stays the real one:
#: the CLI's login lives under it, and ``--restricted`` already keeps the
#: user's settings, skills and plugins out.  Everything else -- SSH agents,
#: cloud credentials, tokens for other services -- is dropped.
ENV_ALLOWLIST = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TERM",
    "LANG", "LC_ALL", "LC_CTYPE",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR",
)


def absolute_rule(tool: str, directory: Path) -> str:
    """A permission rule for everything under ``directory``.

    A leading ``//`` marks an absolute path; a single ``/`` would be read
    relative to the settings file.
    """
    return f"{tool}(/{directory.resolve()}/**)"


ARMS = ("ours", "docx-cli", "docx-skill", "no-skill")


def tool_directories(arm: str) -> list[Path]:
    """Where an arm's tool lives: readable, never writable."""
    return {
        "ours": [OURS_PLUGIN],
        "docx-cli": [DOCX_CLI_PLUGIN],
        "docx-skill": [DOCX_SKILL] if DOCX_SKILL else [],
        "no-skill": [],
    }[arm]


#: Readable by default under the sandbox, so closed explicitly.  The tools
#: under test never need any of these.
SECRET_PATHS = ["~/.ssh", "~/.aws", "~/.gnupg", "~/.config/gh", "~/.netrc", "~/.docker", "~/.kube"]


def sandbox_for(arm: str) -> dict:
    sandbox = {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
        "failIfUnavailable": True,
        "filesystem": {"denyRead": SECRET_PATHS},
        "network": {"allowedDomains": []},
    }
    if arm == "docx-cli":
        # `bunx` resolves the pinned package against the registry and keeps it
        # in its own cache.
        sandbox["filesystem"]["allowWrite"] = ["~/.bun/install/cache"]
        sandbox["network"]["allowedDomains"] = ["registry.npmjs.org"]
    return sandbox


def settings_for(arm: str, workdir: Path) -> dict:
    tools = tool_directories(arm)
    sandbox = sandbox_for(arm)
    # `--add-dir` makes a directory writable to the sandbox as well as
    # readable to the file tools; only the second is wanted for the tools.
    sandbox["filesystem"]["denyWrite"] = [str(directory.resolve()) for directory in tools]
    return {
        "sandbox": sandbox,
        "permissions": {
            "allow": [
                "Bash",
                absolute_rule("Read", workdir),
                absolute_rule("Edit", workdir),
                *(absolute_rule("Read", directory) for directory in tools),
                "Skill",
                "TodoWrite",
            ],
            # Belt and braces: a run must never be able to rewrite the tool it
            # is being measured on.
            "deny": [absolute_rule("Edit", directory) for directory in tools],
        }
    }


def minimal_environment(shim: Path) -> dict[str, str]:
    environment = {name: os.environ[name] for name in ENV_ALLOWLIST if name in os.environ}
    environment["PATH"] = f"{shim}:{environment.get('PATH', '/usr/bin:/bin')}"
    return environment


def write_shim(shim: Path, name: str, target: str) -> None:
    wrapper = shim / name
    wrapper.write_text(f'#!/bin/sh\nexec {target} "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)


def docx_skill_as_plugin(workdir: Path) -> Path:
    """Expose the bundled ``docx`` skill through ``--plugin-dir``.

    ``--restricted`` hides the user's own skill directory, which is what makes
    the arms comparable.  The skill is linked, never copied: it is Anthropic's
    proprietary material and must not be duplicated into this repository.
    """
    plugin = workdir / "docx-skill-plugin"
    (plugin / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "docx-skill-arm", "version": "0.0.0", "description": "benchmark arm"}),
        encoding="utf-8",
    )
    skills = plugin / "skills"
    skills.mkdir(exist_ok=True)
    link = skills / "docx"
    if not link.exists():
        link.symlink_to(DOCX_SKILL, target_is_directory=True)
    return plugin


def claude_command(arm: str, model: str, workdir: Path, max_turns: int) -> tuple[list[str], dict[str, str]]:
    settings = workdir / "settings.json"
    settings.write_text(json.dumps(settings_for(arm, workdir), indent=2), encoding="utf-8")

    # `python`/`python3` must be the interpreter running this benchmark, which
    # has lxml and defusedxml; the system one may not.
    shim = workdir / "bin"
    shim.mkdir(exist_ok=True)
    for name in ("python", "python3"):
        write_shim(shim, name, shlex.quote(sys.executable))
    environment = minimal_environment(shim)

    command = [
        "claude", "-p",
        "--output-format", "json",
        "--model", model,
        "--max-turns", str(max_turns),
        # `--restricted` ignores the user's own settings, skills and plugins, so
        # an arm sees only the tool it is meant to be measuring.  Without it the
        # machine's installed `docx` skill leaks into every arm.
        "--restricted",
        "--tools", TOOLS,
        "--settings", str(settings),
        "--permission-mode", "dontAsk",
        "--strict-mcp-config",
        "--add-dir", str(workdir),
    ]
    if arm == "ours":
        command += ["--plugin-dir", str(OURS_PLUGIN), "--add-dir", str(OURS_PLUGIN)]
    elif arm == "docx-cli":
        command += ["--plugin-dir", str(DOCX_CLI_PLUGIN), "--add-dir", str(DOCX_CLI_PLUGIN)]
        write_shim(shim, "docx", f"bunx {arms.DOCX_CLI_PACKAGE}")
    elif arm == "docx-skill":
        plugin = docx_skill_as_plugin(workdir)
        command += ["--plugin-dir", str(plugin), "--add-dir", str(DOCX_SKILL.resolve())]
    elif arm == "no-skill":
        pass  # the model alone: file tools and a sandboxed shell, no skill
    else:
        raise ValueError(arm)
    return command, environment


def preamble(arm: str) -> str:
    """Nothing arm-specific: each arm has exactly one document skill loaded."""
    return ""


def run_once(arm: str, language: str, attempt: int, model: str, root: Path,
             paragraphs, selected, max_turns: int) -> Run:
    workdir, source, output = build_workdir(root, arm, language, attempt, paragraphs, selected)
    expected = intended_paragraphs(paragraphs, selected)
    minimum = sum(task.expected_deleted + task.expected_inserted for task in selected)

    command, environment = claude_command(arm, model, workdir, max_turns)
    prompt = preamble(arm) + PROMPT.format(
        source=source, instructions=workdir / "edits.txt", output=output, author=arms.AUTHOR
    )

    started = time.perf_counter()
    # The prompt goes on stdin, not as a trailing argument: several of the
    # flags above take a variable number of values and would swallow it.
    completed = subprocess.run(
        command, input=prompt, text=True, capture_output=True, cwd=workdir, env=environment
    )
    elapsed = time.perf_counter() - started
    # Kept for diagnosis: the transcript summary is the only record of what the
    # model tried, including any command the allowlist turned away.
    (workdir / "claude.json").write_text(completed.stdout or "", encoding="utf-8")

    usage = {}
    cost = None
    turns = 0
    detail = ""
    try:
        payload = json.loads(completed.stdout)
        usage = payload.get("usage", {}) or {}
        cost = payload.get("total_cost_usd")
        turns = payload.get("num_turns", 0)
        if payload.get("is_error"):
            detail = str(payload.get("result", ""))[:300]
        denials = payload.get("permission_denials") or []
        if denials:
            shown = "; ".join(
                f"{d.get('tool_name', '?')}: {str((d.get('tool_input') or {}).get('command') or (d.get('tool_input') or {}).get('file_path') or '')[:120]}"
                for d in denials
            )
            detail = (detail + f" [{len(denials)} permission denial(s): {shown}]").strip()
    except (json.JSONDecodeError, TypeError):
        detail = (completed.stderr or completed.stdout).strip()[:300]

    correct = 0
    landed_minimum = 0
    reversible = False
    revision_chars = None
    opens = False
    if output.exists() and measure.opens_as_a_package(output):
        opens = True
        found = measure.measure(output)
        revision_chars = found.revision_chars
        reversible = found.rejected_text == paragraphs
        landed = [
            task
            for task in selected
            if task.paragraph < len(found.accepted_text)
            and found.accepted_text[task.paragraph] == expected[task.paragraph]
        ]
        correct = len(landed)
        landed_minimum = sum(task.expected_deleted + task.expected_inserted for task in landed)
    elif not detail:
        detail = "no readable .docx was produced"

    return Run(
        arm=arm, language=language, attempt=attempt,
        ok=opens and correct == len(selected) and reversible,
        tasks_correct=correct, tasks_total=len(selected), reversible=reversible,
        revision_chars=revision_chars, minimum_revision_chars=minimum,
        landed_minimum_chars=landed_minimum,
        opens_as_package=opens,
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0)),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0)),
        turns=turns, seconds=round(elapsed, 1), cost_usd=cost, detail=detail,
    )


def _mean_ratio(runs: list[Run]) -> float | None:
    ratios = [
        run.revision_chars / run.landed_minimum_chars
        for run in runs
        if run.revision_chars is not None and run.landed_minimum_chars
    ]
    return statistics.mean(ratios) if ratios else None


def summarise(runs: list[Run]) -> ArmSummary:
    first = runs[0]
    graded = [run for run in runs if run.revision_chars is not None]
    chars = statistics.mean(run.revision_chars for run in graded) if graded else None
    costs = [run.cost_usd for run in runs if run.cost_usd is not None]
    return ArmSummary(
        arm=first.arm,
        language=first.language,
        attempts=len(runs),
        solved=statistics.mean(run.tasks_correct for run in runs),
        revision_chars=chars,
        noise_ratio=_mean_ratio(runs),
        input_tokens=statistics.mean(
            run.input_tokens + run.cache_read_tokens + run.cache_creation_tokens for run in runs
        ),
        output_tokens=statistics.mean(run.output_tokens for run in runs),
        seconds=statistics.mean(run.seconds for run in runs),
        cost_usd=statistics.mean(costs) if costs else None,
        runs=runs,
    )


def render(summaries: list[ArmSummary]) -> str:
    lines = [
        "| language | arm | edits applied | marked-up chars | vs minimum | input tokens | output tokens | seconds | USD |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for summary in summaries:
        total = summary.runs[0].tasks_total
        lines.append(
            "| {lang} | {arm} | {solved:.1f}/{total} | {chars} | {ratio} | {inp:,.0f} | {out:,.0f} | {sec:.0f} | {cost} |".format(
                lang=summary.language, arm=summary.arm, solved=summary.solved, total=total,
                chars="—" if summary.revision_chars is None else f"{summary.revision_chars:,.0f}",
                ratio="—" if summary.noise_ratio is None else f"{summary.noise_ratio:.1f}x",
                inp=summary.input_tokens, out=summary.output_tokens, sec=summary.seconds,
                cost="—" if summary.cost_usd is None else f"{summary.cost_usd:.3f}",
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--lang", choices=("ja", "en", "both"), default="both")
    parser.add_argument("--arm", action="append", choices=ARMS, default=None)
    parser.add_argument("--tasks", type=int, default=5, help="edits per document (kept small: each run pays for them)")
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--paragraphs", type=int, default=20)
    parser.add_argument("--out-dir", type=Path, default=HERE / "out" / "agentic")
    args = parser.parse_args()
    # Each `claude -p` runs with its workdir as cwd, so every path handed to it
    # must be absolute or it resolves against the wrong directory.
    args.out_dir = args.out_dir.resolve()

    selected_arms = args.arm or list(ARMS)
    if "docx-skill" in selected_arms and DOCX_SKILL is None:
        if args.arm:
            parser.error("docx skill not found; install it or set DOCX_SKILL_DIR")
        print("docx skill not found; skipping the docx-skill arm "
              "(install it or set DOCX_SKILL_DIR)", file=sys.stderr)
        selected_arms.remove("docx-skill")
    languages = ("ja", "en") if args.lang == "both" else (args.lang,)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    summaries: list[ArmSummary] = []
    for language in languages:
        paragraphs = corpus.paragraphs(language, count=args.paragraphs)
        # One task of each kind first, so a short run still covers insert,
        # delete, replace and rewrite rather than four variations of one.
        per_kind = max(1, -(-args.tasks // 4))
        every = task_module.build(language, per_kind=per_kind, paragraph_count=args.paragraphs)
        selected = every[: args.tasks]
        for arm in selected_arms:
            runs = []
            for attempt in range(1, args.runs + 1):
                print(f"running {language}/{arm} attempt {attempt}/{args.runs}…", file=sys.stderr)
                run = run_once(arm, language, attempt, args.model, args.out_dir,
                               paragraphs, selected, args.max_turns)
                print(f"  applied {run.tasks_correct}/{run.tasks_total}, "
                      f"{run.revision_chars} marked-up chars, {run.seconds:.0f}s "
                      f"{run.detail}", file=sys.stderr)
                runs.append(run)
            summaries.append(summarise(runs))

    table = render(summaries)
    print(table)
    payload = {
        "model": args.model,
        "runs_per_arm": args.runs,
        "tasks_per_run": args.tasks,
        "summaries": [
            {**{key: value for key, value in asdict(summary).items() if key != "runs"},
             "runs": [asdict(run) for run in summary.runs]}
            for summary in summaries
        ],
    }
    (args.out_dir / "agentic.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out_dir / "agentic.md").write_text(table + "\n", encoding="utf-8")
    print(f"\nwrote {args.out_dir / 'agentic.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
