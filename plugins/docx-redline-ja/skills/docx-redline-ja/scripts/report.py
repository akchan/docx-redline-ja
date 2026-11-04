"""The run report (``result.json``) and its stdout summary."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

#: 7: five verification checks; every position is {paragraph, locator, start, end}.
SCHEMA_VERSION = 7

#: How many ``occurrences`` of one skipped record the stdout summary keeps.
#: A few examples are enough to fix an ``ambiguous`` record.
STDOUT_OCCURRENCES = 5


def render(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    # A lone surrogate from the input (json.loads accepts one) cannot be
    # written as UTF-8; turn it back into its JSON escape.
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def counts(plans) -> dict:
    reasons = Counter(plan.reason for plan in plans if plan.status == "skipped")
    applied = [plan for plan in plans if plan.status == "applied"]
    return {
        "records": len(plans),
        "applied": len(applied),
        "skipped": sum(reasons.values()),
        "hunks": sum(len(plan.hunks) for plan in applied),
        "by_reason": dict(sorted(reasons.items())),
    }


def build_result(
    *,
    source: Path,
    out: Path | None,
    findings: Path,
    author: str,
    date: str,
    plans,
    verification: dict,
    notes: list[str],
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "docx-redline-ja",
        "source": str(source),
        "findings": str(findings),
        "out": None if out is None else str(out),
        "author": author,
        "date": date,
        "counts": counts(plans),
        "verification": verification,
        "notes": notes,
        "records": [plan.to_json() for plan in plans],
    }


def summarize_for_stdout(result: dict, result_path: Path) -> dict:
    """The stdout summary: counts, verification, notes and the skipped records.

    Applied records are left out, since the counts already cover them.  The
    full report stays in ``result.json``.
    """
    records = result["records"]
    skipped = []
    for record in records:
        if record["status"] != "skipped":
            continue
        occurrences = record.get("occurrences")
        if occurrences and len(occurrences) > STDOUT_OCCURRENCES:
            record = dict(record)
            record["occurrences"] = occurrences[:STDOUT_OCCURRENCES]
            record["occurrences_truncated"] = len(occurrences) - STDOUT_OCCURRENCES
        skipped.append(record)

    summary = {
        "tool": result["tool"],
        "out": result["out"],
        "result": str(result_path),
        "counts": result["counts"],
        "verification": result["verification"],
        "notes": result["notes"],
        "records": skipped,
    }
    omitted = len(records) - len(skipped)
    if omitted:
        summary["records_omitted"] = omitted
    return summary
