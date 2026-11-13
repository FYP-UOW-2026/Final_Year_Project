"""BioAudit desktop GUI (PySide6).

A thin shell over `bioaudit.core` — the same orchestration the CLI drives.
Long-running work (APK decompilation, ADB probing, the AI layer) runs on a
QThread so the window never freezes. Findings are shown by reusing the report
renderer (`report.generator.render_html`) inside a QTextBrowser, so the on-screen
view matches the exported report exactly.

Launch with:  python -m bioaudit gui
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from typing import Callable

from .. import core
from ..adb import Adb, adb_available
from ..config import Config
from ..masking import mask_serial
from ..models import TestRun
from ..report import generator
from .formatting import format_when, severity_summary


def _require_pyside():
    try:
        import PySide6  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "The desktop GUI needs PySide6.\n"
            "Install it with:  pip install PySide6\n"
            "(Or use the CLI: python -m bioaudit scan-apk <apk>)\n"
        )
        raise SystemExit(1)


# --------------------------------------------------------------------------- #
# Background worker: runs a callable off the UI thread and reports the result.
# --------------------------------------------------------------------------- #

def _make_worker_classes():
    """Defined inside a function so PySide6 is only imported when the GUI runs."""
    from PySide6.QtCore import QObject, QThread, Signal

    class Worker(QObject):
        finished = Signal(object)   # emits the return value of `fn`
        failed = Signal(str)        # emits a formatted traceback / message
        progress = Signal(str)      # emits a plain-language step description
        cancelled = Signal()        # emits when the job stopped at the user's request

        def __init__(self, fn: Callable[[Callable[[str], None]], object]):
            super().__init__()
            self._fn = fn

        def run(self) -> None:
            from .. import core

            try:
                # The job is handed the emitter rather than the signal itself, so job
                # closures stay plain callables and know nothing about Qt.
                result = self._fn(self.progress.emit)
            except core.ScanCancelled:
                # Not a failure. The user asked for this, so it gets its own path and
                # does not show them a traceback for something they chose.
                self.cancelled.emit()
                return
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                tb = traceback.format_exc()
                _log_diag(f"Worker job raised:\n{tb}")
                self.failed.emit(f"{type(exc).__name__}: {exc}\n\n{tb}")
                return
            self.finished.emit(result)

    return Worker, QThread


# The job step that uploads a finished run; it cannot be cancelled.
SAVING_STEP = "Saving to your account"

# Held for the life of the process: while it listens, this is the running copy.
_instance_server = None


def _hand_over_to_running_app(on_new_launch: Callable[[], None]) -> bool:
    """True if BioAudit is already open, in which case it has been told to come forward.

    Otherwise this launch becomes the running copy and `on_new_launch` is called whenever
    a later one starts. Anything going wrong here must not stop the app from opening, so
    only a definite answer that another copy holds the name counts.
    """
    global _instance_server
    if _instance_server is not None:
        return False

    import getpass

    from PySide6.QtNetwork import QAbstractSocket, QLocalServer, QLocalSocket

    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - only used to keep users' copies apart
        user = "user"
    name = f"bioaudit-desktop-{user}"

    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(500):
        probe.disconnectFromServer()
        return True

    # A crash can leave the name behind on macOS and Linux.
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.newConnection.connect(lambda: (server.nextPendingConnection().deleteLater(),
                                          on_new_launch()))
    if not server.listen(name):
        return server.serverError() == QAbstractSocket.AddressInUseError
    _instance_server = server
    return False


_crash_log_handles: list = []   # keep the file object alive for the process lifetime


def _log_diag(text: str) -> None:
    """Append a line to the crash log (best effort), for post-mortem diagnosis."""
    for fh in _crash_log_handles:
        try:
            fh.write(text + "\n")
        except Exception:
            pass


def _install_crash_logging() -> str | None:
    """Log native crashes (segfaults) and uncaught exceptions to a file.

    A frozen windowed app has no console, so a native crash on a worker thread
    would otherwise just vanish. faulthandler catches those with a traceback.
    Returns the log path, or None if it could not be set up.
    """
    import datetime
    import faulthandler
    import threading
    from pathlib import Path

    try:
        path = Path(os.path.expanduser("~")) / "bioaudit-crash.log"
        fh = open(path, "a", buffering=1, encoding="utf-8")
        _crash_log_handles.append(fh)
        fh.write(f"\n=== BioAudit GUI started {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
        faulthandler.enable(fh)

        def _hook(exc_type, exc, tb):
            import traceback
            fh.write("Uncaught exception:\n")
            traceback.print_exception(exc_type, exc, tb, file=fh)

        sys.excepthook = _hook
        if hasattr(threading, "excepthook"):
            threading.excepthook = lambda a: _hook(a.exc_type, a.exc_value, a.exc_traceback)
        return str(path)
    except Exception:
        return None


def run(cfg: Config | None = None) -> int:
    """Entry point used by `cli.cmd_gui`. Returns a process exit code."""
    _require_pyside()
    _install_crash_logging()

    from PySide6.QtCore import Qt, QPoint, QRect, QSize
    from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import (
        QApplication, QButtonGroup, QCheckBox, QComboBox, QCompleter, QFileDialog, QFormLayout,
        QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu,
        QMessageBox, QProgressBar, QPushButton, QTabWidget,
        QTextBrowser, QVBoxLayout, QWidget,
    )

    from . import history_view, team, theme
    from .confirm_dialog import confirm_by_typing

    # --- small layout helpers, shared by every tab --------------------------
    # Grouping controls into titled cards is what turns a flat stack of widgets into
    # something readable: it tells the user which inputs belong together and where one
    # decision ends and the next begins.

    def _section(title: str, accent: str | None = None):
        """A titled card. Returns (box, inner_layout) so callers can fill it.

        `accent` picks one of theme.TILE_ACCENTS for a coloured top edge, so a card on
        the Scan tab, the Assess tab, and the History tab each read as visually distinct
        rather than as identical grey boxes.
        """
        box = QGroupBox(title)
        if accent:
            box.setProperty("accent", accent)
        inner = QVBoxLayout(box)
        inner.setContentsMargins(2, 6, 2, 2)
        inner.setSpacing(8)
        return box, inner

    def _tile_button(accent: str, icon: str, title: str, subtitle: str) -> QPushButton:
        """A large clickable card for the Home tab.

        Built from a QPushButton with its own layout of labels rather than the button's
        own text, because a tile needs two font sizes (a bold title, a muted subtitle)
        that a single QPushButton label can't express. `icon` names an entry in
        theme.ICONS, drawn as a vector in the accent colour.
        """
        btn = QPushButton()
        btn.setProperty("tileAccent", accent)
        btn.setCursor(Qt.PointingHandCursor)
        # Reachable by Tab like any other control; the stylesheet draws the focus ring.
        btn.setFocusPolicy(Qt.StrongFocus)
        btn.setAccessibleName(title)
        btn.setMinimumSize(220, 150)

        inner = QVBoxLayout(btn)
        inner.setContentsMargins(18, 16, 18, 16)
        inner.setSpacing(4)
        icon_label = QLabel()
        icon_label.setPixmap(theme.icon_pixmap(icon, theme.DARK["primary"], 30))
        icon_label.setObjectName("tileIcon")
        title_label = QLabel(title)
        title_label.setObjectName("tileTitle")
        title_label.setWordWrap(True)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("tileSubtitle")
        subtitle_label.setWordWrap(True)
        inner.addWidget(icon_label)
        inner.addStretch(1)
        inner.addWidget(title_label)
        inner.addWidget(subtitle_label)
        return btn

    def _hint(text: str) -> QLabel:
        """Small muted explanatory text under a control."""
        label = QLabel(text)
        label.setObjectName("hint")
        label.setWordWrap(True)
        return label

    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    class MenuAbove(QMenu):
        """A menu that opens upward from `anchor`, for a button at the foot of the window.

        Qt drops a button's menu below it, which from the bottom of the sidebar hung the
        menu off the window and over the account name.
        """
        anchor = None

        def showEvent(self, event) -> None:  # noqa: N802 - Qt override
            if self.anchor is not None:
                self.setMinimumWidth(self.anchor.width())
                self.move(self.anchor.mapToGlobal(QPoint(0, -self.sizeHint().height() - 4)))
            super().showEvent(event)

    class ToggleSwitch(QCheckBox):
        """A checkbox that paints itself as an on/off switch instead of a tick-box.

        Reusing QCheckBox keeps the checked-state signal, Space-to-toggle, and mouse
        handling for free; a stylesheet indicator can't draw a real sliding knob, so
        painting is done by hand instead. Call setProperty("_palette", colours) with
        the app's palette dict before it is shown, or it falls back to grey.
        """

        _TRACK_W, _TRACK_H = 34, 18

        def hitButton(self, pos) -> bool:  # noqa: N802 - Qt override
            return self.rect().contains(pos)

        def sizeHint(self) -> QSize:
            base = super().sizeHint()
            width = self._TRACK_W
            if self.text():
                width += 8 + self.fontMetrics().horizontalAdvance(self.text()) + 2
            return QSize(width, max(base.height(), self._TRACK_H + 8))

        def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
            p = self.property("_palette") or {}
            on, enabled = self.isChecked(), self.isEnabled()
            if not enabled:
                track, border, knob = p.get("disabled_bg", "#ccc"), p.get("border", "#ccc"), p.get("disabled_text", "#fff")
            elif on:
                track = border = p.get("primary", "#0b57d0")
                knob = p.get("primary_text", "#ffffff")
            else:
                track, border, knob = p.get("surface", "#ffffff"), p.get("border_strong", "#999"), p.get("muted", "#999")

            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)

            track_rect = QRect(0, (self.height() - self._TRACK_H) // 2, self._TRACK_W, self._TRACK_H)
            painter.setPen(QPen(QColor(border), 1))
            painter.setBrush(QBrush(QColor(track)))
            painter.drawRoundedRect(track_rect, self._TRACK_H / 2, self._TRACK_H / 2)

            knob_d = self._TRACK_H - 4
            knob_x = track_rect.right() - knob_d - 1 if on else track_rect.left() + 2
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(knob)))
            painter.drawEllipse(QRect(knob_x, track_rect.top() + 2, knob_d, knob_d))

            if self.text():
                text_rect = QRect(self._TRACK_W + 8, 0, self.width() - self._TRACK_W - 8, self.height())
                painter.setPen(QColor(p.get("text" if enabled else "disabled_text", "#000")))
                painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.text())
            painter.end()

    Worker, QThread = _make_worker_classes()
    cfg = cfg or Config.load()

    # A double-clicked exe can start in a directory it cannot write to (Program
    # Files, a read-only mount, etc.), which would make the report export raise.
    # Anchor it, and the saved session file, under the user's home so writes always work.
    from .. import session as _session_module
    try:
        _base = _session_module.default_base_dir()
        if not os.path.isabs(cfg.report["output_dir"]):
            cfg.report["output_dir"] = str(_base / cfg.report["output_dir"])
    except Exception as exc:  # noqa: BLE001
        from pathlib import Path
        _base = Path(os.path.expanduser("~")) / "BioAudit"
        _log_diag(f"could not set up writable output dir: {exc}")

    def _brand_logo(size: int, dpr: float = 1.0):
        """Brand mark scaled in device pixels so it stays sharp on HiDPI screens;
        None if the asset is missing."""
        path = os.path.join(os.path.dirname(__file__), "assets", "logo_mark.png")
        if not os.path.exists(path):
            return None
        d = max(dpr, 1.0)
        px = QPixmap(path).scaled(int(size * d), int(size * d), Qt.KeepAspectRatio,
                                  Qt.SmoothTransformation)
        px.setDevicePixelRatio(d)
        return px

    def _set_macos_app_name(name: str) -> None:
        """Menu bar / Dock show the bundle name, which is "Python" when run from source.
        Patch it in memory before Qt starts; no-op elsewhere and on any failure."""
        if sys.platform != "darwin":
            return
        try:
            import ctypes
            objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.dylib")
            ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.sel_registerName.restype = ctypes.c_void_p
            send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_void_p)(("objc_msgSend", objc))
            send0 = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
                ("objc_msgSend", objc))
            sel = lambda n: objc.sel_registerName(n)  # noqa: E731
            bundle = send0(objc.objc_getClass(b"NSBundle"), sel(b"mainBundle"))
            info = send0(bundle, sel(b"infoDictionary"))
            nsstr = lambda t: send(objc.objc_getClass(b"NSString"),  # noqa: E731
                                   sel(b"stringWithUTF8String:"), t.encode())
            for key in ("CFBundleName", "CFBundleDisplayName"):
                ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_void_p)(("objc_msgSend", objc))(
                    info, sel(b"setObject:forKey:"), nsstr(name), nsstr(key))
        except Exception as exc:  # noqa: BLE001
            _log_diag(f"could not set macOS app name: {exc}")

    class MainWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.cfg = cfg
            self._thread: QThread | None = None
            self._worker: Worker | None = None
            # Separate thread pair for auto-explain, so it can run independently of a
            # scan/assess job (e.g. after a retry-upload, with no job in flight).
            self._explain_thread: QThread | None = None
            self._history_load_thread: QThread | None = None
            self._history_load_worker = None
            # And one for fetching ADB, which needs no job widgets at all.
            self._adb_thread: QThread | None = None
            self._explain_worker: Worker | None = None
            self._explain_target: dict | None = None
            self._last_run = None
            self._job_ctx: dict | None = None
            # Set while a job runs so the worker thread can see a cancel request. An Event
            # is used rather than a plain bool because it is written on the UI thread and
            # read on the worker thread.
            self._cancel_event = None
            self._client_name = ""
            # Set when a run has been saved to the account, which is what the server-backed
            # explain and export buttons need.
            self._last_scan_id: str | None = None

            # Optional server connection. Stays None unless the user signs in, and
            # nothing in the scanning path depends on it.
            self.api = None
            self._session_dir = str(_base)

            # The colours the stylesheet was built from, so the HTML rendered into the
            # results panes matches the surrounding chrome in either theme.
            self.palette_colours = palette_colours

            self.setWindowTitle("BioAudit — Android biometric authentication security")
            self.resize(1060, 780)
            self.setMinimumSize(820, 600)

            self._build_account_menu()

            # The pages still live in a QTabWidget, so every existing call that switches or
            # hides a tab keeps working. Its tab bar is hidden: the sidebar is the
            # navigation now, because a wide desktop window has room for labelled nav
            # down the side and top tabs read as an afterthought.
            tabs = QTabWidget()
            tabs.setObjectName("pages")
            tabs.setDocumentMode(True)
            tabs.tabBar().hide()
            tabs.addTab(self._build_home_tab(), "Home")
            # Indices kept because the assessment tabs and History are hidden for an admin
            # account, which oversees a team's assessments rather than running its own.
            self._scan_tab_index = tabs.addTab(self._build_scan_tab(), "Scan an APK")
            self._assess_tab_index = tabs.addTab(self._build_assess_tab(), "Assess a device")
            self._history_tab_index = tabs.addTab(self._build_history_tab(), "History")
            self.team_tab = team.make_team_tab(self, self.palette_colours)
            self._team_tab_index = tabs.addTab(self.team_tab, "Team")
            self.tabs = tabs

            # Title and one-line purpose for whichever page is open, in tab order.
            self._page_info = [
                ("Home", "Pick a test to run, or open your past results."),
                ("Scan an APK", "Read an app file for weak biometric checks. No phone needed."),
                ("Assess a device", "Test an installed app on a connected phone, over ADB."),
                ("History", "Past runs saved to your account."),
                ("Team", "Members, invitations, flagged runs and activity."),
            ]
            self.page_title = QLabel("")
            self.page_title.setObjectName("pageTitle")
            self.page_subtitle = QLabel("")
            self.page_subtitle.setObjectName("pageSubtitle")
            header = QVBoxLayout()
            header.setContentsMargins(16, 0, 16, 0)
            header.setSpacing(2)
            header.addWidget(self.page_title)
            header.addWidget(self.page_subtitle)

            content = QWidget()
            column = QVBoxLayout(content)
            column.setContentsMargins(12, 22, 12, 6)
            column.setSpacing(0)
            column.addLayout(header)
            column.addSpacing(10)
            column.addWidget(tabs, 1)

            shell = QWidget()
            row = QHBoxLayout(shell)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(0)
            row.addWidget(self._build_sidebar())
            row.addWidget(content, 1)
            self.setCentralWidget(shell)

            tabs.currentChanged.connect(self._on_page_changed)
            self._on_page_changed(tabs.currentIndex())

            self.statusBar().showMessage("Ready")
            self._restore_saved_session()
            self._refresh_account_ui()

            # The welcome gate is a startup step, not a constructor step: show it once the
            # window itself is on screen (via a zero-delay timer) so it appears over a
            # painted app rather than a blank one. Skipped when a saved session already
            # signed the user in, and suppressible for automated tests.
            if self.api is None and not os.environ.get("BIOAUDIT_SKIP_WELCOME"):
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, self._require_signed_in_or_quit)
            elif self.api is not None and not os.environ.get("BIOAUDIT_SKIP_WELCOME"):
                # A saved session skips the welcome gate, so this is the only point a
                # returning student who never entered their code would be asked for it,
                # rather than finding out when a finished scan's upload is refused.
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, self._prompt_student_verification)

        # ---- Shell -------------------------------------------------------- #

        def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
            """Stop a running job before the window goes.

            Closing used to destroy the job's thread while it was still running, which
            Qt answers by aborting the whole process. The job is asked to stop and its
            thread is waited for instead, with the window already hidden so the wait
            for the current step to end is not spent looking at a frozen window.
            """
            busy = [t for t in (self._thread, self._explain_thread, self._adb_thread,
                                 self._history_load_thread)
                    if t is not None and t.isRunning()]
            if busy:
                if QMessageBox.question(
                    self, "A run is still going",
                    "Stop it and close BioAudit?\n\nA run that has not started saving to "
                    "your account is discarded.",
                ) != QMessageBox.Yes:
                    event.ignore()
                    return
                if self._cancel_event is not None:
                    self._cancel_event.set()
                self.hide()
                for thread in busy:
                    thread.quit()
                    thread.wait()
            event.accept()

        def _build_sidebar(self) -> QFrame:
            """Brand, one labelled nav item per page, and the signed-in account."""
            bar = QFrame()
            bar.setObjectName("sidebar")
            bar.setFixedWidth(224)
            layout = QVBoxLayout(bar)
            layout.setContentsMargins(0, 22, 0, 16)
            layout.setSpacing(2)

            brand = QHBoxLayout()
            brand.setContentsMargins(18, 0, 18, 0)
            brand.setSpacing(10)
            logo = QLabel()
            logo.setFixedSize(40, 40)
            logo.setPixmap(_brand_logo(40, logo.devicePixelRatioF()) or
                           theme.icon_pixmap("shield", theme.DARK["primary"], 28))
            names = QVBoxLayout()
            names.setSpacing(0)
            name = QLabel("BioAudit")
            name.setObjectName("brandName")
            tag = QLabel("android biometrics")
            tag.setObjectName("brandTag")
            names.addWidget(name)
            names.addWidget(tag)
            brand.addWidget(logo)
            brand.addLayout(names)
            brand.addStretch(1)
            layout.addLayout(brand)
            layout.addSpacing(26)

            heading = QLabel("Menu")
            heading.setObjectName("navHeading")
            layout.addWidget(heading)
            layout.addSpacing(4)

            # In tab order, so a button's id is the index of the page it opens.
            items = [("home", "Home"), ("scan", "Scan an APK"),
                     ("assess", "Assess a device"), ("history", "History"),
                     ("team", "Team")]
            self.nav_group = QButtonGroup(self)
            self.nav_group.setExclusive(True)
            self.nav_buttons = []
            for index, (icon_name, label) in enumerate(items):
                btn = QPushButton(label)
                btn.setObjectName("navItem")
                btn.setCheckable(True)
                btn.setCursor(Qt.PointingHandCursor)
                # Muted when idle, accent when it is the current page.
                icon = QIcon()
                icon.addPixmap(theme.icon_pixmap(icon_name, theme.DARK["muted"], 36),
                               QIcon.Normal, QIcon.Off)
                icon.addPixmap(theme.icon_pixmap(icon_name, theme.DARK["primary"], 36),
                               QIcon.Normal, QIcon.On)
                btn.setIcon(icon)
                btn.setIconSize(QSize(18, 18))
                btn.clicked.connect(lambda _checked=False, i=index: self._on_nav_clicked(i))
                self.nav_group.addButton(btn, index)
                self.nav_buttons.append(btn)
                layout.addWidget(btn)

            layout.addStretch(1)
            divider = QFrame()
            divider.setObjectName("sidebarDivider")
            layout.addWidget(divider)
            layout.addSpacing(12)

            # Kept with the page navigation: as a menu bar along the top of the window it
            # was easy to miss.
            self.account_button = QPushButton("Account")
            self.account_button.setObjectName("accountButton")
            self.account_button.setCursor(Qt.PointingHandCursor)
            self.account_button.setIcon(QIcon(theme.icon_pixmap("account", theme.DARK["text"], 36)))
            self.account_button.setIconSize(QSize(18, 18))
            self.account_button.setMenu(self.account_menu)
            self.account_menu.anchor = self.account_button
            layout.addWidget(self.account_button)
            layout.addSpacing(10)

            # Signed-in state stays visible at all times rather than behind a menu.
            self.account_label = QLabel("")
            self.account_label.setObjectName("accountBadge")
            self.account_label.setWordWrap(True)
            self.account_label.setContentsMargins(18, 0, 18, 0)
            layout.addWidget(self.account_label)
            return bar

        def _on_page_changed(self, index: int) -> None:
            """Keep the header and the highlighted nav item in step with the page."""
            if 0 <= index < len(self._page_info):
                title, subtitle = self._page_info[index]
                self.page_title.setText(title)
                self.page_subtitle.setText(subtitle)
            btn = self.nav_group.button(index)
            if btn is not None:
                btn.setChecked(True)

        def _on_nav_clicked(self, index: int) -> None:
            self.tabs.setCurrentIndex(index)
            # Clicking History always lands on the list, even from an open report
            # (re-clicking the current tab emits no currentChanged).
            if index == self._history_tab_index:
                self._history_page.show_list()

        def _sync_nav(self) -> None:
            """Show a nav item only while its page is available to this account."""
            for index, btn in enumerate(self.nav_buttons):
                btn.setVisible(self.tabs.isTabVisible(index))
            self._on_page_changed(self.tabs.currentIndex())

        # ---- Account ------------------------------------------------------ #

        def _build_account_menu(self) -> None:
            # Opened from the button at the foot of the sidebar (see _build_sidebar).
            menu = MenuAbove("Account", self)
            self.account_menu = menu

            self.action_sign_in = QAction("Sign in…", self)
            self.action_sign_in.triggered.connect(self._sign_in)
            menu.addAction(self.action_sign_in)

            self.action_sign_out = QAction("Sign out", self)
            self.action_sign_out.triggered.connect(self._sign_out)
            menu.addAction(self.action_sign_out)

            menu.addSeparator()

            self.action_account_info = QAction("Account details…", self)
            self.action_account_info.triggered.connect(self._show_account_details)
            menu.addAction(self.action_account_info)

            self.action_settings = QAction("Account settings…", self)
            self.action_settings.setToolTip(
                "Change your display name, email address, or password, or delete your account.")
            self.action_settings.triggered.connect(self._open_account_settings)
            menu.addAction(self.action_settings)

            self.action_verify_student = QAction("Verify student email…", self)
            self.action_verify_student.setToolTip(
                "Personal accounts are for students: verify your university email to run scans.")
            self.action_verify_student.triggered.connect(self._verify_student)
            menu.addAction(self.action_verify_student)

            self.action_join_org = QAction("Join an organisation…", self)
            self.action_join_org.triggered.connect(self._join_organisation)
            menu.addAction(self.action_join_org)

        def _restore_saved_session(self) -> None:
            """Reuse a stored session if the user asked to stay signed in.

            Runs on startup, so it must never block for long or raise. A dead session or
            an unreachable server simply leaves the app signed out.
            """
            # A saved session means the user ticked "keep me signed in", so honour it even
            # when the API section is off by default: choosing to stay signed in is itself
            # the opt-in. Skip the work only when neither the flag nor a saved file exists.
            from ..session import restore_session, session_path
            if not self.cfg.api.get("enabled", False) and not session_path(self._session_dir).exists():
                return
            try:
                client = restore_session(self._session_dir, self.cfg.api["base_url"])
            except Exception as exc:  # noqa: BLE001
                _log_diag(f"session restore failed: {exc}")
                return
            if client is not None:
                self.api = client
                self.cfg.api["enabled"] = True
                self.statusBar().showMessage("Signed in from a saved session.")

        def _show_welcome_gate(self) -> bool:
            """Front door: the user must register, join a team, or sign in to continue.

            Follows the functional hierarchy's unregistered-user paths (register / join via
            invite), with a sign-in tab for returning users. There is no guest mode: BioAudit
            keeps no local copy of a scan, so with nobody signed in there is nowhere for a
            result to go. Returns whether the user ended up signed in.
            """
            from .signin import show_welcome_dialog

            try:
                client, _remember = show_welcome_dialog(
                    self, base_url=self.cfg.api["base_url"], base_dir=self._session_dir)
            except Exception as exc:  # noqa: BLE001 - a broken gate must not trap the user
                _log_diag(f"welcome gate failed: {exc}")
                client = None

            if client is None:
                return False

            self.api = client
            self.cfg.api["enabled"] = True
            self.statusBar().showMessage(f"Signed in as {client.account.email}.")
            self._refresh_account_ui()
            self._prompt_student_verification()
            return True

        def _require_signed_in_or_quit(self) -> None:
            """Show the welcome gate; close the app if it is dismissed without signing in.

            There is nothing a signed-out session can do here: no local storage to fall
            back to, no guest mode. Closing rather than limping on with a dead window is
            the honest response, and mirrors what clicking the window's own close button
            does, so it needs no special-casing at the call sites.

            Skipped under the same flag the startup gate honours: an automated test that
            drives sign-out directly wants to inspect the resulting signed-out state, not
            hit a blocking modal dialog or have the process quit out from under it.
            """
            if os.environ.get("BIOAUDIT_SKIP_WELCOME"):
                return
            if not self._show_welcome_gate():
                self.close()

        def _refresh_account_ui(self) -> None:
            from .signin import account_summary

            account = self.api.account if self.api else None
            signed_in = account is not None

            self.account_label.setText(account_summary(account))
            self.action_sign_in.setText("Switch account…" if signed_in else "Sign in…")
            self.action_sign_out.setEnabled(signed_in)
            self.action_account_info.setEnabled(signed_in)
            self.action_settings.setEnabled(signed_in)
            # Joining is only offered when you are not already in a team; the server refuses
            # a second organisation, so offering it would only produce an error.
            self.action_join_org.setVisible(signed_in and not account.organisation_id)
            self.action_verify_student.setVisible(
                signed_in and account.needs_student_verification)

            # Team-only actions on the History tab.
            self._update_compare_button()
            self._update_save_report_buttons()

            # An admin account is an oversight role: it supervises a team's assessments
            # rather than running its own, and the server refuses a scan from one. Hiding
            # the two tabs is what stops that becoming a rejected upload after the work of
            # a full scan has already been done.
            # History goes too: it lists only the account's own runs, so for an admin it
            # could never be anything but empty. Members' runs are read from Team instead.
            is_admin = signed_in and account.is_admin
            own_runs_pages = (self._scan_tab_index, self._assess_tab_index,
                              self._history_tab_index)
            for index in own_runs_pages:
                self.tabs.setTabVisible(index, not is_admin)
            # Mirror the same gate on the Home tab: the tiles that open those pages are
            # removed entirely rather than left as a disabled dead end. Team is then the
            # only tile, so it moves to the top row and spans it instead of sitting alone
            # in the bottom-right cell beside an empty gap.
            for tile in (self.home_scan_tile, self.home_assess_tile, self.home_history_tile):
                tile.setVisible(not is_admin)
            self.home_grid.removeWidget(self.home_team_tile)
            if is_admin:
                self.home_grid.addWidget(self.home_team_tile, 0, 0, 1, 2)
            else:
                self.home_grid.addWidget(self.home_team_tile, 1, 1)
            self._page_info[0] = ("Home", "Oversee your team's members and their runs."
                                  if is_admin else
                                  "Pick a test to run, or open your past results.")
            self._on_page_changed(self.tabs.currentIndex())

            # Team tools only make sense for an organisation admin -- hide the tab itself
            # for everyone else rather than showing it with a "not an admin" placeholder.
            team_visible = signed_in and account.is_admin and bool(account.organisation_id)
            self.tabs.setTabVisible(self._team_tab_index, team_visible)
            if is_admin and self.tabs.currentIndex() in own_runs_pages:
                self.tabs.setCurrentIndex(self._team_tab_index if team_visible else 0)
            if not team_visible and self.tabs.currentIndex() == self._team_tab_index:
                self.tabs.setCurrentIndex(0 if is_admin else self._scan_tab_index)

            self.team_tab.refresh_visibility()
            # The Home tile follows the same rule as the page it opens.
            self.home_team_tile.setVisible(team_visible)
            self._sync_nav()
            # The History tab has nothing of its own to poll on a timer, so any moment
            # the signed-in account might have changed is also a moment to refresh it.
            self._reload_history()
            self._check_organisation_subscription(account)

        def _check_organisation_subscription(self, account) -> None:
            """Explain a suspended organisation once, at the moment someone signs in.

            The server already refuses everything for an organisation whose paid year
            ended without renewal. This turns that into one clear message: a member is
            told to ask their admin and signed out, and an admin is taken to Team →
            Subscription, the one place still open to them, to renew.
            """
            if account is None or not account.organisation_id:
                self._suspension_shown_for = None
                return
            try:
                org = self.api.my_organisation()
            except Exception:  # noqa: BLE001 - a failed check must not block sign-in
                return
            subscription = (org or {}).get("subscription") or {}
            if subscription.get("active") is not False:
                self._suspension_shown_for = None
                return
            if getattr(self, "_suspension_shown_for", None) == account.id:
                return
            self._suspension_shown_for = account.id

            from PySide6.QtCore import QTimer
            if subscription.get("state") == "unpaid":
                # Just signed up in the app: the first year is paid before anything opens.
                if account.is_admin:
                    self.tabs.setCurrentIndex(self._team_tab_index)
                    if QMessageBox.question(
                            self, "Pay for your subscription",
                            "Your organisation is set up. One yearly subscription covers you "
                            "and everyone you invite, and it renews automatically until you "
                            "cancel.\n\nOpen the payment page now?") == QMessageBox.Yes:
                        self.team_tab._renew_subscription()
                else:
                    QMessageBox.warning(
                        self, "Subscription not paid",
                        "Your organisation's subscription has not been paid yet.\n\nAsk your "
                        "organisation's admin to pay for it. You will be signed out now.")
                    QTimer.singleShot(0, self._sign_out)
            elif account.is_admin:
                QMessageBox.warning(
                    self, "Subscription ended",
                    "Your organisation's subscription has ended, so your account and every "
                    "member's account are suspended.\n\nRenew it from Team → Subscription to "
                    "restore access for everyone.")
                self.tabs.setCurrentIndex(self._team_tab_index)
            else:
                QMessageBox.warning(
                    self, "Subscription ended",
                    "Your organisation's subscription has ended, so your account is "
                    "suspended until it is renewed.\n\nAsk your organisation's admin to renew "
                    "it. You will be signed out now.")
                # Deferred: this can run inside a sign-in flow, which should finish first.
                QTimer.singleShot(0, self._sign_out)

        def _update_compare_button(self) -> None:
            """Comparing needs a team account and two saved runs to compare.

            Called when the account changes and whenever History reloads, since a delete,
            a clear or a new run can each move the count across two.
            """
            team = bool(self.api and self.api.account and self.api.account.is_team)
            enough = len(self._history_ids) >= 2
            self.compare_btn.setEnabled(team and enough)
            if not team:
                tip = "Comparing runs is for team accounts."
            elif not enough:
                tip = "Save at least two runs to compare them."
            else:
                tip = "Compare two saved runs to see what was fixed and what is new."
            self.compare_btn.setToolTip(tip)

        def _update_save_report_buttons(self) -> None:
            """Single place the three "Save report from account" buttons get their state.

            Enabled whenever there is a scan to save, for every account tier. Called
            whenever the account might have changed (_refresh_account_ui) and whenever
            the History selection changes (_open_history_scan), not just right after a job finishes -- otherwise a run
            saved while signed out stays showing a save button with nothing behind it.
            """
            tooltip = "Download this run's report from your account."

            for btn in (self.scan_save_report_btn, self.assess_save_report_btn):
                btn.setEnabled(bool(self.api) and bool(self._last_scan_id))
                btn.setToolTip(tooltip)

            history_ok = self._history_selected_index() >= 0
            self.history_save_report_btn.setEnabled(bool(self.api) and history_ok)
            self.history_open_report_btn.setEnabled(bool(self.api) and history_ok)
            self.history_save_report_btn.setToolTip(tooltip)

        def _switch_to_saved(self) -> "bool | None":
            """Offer the other saved accounts. Returns True if switched, False if the user
            wants a password sign-in instead, None if they cancelled."""
            from PySide6.QtWidgets import QApplication, QInputDialog
            from .signin import list_accounts, switch_to

            current = self.api.account.email if self.api and self.api.account else None
            others = [a for a in list_accounts(self._session_dir) if a.email != current]
            if not others:
                return False

            add_new = "Add another account…"
            choice, ok = QInputDialog.getItem(
                self, "Switch account", "Saved accounts:",
                [a.email for a in others] + [add_new], 0, False)
            if not ok:
                return None
            if choice == add_new:
                return False

            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                client = switch_to(self._session_dir, choice, self.cfg.api["base_url"])
            finally:
                QApplication.restoreOverrideCursor()
            if client is None:
                self.statusBar().showMessage(
                    f"The saved sign-in for {choice} expired. Enter the password again.")
                return False

            self.api = client
            self.cfg.api["enabled"] = True
            self._refresh_account_ui()
            self.statusBar().showMessage(f"Switched to {client.account.email}")
            return True

        def _sign_in(self) -> None:
            from .signin import show_signin_dialog

            switched = self._switch_to_saved()
            if switched is not False:
                return

            client, _remember = show_signin_dialog(
                self, base_url=self.cfg.api["base_url"], base_dir=self._session_dir
            )
            if client is None:
                return

            self.api = client
            # Signing in is also how the server gets enabled for this session, so a user
            # who never signs in is never asked about a server they do not have.
            self.cfg.api["enabled"] = True
            self._refresh_account_ui()
            self.statusBar().showMessage(f"Signed in as {client.account.email}")
            self._prompt_student_verification()

        def _prompt_student_verification(self) -> None:
            """Ask for the student code straight after sign-in, while the email is fresh.

            Without this, an unverified student would only find out at the end of a full
            scan, when the upload is refused.
            """
            if self.api is None or self.api.account is None:
                return
            if self.api.account.needs_student_verification:
                self._verify_student()

        def _verify_student(self) -> None:
            if self.api is None:
                return
            from .account_dialogs import show_student_verification_dialog

            result = show_student_verification_dialog(self, self.api)
            if result.changed:
                self._refresh_account_ui()
                self.statusBar().showMessage(result.message)

        def _sign_out(self) -> None:
            from .signin import clear_session

            if self.api is not None:
                self.api.logout()
            clear_session(self._session_dir)
            self.api = None
            self._refresh_account_ui()
            self.statusBar().showMessage("Signed out.")
            # No account, no local storage, nowhere for the next scan to go: ask for a
            # sign-in immediately rather than leaving buttons on screen that would fail.
            self._require_signed_in_or_quit()

        def _open_account_settings(self) -> None:
            """Display name, email, password, and account deletion."""
            if self.api is None:
                return
            from .account_dialogs import show_profile_dialog

            result = show_profile_dialog(self, self.api)
            if result.account_deleted or result.signed_out:
                # The session is gone either way, so clear it here rather than leaving the
                # window believing it is still signed in.
                self._force_sign_out(result.message)
            elif result.changed:
                try:
                    self.api.get_profile()
                except Exception:  # noqa: BLE001 - display only
                    pass
                self._refresh_account_ui()

        def _join_organisation(self) -> None:
            if self.api is None:
                return
            from .account_dialogs import show_join_organisation_dialog

            result = show_join_organisation_dialog(self, self.api)
            if result.signed_out:
                self._force_sign_out(result.message)

        def _force_sign_out(self, message: str = "") -> None:
            """Drop the session locally after the server has already ended it.

            Used when an action revokes the session as a side effect, such as changing a
            password or joining a team. Calling logout() would only fail against a token the server
            has already rejected.
            """
            from .signin import clear_session

            clear_session(self._session_dir)
            self.api = None
            self._refresh_account_ui()
            self.statusBar().showMessage(message or "Signed out.")
            self._require_signed_in_or_quit()

        def _show_account_details(self) -> None:
            if self.api is None:
                return
            try:
                account = self.api.get_profile()
                # The history listing is where the server reports the account's limit.
                limit = self.api.list_history(limit=1).get("historyLimit")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Could not load account", str(exc))
                return

            lines = [
                f"<b>Email:</b> {account.email}",
                f"<b>Name:</b> {account.display_name or 'not set'}",
                f"<b>Account type:</b> {'team' if account.is_team else 'student'}",
                f"<b>Role:</b> {account.role}",
                f"<b>Student email:</b> {_student_status_text(account)}",
                f"<b>Scans saved:</b> {account.scan_count}",
                f"<b>History kept:</b> {'unlimited' if limit is None else f'newest {limit}'}",
            ]
            if account.organisation_id:
                try:
                    org = self.api.my_organisation()
                    if org:
                        lines.append(f"<b>Organisation:</b> {org['name']} ({org['memberCount']} members)")
                except Exception:  # noqa: BLE001 - detail is optional
                    pass

            QMessageBox.information(self, "Account details", "<br>".join(lines))

        # ---- Server sync -------------------------------------------------- #

        def _upload(self, run_, *, authorised: bool, apk_file_name: str | None,
                    into: dict) -> None:
            """Upload a finished run, recording the outcome in `into`.

            Called from the worker thread, so it must not touch a widget. The result is
            left in the dict the caller passed, which the main thread reads once the
            finished signal arrives. Emitting that signal is the handover point, so no
            further synchronisation is needed.

            Every error is captured rather than raised, so a failed upload cannot crash
            the job — but note that BioAudit keeps no local copy, so a failure here really
            does mean the run is not saved anywhere until a retry succeeds.
            """
            from ..api import ApiClientError

            try:
                result = self.api.upload_run(
                    run_, authorised=authorised, apk_file_name=apk_file_name,
                    client_name=self._client_name or None,
                )
                into["scan_id"] = result["scan"]["id"]
                retention = result.get("retention")
                into["message"] = "Saved to your account."
                if retention:
                    into["message"] += " " + retention["message"]
            except ApiClientError as exc:
                into["error"] = exc.message
            except Exception as exc:  # noqa: BLE001
                into["error"] = f"{type(exc).__name__}: {exc}"

        def _retry_upload(self) -> None:
            """Try again to save the last run whose upload failed.

            Runs on the main thread with a short blocking call rather than spinning up a
            whole worker thread: the run's findings already exist, this is just one HTTP
            request, and the wait cursor is enough feedback for something this quick.
            """
            ctx = self._job_ctx
            if not ctx or ctx.get("run") is None:
                return
            if self.api is None:
                QMessageBox.information(
                    self, "Not signed in",
                    "Sign in from the Account menu, then retry saving this run.")
                return

            retry_btn = ctx.get("retry_btn")
            if retry_btn is not None:
                retry_btn.setEnabled(False)
            self.setCursor(Qt.WaitCursor)
            try:
                sync_result: dict = {}
                self._upload(ctx["run"], authorised=True,
                            apk_file_name=ctx.get("apk_file_name"), into=sync_result)
            finally:
                self.setCursor(Qt.ArrowCursor)

            ctx["sync"] = sync_result
            self._last_scan_id = sync_result.get("scan_id")
            self._update_save_report_buttons()

            self._render_run(ctx)   # also rebuilds the severity buttons and the retry button

            self.statusBar().showMessage(
                "Saved to your account." if sync_result.get("scan_id")
                else f"Still not saved: {sync_result.get('error', 'unknown error')}")
            self._reload_history()
            # After the message above, so it isn't wiped out immediately by it if the
            # checkbox is ticked — this one keeps updating the status bar as it goes.
            self._maybe_auto_explain(ctx)

        # ---- Home tab ------------------------------------------------------ #

        def _build_home_tab(self) -> QWidget:
            """A dashboard landing page: a hero header and a grid of coloured tiles,
            one per feature, that jump to the tab doing the real work. Exists so first
            launch shows something more inviting than a blank "Scan an APK" form, and so
            related features (scan vs. assess, history vs. team) read as a set of choices
            rather than a row of easily-missed tab labels.
            """
            w = QWidget()
            layout = QVBoxLayout(w)
            layout.setContentsMargins(16, 8, 16, 16)
            layout.setSpacing(12)

            # The page header already names the app's purpose, so Home opens straight
            # onto the choices instead of repeating a title banner.
            choose = QLabel("Choose what to do")
            choose.setObjectName("sectionHeading")
            layout.addWidget(choose)

            grid = QGridLayout()
            grid.setSpacing(14)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)

            self.home_scan_tile = _tile_button(
                "scan", "scan", "Scan an APK",
                "Static analysis of an .apk file on disk. No device needed.")
            self.home_scan_tile.clicked.connect(
                lambda: self.tabs.setCurrentIndex(self._scan_tab_index))

            self.home_assess_tile = _tile_button(
                "assess", "assess", "Assess a device",
                "Full runtime assessment against an installed app, over ADB.")
            self.home_assess_tile.clicked.connect(
                lambda: self.tabs.setCurrentIndex(self._assess_tab_index))

            self.home_history_tile = _tile_button(
                "history", "history", "History",
                "Browse past runs saved to your account, and compare two of them.")
            self.home_history_tile.clicked.connect(
                lambda: self.tabs.setCurrentIndex(self._history_tab_index))

            self.home_team_tile = _tile_button(
                "team", "team", "Team",
                "Admin oversight: members, invitations, flagged runs, activity.")
            self.home_team_tile.clicked.connect(
                lambda: self.tabs.setCurrentIndex(self._assess_tab_index + 2))

            grid.addWidget(self.home_scan_tile, 0, 0)
            grid.addWidget(self.home_assess_tile, 0, 1)
            grid.addWidget(self.home_history_tile, 1, 0)
            grid.addWidget(self.home_team_tile, 1, 1)
            self.home_grid = grid   # the Team tile moves within it for an admin
            layout.addLayout(grid)

            # What happens during a test, so a first-time user knows what they are
            # starting before they start it.
            layout.addSpacing(14)
            how = QLabel("How a test works")
            how.setObjectName("sectionHeading")
            layout.addWidget(how)
            steps = QHBoxLayout()
            steps.setSpacing(14)
            for number, title, body in (
                ("01", "Read the app file",
                 "Checks the manifest and code for biometric checks that trust a "
                 "simple yes or no."),
                ("02", "Probe a real phone",
                 "Opens protected screens directly over ADB, with no root and no "
                 "changes to the app."),
                ("03", "Explain the fix",
                 "Each confirmed finding comes with guidance checked against "
                 "OWASP Mobile Top 10 and MASVS."),
            ):
                card = QFrame()
                card.setObjectName("stepCard")
                inner = QVBoxLayout(card)
                inner.setContentsMargins(16, 14, 16, 14)
                inner.setSpacing(4)
                num = QLabel(number)
                num.setObjectName("stepNumber")
                head = QLabel(title)
                head.setObjectName("stepTitle")
                text = QLabel(body)
                text.setObjectName("stepBody")
                text.setWordWrap(True)
                inner.addWidget(num)
                inner.addWidget(head)
                inner.addWidget(text)
                inner.addStretch(1)
                steps.addWidget(card, 1)
            layout.addLayout(steps)
            layout.addStretch(1)
            return w

        # ---- Scan APK tab ------------------------------------------------- #

        def _build_scan_tab(self) -> QWidget:
            w = QWidget()
            layout = QVBoxLayout(w)
            layout.setContentsMargins(16, 12, 16, 12)
            layout.setSpacing(10)

            # --- target -----------------------------------------------
            target, tl = _section("Target", accent="scan")
            row = QHBoxLayout()
            row.setSpacing(8)
            self.scan_apk_edit = QLineEdit()
            self.scan_apk_edit.setPlaceholderText(
                "Path to an .apk file you own or are authorised to test")
            browse = QPushButton("Browse…")
            browse.clicked.connect(self._pick_scan_apk)
            row.addWidget(self.scan_apk_edit, 1)
            row.addWidget(browse)
            tl.addLayout(row)
            tl.addWidget(_hint(
                "Reads the app's settings and compiled code without running it. No phone needed."))
            layout.addWidget(target)

            # Its own section rather than another tick box in a list, because it is a
            # legal confirmation and not a preference. A static scan touches no live app,
            # but the result is still an assessment of software someone owns, and the
            # confirmation is what puts that on record before the run happens.
            auth, al = _section("Authorisation", accent="scan")
            self.scan_authorized = QCheckBox(
                "I own this app, or I have permission to test it")
            self.scan_authorized.setStyleSheet("font-weight: 600;")
            al.addWidget(self.scan_authorized)
            self.scan_client = QLineEdit()
            self.scan_client.setPlaceholderText(
                "Client name for the authorisation record (optional)")
            al.addWidget(self.scan_client)
            al.addWidget(_hint(
                "Required. Assessing an app you do not own or have permission to test "
                "can be an offence."))
            layout.addWidget(auth)

            # --- action -----------------------------------------------
            action = QHBoxLayout()
            action.setSpacing(10)
            self.scan_btn = QPushButton("Run static scan")
            self.scan_btn.setObjectName("primary")
            self.scan_btn.setCursor(Qt.PointingHandCursor)
            self.scan_btn.clicked.connect(self._run_scan)
            self.scan_open_report = QPushButton("Open full report")
            self.scan_open_report.setEnabled(False)
            self.scan_open_report.clicked.connect(self._open_last_report)
            # Optional: once this run is saved to the account, ask the server in the
            # background to write a plain-language explanation and fix for every finding.
            self.scan_use_ai = ToggleSwitch("Get AI explanations")
            self.scan_use_ai.setProperty("_palette", self.palette_colours)
            self.scan_use_ai.setToolTip(
                "After this run is saved, ask the server to write a plain-language "
                "explanation and fix for every finding. Runs in the background; open "
                "the report afterwards to read them.")
            # This needs the run to exist on the server, so it stays disabled until a
            # run has been saved to the account.
            self.scan_save_report_btn = QPushButton("Save report from account")
            self.scan_save_report_btn.setEnabled(False)
            self.scan_save_report_btn.setToolTip(
                "Download this run's report from your account. For team accounts.")
            self.scan_save_report_btn.clicked.connect(self._save_server_report)
            # Only shown while a run is going, so the row is not cluttered when idle.
            self.scan_cancel_btn = QPushButton("Cancel")
            self.scan_cancel_btn.setToolTip(
                "Stop after the current step finishes. A step already reading the file "
                "cannot be interrupted part way.")
            self.scan_cancel_btn.clicked.connect(self._cancel_job)
            self.scan_cancel_btn.hide()
            # Only shown when the automatic upload failed, since BioAudit keeps no local
            # copy and this is the only way to get the run saved after that.
            self.scan_retry_btn = QPushButton("Retry saving to your account")
            self.scan_retry_btn.clicked.connect(self._retry_upload)
            self.scan_retry_btn.hide()

            action.addWidget(self.scan_btn)
            action.addWidget(self.scan_cancel_btn)
            action.addWidget(self.scan_retry_btn)
            action.addWidget(self.scan_open_report)
            action.addWidget(self.scan_use_ai)
            action.addWidget(self.scan_save_report_btn)
            action.addStretch(1)
            layout.addLayout(action)

            # Naming the current step is what stops a long run looking like a freeze.
            self.scan_stage = QLabel("")
            self.scan_stage.setObjectName("hint")
            self.scan_stage.hide()
            layout.addWidget(self.scan_stage)

            self.scan_progress = QProgressBar()
            self.scan_progress.setRange(0, 0)  # indeterminate
            self.scan_progress.setTextVisible(False)
            self.scan_progress.hide()
            layout.addWidget(self.scan_progress)

            # --- results ----------------------------------------------
            self.scan_summary = self._make_summary_bar()
            layout.addWidget(self.scan_summary)

            self.scan_results = QTextBrowser()
            self.scan_results.setOpenExternalLinks(True)
            self.scan_results.setHtml(theme.empty_state_html(
                "Check an app's file",
                ["Choose an .apk file above, then run the scan.",
                 "It flags a debuggable build, backup exposure, screens left open to other "
                 "apps, and a fingerprint check that is not tied to a key.",
                 "The result is saved to your account automatically when it finishes."],
                self.palette_colours))
            layout.addWidget(self.scan_results, 1)

            return w

        def _pick_scan_apk(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "Select APK", "", "Android package (*.apk);;All files (*)")
            if path:
                self.scan_apk_edit.setText(path)

        def _run_scan(self) -> None:
            if not self.scan_authorized.isChecked():
                QMessageBox.warning(
                    self, "Authorisation required",
                    "Confirm you own this app, or have permission to test it, before "
                    "scanning. Tick the authorisation box first.")
                return
            apk = self.scan_apk_edit.text().strip()
            if not apk:
                QMessageBox.warning(self, "No APK", "Choose an APK file first.")
                return
            if not os.path.exists(apk):
                QMessageBox.warning(self, "Not found", f"File does not exist:\n{apk}")
                return

            sync_result: dict = {}
            apk_name = os.path.basename(apk)
            # Read on the main thread: the worker must not touch a widget.
            authorised = self.scan_authorized.isChecked()
            self._client_name = self.scan_client.text().strip()

            def job(report) -> TestRun:
                # Heavy work on the worker thread; the report (weasyprint/GTK) is
                # written later on the main thread in _start_job's finished handler.
                stop = self._cancel_event
                run_ = core.build_scan_apk(
                    apk, self.cfg, on_progress=report,
                    should_cancel=(stop.is_set if stop else None))
                core.process_findings(run_, on_progress=report)
                # Last chance to stop: once the upload starts the run is kept.
                if stop is not None and stop.is_set():
                    raise core.ScanCancelled("Stopped at your request.")
                report(SAVING_STEP)
                # Always true by this point -- _run_scan refuses to start without the
                # authorisation box ticked -- but recorded from the control rather than
                # hard-coded, so the stored run reflects what the user actually confirmed.
                # Uploading is not optional: BioAudit keeps no local copy, so this is the
                # only place the run ends up.
                self._upload(run_, authorised=authorised, apk_file_name=apk_name,
                             into=sync_result)
                return run_

            self._start_job(job, self.scan_btn, self.scan_progress, self.scan_results,
                            self.scan_open_report, "Starting",
                            sync_result=sync_result, summary_label=self.scan_summary,
                            stage_label=self.scan_stage, cancel_btn=self.scan_cancel_btn,
                            retry_btn=self.scan_retry_btn, apk_file_name=apk_name,
                            use_ai_checkbox=self.scan_use_ai)

        # ---- Assess device tab -------------------------------------------- #

        def _build_assess_tab(self) -> QWidget:
            w = QWidget()
            layout = QVBoxLayout(w)
            layout.setContentsMargins(16, 12, 16, 12)
            layout.setSpacing(10)

            # --- device and app ---------------------------------------
            target, tl = _section("Device and app", accent="assess")
            form = QFormLayout()
            form.setSpacing(8)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

            dev_row = QHBoxLayout()
            dev_row.setSpacing(8)
            self.device_combo = QComboBox()
            refresh = QPushButton("Refresh")
            refresh.clicked.connect(self._refresh_devices)
            # Only shown when this computer has no ADB at all.
            self.get_adb_btn = QPushButton("Download ADB")
            self.get_adb_btn.setObjectName("primary")
            self.get_adb_btn.setToolTip(
                "Fetch Google's ADB tool for BioAudit to use. Nothing is installed system-wide.")
            self.get_adb_btn.clicked.connect(self._download_adb)
            self.get_adb_btn.hide()
            dev_row.addWidget(self.device_combo, 1)
            dev_row.addWidget(refresh)
            dev_row.addWidget(self.get_adb_btn)
            form.addRow(_field_label("Device"), dev_row)

            pkg_row = QHBoxLayout()
            pkg_row.setSpacing(8)
            self.package_combo = QComboBox()
            self.package_combo.setEditable(True)          # type to filter, or pick
            self.package_combo.setInsertPolicy(QComboBox.NoInsert)
            self.package_combo.lineEdit().setPlaceholderText("com.example.app, or click Load apps")
            comp = self.package_combo.completer()
            if comp:
                comp.setFilterMode(Qt.MatchContains)      # match anywhere in the name
                comp.setCompletionMode(QCompleter.PopupCompletion)
            self.include_system = QCheckBox("incl. system")
            load_apps = QPushButton("Load apps")
            load_apps.clicked.connect(self._load_packages)
            pkg_row.addWidget(self.package_combo, 1)
            pkg_row.addWidget(self.include_system)
            pkg_row.addWidget(load_apps)
            form.addRow(_field_label("App"), pkg_row)

            apk_row = QHBoxLayout()
            apk_row.setSpacing(8)
            self.assess_apk_edit = QLineEdit()
            self.assess_apk_edit.setPlaceholderText(
                "Optional, but adds the file checks and finds more open screens")
            browse = QPushButton("Browse…")
            browse.clicked.connect(self._pick_assess_apk)
            apk_row.addWidget(self.assess_apk_edit, 1)
            apk_row.addWidget(browse)
            form.addRow(_field_label("APK file"), apk_row)

            tl.addLayout(form)
            layout.addWidget(target)

            # --- authorisation ----------------------------------------
            # Its own section rather than another tick box in a list, because it is a
            # legal confirmation and not a preference.
            auth, al = _section("Authorisation", accent="assess")
            self.assess_authorized = QCheckBox(
                "I own this app, or I have permission to test it")
            self.assess_authorized.setStyleSheet("font-weight: 600;")
            al.addWidget(self.assess_authorized)
            self.assess_client = QLineEdit()
            self.assess_client.setPlaceholderText(
                "Client name for the authorisation record (optional)")
            al.addWidget(self.assess_client)
            al.addWidget(_hint(
                "Required. This test opens the app's screens on a real device, and testing "
                "an app without permission can be an offence."))
            layout.addWidget(auth)

            # --- action -----------------------------------------------
            action = QHBoxLayout()
            action.setSpacing(10)
            self.assess_btn = QPushButton("Run full assessment")
            self.assess_btn.setObjectName("primary")
            self.assess_btn.setCursor(Qt.PointingHandCursor)
            self.assess_btn.clicked.connect(self._run_assess)
            self.assess_open_report = QPushButton("Open full report")
            self.assess_open_report.setEnabled(False)
            self.assess_open_report.clicked.connect(self._open_last_report)
            # Optional: once this run is saved to the account, ask the server in the
            # background to write a plain-language explanation and fix for every finding.
            self.assess_use_ai = ToggleSwitch("Get AI explanations")
            self.assess_use_ai.setProperty("_palette", self.palette_colours)
            self.assess_use_ai.setToolTip(
                "After this run is saved, ask the server to write a plain-language "
                "explanation and fix for every finding. Runs in the background; open "
                "the report afterwards to read them.")
            # This needs the run to exist on the server, so it stays disabled until a
            # run has been saved to the account.
            self.assess_save_report_btn = QPushButton("Save report from account")
            self.assess_save_report_btn.setEnabled(False)
            self.assess_save_report_btn.setToolTip(
                "Download this run's report from your account. For team accounts.")
            self.assess_save_report_btn.clicked.connect(self._save_server_report)
            # Only shown while a run is going, so the row is not cluttered when idle.
            self.assess_cancel_btn = QPushButton("Cancel")
            self.assess_cancel_btn.setToolTip(
                "Stop after the current step finishes. A step already talking to the "
                "device cannot be interrupted part way.")
            self.assess_cancel_btn.clicked.connect(self._cancel_job)
            self.assess_cancel_btn.hide()
            # Only shown when the automatic upload failed, since BioAudit keeps no local
            # copy and this is the only way to get the run saved after that.
            self.assess_retry_btn = QPushButton("Retry saving to your account")
            self.assess_retry_btn.clicked.connect(self._retry_upload)
            self.assess_retry_btn.hide()

            action.addWidget(self.assess_btn)
            action.addWidget(self.assess_cancel_btn)
            action.addWidget(self.assess_retry_btn)
            action.addWidget(self.assess_open_report)
            action.addWidget(self.assess_use_ai)
            action.addWidget(self.assess_save_report_btn)
            action.addStretch(1)
            layout.addLayout(action)

            # Naming the current step is what stops a long run looking like a freeze.
            self.assess_stage = QLabel("")
            self.assess_stage.setObjectName("hint")
            self.assess_stage.hide()
            layout.addWidget(self.assess_stage)

            self.assess_progress = QProgressBar()
            self.assess_progress.setRange(0, 0)
            self.assess_progress.setTextVisible(False)
            self.assess_progress.hide()
            layout.addWidget(self.assess_progress)

            # --- results ----------------------------------------------
            self.assess_summary = self._make_summary_bar()
            layout.addWidget(self.assess_summary)

            self.assess_results = QTextBrowser()
            self.assess_results.setOpenExternalLinks(True)
            self.assess_results.setHtml(theme.empty_state_html(
                "Test an app on a real phone",
                ["Connect a phone with USB debugging on, then click Refresh and Load apps.",
                 "This tries to open the app's private screens directly, checks whether any "
                 "part of it gives away answers to guesses, and reads the phone's log.",
                 "Tick the authorisation box before running."],
                self.palette_colours))
            layout.addWidget(self.assess_results, 1)

            self._refresh_devices()
            return w

        def _pick_assess_apk(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "Select APK", "", "Android package (*.apk);;All files (*)")
            if path:
                self.assess_apk_edit.setText(path)

        def _load_packages(self) -> None:
            # A phone plugged in after the window opened would otherwise never be noticed:
            # the device list is only built once, when the tab is created.
            if not self.device_combo.isEnabled():
                self._refresh_devices()
            if not adb_available(self.cfg.device["adb_path"]):
                QMessageBox.information(
                    self, "ADB needed",
                    "Listing a phone's apps needs ADB. Press Download ADB, next to the "
                    "device list, to set it up.")
                return

            serial = (self.device_combo.currentData()
                      if self.device_combo.isEnabled() else None)
            try:
                adb = Adb(self.cfg.device["adb_path"], serial or self.cfg.device["serial"])
                pkgs = adb.list_packages(third_party_only=not self.include_system.isChecked())
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(
                    self, "Could not list apps",
                    f"Failed to list installed packages:\n{exc}\n\n"
                    "Make sure a device is connected and authorized.")
                return

            # An empty list is not an error, so it would otherwise look exactly like a
            # successful load and leave the user clicking a button that seems dead.
            if not pkgs:
                QMessageBox.information(
                    self, "No apps found",
                    "The device reported no installed apps.\n\n"
                    "If you are looking for a built-in app, tick \"incl. system\" — the "
                    "list is limited to apps you installed yourself by default.")
                self.statusBar().showMessage("No apps found on the device.")
                return

            typed = self.package_combo.currentText()
            self.package_combo.clear()
            self.package_combo.addItems(pkgs)
            self.package_combo.setEditText(typed)   # keep whatever was already typed
            self.statusBar().showMessage(
                f"Loaded {len(pkgs)} app(s) — type to filter, or pick from the list")
            # With nothing typed the combo's visible text is unchanged by the load, so
            # without this the button looks like it did nothing at all. Showing the list
            # is the only feedback that lands where the user is already looking.
            if not typed.strip():
                self.package_combo.showPopup()

        def _refresh_devices(self) -> None:
            self.device_combo.clear()
            has_adb = adb_available(self.cfg.device["adb_path"])
            self.get_adb_btn.setVisible(not has_adb)
            if not has_adb:
                self.device_combo.addItem("(ADB is not set up on this computer: press Download ADB)")
                self.device_combo.setEnabled(False)
                return
            try:
                states = Adb(self.cfg.device["adb_path"], self.cfg.device["serial"]).device_states()
            except Exception as exc:  # noqa: BLE001
                self.statusBar().showMessage(f"adb error: {exc}")
                states = {}
            serials = [serial for serial, state in states.items() if state == "device"]
            if serials:
                # Show a masked label but keep the real serial as item data: adb needs
                # the real one, a screen share should not show it.
                for ser in serials:
                    self.device_combo.addItem(mask_serial(ser), ser)
                self.device_combo.setEnabled(True)
                return
            # A phone adb can see but cannot use says why, rather than looking absent.
            if "unauthorized" in states.values():
                self.device_combo.addItem(
                    "(phone found, not allowed yet: unlock it, tap Allow on its USB debugging "
                    "prompt, then press Refresh)")
            elif states:
                self.device_combo.addItem(
                    "(phone found but offline: unplug it, plug it back in, then press Refresh)")
            else:
                self.device_combo.addItem("(no device — check USB debugging)")
                self.statusBar().showMessage(
                    "No phone found. Check USB debugging is on, the cable carries data (not "
                    "only charging), and Windows has the phone's USB driver.")
            self.device_combo.setEnabled(False)

        def _download_adb(self) -> None:
            """Fetch Google's platform-tools, so assessing a phone needs nothing installed."""
            from .. import platform_tools

            if QMessageBox.question(
                self, "Download ADB",
                "Assessing a phone needs ADB, Google's tool for talking to Android devices.\n\n"
                "BioAudit can download Google's Android SDK Platform-Tools (about 7 MB) from "
                "dl.google.com and keep them in its own folder. Nothing is installed "
                "system-wide.\n\n"
                f"By downloading them you agree to Google's Android SDK terms:\n"
                f"{platform_tools.TERMS_URL}\n\nDownload ADB now?",
            ) != QMessageBox.Yes:
                return

            self.get_adb_btn.setEnabled(False)
            self.get_adb_btn.setText("Downloading…")
            self._adb_thread = QThread()
            self._adb_worker = Worker(lambda report: platform_tools.download(report))
            self._adb_worker.moveToThread(self._adb_thread)
            self._adb_thread.started.connect(self._adb_worker.run)
            self._adb_worker.progress.connect(lambda m: self.statusBar().showMessage(m + "…"))
            self._adb_worker.finished.connect(self._on_adb_downloaded)
            self._adb_worker.failed.connect(self._on_adb_download_failed)
            self._adb_thread.start()

        def _finish_adb_download(self) -> None:
            self._adb_thread.quit()
            self._adb_thread.wait()
            self._adb_thread = None
            self._adb_worker = None
            self.get_adb_btn.setEnabled(True)
            self.get_adb_btn.setText("Download ADB")

        def _on_adb_downloaded(self, _path) -> None:
            self._finish_adb_download()
            self._refresh_devices()
            self.statusBar().showMessage("ADB is ready.")
            if not self.device_combo.isEnabled():
                QMessageBox.information(
                    self, "ADB is ready",
                    "ADB is set up. To see your phone here, turn on USB debugging in its "
                    "Developer options, connect it by USB, accept the \"Allow USB "
                    "debugging?\" prompt on the phone, then press Refresh.")

        def _on_adb_download_failed(self, message: str) -> None:
            self._finish_adb_download()
            # The worker sends "Type: message" and a traceback; the first line says it.
            first = message.split("\n", 1)[0].split(": ", 1)[-1]
            QMessageBox.warning(self, "Could not set up ADB", first)

        def _run_assess(self) -> None:
            if not self.assess_authorized.isChecked():
                QMessageBox.warning(
                    self, "Authorization required",
                    "Runtime probing requires confirming you own or are authorized "
                    "to test this app. Tick the authorization box first.")
                return
            package = self.package_combo.currentText().strip()
            if not package:
                QMessageBox.warning(self, "No package", "Enter the target package name.")
                return

            apk = self.assess_apk_edit.text().strip() or None
            if apk and not os.path.exists(apk):
                QMessageBox.warning(self, "Not found", f"APK does not exist:\n{apk}")
                return

            serial = self.device_combo.currentData() or ""

            # Honour the device selected in the combo box for this run.
            cfg = self.cfg
            selected_serial = serial if self.device_combo.isEnabled() else None
            sync_result: dict = {}
            apk_name = os.path.basename(apk) if apk else None
            self._client_name = self.assess_client.text().strip()

            def job(report) -> TestRun:
                stop = self._cancel_event
                adb = Adb(cfg.device["adb_path"], selected_serial or cfg.device["serial"])
                run_ = core.build_assess(
                    package, apk, cfg, adb=adb, on_progress=report,
                    should_cancel=(stop.is_set if stop else None))
                core.process_findings(run_, on_progress=report)
                # Last chance to stop: once the upload starts the run is kept.
                if stop is not None and stop.is_set():
                    raise core.ScanCancelled("Stopped at your request.")
                report(SAVING_STEP)
                # The authorisation box was ticked to get here, and the server refuses a
                # device assessment that does not carry that confirmation. Uploading is
                # not optional: BioAudit keeps no local copy of a run.
                self._upload(run_, authorised=True, apk_file_name=apk_name,
                             into=sync_result)
                return run_

            self._start_job(job, self.assess_btn, self.assess_progress, self.assess_results,
                            self.assess_open_report, "Starting",
                            sync_result=sync_result, summary_label=self.assess_summary,
                            stage_label=self.assess_stage, cancel_btn=self.assess_cancel_btn,
                            retry_btn=self.assess_retry_btn, apk_file_name=apk_name,
                            use_ai_checkbox=self.assess_use_ai)

        # ---- History tab -------------------------------------------------- #

        def _build_history_tab(self) -> QWidget:
            self._history_page = history_view.make_history_page(
                self.palette_colours, on_open=self._open_history_scan,
                on_refresh=self._reload_history, on_clear=self._clear_history,
                on_delete_selected=self._delete_history_runs)

            report = QWidget()
            layout = QVBoxLayout(report)
            layout.setContentsMargins(16, 12, 16, 12)
            layout.setSpacing(10)

            actions = QHBoxLayout()
            actions.setSpacing(8)
            back_btn = QPushButton("Back")
            back_btn.clicked.connect(self._history_page.show_list)
            self.compare_btn = QPushButton("Compare two runs…")
            self.compare_btn.setEnabled(False)
            self.compare_btn.clicked.connect(self._compare_runs)
            self.history_open_report_btn = QPushButton("Open full report")
            self.history_open_report_btn.setEnabled(False)
            self.history_open_report_btn.clicked.connect(self._open_history_report)
            self.history_save_report_btn = QPushButton("Save report from account")
            self.history_save_report_btn.setEnabled(False)
            self.history_save_report_btn.clicked.connect(self._save_history_report)
            self.delete_run_btn = QPushButton("Delete this run")
            self.delete_run_btn.clicked.connect(self._delete_history_run)
            actions.addWidget(back_btn)
            actions.addWidget(self.compare_btn)
            actions.addWidget(self.history_open_report_btn)
            actions.addWidget(self.history_save_report_btn)
            actions.addStretch(1)
            actions.addWidget(self.delete_run_btn)
            layout.addLayout(actions)

            self.history_results = QTextBrowser()
            self.history_results.setOpenExternalLinks(True)
            layout.addWidget(self.history_results, 1)
            self._history_page.add_report_widget(report)

            self._history_ids: list[str] = []
            self._history_scans: list[dict] = []
            self._history_selected_id: "str | None" = None
            self._reload_history()
            return self._history_page

        def _history_selected_index(self) -> int:
            """Index of the open run in `_history_ids`, or -1 when none is open."""
            try:
                return self._history_ids.index(self._history_selected_id)
            except ValueError:
                return -1

        def _reload_history(self) -> None:
            """Refill the History table from the account's history on the server.

            The only history there is: BioAudit keeps no local copy of a scan. Called
            before the welcome gate has necessarily resolved (during tab construction),
            so it tolerates `self.api` still being None rather than assuming sign-in.
            """
            self._history_scans = []
            self._history_ids = []
            limit = None
            load_error = None
            if self.api is not None:
                try:
                    data = self.api.list_history(limit=100)
                    limit = data.get("historyLimit")
                    self._history_scans = [s for s in data.get("scans", []) if s.get("id")]
                    self._history_ids = [s["id"] for s in self._history_scans]
                except Exception as exc:  # noqa: BLE001
                    # Deliberately not the status bar: this can run right after a job
                    # finished and left a more important message there (e.g. "not saved
                    # to your account"), and a background history refresh failing is not
                    # worth overwriting that with.
                    load_error = str(exc)
                    _log_diag(f"history reload failed: {load_error}")
            if self._history_selected_id not in self._history_ids:
                self._history_selected_id = None
                self._history_page.show_list()
            self._history_page.set_scans(self._history_scans, error=load_error, limit=limit)
            self._update_compare_button()
            self._update_save_report_buttons()

        def _open_history_scan(self, scan_id: str) -> None:
            """View Report: switch to the report page at once, fetch the run off-thread.

            The fetch is a network call; doing it on the UI thread froze the window with
            no sign the click had registered. Now the click is acknowledged immediately
            (row highlight, busy cursor, "Loading report" page) and the result is drawn
            by `_on_history_scan_loaded` when the worker finishes.
            """
            if self.api is None or scan_id not in self._history_ids:
                return
            if self._history_load_thread is not None:
                return   # one fetch at a time; ignores double clicks
            self._history_selected_id = scan_id
            self._update_save_report_buttons()
            self._history_page.mark_opening(scan_id)
            self.history_results.setHtml(theme.empty_state_html(
                "Loading report…", ["Fetching this run from your account."],
                self.palette_colours))
            self._history_page.show_report()
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.statusBar().showMessage("Loading report…")

            api = self.api

            def job(_progress):
                try:
                    return scan_id, api.get_scan(scan_id), None
                except Exception as exc:  # noqa: BLE001 - shown in the report pane
                    return scan_id, None, str(exc)

            self._history_load_thread = QThread()
            self._history_load_worker = Worker(job)
            self._history_load_worker.moveToThread(self._history_load_thread)
            self._history_load_thread.started.connect(self._history_load_worker.run)
            self._history_load_worker.finished.connect(self._on_history_scan_loaded)
            self._history_load_thread.start()

        def _on_history_scan_loaded(self, result) -> None:
            scan_id, scan, error = result
            if self._history_load_thread is not None:
                self._history_load_thread.quit()
                self._history_load_thread.wait()
            self._history_load_thread = None
            self._history_load_worker = None
            QApplication.restoreOverrideCursor()
            self._history_page.mark_opening(None)
            self.statusBar().clearMessage()
            if scan_id != self._history_selected_id:
                return   # user went Back / picked another run meanwhile
            if error is not None:
                self.history_results.setHtml(
                    f"<pre style='color:{theme.ERROR_TEXT}; white-space:pre-wrap'>{_escape(error)}</pre>")
            else:
                self.history_results.setHtml(_payload_to_html(scan))

        def _delete_history_run(self) -> None:
            if self.api is None:
                return
            index = self._history_selected_index()
            if index < 0:
                QMessageBox.information(self, "Nothing selected", "Open a run from the list first.")
                return

            run_id = self._history_ids[index]
            scan = self._history_scans[index]
            label = (f"{format_when(scan.get('createdAt'))}  ·  "
                     f"{history_view.package_of(scan)}  ·  "
                     f"{severity_summary(scan.get('counts') or {})}")
            if QMessageBox.question(
                self, "Delete run",
                f"Permanently delete this run from your account?\n\n{label}",
            ) != QMessageBox.Yes:
                return

            try:
                self.api.delete_scan(run_id)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Could not delete", str(exc))
                return

            self.statusBar().showMessage("Run deleted from your account.")
            self._reload_history()

        def _clear_history(self) -> None:
            if self.api is None:
                return
            if not self._history_ids:
                QMessageBox.information(self, "Nothing to clear", "There is no history yet.")
                return

            if not confirm_by_typing(
                self, "Clear history",
                f"Permanently delete all {len(self._history_ids)} run(s) saved to your "
                "account?\n\nThis cannot be undone. Exported report files on this "
                "computer are not affected.",
                len(self._history_ids),
            ):
                return

            try:
                removed = self.api.clear_history()
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Could not clear history", str(exc))
                return

            self.statusBar().showMessage(f"Cleared {removed} run(s) from your account.")
            self._history_page.clear_selection()
            self._reload_history()

        def _delete_history_runs(self, ids: list) -> None:
            """Delete the runs ticked on the History page, after a typed-code confirm."""
            if self.api is None or not ids:
                return
            if not confirm_by_typing(
                self, "Delete selected runs",
                f"Permanently delete {len(ids)} selected run(s) from your account?\n\n"
                "This cannot be undone. Exported report files on this computer are "
                "not affected.",
                len(ids),
            ):
                return

            failed = []
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                for scan_id in ids:
                    try:
                        self.api.delete_scan(scan_id)
                    except Exception as exc:  # noqa: BLE001
                        failed.append(str(exc))
            finally:
                QApplication.restoreOverrideCursor()

            done = len(ids) - len(failed)
            msg = f"Deleted {done} of {len(ids)} run(s)."
            if failed:
                msg += f" {len(failed)} failed."
                QMessageBox.warning(self, "Some runs were not deleted", f"{msg}\n\n{failed[0]}")
            self.statusBar().showMessage(msg)
            self._history_page.clear_selection()
            self._reload_history()

        def _compare_runs(self) -> None:
            """Team accounts. Compare two runs saved to the account.

            Uses the server's copies rather than the local database, because the comparison
            itself is a server feature and matching is done there by category and component.
            """
            if self.api is None:
                return
            from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout

            try:
                self.setCursor(Qt.WaitCursor)
                scans = self.api.list_history(limit=100).get("scans", [])
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Could not load your runs", str(exc))
                return
            finally:
                self.setCursor(Qt.ArrowCursor)

            if len(scans) < 2:
                QMessageBox.information(
                    self, "Not enough runs",
                    "You need at least two runs saved to your account before they can be "
                    "compared. Run a scan or an assessment first — every finished run is "
                    "saved automatically.")
                return

            def describe(scan: dict) -> str:
                target = scan.get("target") or {}
                label = target.get("packageName") or target.get("apkFileName") or "unknown"
                return f"{format_when(scan.get('createdAt'))}  ·  {label}"

            labels = [describe(s) for s in scans]

            dialog = QDialog(self)
            dialog.setWindowTitle("Compare two runs")
            dialog.setMinimumWidth(520)
            dl = QVBoxLayout(dialog)
            intro = QLabel(
                "Pick the earlier run as the baseline and the later one as the current "
                "result. Findings are matched by category and component, so a fix shows up "
                "as resolved even though each run generates fresh identifiers.")
            intro.setWordWrap(True)
            dl.addWidget(intro)

            form = QFormLayout()
            baseline_combo = QComboBox()
            baseline_combo.addItems(labels)
            baseline_combo.setCurrentIndex(min(1, len(labels) - 1))
            current_combo = QComboBox()
            current_combo.addItems(labels)
            current_combo.setCurrentIndex(0)
            form.addRow("Baseline (earlier):", baseline_combo)
            form.addRow("Current (later):", current_combo)
            dl.addLayout(form)

            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.button(QDialogButtonBox.Ok).setText("Compare")
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            dl.addWidget(buttons)

            if dialog.exec() != QDialog.Accepted:
                return

            a, b = baseline_combo.currentIndex(), current_combo.currentIndex()
            if a == b:
                QMessageBox.information(self, "Same run", "Pick two different runs.")
                return

            try:
                self.setCursor(Qt.WaitCursor)
                comparison = self.api.compare_scans(scans[a]["id"], scans[b]["id"])
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Could not compare", str(exc))
                return
            finally:
                self.setCursor(Qt.ArrowCursor)

            self.history_results.setHtml(_comparison_html(comparison, self.palette_colours))
            self._history_page.show_report()
            summary = comparison["summary"]
            self.statusBar().showMessage(
                f"Comparison: {summary['resolved']} fixed, {summary['introduced']} new, "
                f"{summary['unchanged']} unchanged.")


        # ---- Shared job machinery ----------------------------------------- #

        def _start_job(self, job, button, progress, results_view, open_btn, status_msg,
                       sync_result: dict | None = None, summary_label=None,
                       stage_label=None, cancel_btn=None, retry_btn=None,
                       apk_file_name: str | None = None, use_ai_checkbox=None) -> None:
            import threading

            if self._thread is not None:
                QMessageBox.information(self, "Busy", "A task is already running.")
                return

            self._cancel_event = threading.Event()

            button.setEnabled(False)
            button.hide()
            open_btn.setEnabled(False)
            if cancel_btn is not None:
                cancel_btn.setEnabled(True)
                cancel_btn.show()
            if retry_btn is not None:
                # A previous failed run's retry button must not linger once a fresh run
                # starts; it gets its own chance to show again if this one fails too.
                retry_btn.hide()
            if stage_label is not None:
                stage_label.setText(status_msg)
                stage_label.show()
            progress.show()
            # Clear the previous run's badges so a stale summary is never shown beside
            # a run that is still going.
            if summary_label is not None:
                summary_label.hide()
            results_view.setHtml(
                f"<div style='padding:18px 20px;color:{self.palette_colours['muted']};"
                f"font-family:\"Segoe UI\",Arial,sans-serif'>{status_msg}</div>")
            self.statusBar().showMessage(status_msg)

            # Remember which widgets this job drives; the finished/failed handlers
            # (which run on the MAIN thread) read this. `sync_result` is filled in by the
            # worker thread and read here afterwards; the finished signal orders the two.
            self._job_ctx = {
                "button": button, "progress": progress,
                "results_view": results_view, "open_btn": open_btn,
                "sync": sync_result if sync_result is not None else {},
                "summary": summary_label,
                "stage": stage_label,
                "cancel_btn": cancel_btn,
                "retry_btn": retry_btn,
                "apk_file_name": apk_file_name,
                "use_ai_checkbox": use_ai_checkbox,
                "run": None,
            }

            self._thread = QThread()
            self._worker = Worker(job)
            self._worker.moveToThread(self._thread)
            self._thread.started.connect(self._worker.run)
            # Connect to bound methods of `self` — a QObject living on the main
            # thread. Because the worker emits from the worker thread, Qt uses a
            # queued connection and runs these slots on the MAIN thread, which is
            # mandatory: they touch Qt widgets and weasyprint/GTK, neither of which
            # is safe off the main thread (doing so segfaults the frozen build).
            self._worker.finished.connect(self._on_job_finished)
            self._worker.failed.connect(self._on_job_failed)
            self._worker.progress.connect(self._on_job_progress)
            self._worker.cancelled.connect(self._on_job_cancelled)
            self._thread.start()

        def _on_job_progress(self, stage: str) -> None:
            """Show the step the worker has just started. Runs on the main thread."""
            ctx = self._job_ctx or {}
            label = ctx.get("stage")
            if label is not None:
                label.setText(stage + "…")
            # The upload cannot be taken back part way, so Cancel stops being offered.
            if stage == SAVING_STEP and ctx.get("cancel_btn") is not None:
                ctx["cancel_btn"].setEnabled(False)
            self.statusBar().showMessage(stage + "…")

        def _cancel_job(self) -> None:
            """Ask the running job to stop at its next step boundary."""
            if self._cancel_event is None:
                return
            self._cancel_event.set()
            ctx = self._job_ctx or {}
            if ctx.get("cancel_btn") is not None:
                # Disabled rather than hidden, so the user can see the request landed
                # instead of wondering whether the click registered.
                ctx["cancel_btn"].setEnabled(False)
            if ctx.get("stage") is not None:
                ctx["stage"].setText("Stopping after the current step…")
            self.statusBar().showMessage("Stopping after the current step…")

        def _on_job_cancelled(self) -> None:
            ctx = self._job_ctx
            if ctx.get("stage") is not None:
                ctx["stage"].hide()
            ctx["results_view"].setHtml(theme.empty_state_html(
                "Stopped",
                ["You cancelled this run, so no findings were produced.",
                 "Nothing was saved and nothing on the device was changed.",
                 "Start it again whenever you are ready."],
                self.palette_colours))
            self.statusBar().showMessage("Run cancelled.")
            self._cleanup_thread(ctx["button"], ctx["progress"])

        def _render_run(self, ctx) -> None:
            """Write the local report file and show it in the results view, with the
            badges and buttons that go with it.

            Shared by the normal finish path and by the AI-explain completion path
            (success or failure), so a held-back report reveals itself looking exactly
            like an immediately-shown one would have.
            """
            run_ = ctx["run"]
            sync = ctx.get("sync") or {}
            # Nothing is written to disk here: a report is only saved when the user
            # picks "Save report" and chooses where it goes.
            self._last_run = run_

            # Prepend the sync outcome so a failed upload is visible without hiding the
            # findings, which are the point of the run.
            html = _sync_banner_html(sync) + generator.render_html(run_, dark=True)
            ctx["banner"] = _sync_banner_html(sync)
            if ctx.get("retry_btn") is not None:
                ctx["retry_btn"].setVisible(bool(sync.get("error")))
                ctx["retry_btn"].setEnabled(True)

            ctx["results_view"].setHtml(html)
            ctx["open_btn"].setEnabled(True)
            self._build_severity_bar(ctx)

        def _make_summary_bar(self):
            """Package name plus a row of severity jump buttons (filled per run)."""
            bar = QWidget()
            row = QHBoxLayout(bar)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            bar.package_label = QLabel("")
            bar.package_label.setTextFormat(Qt.RichText)
            row.addWidget(bar.package_label)
            bar.buttons_box = QHBoxLayout()
            bar.buttons_box.setSpacing(6)
            row.addLayout(bar.buttons_box)
            row.addStretch(1)
            bar.hide()
            return bar

        def _build_severity_bar(self, ctx) -> None:
            """One button per severity that actually has findings.

            Clicking one scrolls the report to the first finding of that severity. It
            never filters: the report always shows everything.
            """
            bar = ctx.get("summary")
            if bar is None:
                return
            run_ = ctx["run"]
            box = bar.buttons_box
            while box.count():
                item = box.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            bar.package_label.setText(
                f"<span style='color:{self.palette_colours['muted']}'>"
                f"{_escape(run_.package)}</span>")

            present = [(label, n) for label, n in reversed(list(run_.counts().items())) if n]
            if not present:
                bar.package_label.setText(
                    bar.package_label.text()
                    + f"&nbsp;&nbsp;<span style='color:{self.palette_colours['muted']}'>"
                    "No findings. That is not proof the app is safe, only that these "
                    "checks found nothing.</span>")
            for label, n in present:
                b = QPushButton(f"{n} {label}")
                b.setCursor(Qt.PointingHandCursor)
                b.setProperty("sev", label.lower())
                b.setToolTip(f"Jump to the first {label} finding")
                b.clicked.connect(
                    lambda _checked=False, a=generator.severity_anchor(label):
                    ctx["results_view"].scrollToAnchor(a))
                box.addWidget(b)
            bar.show()

        def _explain_would_start(self, ctx) -> bool:
            """Whether _maybe_auto_explain would actually kick off a job for this ctx.

            Checked ahead of the "Done" status message so _on_job_finished knows
            whether to hold the report back, without starting the job early.
            """
            checkbox = (ctx or {}).get("use_ai_checkbox")
            return bool(
                checkbox and checkbox.isChecked()
                and self.api and self._last_scan_id
                and self._explain_thread is None
            )

        def _on_job_finished(self, run_: object) -> None:
            ctx = self._job_ctx
            ctx["run"] = run_   # kept for the retry button, whether or not the upload worked
            if ctx.get("stage") is not None:
                ctx["stage"].hide()

            sync = ctx.get("sync") or {}
            # The server-backed actions only make sense once the run exists there.
            self._last_scan_id = sync.get("scan_id")
            self._update_save_report_buttons()

            # If AI explanations are about to be requested, hold the report back until
            # they arrive (or give up) instead of showing it once and then changing it
            # under the reader — see _render_run, called again from the explain handlers.
            waiting_on_ai = self._explain_would_start(ctx)
            if waiting_on_ai:
                ctx["results_view"].setHtml(theme.empty_state_html(
                    "Getting AI explanations…",
                    [f"{run_.package} finished scanning.",
                     "Findings are being explained by AI before the report is shown — "
                     "this can take a little while.",
                     "Untick \"Get AI explanations\" next time to see the report "
                     "immediately instead."],
                    self.palette_colours))
                ctx["open_btn"].setEnabled(False)
            else:
                self._render_run(ctx)

            counts = run_.counts()
            summary = ", ".join(f"{k} {v}" for k, v in counts.items() if v) or "no findings"
            status = f"Done: {run_.package} — {summary}"
            if sync.get("error"):
                status += "  (not saved to account)"
            elif sync.get("scan_id"):
                status += "  (saved to account)"
            self.statusBar().showMessage(status)
            self._cleanup_thread(ctx["button"], ctx["progress"])
            # After the "Done" message, so it isn't wiped out immediately by it — this
            # one keeps updating the status bar as it goes.
            if waiting_on_ai:
                self._maybe_auto_explain(ctx)

        def _on_job_failed(self, msg: str) -> None:
            ctx = self._job_ctx
            if ctx.get("stage") is not None:
                ctx["stage"].hide()
            ctx["results_view"].setHtml(
                f"<pre style='color:{theme.ERROR_TEXT}; white-space:pre-wrap'>{msg}</pre>")
            self.statusBar().showMessage("Failed")
            self._cleanup_thread(ctx["button"], ctx["progress"])

        def _cleanup_thread(self, button, progress) -> None:
            progress.hide()
            button.setEnabled(True)
            button.show()
            ctx = self._job_ctx or {}
            if ctx.get("cancel_btn") is not None:
                ctx["cancel_btn"].hide()
            self._cancel_event = None
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait()
            self._thread = None
            self._worker = None
            self._reload_history()

        def _maybe_auto_explain(self, ctx) -> bool:
            """Ask the server to explain every finding of the run just saved, if the
            tab's "Get AI explanations" checkbox is ticked. Returns whether a job was
            actually started, so the caller knows whether to wait for it.

            Runs on its own worker thread, separate from the scan/assess job machinery,
            because this fires automatically the moment a run is saved rather than from
            a user click — freezing the window with no click to explain why would be a
            much worse surprise than a normal button doing it. Done server-side so the
            AI key stays on the server, and every finding's evidence is stripped of
            secrets before it is sent. Explanations already generated are reused rather
            than paid for twice.

            The on-screen report and the local report file are both a snapshot taken the
            moment the run finished, before any explanation exists — so once the server
            has them, they are copied back into that same run and both views are
            refreshed. Without this, the explanations would only ever be visible through
            the server-backed "Save report from account" option.
            """
            checkbox = (ctx or {}).get("use_ai_checkbox")
            if checkbox is None or not checkbox.isChecked():
                return False
            if self.api is None or not self._last_scan_id:
                return False
            if self._explain_thread is not None:
                return False  # a previous auto-explain job is still running

            scan_id = self._last_scan_id
            api = self.api

            def job(_progress):
                import time

                scan = api.get_scan(scan_id)
                findings = scan.get("findings", [])
                explained = 0
                failures = []
                updates = {}    # local finding index -> {explanation, mitigation, references}
                first = True
                for index, finding in enumerate(findings):
                    if finding.get("explanation") and finding.get("mitigation"):
                        continue    # already has one
                    # The free Gemini tier allows only a handful of requests per minute.
                    # Spacing calls out keeps a multi-finding run under that ceiling instead
                    # of bursting them back-to-back and tripping a 429.
                    if not first:
                        time.sleep(5)
                    first = False
                    try:
                        updates[index] = api.explain_finding(scan_id, index)
                        explained += 1
                    except Exception as exc:  # noqa: BLE001 - keep going through the rest
                        failures.append(f"{finding.get('title', 'finding')}: {exc}")
                return explained, failures, updates

            # Captured now, not read back off ctx later: a new scan/assess job could
            # start (and overwrite self._job_ctx) before this one finishes. A shallow
            # copy is enough — the widgets it points at are the ones this run's tab
            # already owns, and _render_run needs the same keys _on_job_finished does.
            self._explain_target = dict(ctx)
            self.statusBar().showMessage("Getting AI explanations…")
            self._explain_thread = QThread()
            self._explain_worker = Worker(job)
            self._explain_worker.moveToThread(self._explain_thread)
            self._explain_thread.started.connect(self._explain_worker.run)
            self._explain_worker.finished.connect(self._on_explain_finished)
            self._explain_worker.failed.connect(self._on_explain_failed)
            self._explain_thread.start()
            return True

        def _on_explain_finished(self, result) -> None:
            explained, failures, updates = result
            target = self._explain_target or {}
            self._cleanup_explain_thread()

            run_ = target.get("run")
            if run_ is not None:
                for index, data in updates.items():
                    if 0 <= index < len(run_.findings):
                        f = run_.findings[index]
                        f.explanation = data.get("explanation")
                        f.mitigation = data.get("mitigation")
                # Reveals a held-back report, or re-renders an already-shown one — either
                # way this is the point the reader actually gets to see the result.
                self._render_run(target)

            if explained == 0 and not failures:
                self.statusBar().showMessage("No findings needed an AI explanation.")
                return
            if explained == 0 and failures:
                self.statusBar().showMessage(f"AI explanations failed: {failures[0]}")
                return
            status = f"{explained} finding(s) explained by AI."
            if failures:
                status += f" {len(failures)} could not be explained."
            self.statusBar().showMessage(status)

        def _on_explain_failed(self, message: str) -> None:
            target = self._explain_target or {}
            self._cleanup_explain_thread()
            if target.get("run") is not None:
                self._render_run(target)   # reveal the held-back report even on failure
            self.statusBar().showMessage(f"AI explanations unavailable: {message}")

        def _cleanup_explain_thread(self) -> None:
            if self._explain_thread is not None:
                self._explain_thread.quit()
                self._explain_thread.wait()
            self._explain_thread = None
            self._explain_worker = None

        def _save_server_report(self) -> None:
            """Download the report the server holds for the last saved run."""
            if self.api is None or not self._last_scan_id:
                return
            path, _ = QFileDialog.getSaveFileName(
                self, "Save report", f"bioaudit-{self._last_scan_id}.html",
                "Web page (*.html);;All files (*)")
            if not path:
                return
            try:
                self.setCursor(Qt.WaitCursor)
                html = self.api.export_report(self._last_scan_id)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(html)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Could not save the report", str(exc))
                return
            finally:
                self.setCursor(Qt.ArrowCursor)
            self.statusBar().showMessage(f"Report saved to {path}")
            if QMessageBox.question(self, "Report saved",
                                    "Open it now?") == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))

        def _save_history_report(self) -> None:
            """Download the report for whichever run is selected in History.

            Separate from _save_server_report because that one is scoped to the run just
            finished this session (self._last_scan_id); this one saves an older run picked
            from the account's history instead.
            """
            index = self._history_selected_index()
            if self.api is None or index < 0:
                return
            scan_id = self._history_ids[index]
            path, _ = QFileDialog.getSaveFileName(
                self, "Save report", f"bioaudit-{scan_id}.html",
                "Web page (*.html);;All files (*)")
            if not path:
                return
            try:
                self.setCursor(Qt.WaitCursor)
                html = self.api.export_report(scan_id)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(html)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Could not save the report", str(exc))
                return
            finally:
                self.setCursor(Qt.ArrowCursor)
            self.statusBar().showMessage(f"Report saved to {path}")
            if QMessageBox.question(self, "Report saved",
                                    "Open it now?") == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))

        def _open_history_report(self) -> None:
            """Open the selected History run as a full (light) report in the browser.

            Rendered from the server copy into a temp file; unlike _save_history_report
            this is not team-gated and does not ask where to save.
            """
            index = self._history_selected_index()
            if self.api is None or index < 0:
                return
            scan_id = self._history_ids[index]
            try:
                self.setCursor(Qt.WaitCursor)
                html = _payload_to_html(self.api.get_scan(scan_id), dark=False)
                path = os.path.join(tempfile.gettempdir(), f"bioaudit-{scan_id}.html")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(html)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Could not open the report", str(exc))
                return
            finally:
                self.setCursor(Qt.ArrowCursor)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

        def _open_last_report(self) -> None:
            """View the last run in the browser from a temp file; nothing is saved."""
            if self._last_run is None:
                QMessageBox.information(self, "No report", "No report is available yet.")
                return
            try:
                path = os.path.join(tempfile.gettempdir(), "bioaudit-last-report.html")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(generator.render_html(self._last_run, dark=False))
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Could not open the report", str(exc))
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    _set_macos_app_name("BioAudit")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("BioAudit")
    _icon_path = os.path.join(os.path.dirname(__file__), "assets", "logo_icon.png")
    if os.path.exists(_icon_path):
        app.setWindowIcon(QIcon(_icon_path))

    window = None

    def _bring_to_front() -> None:
        """Another launch asked for the app: show this one instead of opening a second."""
        if window is None:
            return
        if window.isMinimized():
            window.showNormal()
        target = QApplication.activeModalWidget() or window
        target.raise_()
        target.activateWindow()

    if _hand_over_to_running_app(_bring_to_front):
        return 0

    # Styling must not be able to stop the app opening, so a failure here degrades to
    # Qt's default look rather than raising.
    try:
        palette_colours = theme.apply_theme(app)
    except Exception as exc:  # noqa: BLE001
        _log_diag(f"could not apply theme: {exc}")
        palette_colours = theme.LIGHT

    window = MainWindow()
    window.show()
    return app.exec()


def _student_status_text(account) -> str:
    sv = account.student_verification
    if not sv.get("required"):
        return "not needed on this plan"
    if sv.get("status") == "verified":
        university = (sv.get("university") or {}).get("name")
        return f"verified ({university})" if university else "verified"
    if not sv.get("eligible"):
        return "not a university address (personal accounts are for students)"
    return "not yet verified (Account menu, Verify student email)"


def _comparison_html(comparison: dict, palette: dict) -> str:
    """Render a run-to-run comparison.

    Resolved comes first because that is the question a developer actually has after a fix:
    did the thing I changed go away? New findings come second, since a regression matters
    more than a list of what stayed the same.
    """
    from . import theme

    summary = comparison["summary"]

    def group(title: str, findings: list, colour: str, empty: str) -> str:
        if not findings:
            return (f"<h3 style='color:{palette['muted']};font-size:11pt'>{title}</h3>"
                    f"<p style='color:{palette['muted']}'>{empty}</p>")
        items = []
        for f in findings:
            sev = str(f.get("severity", "info")).lower()
            sev_colour = theme.SEVERITY_COLOURS.get(sev, theme.SEVERITY_COLOURS["info"])
            component = f.get("component")
            items.append(
                f"<li style='margin-bottom:6px'>"
                f"<span style='background:{sev_colour};color:#fff;padding:1px 7px;"
                f"border-radius:9px;font-size:8pt;font-weight:700'>{_escape(sev.upper())}</span> "
                f"{_escape(f.get('title', ''))}"
                + (f"<br><span style='color:{palette['muted']};font-size:9pt'>"
                   f"{_escape(component)}</span>" if component else "")
                + "</li>"
            )
        return (f"<h3 style='color:{colour};font-size:11pt'>{title} ({len(findings)})</h3>"
                f"<ul style='padding-left:18px;margin-top:4px'>{''.join(items)}</ul>")

    return f"""
    <div style="font-family:'Segoe UI',Arial,sans-serif;padding:14px 18px">
      <h2 style="color:{palette['text']};font-size:14pt;margin:0 0 4px 0">Run comparison</h2>
      <p style="color:{palette['muted']};margin:0 0 14px 0">
        Baseline {_escape(format_when(comparison['baseline'].get('createdAt')))}
        &nbsp;→&nbsp;
        Current {_escape(format_when(comparison['current'].get('createdAt')))}
      </p>
      <p style="margin-bottom:16px">
        <span style="background:#188038;color:#fff;padding:2px 9px;border-radius:10px;
              font-weight:700;font-size:9pt">{summary['resolved']} fixed</span>&nbsp;&nbsp;
        <span style="background:#d93025;color:#fff;padding:2px 9px;border-radius:10px;
              font-weight:700;font-size:9pt">{summary['introduced']} new</span>&nbsp;&nbsp;
        <span style="background:{palette['muted']};color:#fff;padding:2px 9px;
              border-radius:10px;font-weight:700;font-size:9pt">
              {summary['unchanged']} unchanged</span>
      </p>
      {group("Fixed since the baseline", comparison.get("resolved", []), theme.SUCCESS_TEXT,
             "Nothing was fixed between these two runs.")}
      {group("New in the current run", comparison.get("introduced", []), theme.ERROR_TEXT,
             "No new problems appeared. Good.")}
      {group("Still present", comparison.get("unchanged", []), palette["muted"],
             "Nothing carried over.")}
    </div>
    """


def _escape(value: object) -> str:
    """Escape text before it goes into the on-screen HTML.

    Server messages and target names both end up in these views, and a package name or
    an error string is not something to trust as markup.
    """
    import html

    return html.escape(str(value if value is not None else ""))


def _sync_banner_html(sync: dict) -> str:
    """The strip shown above a run's findings reporting whether it saved.

    Shared between a fresh run and a retry, so both look identical. There is no local
    fallback to reassure the user about on failure: the account is the only place a run
    is stored, so the banner says that plainly and points at the retry button instead.
    """
    if sync.get("error"):
        return (
            "<div style='background:#5c1f1b;color:#ffffff;border-left:4px solid #d93025;"
            "padding:8px 12px;margin-bottom:12px'>"
            f"<b>Not saved to your account:</b> {_escape(sync['error'])}<br>"
            "This run is not stored anywhere yet — BioAudit does not keep a local copy. "
            "The findings are still shown below; use \"Retry saving\" once you are back "
            "online, or before you close this run.</div>"
        )
    if sync.get("message"):
        return (
            "<div style='background:#1d4a2c;color:#ffffff;border-left:4px solid #188038;"
            "padding:8px 12px;margin-bottom:12px'>"
            f"{_escape(sync['message'])}</div>"
        )
    return ""


def _payload_to_html(payload: dict, dark: bool = True) -> str:
    """Reconstruct a TestRun from a scan fetched from the server and render it via the
    report generator, so a history entry looks identical to a fresh result.

    The shape here is whatever `ApiClient.get_scan` returns — a package/device name
    nested under `target`, and each finding's severity stored as its lower-case enum
    name (see `_finding_to_payload` in `api/client.py`), not the capitalised label a
    fresh `TestRun` uses on screen.
    """
    from ..models import Finding, Severity

    def severity_from(value: str) -> Severity:
        try:
            return Severity[str(value).upper()]
        except KeyError:
            return Severity.INFO

    target = payload.get("target") or {}
    package = target.get("packageName") or target.get("apkFileName") or "?"
    run = TestRun(package=package, device_serial=target.get("deviceSerial"))
    run.id = payload.get("id", run.id)
    for fd in payload.get("findings", []):
        run.add(Finding(
            category=fd.get("category", ""),
            title=fd.get("title", ""),
            severity=severity_from(fd.get("severity", "info")),
            owasp=fd.get("owasp", []),
            evidence=fd.get("evidence", ""),
            source=fd.get("source", ""),
            confidence=fd.get("confidence", "confirmed"),
            component=fd.get("component"),
            explanation=fd.get("explanation"),
            mitigation=fd.get("mitigation"),
            references=fd.get("references", []),
        ))
    return generator.render_html(run, dark=dark)


if __name__ == "__main__":
    raise SystemExit(run())
