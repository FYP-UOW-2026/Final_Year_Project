"""The "Create account" form, shared by the welcome and sign-in dialogs.

Modelled on the way GitHub Education and Apple's student pricing work: you say which
university you attend by picking it from a searchable list, and the email you register
with has to be one that university issues. The server then emails a six-digit code to
prove you can read that inbox. A second mode covers the other kind of account, a team
whose creator becomes its admin, which needs no student check at all.

PySide6 is imported inside make_register_form so this module, like the rest of gui/,
costs nothing to import when the GUI is not running.
"""

from __future__ import annotations

from typing import Optional

from ..api import ApiClient, ApiClientError

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
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QButtonGroup, QComboBox, QCompleter, QFormLayout, QHBoxLayout, QLabel,
        QLineEdit, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
    )

    STUDENT, TEAM = 0, 1

    def password_field(placeholder: str = "") -> QLineEdit:
        field = QLineEdit()
        field.setEchoMode(QLineEdit.Password)
        if placeholder:
            field.setPlaceholderText(placeholder)
        return field

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
            self.mode_group.idClicked.connect(self.pages.setCurrentIndex)

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
            self.stu_password = password_field("At least 8 characters")
            self.stu_confirm = password_field()

            form.addRow("University:", self.uni_combo)
            form.addRow("", self.uni_hint)
            form.addRow("Name:", self.stu_name)
            form.addRow("Uni email:", self.stu_email)
            form.addRow("Password:", self.stu_password)
            form.addRow("Confirm:", self.stu_confirm)
            form.addRow("", hint(
                "BioAudit is free for students. We will email a six-digit code to your "
                "university address to confirm you study there."))
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
            self.team_password = password_field("At least 8 characters")
            self.team_confirm = password_field()

            form.addRow("Organisation:", self.team_org)
            form.addRow("Name:", self.team_name)
            form.addRow("Work email:", self.team_email)
            form.addRow("Password:", self.team_password)
            form.addRow("Confirm:", self.team_confirm)
            form.addRow("", hint(
                "Creates a team and makes you its admin, so you can invite colleagues "
                "and oversee their assessments. Joining someone else's team? Use the "
                "invite token they sent you instead."))
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

        def set_return_action(self, callback) -> None:
            """Pressing Enter in the last field of either page submits the dialog."""
            self.stu_confirm.returnPressed.connect(callback)
            self.team_confirm.returnPressed.connect(callback)

        def submit(self, client: ApiClient) -> Optional[str]:
            """Validate, then register. Returns a message to show, or None on success."""
            if self.pages.currentIndex() == TEAM:
                org = self.team_org.text().strip()
                email = self.team_email.text().strip()
                password = self.team_password.text()
                if not org:
                    return "Give your organisation a name."
                if not email:
                    return "Enter your work email address."
                if problem := _password_problem(password, self.team_confirm.text()):
                    return problem
                client.register_admin(email, password, org,
                                      self.team_name.text().strip() or None)
                return None

            found, uni = self._selected_university()
            if not found:
                return f"Choose your university from the list, or pick \"{NOT_LISTED}\"."
            email = self.stu_email.text().strip()
            password = self.stu_password.text()
            if not email:
                return "Enter your university email address."
            if uni is not None and not email_matches(email, uni["domains"]):
                endings = " or ".join(f"@{d}" for d in uni["domains"])
                return f"Use your {uni['name']} email address. It should end in {endings}."
            if problem := _password_problem(password, self.stu_confirm.text()):
                return problem
            client.register(email, password, self.stu_name.text().strip() or None,
                            university_id=uni["id"] if uni else None)
            return None

    return RegisterForm()


def _password_problem(password: str, confirm: str) -> Optional[str]:
    if password != confirm:
        return "Those passwords do not match."
    if len(password) < 8:
        return "Use a password of at least 8 characters."
    return None
