"""field_error's wording logic, tested without Qt (route_api_error needs widgets)."""

import pytest

from bioaudit.api import ApiClientError
from bioaudit.gui.field_error import (
    MESSAGES, email_message, password_message, server_errors, status_message,
)


def err(status, details=None, message="Invalid request body."):
    return ApiClientError(message, status=status, details=details or [])


def test_screenshot_case_bad_email_is_fixed_wording():
    exc = err(400, [{"field": "email", "message": "Enter a valid email address."}])
    assert server_errors(exc) == [("email", "Invalid email address.")]


def test_password_field_uses_context_key():
    exc = err(400, [{"field": "password", "message": "too short"}])
    assert server_errors(exc, "current_password")[0][1] == MESSAGES["current_password"]
    assert server_errors(exc)[0][1] == MESSAGES["new_password"]


def test_common_password_detail():
    exc = err(400, [{"field": "newPassword", "message": "That password is too common"}])
    assert server_errors(exc)[0][1] == MESSAGES["password_common"]


def test_unknown_field_is_generic():
    exc = err(400, [{"field": "whatever", "message": "x"}])
    assert server_errors(exc)[0][1] == MESSAGES["generic"]


@pytest.mark.parametrize("status,key", [(429, "rate_limited"), (None, "offline"),
                                        (500, "server"), (404, "generic")])
def test_status_message(status, key):
    assert status_message(err(status)) == MESSAGES[key]


def test_suspended():
    assert status_message(err(403, message="Organisation suspended")) == MESSAGES["suspended"]


def test_non_api_errors():
    assert status_message(TimeoutError()) == MESSAGES["offline"]
    assert status_message(ValueError("boom")) == MESSAGES["server"]


def test_local_checks():
    assert email_message("") == MESSAGES["email_empty"]
    assert email_message("ju") == MESSAGES["email"]
    assert email_message("a@b.co") is None
    assert password_message(None) is None
    assert password_message("Too common") == MESSAGES["password_common"]
    assert password_message("Too short") == MESSAGES["new_password"]


def test_no_raw_server_text_ever_shown():
    exc = err(400, [{"field": "email", "message": "Enter a valid email address."}])
    shown = [m for _, m in server_errors(exc)] + [status_message(exc)]
    assert not any("Invalid request" in m or "Enter a valid" in m for m in shown)
