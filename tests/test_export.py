import json

from bioaudit.cli import build_parser
from bioaudit.models import Finding, Severity, TestRun
from bioaudit.report import export


def _run(*severities):
    run = TestRun(package="com.example.app")
    for i, sev in enumerate(severities):
        run.add(Finding(category=f"cat-{i}", title=f"Finding {i}", severity=sev,
                        owasp=["M3"], evidence="proof", source="static"))
    return run


def test_sarif_shape():
    doc = json.loads(export.to_sarif(_run(Severity.HIGH, Severity.LOW)))
    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    assert run["tool"]["driver"]["name"] == "BioAudit"
    assert [r["level"] for r in run["results"]] == ["error", "note"]
    assert {r["id"] for r in run["tool"]["driver"]["rules"]} == {"cat-0", "cat-1"}
    assert run["results"][0]["properties"]["owasp"] == ["M3"]


def test_json_round_trips_run_dict():
    run = _run(Severity.MEDIUM)
    assert json.loads(export.to_json(run))["package"] == "com.example.app"


def test_fail_on_threshold():
    run = _run(Severity.MEDIUM)
    assert export.exceeds(run, "medium")
    assert export.exceeds(run, "low")
    assert not export.exceeds(run, "high")
    assert not export.exceeds(run, None)
    assert not export.exceeds(_run(Severity.INFO), "low")
    assert not export.exceeds(_run(), "low")


def test_cli_accepts_ci_flags():
    args = build_parser().parse_args(
        ["scan-apk", "a.apk", "--format", "sarif", "--output", "o.sarif", "--fail-on", "high"])
    assert (args.format, args.output, args.fail_on) == ("sarif", "o.sarif", "high")


def test_env_refresh_token_signs_in(monkeypatch, tmp_path):
    from bioaudit import session
    from bioaudit.api import ApiClient

    monkeypatch.setenv(session.REFRESH_TOKEN_ENV, "tok")
    monkeypatch.setattr(ApiClient, "refresh", lambda self: None)
    monkeypatch.setattr(ApiClient, "get_profile", lambda self: None)
    client = session.restore_session(tmp_path, "http://x/api")
    assert client is not None and client.session.refresh_token == "tok"
    assert not list(tmp_path.iterdir())  # nothing persisted
