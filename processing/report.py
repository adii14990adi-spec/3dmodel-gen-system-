"""PASS/WARNING/ERROR validation reports with actionable diagnostics."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SEVERITY_RANK = {"INFO": 0, "PASS": 1, "WARNING": 2, "ERROR": 3}


def _check(entry: dict[str, Any]) -> dict[str, str]:
    return {
        "description": str(entry.get("description", "")),
        "asset": str(entry.get("asset", "")),
        "location": str(entry.get("location", "")),
        "component": str(entry.get("component", "")),
        "severity": str(entry.get("severity", "WARNING")).upper(),
        "code": str(entry.get("code", "")),
        "suggested_fix": str(entry.get("suggested_fix", "")),
    }


def build_report(asset_id: str, kind: str, source: str, checks: list[dict[str, Any]],
                 context: dict[str, Any] | None = None) -> dict[str, Any]:
    normalised = [_check(c) for c in checks]
    summary = {sev: sum(1 for c in normalised if c["severity"] == sev) for sev in ("PASS", "WARNING", "ERROR", "INFO")}
    status = "ERROR" if summary["ERROR"] else ("WARNING" if summary["WARNING"] else "PASS")
    report = {
        "schema_version": "1.0.0", "asset_id": asset_id, "kind": kind,
        "source": source, "status": status, "summary": summary,
        "checks": normalised,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "context": context or {},
    }
    return report


def to_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Validation report — `{report['asset_id']}`", "",
             f"**Status:** {report['status']}  ",
             f"**Kind:** {report['kind']}  ",
             f"**Source:** `{report['source']}`  ",
             f"**Generated:** {report['generated_at']}  ", ""]
    s = report["summary"]
    lines.append(f"PASS {s['PASS']} · WARNING {s['WARNING']} · ERROR {s['ERROR']} · INFO {s['INFO']}")
    lines += ["", "| Severity | Code | Description | Asset | Location | Suggested fix |",
              "|---|---|---|---|---|---|"]
    for c in report["checks"]:
        lines.append("| {severity} | {code} | {description} | {asset} | {location} | {suggested_fix} |".format(
            **{k: v.replace("|", "\\|") for k, v in c.items()}))
    if not report["checks"]:
        lines.append("| PASS | — | No issues found | — | — | — |")
    ctx = report.get("context") or {}
    if ctx:
        lines += ["", "## Processing context", ""]
        for k, v in ctx.items():
            lines.append(f"- **{k}**: {v}")
    lines += ["", "Diagnostics are generated from real file inspection and the official "
              "Khronos glTF Validator (where applicable). Unsupported or lost features are "
              "listed as WARNING/ERROR rather than being silently discarded.", ""]
    return "\n".join(lines)
