"""How the app reads a student account whose three years of student access have ended."""

from bioaudit.api import Account
from bioaudit.gui.signin import account_summary


def _student(status: str, ends: str = "2029-11-12T00:00:00.000Z") -> Account:
    return Account.from_payload({
        "id": "u1", "email": "stu@example.ac.uk", "tier": "student", "role": "member",
        "studentVerification": {"status": status, "required": True, "eligible": True,
                                "accessEndsAt": ends},
    })


def test_an_expired_student_is_shown_as_ended_not_unverified():
    account = _student("expired")
    assert account.student_access_ended
    assert account.needs_student_verification, "still locked out of scanning"
    assert "student access ended" in account_summary(account)
    assert "unverified" not in account_summary(account)


def test_the_end_date_reads_as_a_day():
    assert _student("verified").student_access_ends_on == "12 November 2029"


def test_a_verified_student_has_not_ended():
    account = _student("verified")
    assert not account.student_access_ended
    assert not account.needs_student_verification


def test_a_team_member_is_never_ended():
    member = Account.from_payload({
        "id": "u2", "email": "m@example.com", "tier": "team", "role": "member",
        "organisationId": "org1",
        "studentVerification": {"status": "expired", "required": False, "accessEndsAt": None},
    })
    assert not member.student_access_ended
    assert member.student_access_ends_on is None
