"""Evidence redaction before upload (bioaudit/redact.py). No server needed."""

from bioaudit.api import ApiClient
from bioaudit.models import Finding, Severity
from bioaudit.redact import PLACEHOLDER, redact


def test_redacts_keys_inside_longer_identifiers():
    assert redact("access_token=abc123") == f"access_token={PLACEHOLDER}"
    assert redact("refresh_token: zzz9") == f"refresh_token: {PLACEHOLDER}"
    assert redact("db_password=hunter2") == f"db_password={PLACEHOLDER}"


def test_redacts_json_style_keys():
    assert redact('{"password":"hunter2"}') == f'{{"password":"{PLACEHOLDER}"}}'


def test_bearer_token_is_removed_not_just_the_word():
    out = redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz")
    assert "abcdefghijklmnopqrstuvwxyz" not in out
    assert PLACEHOLDER in out


def test_redacting_twice_changes_nothing():
    once = redact("token=abc123")
    assert redact(once) == once


def test_leaves_ordinary_evidence_alone():
    text = "`am start -n com.example.app/.SecretActivity` -> Starting: Intent { cmp=x }"
    assert redact(text) == text


def test_upload_payload_carries_redacted_evidence():
    finding = Finding(
        category="logcat-leak",
        title="Sensitive value logged to logcat",
        severity=Severity.HIGH,
        owasp=["M9"],
        evidence="logcat line: D/App: session_token=s3cr3tvalue",
        source="observers.logcat",
        confidence="confirmed",
    )
    payload = ApiClient._finding_to_payload(finding)
    assert "s3cr3tvalue" not in payload["evidence"]
    # The local finding is untouched; only what is sent is redacted.
    assert "s3cr3tvalue" in finding.evidence
