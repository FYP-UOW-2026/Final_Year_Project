"""Type-to-confirm dialog for destructive actions (deleting scan history).

`make_code` and `matches` are pure so they can be unit-tested without a display.
`confirm_by_typing` imports PySide6 only when called, like the rest of the GUI.
"""

from __future__ import annotations

import secrets

# No 0/O, 1/I/L: the code is read off the screen and retyped, so look-alikes are out.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_PREFIX = "DELETE-"
CODE_LENGTH = 4


def make_code() -> str:
    """A fresh random confirmation code such as 'DELETE-7K3Q'."""
    return CODE_PREFIX + "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def matches(code: str, text: str) -> bool:
    """True only for an exact match (surrounding whitespace ignored, case-sensitive)."""
    return bool(code) and (text or "").strip() == code


def confirm_by_typing(parent, title: str, message: str, count: int) -> bool:
    """Modal dialog. True only if the user typed the shown code and pressed Delete."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                                   QVBoxLayout)

    code = make_code()

    class ConfirmDialog(QDialog):
        def __init__(self):
            super().__init__(parent)
            self.setWindowTitle(title)
            self.setModal(True)
            self.setMinimumWidth(420)

            lay = QVBoxLayout(self)
            lay.setSpacing(10)
            body = QLabel(message)
            body.setWordWrap(True)
            lay.addWidget(body)

            shown = QLabel(f"To confirm, type <b>{code}</b> below.")
            shown.setTextFormat(Qt.RichText)
            lay.addWidget(shown)

            self.entry = QLineEdit()
            self.entry.setPlaceholderText(code)
            self.entry.textChanged.connect(self._changed)
            self.entry.returnPressed.connect(self._enter)
            lay.addWidget(self.entry)

            row = QHBoxLayout()
            row.addStretch(1)
            cancel = QPushButton("Cancel")
            cancel.setProperty("ghost", True)
            cancel.clicked.connect(self.reject)
            self.go = QPushButton(f"Delete {count} scan{'' if count == 1 else 's'}")
            self.go.setProperty("danger", True)
            self.go.setEnabled(False)
            self.go.clicked.connect(self.accept)
            row.addWidget(cancel)
            row.addWidget(self.go)
            lay.addLayout(row)
            # No default button: Enter in the field must not trigger Cancel.
            cancel.setAutoDefault(False)
            self.go.setAutoDefault(False)

        def _changed(self, text: str) -> None:
            self.go.setEnabled(matches(code, text))

        def _enter(self) -> None:
            # Enter confirms only when the code is right; otherwise nothing happens.
            if matches(code, self.entry.text()):
                self.accept()

    return ConfirmDialog().exec() == QDialog.Accepted
