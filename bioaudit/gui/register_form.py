"""The "Create account" form, shared by the welcome and sign-in dialogs.

Modelled on the way GitHub Education and Apple's student pricing work: you say which
university you attend by picking it from a searchable list, and the email you register
with has to be one that university issues. Send code has the server email a six-digit
code to prove you can read that inbox, and the account is only created once that code
is entered, so a mistyped address simply never gets a code; if it bounces, the form
says so. A second mode covers the other kind of account, a team whose creator
becomes its admin, which needs no student check at all.

PySide6 is imported inside make_register_form so this module, like the rest of gui/,
costs nothing to import when the GUI is not running.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from ..api import ApiClient, ApiClientError
from ..password_policy import password_strength_problem
from .field_error import MESSAGES, email_message, password_message, route_api_error
from .formatting import tidy_code
from .theme import ERROR_TEXT, SUCCESS_TEXT

NOT_LISTED = "My university isn't listed"

# Fetched once per run: the list only changes when the server is redeployed.
_university_cache: Optional[list[dict]] = None


def load_universities(base_url: str) -> Optional[list[dict]]:
    """The server's university list, or None if it could not be reached."""
    global _university_cache
    if _university_cache is None:
        try:
            _university_cache = ApiClient(base_url).list_universities()
        except ApiClientError:
            return None
    return _university_cache


def email_matches(email: str, domains: list[str]) -> bool:
    """Same rule as the server: the domain itself or any subdomain of it."""
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    return bool(domain) and any(f".{domain}".endswith(f".{d}") for d in domains)


def make_register_form(base_url: str):
    """Build the form. Call `.submit(client)`; it returns an error message or None."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QButtonGroup, QCheckBox, QComboBox, QCompleter, QFormLayout, QHBoxLayout, QLabel,
        QLineEdit, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
    )

    STUDENT, TEAM = 0, 1
    # After a code goes out, how often and for how long to ask whether it bounced. A
    # mailbox that does not exist is usually refused within seconds, rarely minutes.
    BOUNCE_POLL_MS = 5_000
    BOUNCE_WATCH_SECONDS = 120

    def password_field(placeholder: str = "") -> QLineEdit:
        field = QLineEdit()
        field.setEchoMode(QLineEdit.Password)
        if placeholder:
            field.setPlaceholderText(placeholder)
        return field

    def side_by_side(first: QWidget, second: QWidget) -> QHBoxLayout:
        """Two fields on one row: the repeat of an entry sits beside it, keeping the form short."""
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(first, 1)
        row.addWidget(second, 1)
        return row

    def hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("hint")
        return label

    class RegisterForm(QWidget):
        def __init__(self) -> None:
            super().__init__()
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 8, 0, 0)
            layout.setSpacing(10)

            # Segmented "who are you" switch, the first question on both GitHub's and
            # Apple's education sign-up.
            seg = QHBoxLayout()
            seg.setSpacing(0)
            self.student_btn = QPushButton("I'm a student")
            self.team_btn = QPushButton("I'm setting up a team")
            self.mode_group = QButtonGroup(self)
            for index, (button, pos) in enumerate(
                ((self.student_btn, "left"), (self.team_btn, "right"))
            ):
                button.setCheckable(True)
                button.setObjectName("segment")
                button.setProperty("segmentPos", pos)
                button.setCursor(Qt.PointingHandCursor)
                self.mode_group.addButton(button, index)
                seg.addWidget(button, 1)
            self.student_btn.setChecked(True)
            layout.addLayout(seg)

            self.pages = QStackedWidget()
            self.pages.addWidget(self._build_student_page())
            self.pages.addWidget(self._build_team_page())
            layout.addWidget(self.pages)
            self.tos_box = QCheckBox(
                "I accept the Terms of Use: I will only test apps I own or am "
                "authorised to test.")
            self.tos_box.setToolTip("See docs/TERMS.md")
            layout.addWidget(self.tos_box)
            self.mode_group.idClicked.connect(self.pages.setCurrentIndex)

            # (email, sendId, deadline) of the code being watched for a bounce.
            self._watching = None
            self._bounce_timer = QTimer(self)
            self._bounce_timer.setInterval(BOUNCE_POLL_MS)
            self._bounce_timer.timeout.connect(self._check_bounce)

        # ---- pages -------------------------------------------------------- #

        def _build_student_page(self) -> QWidget:
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(0, 4, 0, 0)

            self.uni_combo = QComboBox()
            self.uni_combo.setEditable(True)
            self.uni_combo.setInsertPolicy(QComboBox.NoInsert)
            self.uni_combo.setMaxVisibleItems(12)
            self.uni_combo.lineEdit().setPlaceholderText("Search for your university")

            universities = load_universities(base_url)
            for uni in sorted(universities or [], key=lambda u: u["name"]):
                self.uni_combo.addItem(f"{uni['name']}  ·  {uni['country']}", uni)
            self.uni_combo.addItem(NOT_LISTED, None)

            # Type-to-search anywhere in the name, the way the education sign-up pages
            # behave, rather than Qt's default of matching only from the start.
            completer = QCompleter(self.uni_combo.model(), self.uni_combo)
            completer.setFilterMode(Qt.MatchContains)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setCompletionMode(QCompleter.PopupCompletion)
            self.uni_combo.setCompleter(completer)
            self.uni_combo.setCurrentIndex(-1)
            self.uni_combo.setEditText("")
            self.uni_combo.currentIndexChanged.connect(self._on_university_changed)

            self.uni_hint = QLabel()
            self.uni_hint.setWordWrap(True)
            self.uni_hint.setObjectName("universityHint")
            self.uni_hint.setVisible(universities is None)
            if universities is None:
                self.uni_hint.setText(
                    "Could not load the university list from the server. You can still "
                    "register with your university email address.")

            self.stu_name = QLineEdit()
            self.stu_name.setPlaceholderText("Optional")
            self.stu_email = QLineEdit()
            self.stu_email.setPlaceholderText("you@university.ac.uk")
            self.stu_password = password_field("At least 12 characters, mixed case, digit, symbol")
            self.stu_confirm = password_field("Retype password")

            self.stu_code, self.send_code_btn, self.code_status, code_row = self._code_row()

            form.addRow("University:", self.uni_combo)
            form.addRow("", self.uni_hint)
            form.addRow("Name:", self.stu_name)
            form.addRow("Uni email:", self.stu_email)
            form.addRow("Password:", side_by_side(self.stu_password, self.stu_confirm))
            form.addRow("Code:", code_row)
            form.addRow("", self.code_status)
            form.addRow("", hint(
                "Free for students. Send code emails you a six-digit code to confirm you "
                "study there."))
            return page

        def _build_team_page(self) -> QWidget:
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(0, 4, 0, 0)

            self.team_org = QLineEdit()
            self.team_org.setPlaceholderText("Your organisation's name")
            self.team_name = QLineEdit()
            self.team_name.setPlaceholderText("Optional")
            self.team_email = QLineEdit()
            self.team_email.setPlaceholderText("you@company.com")
            self.team_password = password_field("At least 12 characters, mixed case, digit, symbol")
            self.team_confirm = password_field("Retype password")
            self.team_solo = QCheckBox("I work alone (freelancer or consultant)")

            form.addRow("Organisation:", self.team_org)
            form.addRow("Name:", self.team_name)
            form.addRow("Work email:", self.team_email)
            form.addRow("Password:", side_by_side(self.team_password, self.team_confirm))
            form.addRow("Code:", team_code_row)
            form.addRow("", self.team_code_status)
            form.addRow("", self.team_solo)
            form.addRow("", hint(
                "You become the team's admin and can invite colleagues. Send code confirms "
                "your email. Joining someone else's team? Use their invite instead."))
            return page

        # ---- behaviour ---------------------------------------------------- #

        def _selected_university(self):
            """(found, university-or-None). found is False if the text matches no entry."""
            index = self.uni_combo.findText(self.uni_combo.currentText().strip())
            if index < 0:
                return False, None
            return True, self.uni_combo.itemData(index)

        def _on_university_changed(self, _index: int) -> None:
            found, uni = self._selected_university()
            if not found:
                return
            if uni is None:
                self.uni_hint.setText(
                    "Use the email address your university gave you. Most end in "
                    ".ac.uk, .edu, or similar.")
                self.stu_email.setPlaceholderText("you@university.ac.uk")
            else:
                endings = " or ".join(f"<b>@{d}</b>" for d in uni["domains"])
                self.uni_hint.setText(
                    f"Use your {uni['name']} email address. It should end in {endings}.")
                self.stu_email.setPlaceholderText(f"you@{uni['domains'][0]}")
            self.uni_hint.setVisible(True)

        def _student_details(self):
            """(problem, email, university). problem is a message to show, or None."""
            found, uni = self._selected_university()
            if not found:
                return (f"Choose your university from the list, or pick \"{NOT_LISTED}\".",
                        "", None)
            email = self.stu_email.text().strip()
            if bad := email_message(email):
                return bad, email, uni
            if uni is not None and not email_matches(email, uni["domains"]):
                endings = " or ".join(f"@{d}" for d in uni["domains"])
                return (f"{MESSAGES['email']} Use your {uni['name']} email, ending {endings}.",
                        email, uni)
            return None, email, uni

        def _team_details(self):
            """(problem, email). problem is a message to show, or None."""
            email = self.team_email.text().strip()
            if not email:
                return "Enter your work email address.", email
            return None, email

        def _code_row(self):
            """A code field and its Send code button, plus the line under them that says
            how the send went. The same on both pages."""
            code = QLineEdit()
            code.setPlaceholderText("Six-digit code from the email")
            # No six-character limit: it would cut a pasted "1 2 3 4 5 6" down to "1 2 3"
            # before it could be tidied to its digits.
            code.textEdited.connect(
                lambda text: text != tidy_code(text) and code.setText(tidy_code(text)))
            send = QPushButton("Send code")
            send.setCursor(Qt.PointingHandCursor)
            send.clicked.connect(self._send_code)
            row = QHBoxLayout()
            row.addWidget(code, 1)
            row.addWidget(send)
            status = QLabel()
            status.setWordWrap(True)
            status.setVisible(False)
            return code, send, status, row

        def _page_code_widgets(self):
            """(code field, Send code button, status line) on the page showing now."""
            if self.pages.currentIndex() == TEAM:
                return self.team_code, self.team_send_btn, self.team_code_status
            return self.stu_code, self.send_code_btn, self.code_status

        def _show_code_status(self, message: str, error: bool, label=None) -> None:
            label = label or self._page_code_widgets()[2]
            colour = ERROR_TEXT if error else SUCCESS_TEXT
            label.setText(f"<span style='color:{colour}'>{message}</span>")
            label.setVisible(True)

        def _send_code(self) -> None:
            """Email the sign-up code to the address entered so far on this page."""
            code_field, send_btn, status_label = self._page_code_widgets()
            if self.pages.currentIndex() == TEAM:
                problem, email = self._team_details()
                university_id, account_type = None, "team"
            else:
                problem, email, uni = self._student_details()
                university_id, account_type = (uni["id"] if uni else None), "student"
            if problem:
                self._show_code_status(problem, error=True, label=status_label)
                return
            self._bounce_timer.stop()
            self.setCursor(Qt.WaitCursor)
            try:
                sent = ApiClient(base_url).send_signup_code(
                    email, university_id, account_type=account_type)
            except ApiClientError as exc:
                route_api_error(exc, {"email": self.stu_email},
                                lambda text: self._show_code_status(text, error=True),
                                overrides={409: ("email_taken", self.stu_email)})
                return
            finally:
                self.setCursor(Qt.ArrowCursor)
            send_btn.setText("Send again")
            # The address in bold, so a typo in it stands out before anyone sits waiting
            # for a code that went somewhere else.
            message = re.sub(re.escape(email), lambda m: f"<b>{m.group(0)}</b>",
                             sent.get("message", ""), flags=re.IGNORECASE)
            self._show_code_status(message, error=False, label=status_label)
            code_field.setFocus()

            # Only a code that really went out can bounce; a development server just
            # writes it to its log.
            if sent.get("delivered") and sent.get("sendId"):
                self._watching = (email, sent["sendId"],
                                  time.monotonic() + BOUNCE_WATCH_SECONDS, status_label)
                self._bounce_timer.start()

        def _check_bounce(self) -> None:
            """Ask whether the code just sent bounced, and say so if it did."""
            email, send_id, deadline, status_label = self._watching
            if time.monotonic() > deadline:
                self._bounce_timer.stop()
                return
            try:
                status = ApiClient(base_url).signup_code_status(email, send_id)
            except ApiClientError:
                # The code was used or replaced, or the server is out of reach. Either
                # way there is nothing more to learn about this send.
                self._bounce_timer.stop()
                return
            if status.get("status") == "bounced":
                self._bounce_timer.stop()
                self._show_code_status(status.get("message", ""), error=True,
                                       label=status_label)

        def set_return_action(self, callback) -> None:
            """Pressing Enter in the last field of either page submits the dialog."""
            self.stu_code.returnPressed.connect(callback)
            self.team_code.returnPressed.connect(callback)

        def submit(self, client: ApiClient) -> Optional[str]:
            """Validate, then register. Returns a message to show, or None on success.

            When the problem belongs to one input, `error_field` is set to it so the
            caller can show the message there.
            """
            self.error_field = None
            if not self.tos_box.isChecked():
                return "Accept the Terms of Use to create an account."
            if self.pages.currentIndex() == TEAM:
                org = self.team_org.text().strip()
                password = self.team_password.text()
                if not org:
                    return self._bad(self.team_org, MESSAGES["org"])
                if bad := email_message(email):
                    return self._bad(self.team_email, bad)
                if problem := _password_problem(password, self.team_confirm.text()):
                    return self._bad(self._password_target(
                        password, self.team_confirm.text(),
                        self.team_password, self.team_confirm), problem)
                client.register_admin(email, password, org,
                                      self.team_name.text().strip() or None,
                                      tos_accepted=True,
                                      solo=self.team_solo.isChecked(),
                                      code=code)
                return None

            problem, email, uni = self._student_details()
            if problem:
                return self._bad(self.stu_email, problem)
            password = self.stu_password.text()
            if problem := _password_problem(password, self.stu_confirm.text()):
                return self._bad(self._password_target(
                    password, self.stu_confirm.text(),
                    self.stu_password, self.stu_confirm), problem)
            code = self.stu_code.text().strip()
            if len(code) != 6 or not code.isdigit():
                return self._bad(self.stu_code, MESSAGES["code"])
            client.register(email, password, self.stu_name.text().strip() or None,
                            university_id=uni["id"] if uni else None, code=code,
                            tos_accepted=True)
            return None

        def server_fields(self) -> dict:
            """Server field name -> input, for the page being shown (see route_api_error)."""
            if self.pages.currentIndex() == TEAM:
                return {"email": self.team_email, "password": self.team_password,
                        "organisationName": self.team_org, "displayName": self.team_name}
            return {"email": self.stu_email, "password": self.stu_password,
                    "code": self.stu_code, "displayName": self.stu_name}

        def email_field(self):
            return self.team_email if self.pages.currentIndex() == TEAM else self.stu_email

        def _bad(self, field, message: str) -> str:
            self.error_field = field
            return message

        @staticmethod
        def _password_target(password, confirm, password_field, confirm_field):
            """A mismatch is shown on the confirm box, a weak password on the first."""
            return confirm_field if password != confirm else password_field

    return RegisterForm()


def _password_problem(password: str, confirm: str) -> Optional[str]:
    """Catalog wording for what is wrong with a new password, or None."""
    if password != confirm:
        return MESSAGES["confirm"]
    return password_message(password_strength_problem(password))
