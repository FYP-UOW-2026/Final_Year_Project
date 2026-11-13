"""The History tab's "Scan History" table.

Two halves. The top of the file is pure Python (no Qt) so it can be unit-tested without a
display: severity, relative time, serial masking, filtering, sorting and paging of the scan
list that `GET /scans` returns. `make_history_page` at the bottom builds the widgets and
imports PySide6 only when called, like the rest of the GUI.

The server returns at most 100 scans without their findings, so searching, filtering,
sorting and paging all happen here, on that list.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from .formatting import format_when

SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")
PAGE_SIZE = 10

DATE_RANGES = (("All time", None), ("Last 7 days", 7), ("Last 30 days", 30),
               ("Last 90 days", 90))
SEVERITY_FILTERS = (("All Severities", None), ("Critical", "critical"), ("High", "high"),
                    ("Medium", "medium"), ("Low", "low"), ("Info", "info"))


# ----------------------------------------------------------------------------- pure --

def worst_severity(counts: Optional[dict]) -> str:
    """The highest severity with a non-zero count, or 'clean' when there is none."""
    counts = counts or {}
    for name in SEVERITY_ORDER:
        if counts.get(name):
            return name
    return "clean"


def scan_epoch(value) -> Optional[float]:
    """Seconds since the epoch for a server timestamp ({_seconds} or ISO string)."""
    import datetime

    if isinstance(value, dict):
        seconds = value.get("_seconds", value.get("seconds"))
        return float(seconds) if seconds is not None else None
    if isinstance(value, str) and value:
        try:
            moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            return time.mktime(moment.timetuple())
        return moment.timestamp()
    return None


def relative_when(value, now: Optional[float] = None) -> str:
    """'just now', '5 minutes ago', '2 hours ago', '3 days ago'; '' if unreadable."""
    then = scan_epoch(value)
    if then is None:
        return ""
    delta = max(0, int((time.time() if now is None else now) - then))
    for seconds, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if delta >= seconds:
            n = delta // seconds
            return f"{n} {unit}{'' if n == 1 else 's'} ago"
    return "just now"


def serial_tail(serial: Optional[str]) -> str:
    """'R3CY10CJ3F2A' -> '•••• 3F2A'. Emulator names carry nothing personal."""
    if not serial:
        return ""
    if serial.startswith("emulator-"):
        return serial
    return f"•••• {serial[-4:].upper()}"


def package_of(scan: dict) -> str:
    target = scan.get("target") or {}
    return target.get("packageName") or target.get("apkFileName") or "unknown"


def filter_scans(scans: list, text: str = "", severity: Optional[str] = None,
                 days: Optional[int] = None, now: Optional[float] = None) -> list:
    """Scans matching the search text, worst severity and age. Order is kept."""
    needle = (text or "").strip().lower()
    cutoff = None
    if days:
        cutoff = (time.time() if now is None else now) - days * 86400
    out = []
    for scan in scans:
        if severity and worst_severity(scan.get("counts")) != severity:
            continue
        if cutoff is not None:
            when = scan_epoch(scan.get("createdAt"))
            if when is None or when < cutoff:
                continue
        if needle:
            target = scan.get("target") or {}
            haystack = " ".join(str(x) for x in (
                package_of(scan), target.get("appVersion"), target.get("deviceModel"),
                target.get("deviceSerial"), format_when(scan.get("createdAt")),
                worst_severity(scan.get("counts"))) if x).lower()
            if needle not in haystack:
                continue
        out.append(scan)
    return out


def sort_scans(scans: list, key: str = "date", descending: bool = True) -> list:
    """Sorted copy. key: 'date', 'package', 'device' or 'severity'."""
    def severity_rank(scan):
        name = worst_severity(scan.get("counts"))
        return -1 if name == "clean" else len(SEVERITY_ORDER) - SEVERITY_ORDER.index(name)

    keys = {
        "date": lambda s: scan_epoch(s.get("createdAt")) or 0.0,
        "package": lambda s: package_of(s).lower(),
        "device": lambda s: str((s.get("target") or {}).get("deviceModel") or "").lower(),
        "severity": severity_rank,
    }
    return sorted(scans, key=keys.get(key, keys["date"]), reverse=descending)


def page_of(items: list, page: int, size: int = PAGE_SIZE):
    """(items on the page, clamped page number, total pages). Pages count from 0."""
    pages = max(1, -(-len(items) // size))
    page = min(max(0, page), pages - 1)
    return items[page * size:(page + 1) * size], page, pages


# ----------------------------------------------------------------------------- Qt --

def _refresh_icon(colour: str, size: int):
    """Circular-arrow reload icon painted with QPainter (no font dependency)."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

    scale = 4  # draw big, let Qt downscale: smooth edges on hi-dpi
    px = QPixmap(size * scale, size * scale)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(colour)
    pen = QPen(c, size * scale * 0.1)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    m = size * scale * 0.2
    rect = (m, m, size * scale - 2 * m, size * scale - 2 * m)
    # 300 degree arc starting at 1 o'clock, gap at top-right for the arrowhead.
    p.drawArc(int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]), 60 * 16, 300 * 16)
    # Arrowhead at the arc's start (top-right), pointing clockwise.
    import math
    cx = cy = size * scale / 2
    r = rect[2] / 2
    ang = math.radians(60)
    tip = QPointF(cx + r * math.cos(ang), cy - r * math.sin(ang))
    k = size * scale * 0.2
    head = QPolygonF([
        QPointF(tip.x() + k * 0.9, tip.y() - k * 0.1),
        QPointF(tip.x() - k * 0.2, tip.y() - k * 0.9),
        QPointF(tip.x() - k * 0.7, tip.y() + k * 0.7),
    ])
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawPolygon(head)
    p.end()
    return QIcon(px)


def toggle_selection(selected: set, scan_id: str, on: bool) -> set:
    """New selection set with scan_id added (on) or removed (off)."""
    out = set(selected)
    if on:
        out.add(scan_id)
    else:
        out.discard(scan_id)
    return out


def prune_selection(selected: set, scans: list) -> set:
    """Drop ids that no longer exist (after a reload or delete)."""
    live = {s.get("id") for s in scans}
    return {i for i in selected if i in live}


def make_history_page(palette: dict, on_open: Callable[[str], None],
                      on_refresh: Callable[[], None], on_clear: Callable[[], None],
                      on_delete_selected: Callable[[list], None] = lambda ids: None):
    """Build the list page. Returns a QWidget with these extra members:

    stack            QStackedWidget: index 0 the table, index 1 a report widget
                     the caller adds with `add_report_widget`
    set_scans(scans, error=None, limit=None)   replace the data and redraw
    show_list() / show_report()
    """
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                                   QPushButton, QStackedWidget, QTableWidget,
                                   QAbstractItemView, QVBoxLayout, QWidget, QFrame,
                                   QCheckBox)

    from . import theme

    muted = palette.get("muted", "#94a3b8")
    text_colour = palette.get("text", "#e2e8f0")

    class HistoryPage(QWidget):
        def __init__(self):
            super().__init__()
            self._scans: list = []
            self._sort_key, self._sort_desc = "date", True
            self._page = 0
            self._opening_id = None
            self._selected: set = set()

            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            self.stack = QStackedWidget()
            outer.addWidget(self.stack)

            list_page = QWidget()
            lay = QVBoxLayout(list_page)
            lay.setContentsMargins(16, 12, 16, 12)
            lay.setSpacing(10)

            title = QLabel("Scan History")
            title.setObjectName("historyTitle")
            sub = QLabel("View your past biometric security scans. Reports are loaded "
                         "from the backend and not stored locally.")
            sub.setWordWrap(True)
            sub.setStyleSheet(f"color: {muted};")
            lay.addWidget(title)
            lay.addWidget(sub)

            bar = QHBoxLayout()
            bar.setSpacing(8)
            self.search = QLineEdit()
            self.search.setPlaceholderText("Search by package or version")
            self.search.setClearButtonEnabled(True)
            self.severity_combo = QComboBox()
            for label, _v in SEVERITY_FILTERS:
                self.severity_combo.addItem(label)
            self.range_combo = QComboBox()
            for label, _v in DATE_RANGES:
                self.range_combo.addItem(label)
            refresh = QPushButton()
            refresh.setToolTip("Reload from the server")
            # Drawn, not a font glyph: looks the same on every OS/font.
            refresh.setIcon(_refresh_icon(theme.DARK["text"], 22))
            refresh.setIconSize(QSize(22, 22))
            refresh.setFixedWidth(44)
            refresh.clicked.connect(lambda: on_refresh())
            bar.addWidget(self.search, 1)
            bar.addWidget(self.severity_combo)
            bar.addWidget(self.range_combo)
            bar.addWidget(refresh)
            lay.addLayout(bar)

            # Selection bar: select-all (current page) on the left, bulk actions once
            # at least one row is ticked.
            sel = QHBoxLayout()
            sel.setSpacing(8)
            self.select_all = QCheckBox("Select all")
            # 1.2x the normal checkbox: label font and the box itself.
            f = self.select_all.font()
            f.setPointSizeF(f.pointSizeF() * 1.2)
            self.select_all.setFont(f)
            self.select_all.setStyleSheet(
                "QCheckBox::indicator { width: 19px; height: 19px; }")
            self.select_all.clicked.connect(self._select_page)
            self.sel_label = QLabel("")
            self.delete_btn = QPushButton("Delete")
            self.delete_btn.setProperty("danger", True)
            self.delete_btn.clicked.connect(self._delete_clicked)
            self.clear_sel_btn = QPushButton("Clear selection")
            self.clear_sel_btn.setProperty("ghost", True)
            self.clear_sel_btn.clicked.connect(self._clear_selection)
            sel.addWidget(self.select_all)
            sel.addWidget(self.sel_label)
            sel.addWidget(self.delete_btn)
            sel.addWidget(self.clear_sel_btn)
            sel.addStretch(1)
            lay.addLayout(sel)

            self.table = QTableWidget(0, 6)
            self.table.setHorizontalHeaderLabels(
                ["", "Date & Time", "Package", "Top Findings", "Severity", ""])
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.setSelectionMode(QAbstractItemView.NoSelection)
            self.table.setShowGrid(False)
            self.table.setFocusPolicy(Qt.NoFocus)
            hh = self.table.horizontalHeader()
            hh.setSectionsClickable(True)
            hh.setStretchLastSection(False)
            for col, mode in enumerate((QHeaderView.ResizeToContents,
                                        QHeaderView.ResizeToContents, QHeaderView.Stretch,
                                        QHeaderView.ResizeToContents,
                                        QHeaderView.ResizeToContents,
                                        QHeaderView.ResizeToContents)):
                hh.setSectionResizeMode(col, mode)
            hh.sectionClicked.connect(self._header_clicked)
            self._row_ids = []
            self.table.viewport().setCursor(Qt.PointingHandCursor)
            self.table.cellClicked.connect(self._cell_clicked)
            lay.addWidget(self.table, 1)

            self.message = QLabel("")
            self.message.setAlignment(Qt.AlignCenter)
            self.message.setTextFormat(Qt.RichText)
            self.message.setWordWrap(True)
            self.message.setVisible(False)
            lay.addWidget(self.message, 1)

            foot = QHBoxLayout()
            foot.setSpacing(8)
            self.count_label = QLabel("")
            self.count_label.setStyleSheet(f"color: {muted};")
            self.clear_btn = QPushButton("Clear all history")
            self.clear_btn.setProperty("ghost", True)
            self.clear_btn.clicked.connect(lambda: on_clear())
            self.prev_btn = QPushButton("‹")
            self.next_btn = QPushButton("›")
            for b in (self.prev_btn, self.next_btn):
                b.setFixedWidth(36)
            self.page_label = QLabel("")
            self.prev_btn.clicked.connect(lambda: self._go(-1))
            self.next_btn.clicked.connect(lambda: self._go(1))
            foot.addWidget(self.count_label)
            foot.addWidget(self.clear_btn)
            foot.addStretch(1)
            foot.addWidget(self.prev_btn)
            foot.addWidget(self.page_label)
            foot.addWidget(self.next_btn)
            lay.addLayout(foot)

            self.stack.addWidget(list_page)

            self.search.textChanged.connect(self._filters_changed)
            self.severity_combo.currentIndexChanged.connect(self._filters_changed)
            self.range_combo.currentIndexChanged.connect(self._filters_changed)

        # -- public --------------------------------------------------------- #

        def add_report_widget(self, widget) -> None:
            self.stack.addWidget(widget)

        def show_list(self) -> None:
            self.stack.setCurrentIndex(0)

        def mark_opening(self, scan_id: Optional[str]) -> None:
            """Highlight the row being opened (None clears). Instant click feedback."""
            self._opening_id = scan_id
            self._paint_opening()

        def _paint_opening(self) -> None:
            fill = palette.get("surface_alt", "#1e293b")
            for r, rid in enumerate(self._row_ids):
                on = rid == self._opening_id
                for c in range(1, 6):
                    w = self.table.cellWidget(r, c)
                    if w is None:
                        continue
                    if isinstance(w, QLabel):
                        w.setStyleSheet(f"background:{fill};" if on else "")
                    else:
                        w.setObjectName("rowHold")
                        w.setStyleSheet(f"QWidget#rowHold{{background:{fill};}}" if on else "")

        def show_report(self) -> None:
            self.stack.setCurrentIndex(1)

        def set_scans(self, scans: list, error: Optional[str] = None,
                      limit: Optional[int] = None) -> None:
            self._scans = list(scans)
            self._selected = prune_selection(self._selected, self._scans)
            self._error, self._limit = error, limit
            self._redraw()

        def clear_selection(self) -> None:
            self._clear_selection()

        # -- selection ------------------------------------------------------ #

        def _refresh_selection_ui(self) -> None:
            n = len(self._selected)
            self.sel_label.setText(f"{n} selected")
            for w in (self.sel_label, self.delete_btn, self.clear_sel_btn):
                w.setVisible(n > 0)
            self.delete_btn.setText(f"Delete {n}")
            on_page = bool(self._row_ids) and all(i in self._selected for i in self._row_ids)
            self.select_all.blockSignals(True)
            self.select_all.setChecked(on_page)
            self.select_all.blockSignals(False)

        def _row_toggled(self, scan_id: str, on: bool) -> None:
            self._selected = toggle_selection(self._selected, scan_id, on)
            self._refresh_selection_ui()

        def _select_page(self, on: bool) -> None:
            for i in self._row_ids:
                self._selected = toggle_selection(self._selected, i, on)
            for r in range(self.table.rowCount()):
                box = self._row_box(r)
                if box is not None:
                    box.blockSignals(True)
                    box.setChecked(on)
                    box.blockSignals(False)
            self._refresh_selection_ui()

        def _clear_selection(self) -> None:
            self._selected = set()
            for r in range(self.table.rowCount()):
                box = self._row_box(r)
                if box is not None:
                    box.blockSignals(True)
                    box.setChecked(False)
                    box.blockSignals(False)
            self._refresh_selection_ui()

        def _row_box(self, row: int):
            holder = self.table.cellWidget(row, 0)
            return holder.findChild(QCheckBox) if holder is not None else None

        def _delete_clicked(self) -> None:
            if self._selected:
                on_delete_selected(sorted(self._selected))

        # -- internals ------------------------------------------------------ #

        def _filters_changed(self, *_a) -> None:
            self._page = 0
            self._redraw()

        def _go(self, step: int) -> None:
            self._page += step
            self._redraw()

        def _header_clicked(self, col: int) -> None:
            key = {1: "date", 2: "package", 4: "severity"}.get(col)
            if key is None:
                return
            if key == self._sort_key:
                self._sort_desc = not self._sort_desc
            else:
                self._sort_key, self._sort_desc = key, key in ("date", "severity")
            self._page = 0
            self._redraw()

        def _empty(self, title: str, lines: list) -> None:
            self.table.setVisible(False)
            self.message.setText(theme.empty_state_html(title, lines, palette))
            self.message.setVisible(True)
            self.count_label.setText("")
            self.page_label.setText("")
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            self.clear_btn.setVisible(False)
            self._row_ids = []
            self.select_all.setVisible(False)
            self._refresh_selection_ui()

        def _redraw(self) -> None:
            if self._error:
                return self._empty("Could not load your history",
                                   [self._error, "Click the reload button to try again."])
            if not self._scans:
                lines = ["Run a scan or an assessment first.",
                         "Every finished run is saved to your account automatically."]
                if self._limit is not None:
                    lines.append(f"A student account keeps the newest {self._limit} runs.")
                return self._empty("No runs yet", lines)

            sev = SEVERITY_FILTERS[self.severity_combo.currentIndex()][1]
            days = DATE_RANGES[self.range_combo.currentIndex()][1]
            matches = filter_scans(self._scans, self.search.text(), sev, days)
            if not matches:
                self._empty("No matching scans", ["Change the search or the filters."])
                self.clear_btn.setVisible(True)
                self.count_label.setText(f"Showing 0 of {len(self._scans)} scans")
                return

            self.message.setVisible(False)
            self.table.setVisible(True)
            self.select_all.setVisible(True)
            self.clear_btn.setVisible(True)
            ordered = sort_scans(matches, self._sort_key, self._sort_desc)
            rows, self._page, pages = page_of(ordered, self._page)
            self._fill(rows)
            first = self._page * PAGE_SIZE + 1
            self.count_label.setText(
                f"Showing {first}–{first + len(rows) - 1} of {len(matches)} scans"
                + (f" (filtered from {len(self._scans)})" if len(matches) != len(self._scans)
                   else ""))
            self.page_label.setText(f"{self._page + 1} / {pages}")
            self.prev_btn.setEnabled(self._page > 0)
            self.next_btn.setEnabled(self._page < pages - 1)

        def _cell_clicked(self, row: int, col: int) -> None:
            # Column 0 is the checkbox and column 5 the View Report button; both act alone.
            if col not in (0, 5) and 0 <= row < len(self._row_ids):
                on_open(self._row_ids[row])

        def _cell(self, row: int, col: int, html: str) -> None:
            label = QLabel(html)
            label.setTextFormat(Qt.RichText)
            label.setContentsMargins(8, 4, 8, 4)
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.table.setCellWidget(row, col, label)

        def _fill(self, rows: list) -> None:
            self.table.setRowCount(len(rows))
            self._row_ids = [scan["id"] for scan in rows]
            for r, scan in enumerate(rows):
                self.table.setRowHeight(r, 60)
                target = scan.get("target") or {}
                counts = scan.get("counts") or {}
                when = scan.get("createdAt")
                box = QCheckBox()
                box.setChecked(scan["id"] in self._selected)
                box.toggled.connect(lambda on, i=scan["id"]: self._row_toggled(i, on))
                cb_holder = QWidget()
                cbl = QHBoxLayout(cb_holder)
                cbl.setContentsMargins(12, 0, 4, 0)
                cbl.addWidget(box)
                self.table.setCellWidget(r, 0, cb_holder)

                self._cell(r, 1, f"<div style='color:{text_colour}'>{_esc(format_when(when))}"
                                 f"</div><div style='color:{muted}'>{_esc(relative_when(when))}</div>")
                self._cell(r, 2, f"<div style='color:{text_colour}'><b>{_esc(package_of(scan))}"
                                 f"</b></div><div style='color:{muted}'>"
                                 f"{_esc(target.get('appVersion') or '-')}</div>")
                self._cell(r, 3, _findings_html(counts, muted))

                name = worst_severity(counts)
                badge = QLabel(name.capitalize())
                badge.setAlignment(Qt.AlignCenter)
                colour = theme.SEVERITY_COLOURS.get(name, "#166534")
                full = badge.sizeHint().height() + 4  # height with the old 2px padding
                height = round(full * 1.2)
                badge.setStyleSheet(
                    f"background:{colour}; color:#fff; border-radius:{height // 2}px; "
                    "padding:0 12px; font-weight:600; font-size:9pt;")
                badge.setFixedHeight(height)
                holder = QWidget()
                hl = QHBoxLayout(holder)
                hl.setContentsMargins(8, 0, 8, 0)
                hl.addWidget(badge)
                holder.setAttribute(Qt.WA_TransparentForMouseEvents)
                self.table.setCellWidget(r, 4, holder)

                btn = QPushButton("View Report")
                btn.setProperty("ghost", True)
                btn.clicked.connect(lambda _c=False, i=scan["id"]: on_open(i))
                holder2 = QWidget()
                hl2 = QHBoxLayout(holder2)
                hl2.setContentsMargins(8, 0, 8, 0)
                hl2.addWidget(btn)
                self.table.setCellWidget(r, 5, holder2)
            self._paint_opening()
            self._refresh_selection_ui()

    return HistoryPage()


def _esc(value) -> str:
    import html
    return html.escape(str(value))


def _findings_html(counts: dict, muted: str) -> str:
    """Coloured dot + count + name, two per line (2x2 for four severities)."""
    from . import theme

    cells = []
    for name in SEVERITY_ORDER:
        n = counts.get(name) or 0
        if n:
            cells.append(f"<span style='color:{theme.SEVERITY_COLOURS[name]}'>●</span> "
                         f"<span style='color:{muted}'>{n} {name.capitalize()}</span>")
    if not cells:
        return f"<span style='color:{muted}'>No findings</span>"
    rows = ["&nbsp;&nbsp;".join(cells[i:i + 2]) for i in range(0, len(cells), 2)]
    return "<br>".join(rows)
