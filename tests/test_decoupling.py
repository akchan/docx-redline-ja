"""Checks that keep the plugin a self-contained, relocatable directory.

SKILL.md promises that copying the skill directory anywhere is enough to run
it: no external skill, no external CLI, no path baked in at author time.
Nothing in the scripts enforces that, so it is asserted here.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from synthetic_docx import build_public_domain_docx

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "docx-redline-ja"
PLUGIN_DIR = ROOT / "plugins" / PLUGIN
SKILL_DIR = PLUGIN_DIR / "skills" / PLUGIN
SCRIPTS = SKILL_DIR / "scripts"

#: Third-party packages the scripts may import.  Everything else must be the
#: standard library or a module in ``scripts/`` itself.
ALLOWED_THIRD_PARTY = {"lxml"}

#: Anything that would tie a script to one machine or one install location.
FORBIDDEN_PATTERNS = (
    (r"/Users/", "an absolute macOS home path"),
    (r"/home/[a-z]", "an absolute Linux home path"),
    (r"~/\.claude", "a path inside the user's Claude directory"),
    (r"CLAUDE_PLUGIN_ROOT", "a Claude Code plugin environment variable"),
    (r"\bos\.environ\b", "an environment-variable lookup"),
    (r"\bos\.getenv\b", "an environment-variable lookup"),
)


def own_sources() -> list[Path]:
    return [
        path
        for path in sorted(PLUGIN_DIR.rglob("*"))
        if path.is_file()
        and path.suffix in {".py", ".md", ".json"}
        and "__pycache__" not in path.parts
    ]


def test_plugin_runs_from_a_copy_of_its_own_directory(tmp_path: Path):
    """Copy the skill somewhere unrelated and run it there.

    This is the "依存ゼロの単独可搬" claim in SKILL.md, tested end to end: the
    copy has no repository around it.
    """
    relocated = tmp_path / "elsewhere" / "some-other-name"
    shutil.copytree(SKILL_DIR, relocated)

    source = build_public_domain_docx(tmp_path / "source.docx", "ja")
    records = tmp_path / "records.jsonl"
    record = {"old_text": "名前はまだ無い", "new_text": "名前はまだない"}
    records.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    output = tmp_path / "out.docx"

    completed = subprocess.run(
        [
            sys.executable,
            str(relocated / "scripts" / "apply_redline.py"),
            "--docx", str(source),
            "--findings", str(records),
            "--out", str(output),
            "--author", "Test",
        ],
        text=True,
        capture_output=True,
        cwd=tmp_path,
    )

    assert completed.returncode == 0, completed.stderr
    assert output.exists()
    report = json.loads(output.with_suffix(".result.json").read_text(encoding="utf-8"))
    assert all(value is True for value in report["verification"].values()), report["verification"]


def test_scripts_import_only_the_standard_library_lxml_and_each_other():
    """No vendored tree, no other skill's code, nothing installed on the side."""
    local = {path.stem for path in SCRIPTS.glob("*.py")}
    stdlib = set(sys.stdlib_module_names)
    offenders = []
    for path in sorted(SCRIPTS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top not in local | stdlib | ALLOWED_THIRD_PARTY | {"__future__"}:
                    offenders.append(f"{path.name}: import {name}")
    assert offenders == []
    assert not (SCRIPTS / "vendor").exists()


def test_sources_name_no_machine_specific_path():
    offenders: list[str] = []
    for path in own_sources():
        text = path.read_text(encoding="utf-8")
        for pattern, description in FORBIDDEN_PATTERNS:
            for match in re.finditer(pattern, text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line} contains {description}")
    assert offenders == []


def test_skill_md_links_resolve():
    """Every relative link and `<skill_dir>/…` path in SKILL.md must exist."""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    targets = set(re.findall(r"\]\((?!https?:)([^)#]+)\)", text))
    targets |= {match.lstrip("/") for match in re.findall(r"<skill_dir>/(\S+?)(?=[\s`)]|$)", text)}
    missing = sorted(target for target in targets if not (SKILL_DIR / target).exists())
    assert missing == []


def test_entry_points_report_their_own_usage():
    for command in ([], ["locate"], ["read"]):
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "apply_redline.py"), *command, "-h"],
            text=True,
            capture_output=True,
        )
        assert completed.returncode == 0, completed.stderr


def test_plugin_manifest_matches_the_marketplace():
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    listed = {entry["name"]: entry for entry in marketplace["plugins"]}
    manifest = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))

    assert set(listed) == {PLUGIN}
    assert listed[PLUGIN]["source"] == f"./plugins/{PLUGIN}"
    assert listed[PLUGIN]["version"] == manifest["version"]
    assert manifest["name"] == PLUGIN
    assert (SKILL_DIR / "SKILL.md").exists()
