import pytest

from bioaudit import masking
from bioaudit.adb import Adb, AdbError
from bioaudit.masking import mask_serial, mask_text


@pytest.fixture(autouse=True)
def _reset():
    masking.set_enabled(True)
    yield
    masking.set_enabled(True)


def test_hardware_serial_is_masked():
    assert mask_serial("R3CY10CJBCZ") == "R3…CZ"


def test_emulator_and_empty_pass_through():
    assert mask_serial("emulator-5554") == "emulator-5554"
    assert mask_serial(None) == ""


def test_short_serial_does_not_leak():
    assert mask_serial("abcd") == "…"


def test_disabled_shows_real_serial():
    masking.set_enabled(False)
    assert mask_serial("R3CY10CJBCZ") == "R3CY10CJBCZ"


def test_mask_text_replaces_inside_messages():
    out = mask_text("not installed on R3CY10CJBCZ", ["R3CY10CJBCZ"])
    assert "R3CY10CJBCZ" not in out and "R3…CZ" in out


def test_adb_errors_do_not_leak_serial(monkeypatch):
    adb = Adb(serial="R3CY10CJBCZ")
    monkeypatch.setattr(adb, "devices", lambda: ["OTHER123456"])
    with pytest.raises(AdbError) as e:
        adb.require_device()
    assert "R3CY10CJBCZ" not in str(e.value)
    adb2 = Adb()
    monkeypatch.setattr(adb2, "devices", lambda: ["AAAA1111", "BBBB2222"])
    with pytest.raises(AdbError) as e:
        adb2.require_device()
    assert "AAAA1111" not in str(e.value)
