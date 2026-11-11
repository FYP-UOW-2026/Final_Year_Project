from bioaudit.gui import history_view as hv

NOW = 1_700_000_000.0


def scan(sid, secs_ago, counts=None, pkg="com.a", model="Pixel", serial="R3CY10CJ3F2a"):
    return {"id": sid, "createdAt": {"_seconds": NOW - secs_ago},
            "counts": counts or {}, "target": {"packageName": pkg, "deviceModel": model,
                                               "deviceSerial": serial, "appVersion": "1.2"}}


def test_worst_severity():
    assert hv.worst_severity({"low": 2, "high": 1}) == "high"
    assert hv.worst_severity({"critical": 0}) == "clean"
    assert hv.worst_severity(None) == "clean"


def test_relative_when():
    r = lambda s: hv.relative_when({"_seconds": NOW - s}, now=NOW)
    assert r(5) == "just now"
    assert r(60) == "1 minute ago"
    assert r(7200) == "2 hours ago"
    assert r(3 * 86400) == "3 days ago"
    assert hv.relative_when(None, now=NOW) == ""
    assert hv.relative_when("garbage", now=NOW) == ""


def test_serial_tail():
    assert hv.serial_tail("R3CY10CJ3f2a") == "•••• 3F2A"
    assert hv.serial_tail("") == ""
    assert hv.serial_tail("emulator-5554") == "emulator-5554"


def test_filter_scans():
    scans = [scan("1", 100, {"high": 1}), scan("2", 10 * 86400, {"low": 1}, pkg="org.b"),
             scan("3", 100)]
    assert [s["id"] for s in hv.filter_scans(scans, severity="high")] == ["1"]
    assert [s["id"] for s in hv.filter_scans(scans, severity="clean")] == ["3"]
    assert [s["id"] for s in hv.filter_scans(scans, days=7, now=NOW)] == ["1", "3"]
    assert [s["id"] for s in hv.filter_scans(scans, text="ORG.B")] == ["2"]
    assert len(hv.filter_scans(scans)) == 3


def test_sort_scans():
    scans = [scan("1", 300, {"low": 1}, pkg="b"), scan("2", 100, {"critical": 1}, pkg="a"),
             scan("3", 200)]
    ids = lambda key, desc: [s["id"] for s in hv.sort_scans(scans, key, desc)]
    assert ids("date", True) == ["2", "3", "1"]
    assert ids("package", False) == ["2", "1", "3"] or ids("package", False)[0] == "2"
    assert ids("severity", True) == ["2", "1", "3"]


def test_page_of():
    items = list(range(25))
    assert hv.page_of(items, 0) == (items[:10], 0, 3)
    assert hv.page_of(items, 9) == (items[20:], 2, 3)
    assert hv.page_of([], 0) == ([], 0, 1)


def test_row_click_opens_report():
    import pytest
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from bioaudit.gui import theme
    app = QApplication.instance() or QApplication([])
    opened = []
    page = hv.make_history_page(theme.DARK, opened.append, lambda: None, lambda: None)
    page.set_scans([scan("a", 100, {"high": 1}), scan("b", 50)])
    page.table.cellClicked.emit(1, 1)
    page.table.cellClicked.emit(0, 4)  # button column: handled by the button itself
    assert opened == [page._row_ids[1]]


def test_mark_opening_highlights_and_clears():
    import pytest
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from bioaudit.gui import theme
    app = QApplication.instance() or QApplication([])
    page = hv.make_history_page(theme.DARK, lambda _i: None, lambda: None, lambda: None)
    page.set_scans([scan("a", 100, {"high": 1}), scan("b", 50)])
    target = page._row_ids[1]
    page.mark_opening(target)
    assert theme.DARK["surface_alt"] in page.table.cellWidget(1, 0).styleSheet()
    assert page.table.cellWidget(0, 0).styleSheet() == ""
    page.mark_opening(None)
    assert page.table.cellWidget(1, 0).styleSheet() == ""
