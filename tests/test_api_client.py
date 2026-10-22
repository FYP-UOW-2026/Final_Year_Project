"""End-to-end tests for the API client against a running backend.

These are skipped unless a server backed by the Firebase emulators is reachable, so the
normal `pytest tests/` run stays offline and needs nothing installed. A server on a live
Firebase project is deliberately skipped too: these tests register accounts, and against
a real project they would be left behind. To run them:

    cd backend
    npm run dev:emulated       # emulators + API on port 4000; leave this running
    pytest tests/test_api_client.py -v

If port 4000 is taken, start it with PORT=4002 and set BIOAUDIT_TEST_API to
http://127.0.0.1:4002/api for the pytest run.

The point of testing against a real server rather than a mock is that a mock would only
prove the client agrees with my assumptions. This proves it agrees with the API.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import string
import urllib.error
import urllib.request
from typing import Optional

import pytest

from bioaudit.api import Account, ApiClient, ApiClientError
from bioaudit.models import Finding, Severity, TestRun

# Overridable so the suite can run while another backend already holds port 4000.
BASE_URL = os.environ.get("BIOAUDIT_TEST_API", "http://127.0.0.1:4000/api")
# Where `npm run dev:emulated` puts Firestore, and the project it runs as.
FIRESTORE_EMULATOR = os.environ.get("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
EMULATOR_PROJECT = "bioaudit-test"


def _emulated_server_available() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/health", timeout=2) as response:
            health = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return health.get("status") == "ok" and health.get("database") == "emulator"


pytestmark = pytest.mark.skipif(
    not _emulated_server_available(),
    reason=f"No emulator-backed backend at {BASE_URL}. See this file's docstring to run these.",
)


def _unique() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


@pytest.fixture
def client() -> ApiClient:
    return ApiClient(BASE_URL)


KNOWN_CODE = "135790"


def _plant_code(collection: str, doc_id: str, key: str) -> None:
    """Make KNOWN_CODE the right answer to a code the server has just emailed.

    The real code only leaves the server by email, so this overwrites the stored hash
    with one for a known code, straight in the Firestore emulator (which accepts the
    "owner" token from anyone). `key` is what the server hashes the code with: the uid
    for an account's code, the address for a sign-up code.
    """
    doc = (f"http://{FIRESTORE_EMULATOR}/v1/projects/{EMULATOR_PROJECT}/databases/(default)"
           f"/documents/{collection}/{doc_id}?updateMask.fieldPaths=codeHash")
    body = {"fields": {"codeHash": {
        "stringValue": hashlib.sha256(f"{key}:{KNOWN_CODE}".encode()).hexdigest()}}}
    request = urllib.request.Request(
        doc, data=json.dumps(body).encode(), method="PATCH",
        headers={"Content-Type": "application/json", "Authorization": "Bearer owner"})
    urllib.request.urlopen(request, timeout=5).close()


def _register_student(client: ApiClient, email: str, display_name: Optional[str] = None,
                      university_id: Optional[str] = None) -> Account:
    """Sign up as a student without an inbox, entering the code as a user would."""
    client.send_signup_code(email, university_id)
    _plant_code("signupVerifications", hashlib.sha256(email.encode()).hexdigest(), email)
    return client.register(email, "pytest-password-1", display_name,
                           university_id=university_id, code=KNOWN_CODE)


def _verify_student(client: ApiClient) -> None:
    """Enter the code sent to a signed-in student's new address."""
    uid = client.account.id
    _plant_code("studentVerifications", uid, uid)
    client.verify_student_code(KNOWN_CODE)


@pytest.fixture
def signed_in(client: ApiClient) -> ApiClient:
    """A student account that can scan, which means a verified student."""
    _register_student(client, f"pytest-{_unique()}@example.ac.uk", "Pytest User")
    return client


def _sample_run(package: str = "com.example.app") -> TestRun:
    run = TestRun(package=package, device_serial="pytest-device")
    run.add(Finding(
        category="exported-auth-bypass",
        title="Exported activity reachable without authentication",
        severity=Severity.CRITICAL,
        owasp=["M3", "M1"],
        evidence="`am start -n com.example.app/.SecretActivity` -> Starting: Intent{...}",
        source="ipc_oracle",
        confidence="confirmed",
        component="com.example.app.SecretActivity",
    ))
    run.add(Finding(
        category="logcat-leak",
        title="Sensitive value logged to logcat",
        severity=Severity.HIGH,
        owasp=["M9", "M6"],
        evidence="logcat line: auth session token=REDACTED_IN_TEST",
        source="observers.logcat",
        confidence="confirmed",
    ))
    return run


def test_health_reports_status(client: ApiClient):
    assert client.health()["status"] == "ok"


def test_register_then_read_profile(client: ApiClient):
    email = f"pytest-{_unique()}@example.ac.uk"
    account = _register_student(client, email, "Pytest User")

    assert account.email == email
    assert account.tier == "student"
    assert not account.is_team
    assert not account.needs_student_verification
    assert client.signed_in

    fetched = client.get_profile()
    assert fetched.display_name == "Pytest User"


def test_student_cannot_register_without_a_code(client: ApiClient):
    with pytest.raises(ApiClientError) as exc:
        client.register(f"pytest-{_unique()}@example.ac.uk", "pytest-password-1")
    assert exc.value.status == 400
    assert not client.signed_in


def test_a_fresh_signup_code_has_not_bounced(client: ApiClient):
    email = f"pytest-{_unique()}@example.ac.uk"
    sent = client.send_signup_code(email)
    assert client.signup_code_status(email, sent["sendId"]) == {"status": "sent"}

    with pytest.raises(ApiClientError) as exc:
        client.signup_code_status(email, "0" * 32)
    assert exc.value.status == 404


def test_signup_code_needs_a_university_address(client: ApiClient):
    with pytest.raises(ApiClientError) as exc:
        client.send_signup_code(f"pytest-{_unique()}@example.com")
    assert exc.value.status == 400
    assert "students" in exc.value.message


def test_login_with_wrong_password_is_refused(client: ApiClient):
    email = f"pytest-{_unique()}@example.ac.uk"
    _register_student(client, email)
    client.logout()

    with pytest.raises(ApiClientError) as exc:
        client.login(email, "definitely-wrong")
    assert exc.value.status == 401


def test_unknown_email_and_wrong_password_look_identical(client: ApiClient):
    """The API must not reveal which accounts exist. See the backend's auth service."""
    email = f"pytest-{_unique()}@example.ac.uk"
    _register_student(client, email)
    client.logout()

    with pytest.raises(ApiClientError) as wrong_password:
        client.login(email, "definitely-wrong")
    with pytest.raises(ApiClientError) as unknown_email:
        client.login(f"nobody-{_unique()}@example.com", "definitely-wrong")

    assert wrong_password.value.message == unknown_email.value.message


def test_universities_are_listed(client: ApiClient):
    universities = client.list_universities()
    oxford = next(u for u in universities if u["id"] == "oxford")
    assert oxford["domains"] == ["ox.ac.uk"]


def test_registering_needs_an_email_from_the_chosen_university(client: ApiClient):
    with pytest.raises(ApiClientError) as exc:
        client.send_signup_code(f"pytest-{_unique()}@example.com", "oxford")
    assert exc.value.status == 400
    assert "@ox.ac.uk" in exc.value.message

    account = _register_student(client, f"pytest-{_unique()}@ox.ac.uk", university_id="oxford")
    assert account.student_verification["university"]["id"] == "oxford"


def test_student_who_changes_email_must_verify_again(client: ApiClient):
    _register_student(client, f"pytest-{_unique()}@example.ac.uk")
    new_email = f"pytest-{_unique()}@example.ac.uk"
    client.change_email(new_email, "pytest-password-1")
    # Firebase ends every session when an account's email changes, so sign in again.
    account = client.login(new_email, "pytest-password-1")
    assert account.needs_student_verification
    with pytest.raises(ApiClientError) as exc:
        client.upload_run(_sample_run(), authorised=True)
    assert exc.value.status == 403

    _verify_student(client)
    assert not client.account.needs_student_verification
    assert client.upload_run(_sample_run(), authorised=True)["scan"]["id"]


def test_upload_run_and_read_it_back(signed_in: ApiClient):
    run = _sample_run()
    result = signed_in.upload_run(run, authorised=True)

    assert result["scan"]["findingCount"] == 2
    assert result["scan"]["counts"]["critical"] == 1
    assert result["scan"]["counts"]["high"] == 1

    history = signed_in.list_history()
    assert len(history["scans"]) >= 1
    assert history["tier"] == "student"

    scan = signed_in.get_scan(result["scan"]["id"])
    categories = {f["category"] for f in scan["findings"]}
    assert categories == {"exported-auth-bypass", "logcat-leak"}


def test_device_scan_without_authorisation_is_refused(signed_in: ApiClient):
    with pytest.raises(ApiClientError) as exc:
        signed_in.upload_run(_sample_run(), authorised=False)
    assert exc.value.status == 400
    assert "authorised" in exc.value.message.lower()


def test_severity_names_survive_the_round_trip(signed_in: ApiClient):
    """Severity is an IntEnum locally and a lowercase string on the wire."""
    result = signed_in.upload_run(_sample_run(), authorised=True)
    scan = signed_in.get_scan(result["scan"]["id"])
    severities = [f["severity"] for f in scan["findings"]]
    assert "critical" in severities
    assert all(s == s.lower() for s in severities), "severities should be lowercase"


def test_student_is_blocked_from_team_features(signed_in: ApiClient):
    first = signed_in.upload_run(_sample_run("com.a"), authorised=True)
    second = signed_in.upload_run(_sample_run("com.a"), authorised=True)

    with pytest.raises(ApiClientError) as compare:
        signed_in.compare_scans(first["scan"]["id"], second["scan"]["id"])
    assert compare.value.status == 403

    with pytest.raises(ApiClientError) as export:
        signed_in.export_report(first["scan"]["id"])
    assert export.value.status == 403


def test_team_member_can_compare_and_export(client: ApiClient):
    _, organisation = client.register_admin(
        f"pytest-admin-{_unique()}@example.com", "pytest-password-1", "Pytest Team")
    member_email = f"pytest-member-{_unique()}@example.com"
    invitation = client.invite_member(organisation["id"], member_email)

    member = ApiClient(BASE_URL)
    account = member.register(member_email, "pytest-password-1",
                              invitation_token=invitation["token"])
    # Registering with an invitation joins the team in the same step.
    assert account.is_team
    assert account.tier == "team"

    baseline = member.upload_run(_sample_run("com.compare"), authorised=True)

    fixed = _sample_run("com.compare")
    fixed.findings = [f for f in fixed.findings if f.category != "logcat-leak"]
    current = member.upload_run(fixed, authorised=True)

    comparison = member.compare_scans(baseline["scan"]["id"], current["scan"]["id"])
    assert comparison["summary"]["resolved"] == 1
    assert comparison["summary"]["unchanged"] == 1

    html = member.export_report(current["scan"]["id"])
    assert "<!doctype html>" in html.lower()
    assert "com.compare" in html


def test_delete_and_clear_history(signed_in: ApiClient):
    result = signed_in.upload_run(_sample_run(), authorised=True)
    signed_in.delete_scan(result["scan"]["id"])

    signed_in.upload_run(_sample_run(), authorised=True)
    removed = signed_in.clear_history()
    assert removed >= 1
    assert signed_in.list_history()["scans"] == []


def test_admin_can_invite_and_manage_a_member(client: ApiClient):
    admin_email = f"pytest-admin-{_unique()}@example.com"
    account, organisation = client.register_admin(
        admin_email, "pytest-password-1", "Pytest Security Team", "Owner"
    )
    assert account.is_admin
    org_id = organisation["id"]

    member_email = f"pytest-member-{_unique()}@example.com"
    invitation = client.invite_member(org_id, member_email)
    assert invitation["token"], "the one-time token should be returned"

    member = ApiClient(BASE_URL)
    member.register(member_email, "pytest-password-1", "Member",
                    invitation_token=invitation["token"])

    members = client.list_members(org_id)
    assert member_email in {m["email"] for m in members}

    # A member must not reach the admin endpoints.
    with pytest.raises(ApiClientError) as exc:
        member.list_members(org_id)
    assert exc.value.status == 403


def test_admin_cannot_see_member_findings(client: ApiClient):
    """The privacy line this project promises: an app's code stays on its own machine."""
    admin_email = f"pytest-admin-{_unique()}@example.com"
    _, organisation = client.register_admin(
        admin_email, "pytest-password-1", "Pytest Team"
    )
    org_id = organisation["id"]

    member_email = f"pytest-member-{_unique()}@example.com"
    invitation = client.invite_member(org_id, member_email)

    member = ApiClient(BASE_URL)
    member_account = member.register(member_email, "pytest-password-1",
                                     invitation_token=invitation["token"])
    member.upload_run(_sample_run("com.member.private"), authorised=True)

    scans = client.member_scans(org_id, member_account.id)
    assert len(scans) >= 1
    assert "findings" not in scans[0], "an admin must not receive the findings"
    assert scans[0]["counts"], "but the severity counts should be visible"


def test_error_message_is_human_readable(client: ApiClient):
    with pytest.raises(ApiClientError) as exc:
        client.register("not-an-email", "short")
    assert exc.value.status == 400
    # The GUI shows these directly, so they have to read as sentences.
    assert exc.value.details, "validation errors should list the offending fields"
