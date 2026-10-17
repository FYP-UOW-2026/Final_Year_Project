"""Machine-readable run output for CI pipelines: plain JSON and SARIF 2.1.0.

Kept free of any UI or network import. SARIF is what GitHub code scanning, GitLab and
Azure DevOps ingest, so findings show up next to the build without a custom parser.
"""

from __future__ import annotations

import json

from ..models import Severity, TestRun

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"

# Names accepted by `--fail-on`; INFO is left out because it is never a defect.
FAIL_ON_CHOICES = ("low", "medium", "high", "critical")

_SARIF_LEVEL = {
    Severity.CRITICAL: "error",
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "note",
    Severity.INFO: "note",
}


def to_json(run: TestRun) -> str:
    return json.dumps(run.to_dict(), indent=2)


def to_sarif(run: TestRun) -> str:
    findings = run.ranked()
    rules, seen = [], set()
    for f in findings:
        if f.category in seen:
            continue
        seen.add(f.category)
        rules.append({
            "id": f.category,
            "name": f.title,
            "shortDescription": {"text": f.title},
            "properties": {"tags": f.owasp + f.masvs},
        })

    results = []
    for f in findings:
        message = f.title + (f": {f.evidence}" if f.evidence else "")
        results.append({
            "ruleId": f.category,
            "level": _SARIF_LEVEL[f.severity],
            "message": {"text": message},
            "locations": [{
                "logicalLocations": [{"name": f.component or run.package,
                                      "kind": "module"}],
            }],
            "properties": {
                "severity": f.severity.label,
                "confidence": f.confidence,
                "owasp": f.owasp,
                "masvs": f.masvs,
                "source": f.source,
            },
        })

    doc = {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "BioAudit", "rules": rules}},
            "results": results,
            "properties": {"package": run.package},
        }],
    }
    return json.dumps(doc, indent=2)


def render(run: TestRun, fmt: str) -> str:
    return to_sarif(run) if fmt == "sarif" else to_json(run)


def exceeds(run: TestRun, threshold: str | None) -> bool:
    """True when any finding is at or above `threshold` (a `FAIL_ON_CHOICES` name)."""
    if not threshold:
        return False
    limit = Severity[threshold.upper()]
    return any(f.severity >= limit for f in run.findings)
