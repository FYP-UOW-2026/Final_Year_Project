"""Show a validation error as red text directly below the input that caused it.

`flag_field` puts a red label in the layout row under the input, turns the input's border
red and focuses it. The first edit to the input removes both again. The label is inserted
into the layout on first use, so existing forms do not have to reserve space for it, and
it stays hidden (taking no room) while there is nothing to say.

`MESSAGES` is the fixed wording every error uses; `route_api_error` maps a failed request
onto the right input.
"""

from __future__ import annotations

import re
from typing import Optional

from .theme import ERROR_TEXT

_LABEL = "_field_error_label"
_SPACER = "_field_error_spacer"


def _insert_in(layout, widget, label, spacer) -> bool:
    """Put `label` in the row below `widget` inside `layout` or a layout nested in it."""
    from PySide6.QtWidgets import QFormLayout, QVBoxLayout

    if isinstance(layout, QFormLayout):
        row, _role = layout.getWidgetPosition(widget)
        if row >= 0:
            layout.insertRow(row + 1, spacer, label)
            return True
    elif isinstance(layout, QVBoxLayout):
        index = layout.indexOf(widget)
        if index >= 0:
            layout.insertWidget(index + 1, label)
            return True
    for i in range(layout.count()):
        child = layout.itemAt(i).layout()
        if child is not None and _insert_in(child, widget, label, spacer):
            return True
    return False


def _label_for(field):
    """The error label for `field`, created and placed on first use."""
    label = getattr(field, _LABEL, None)
    if label is not None:
        return label

    from PySide6.QtWidgets import QLabel

    label = QLabel("")
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {ERROR_TEXT}; font-size: 12px;")
    label.setVisible(False)
    spacer = QLabel("")
    spacer.setVisible(False)

    # Walk outwards: a field inside a side-by-side row is not in the form itself, its
    # container is, and the message belongs under the whole row.
    widget = field
    while widget is not None:
        parent = widget.parentWidget()
        if parent is None:
            break
        if parent.layout() is not None and _insert_in(parent.layout(), widget, label, spacer):
            break
        widget = parent

    setattr(field, _LABEL, label)
    setattr(field, _SPACER, spacer)
    field.textChanged.connect(lambda *_args, f=field: clear_field_error(f))
    return label


def _repolish(field) -> None:
    field.style().unpolish(field)
    field.style().polish(field)


def flag_field(field, message: str, focus: bool = True) -> None:
    """Show `message` in red under `field` and mark its border red."""
    label = _label_for(field)
    label.setText(message)
    label.setVisible(True)
    getattr(field, _SPACER).setVisible(True)
    field.setProperty("error", True)
    _repolish(field)
    if focus:
        field.setFocus()


def clear_field_error(field) -> None:
    """Undo `flag_field`. Safe to call on a field that was never flagged."""
    label = getattr(field, _LABEL, None)
    if label is None:
        return
    label.setVisible(False)
    label.setText("")
    getattr(field, _SPACER).setVisible(False)
    if field.property("error"):
        field.setProperty("error", False)
        _repolish(field)


def clear_errors(*fields) -> None:
    """Clear every given field; call at the top of a submit handler."""
    for field in fields:
        clear_field_error(field)


# Every validation message the user can see. Server wording is never shown: a failed
# request is reduced to one of these by `server_errors`, so a field always reads the same
# whether the GUI caught the mistake itself or the server did.
PASSWORD_FORMAT = ("Invalid password format. Use at least 12 characters with an upper-case "
                   "letter, a lower-case letter, a number and a symbol.")
MESSAGES = {
    "email": "Invalid email address.",
    "email_empty": "Enter your email address.",
    "email_taken": "An account with this email already exists.",
    "password_empty": "Enter your password.",
    "password_wrong": "Incorrect email or password.",
    "current_password": "Current password is incorrect.",
    "current_password_empty": "Enter your current password.",
    "new_password": PASSWORD_FORMAT,
    "new_password_empty": "Enter a new password.",
    "password_common": "That password is too common. Choose another.",
    "password_same": "Choose a different password from the current one.",
    "confirm": "Passwords do not match.",
    "name": "Enter a name.",
    "name_long": "Name is too long (80 characters max).",
    "code": "Invalid code. Enter the six-digit code from the email.",
    "code_expired": "That code has expired. Send a new one.",
    "token": "Invalid invitation token.",
    "org": "Enter an organisation name.",
    "delete_confirm": "Type DELETE to confirm.",
    "generic": "Check this field and try again.",
    "rate_limited": "Too many attempts. Wait a few minutes and try again.",
    "server": "The server hit a problem. Try again in a moment.",
    "offline": "Cannot reach the server. Check your connection.",
    "suspended": "This account has been suspended.",
    "owner_blocked": "You are the only owner of an organisation with members. Transfer it first.",
    "same_email": "That is already your email address.",
    "same_name": "That is already your display name.",
}

_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email_message(email: str) -> Optional[str]:
    """Catalog wording for a bad email address, or None when it looks fine."""
    if not email:
        return MESSAGES["email_empty"]
    return None if _EMAIL_SHAPE.match(email) else MESSAGES["email"]


def password_message(problem: Optional[str]) -> Optional[str]:
    """Catalog wording for a `password_strength_problem` result (None passes through)."""
    if problem is None:
        return None
    return MESSAGES["password_common"] if "common" in problem.lower() else MESSAGES["new_password"]


# Server field name -> message key. "password" is context dependent (see server_errors).
_FIELD_KEYS = {
    "email": "email", "newEmail": "email",
    "currentPassword": "current_password", "oldPassword": "current_password",
    "newPassword": "new_password",
    "displayName": "name", "name": "name",
    "code": "code", "invitationToken": "token", "token": "token",
    "organisationName": "org", "organizationName": "org", "organisation": "org",
    "confirm": "delete_confirm",
}


def server_errors(exc: BaseException, password_key: str = "new_password"):
    """(server field, fixed message) pairs for a failed request. Pure; no Qt.

    A 400 carries `details` naming each bad field; every one maps to its catalog wording.
    A 409 on an email field means the address is taken. Anything else returns [] and the
    caller falls back to `status_message`.
    """
    details = getattr(exc, "details", None) or []
    out = []
    for d in details:
        field = (d.get("field") if isinstance(d, dict) else None) or ""
        key = password_key if field == "password" else _FIELD_KEYS.get(field, "generic")
        text = (d.get("message") if isinstance(d, dict) else "") or ""
        if key == "new_password" and "common" in text.lower():
            key = "password_common"
        out.append((field, MESSAGES[key]))
    return out


def status_message(exc: BaseException) -> str:
    """Fixed wording for failures that belong to no input (network, 5xx, lockout)."""
    from ..api import ApiClientError

    if isinstance(exc, ApiClientError):
        if exc.organisation_suspended or (
                getattr(exc, "status", None) == 403 and "suspend" in (exc.message or "").lower()):
            return MESSAGES["suspended"]
        status = exc.status
        if status == 429:
            return MESSAGES["rate_limited"]
        if status is None:
            return MESSAGES["offline"]
        if status >= 500:
            return MESSAGES["server"]
        return MESSAGES["generic"]
    if isinstance(exc, (TimeoutError, OSError)):
        return MESSAGES["offline"]
    return MESSAGES["server"]


def route_api_error(exc, fields, on_status, fallback=None, password_key="new_password",
                    overrides=None) -> None:
    """Show a failed request's error under the right input, in catalog wording only.

    fields: {server field name: widget}. A 400 flags each named field it knows; a field
    the form has no input for goes to `fallback` (a widget) or `on_status(text)`.
    overrides: {http status: (message key, widget or None)} for status-specific cases
    such as 401 wrong password or 409 taken email; widget None means the status line.
    """
    status = getattr(exc, "status", None)
    if overrides and status in overrides:
        key, widget = overrides[status]
        if widget is not None:
            return flag_field(widget, MESSAGES[key])
        return on_status(MESSAGES[key])
    pairs = server_errors(exc, password_key) if status == 400 else []
    shown = False
    for field, text in pairs:
        widget = fields.get(field) or fallback
        if widget is not None:
            flag_field(widget, text, focus=not shown)
            shown = True
    if not shown:
        on_status(pairs[0][1] if pairs else status_message(exc))
