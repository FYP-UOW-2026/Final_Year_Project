"""Dialogs for managing your own account.

Covers the functional-hierarchy items that belong to a signed-in user rather than to
scanning: update account details, change email address, change password, delete account,
join an organisation from an invitation, and verify a student email.

Three of these end the session on purpose. Changing a password signs every device out,
and deleting an account obviously does too. Each returns a result telling the caller
whether it has to sign the user out, rather than leaving the window in a state where it
thinks it is still signed in.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..api import ApiClient
from ..password_policy import password_strength_problem
from .field_error import (
    MESSAGES, clear_errors, email_message, flag_field, password_message, route_api_error,
    status_message,
)
from .formatting import tidy_code
from .theme import DARK, ERROR_TEXT, SUCCESS_TEXT


_NAME_MAX = 80


@dataclass
class DialogResult:
    """What the caller needs to know after a dialog closes."""

    changed: bool = False
    signed_out: bool = False      # the session is gone; ask the user to sign in again
    account_deleted: bool = False
    message: str = ""


def show_profile_dialog(parent, client: ApiClient) -> DialogResult:
    """Profile, email, password, and account deletion, in one tabbed window.

    Grouped together because they are all "settings about me", and separated into tabs
    because the consequences differ sharply: renaming yourself is harmless, deleting your
    account is not.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
        QPushButton, QTabWidget, QVBoxLayout, QWidget,
    )

    result = DialogResult()

    class ProfileDialog(QDialog):
        def __init__(self) -> None:
            super().__init__(parent)
            self.setWindowTitle("Account settings")
            self.setMinimumWidth(520)

            layout = QVBoxLayout(self)

            account = client.account
            header = QLabel(
                f"<b>{account.email if account else 'Unknown'}</b>"
                f"<span style='color:{DARK['muted']}'> &nbsp;·&nbsp; "
                f"{('team' if account.is_team else 'student') if account else '?'} account"
                f"{' · admin' if account and account.is_admin else ''}</span>"
            )
            header.setTextFormat(Qt.RichText)
            layout.addWidget(header)

            tabs = QTabWidget()
            tabs.addTab(self._profile_tab(), "Profile")
            tabs.addTab(self._email_tab(), "Email")
            tabs.addTab(self._password_tab(), "Password")
            tabs.addTab(self._danger_tab(), "Delete account")
            layout.addWidget(tabs)

            self.status = QLabel("")
            self.status.setWordWrap(True)
            layout.addWidget(self.status)

            close_row = QHBoxLayout()
            close_row.addStretch(1)
            close = QPushButton("Close")
            close.clicked.connect(self.accept)
            close_row.addWidget(close)
            layout.addLayout(close_row)

        # --- helpers ---------------------------------------------------
        def _say(self, text: str, error: bool = False) -> None:
            colour = ERROR_TEXT if error else SUCCESS_TEXT
            self.status.setText(f"<span style='color:{colour}'>{text}</span>")

        def _hint(self, text: str) -> QLabel:
            label = QLabel(text)
            label.setWordWrap(True)
            label.setObjectName("hint")
            return label

        # --- profile ---------------------------------------------------
        def _profile_tab(self) -> QWidget:
            w = QWidget()
            outer = QVBoxLayout(w)
            form = QFormLayout()
            self.name_edit = QLineEdit(
                (client.account.display_name if client.account else "") or "")
            self.name_edit.setPlaceholderText("The name shown on your account")
            form.addRow("Display name:", self.name_edit)
            outer.addLayout(form)

            save = QPushButton("Save display name")
            save.clicked.connect(self._save_name)
            outer.addWidget(save)
            outer.addStretch(1)
            return w

        def _fail(self, field, message: str) -> None:
            """Show `message` under `field`, and clear the general status line."""
            self.status.setText("")
            flag_field(field, message)

        def _fail_request(self, exc, fields, overrides=None, password_key="new_password") -> None:
            """Show a failed request under the input it names, in catalog wording only.

            `fields` maps a server field name to its input. `overrides` maps an HTTP
            status to (message key, input or None) - a 401 on a password-checked request
            means a wrong password, not an expired session. Anything else, and anything
            not tied to an input, goes to the general status line.
            """
            self.status.setText("")
            route_api_error(exc, fields, lambda text: self._say(text, error=True),
                            password_key=password_key, overrides=overrides)

        def _save_name(self) -> None:
            clear_errors(self.name_edit)
            name = self.name_edit.text().strip()
            current = ((client.account.display_name if client.account else "") or "").strip()
            if not name:
                return self._fail(self.name_edit, MESSAGES["name"])
            if len(name) > _NAME_MAX:
                return self._fail(self.name_edit, MESSAGES["name_long"])
            if name == current:
                return self._fail(self.name_edit, MESSAGES["same_name"])
            try:
                self.setCursor(Qt.WaitCursor)
                client.update_display_name(name)
            except Exception as exc:  # noqa: BLE001 - a slot must not raise
                self._fail_request(exc, {"displayName": self.name_edit, "name": self.name_edit},
                                   {409: ("generic", self.name_edit)})
                return
            finally:
                self.setCursor(Qt.ArrowCursor)
            result.changed = True
            self._say("Display name updated.")

        # --- email -----------------------------------------------------
        def _email_tab(self) -> QWidget:
            w = QWidget()
            outer = QVBoxLayout(w)
            form = QFormLayout()
            self.new_email = QLineEdit()
            self.new_email.setPlaceholderText("you@example.com")
            self.email_password = QLineEdit()
            self.email_password.setEchoMode(QLineEdit.Password)
            form.addRow("New email:", self.new_email)
            form.addRow("Current password:", self.email_password)
            outer.addLayout(form)
            outer.addWidget(self._hint(
                "Your password is required. Changing the address on an account is enough to "
                "take it over through a password reset, so a signed-in session alone is not "
                "sufficient."))
            change = QPushButton("Change email address")
            change.clicked.connect(self._change_email)
            outer.addWidget(change)
            outer.addStretch(1)
            return w

        def _change_email(self) -> None:
            clear_errors(self.new_email, self.email_password)
            email = self.new_email.text().strip()
            password = self.email_password.text()
            current = (client.account.email if client.account else "").lower()
            if bad := email_message(email):
                return self._fail(self.new_email, bad)
            if email.lower() == current:
                return self._fail(self.new_email, MESSAGES["same_email"])
            if not password:
                return self._fail(self.email_password, MESSAGES["current_password_empty"])
            # 401/403 here is the password check; 409 is an address already taken, and
            # 400 is the server rejecting the address itself.
            wrong = ("current_password", self.email_password)
            try:
                self.setCursor(Qt.WaitCursor)
                client.change_email(email, password)
            except Exception as exc:  # noqa: BLE001 - a slot must not raise
                self._fail_request(
                    exc, {"email": self.new_email, "newEmail": self.new_email,
                          "password": self.email_password,
                          "currentPassword": self.email_password},
                    {401: wrong, 403: wrong, 409: ("email_taken", self.new_email)},
                    password_key="current_password")
                return
            finally:
                self.setCursor(Qt.ArrowCursor)
            result.changed = True
            self.email_password.clear()
            self._say("Email address updated. Verify the new address when prompted.")

        # --- password --------------------------------------------------
        def _password_tab(self) -> QWidget:
            w = QWidget()
            outer = QVBoxLayout(w)
            form = QFormLayout()
            self.old_password = QLineEdit()
            self.old_password.setEchoMode(QLineEdit.Password)
            self.new_password = QLineEdit()
            self.new_password.setEchoMode(QLineEdit.Password)
            self.new_password.setPlaceholderText("12+ characters, mixed case, digit, symbol")
            self.confirm_password = QLineEdit()
            self.confirm_password.setEchoMode(QLineEdit.Password)
            form.addRow("Current password:", self.old_password)
            form.addRow("New password:", self.new_password)
            form.addRow("Confirm password:", self.confirm_password)
            outer.addLayout(form)
            outer.addWidget(self._hint(
                "Changing your password signs you out on every device, including this one. "
                "If the old password may have leaked, leaving other sessions alive would "
                "defeat the point of changing it."))
            change = QPushButton("Change password")
            change.clicked.connect(self._change_password)
            outer.addWidget(change)
            outer.addStretch(1)
            return w

        def _change_password(self) -> None:
            clear_errors(self.old_password, self.new_password, self.confirm_password)
            old = self.old_password.text()
            new = self.new_password.text()
            if not old:
                return self._fail(self.old_password, MESSAGES["current_password_empty"])
            if not new:
                return self._fail(self.new_password, MESSAGES["new_password_empty"])
            if new == old:
                return self._fail(self.new_password, MESSAGES["password_same"])
            if problem := password_message(password_strength_problem(new)):
                return self._fail(self.new_password, problem)
            if new != self.confirm_password.text():
                return self._fail(self.confirm_password, MESSAGES["confirm"])

            # 401/403 is the current-password check; any 400 is about the new password.
            wrong = ("current_password", self.old_password)
            try:
                self.setCursor(Qt.WaitCursor)
                client.change_password(old, new)
            except Exception as exc:  # noqa: BLE001 - a slot must not raise
                self._fail_request(
                    exc, {"currentPassword": self.old_password, "oldPassword": self.old_password,
                          "newPassword": self.new_password, "password": self.new_password},
                    {401: wrong, 403: wrong})
                return
            finally:
                self.setCursor(Qt.ArrowCursor)

            result.changed = True
            result.signed_out = True
            result.message = "Password changed. Sign in again with your new password."
            QMessageBox.information(self, "Password changed", result.message)
            self.accept()

        # --- delete ----------------------------------------------------
        def _danger_tab(self) -> QWidget:
            w = QWidget()
            outer = QVBoxLayout(w)

            warning = QLabel(
                f"<b style='color:{ERROR_TEXT}'>This cannot be undone.</b><br>"
                "Deleting your account removes your profile and every scan saved to it on "
                "the server. Results already saved on this computer are kept."
            )
            warning.setWordWrap(True)
            warning.setTextFormat(Qt.RichText)
            outer.addWidget(warning)

            form = QFormLayout()
            self.delete_password = QLineEdit()
            self.delete_password.setEchoMode(QLineEdit.Password)
            self.delete_confirm = QLineEdit()
            self.delete_confirm.setPlaceholderText("Type DELETE to confirm")
            form.addRow("Current password:", self.delete_password)
            form.addRow("Confirmation:", self.delete_confirm)
            outer.addLayout(form)

            delete = QPushButton("Delete my account")
            delete.clicked.connect(self._delete_account)
            outer.addWidget(delete)
            outer.addStretch(1)
            return w

        def _delete_account(self) -> None:
            clear_errors(self.delete_password, self.delete_confirm)
            password = self.delete_password.text()
            if not password:
                return self._fail(self.delete_password, MESSAGES["current_password_empty"])
            if self.delete_confirm.text().strip() != "DELETE":
                return self._fail(self.delete_confirm, MESSAGES["delete_confirm"])

            confirm = QMessageBox.question(
                self, "Delete account",
                "Permanently delete your account and everything saved to it on the server?\n\n"
                "This cannot be undone.",
            )
            if confirm != QMessageBox.Yes:
                return

            # A 409 (sole owner of an organisation with members) is not about either box,
            # so it goes to the general line; a 401/403 is the password check.
            wrong = ("current_password", self.delete_password)
            try:
                self.setCursor(Qt.WaitCursor)
                client.delete_account(password)
            except Exception as exc:  # noqa: BLE001 - a slot must not raise
                self._fail_request(
                    exc, {"password": self.delete_password, "confirm": self.delete_confirm},
                    {401: wrong, 403: wrong, 409: ("owner_blocked", None)},
                    password_key="current_password")
                return
            finally:
                self.setCursor(Qt.ArrowCursor)

            result.changed = True
            result.signed_out = True
            result.account_deleted = True
            result.message = "Your account has been deleted."
            QMessageBox.information(self, "Account deleted", result.message)
            self.accept()

    ProfileDialog().exec()
    return result


def show_join_organisation_dialog(parent, client: ApiClient) -> DialogResult:
    """Redeem an invitation token to join a team.

    The token is a long random string an admin sends you. It works once, so a clear
    message on failure matters: a used or expired token is the most likely problem.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QDialog, QDialogButtonBox, QLabel, QMessageBox, QTextEdit, QVBoxLayout,
    )

    result = DialogResult()

    class JoinDialog(QDialog):
        def __init__(self) -> None:
            super().__init__(parent)
            self.setWindowTitle("Join an organisation")
            self.setMinimumWidth(520)

            layout = QVBoxLayout(self)
            intro = QLabel(
                "Paste the invitation token an admin sent you. Joining a team lets its admin "
                "see that you ran a scan and how many problems it found, but never the "
                "findings themselves."
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)

            self.token_edit = QTextEdit()
            self.token_edit.setPlaceholderText("Paste the invitation token here")
            self.token_edit.setFixedHeight(70)
            layout.addWidget(self.token_edit)

            self.status = QLabel("")
            self.status.setWordWrap(True)
            layout.addWidget(self.status)

            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.button(QDialogButtonBox.Ok).setText("Join")
            buttons.accepted.connect(self._join)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _join(self) -> None:
            clear_errors(self.token_edit)
            self.status.setText("")
            token = self.token_edit.toPlainText().strip()
            if not token:
                return flag_field(self.token_edit, MESSAGES["token"])
            if len(token) < 16 or any(c.isspace() for c in token):
                return flag_field(self.token_edit, MESSAGES["token"])
            try:
                self.setCursor(Qt.WaitCursor)
                joined = client.join_organisation(token)
            except Exception as exc:  # noqa: BLE001 - a slot must not raise
                # Used, expired, wrong, or already in an organisation: all one wording.
                bad = ("token", self.token_edit)
                route_api_error(
                    exc, {"invitationToken": self.token_edit, "token": self.token_edit},
                    lambda text: self.status.setText(f"<span style='color:{ERROR_TEXT}'>{text}</span>"),
                    overrides={400: bad, 404: bad, 409: bad, 410: bad})
                return
            finally:
                self.setCursor(Qt.ArrowCursor)

            result.changed = True
            # Joining changes the account's role, and the server revokes sessions so the new
            # permissions take effect, so the app has to sign in again.
            result.signed_out = True
            name = (joined.get("organisation") or {}).get("name", "the organisation")
            result.message = f"You have joined {name}. Sign in again to refresh your permissions."
            QMessageBox.information(self, "Joined", result.message)
            self.accept()

    JoinDialog().exec()
    return result


def show_student_verification_dialog(parent, client: ApiClient) -> DialogResult:
    """Enter the code emailed to a university address, unlocking scanning.

    Personal accounts are for students, so a student account cannot run assessments
    until this is done. An account whose email is not at a university gets an
    explanation instead of a code box, since no code could ever be sent to it.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
        QPushButton, QVBoxLayout,
    )

    result = DialogResult()
    account = client.account
    sv = account.student_verification if account else {}
    eligible = bool(sv.get("eligible"))
    university = (sv.get("university") or {}).get("name")
    # No code has gone out yet (an older account, or one whose email just changed), so
    # the first press sends one rather than re-sending.
    never_sent = sv.get("status") == "unverified"

    class StudentDialog(QDialog):
        def __init__(self) -> None:
            super().__init__(parent)
            self.setWindowTitle("Verify your student email")
            self.setMinimumWidth(480)
            layout = QVBoxLayout(self)

            if not eligible:
                what = f"a {university}" if university else "a university"
                note = QLabel(
                    "Personal accounts are for students, and "
                    f"<b>{account.email if account else 'this address'}</b> is not "
                    f"{what} email address.<br><br>To run scans, change your account "
                    "email to your university address in Account settings and verify "
                    "it, or join a team with an invitation from its admin.<br><br>You "
                    "can still view and delete any history you already have.")
                note.setWordWrap(True)
                layout.addWidget(note)
                buttons = QDialogButtonBox(QDialogButtonBox.Close)
                buttons.rejected.connect(self.reject)
                layout.addWidget(buttons)
                return

            where = f" at <b>{university}</b>" if university else ""
            if never_sent:
                text = (f"Personal accounts are for students{where}. Press <b>Send code</b> and we "
                        f"will email a six-digit code to <b>{account.email}</b>.")
            else:
                text = (f"Personal accounts are for students{where}. Enter the six-digit code we "
                        f"emailed to <b>{account.email}</b> to unlock scanning. Codes expire "
                        "after 15 minutes.")
            intro = QLabel(text)
            intro.setWordWrap(True)
            layout.addWidget(intro)

            self.code_edit = QLineEdit()
            self.code_edit.setPlaceholderText("123456")
            # No six-character limit: it would cut a pasted "1 2 3 4 5 6" down to "1 2 3"
            # before it could be tidied to its digits.
            self.code_edit.textEdited.connect(
                lambda text: text != tidy_code(text) and self.code_edit.setText(tidy_code(text)))
            self.code_edit.setAlignment(Qt.AlignCenter)
            self.code_edit.setStyleSheet("font-size: 20px; letter-spacing: 6px;")
            self.code_edit.returnPressed.connect(self._verify)
            layout.addWidget(self.code_edit)

            self.status = QLabel("")
            self.status.setWordWrap(True)
            layout.addWidget(self.status)

            row = QHBoxLayout()
            resend = QPushButton("Send code" if never_sent else "Send a new code")
            resend.clicked.connect(self._resend)
            row.addWidget(resend)
            row.addStretch(1)
            later = QPushButton("Later")
            later.clicked.connect(self.reject)
            row.addWidget(later)
            verify = QPushButton("Verify")
            verify.setDefault(True)
            verify.clicked.connect(self._verify)
            row.addWidget(verify)
            layout.addLayout(row)

        def _show(self, message: str, error: bool) -> None:
            colour = ERROR_TEXT if error else SUCCESS_TEXT
            self.status.setText(f"<span style='color:{colour}'>{message}</span>")

        def _resend(self) -> None:
            clear_errors(self.code_edit)
            try:
                self.setCursor(Qt.WaitCursor)
                message = client.send_student_code()
            except Exception as exc:  # noqa: BLE001 - a slot must not raise
                # Sending is not about the code box (rate limit, already verified, not
                # required), so it uses the general line.
                self._show(status_message(exc), error=True)
                return
            finally:
                self.setCursor(Qt.ArrowCursor)
            self._show(message, error=False)

        def _verify(self) -> None:
            clear_errors(self.code_edit)
            self.status.setText("")
            code = self.code_edit.text().strip()
            if not code:
                return flag_field(self.code_edit, MESSAGES["code"])
            if len(code) != 6 or not code.isdigit():
                return flag_field(self.code_edit, MESSAGES["code"])
            try:
                self.setCursor(Qt.WaitCursor)
                client.verify_student_code(code)
            except Exception as exc:  # noqa: BLE001 - a slot must not raise
                # Wrong, expired, none waiting, already verified: all one wording.
                bad = ("code", self.code_edit)
                route_api_error(
                    exc, {"code": self.code_edit},
                    lambda text: self._show(text, error=True),
                    overrides={400: bad, 401: bad, 404: bad, 409: bad, 410: bad})
                return
            finally:
                self.setCursor(Qt.ArrowCursor)

            result.changed = True
            result.message = "Your university email is verified. You can now run scans."
            QMessageBox.information(self, "Verified", result.message)
            self.accept()

    StudentDialog().exec()
    return result
