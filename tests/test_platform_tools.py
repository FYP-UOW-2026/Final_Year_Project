"""Downloading platform-tools and finding the downloaded adb. Offline: a local zip stands in."""

import sys
import zipfile

import pytest

from bioaudit import adb, platform_tools

ADB = "adb.exe" if sys.platform == "win32" else "adb"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point the install folder at a temp dir, and hide any adb this machine really has."""
    monkeypatch.setattr(platform_tools, "install_dir", lambda: tmp_path / "BioAudit" / "platform-tools")
    monkeypatch.setattr(platform_tools, "_earlier_install_dir", lambda: tmp_path / "old" / "platform-tools")
    monkeypatch.setattr(adb.shutil, "which", lambda _name: None)
    real_isfile = adb.os.path.isfile
    sdk_names = ("Android", "android-sdk", "Sdk", "sdk")
    monkeypatch.setattr(adb.os.path, "isfile",
                        lambda p: real_isfile(p) and not any(n in str(p) for n in sdk_names))
    return tmp_path


def _zip(tmp_path, names):
    path = tmp_path / "platform-tools.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, "binary")
    return path.as_uri()


def test_download_unpacks_adb_where_resolve_adb_finds_it(home):
    assert not adb.adb_available()
    url = _zip(home, [f"platform-tools/{ADB}", "platform-tools/AdbWinApi.dll"])
    steps = []
    path = platform_tools.download(steps.append, url=url)
    assert path == str(home / "BioAudit" / "platform-tools" / ADB)
    assert adb.resolve_adb() == path
    assert adb.adb_available()
    assert steps[-1] == "Unpacking ADB"


def test_a_second_download_replaces_the_first(home):
    platform_tools.download(url=_zip(home, [f"platform-tools/{ADB}", "platform-tools/old.dll"]))
    platform_tools.download(url=_zip(home, [f"platform-tools/{ADB}", "platform-tools/new.dll"]))
    folder = home / "BioAudit" / "platform-tools"
    assert (folder / "new.dll").exists() and not (folder / "old.dll").exists()


def test_an_adb_already_on_path_is_still_preferred(home, monkeypatch):
    platform_tools.download(url=_zip(home, [f"platform-tools/{ADB}"]))
    monkeypatch.setattr(adb.shutil, "which", lambda _name: "C:/sdk/adb")
    assert adb.resolve_adb() == "adb"


@pytest.mark.parametrize("names, message", [
    (["platform-tools/fastboot"], "did not contain adb"),
    ([f"platform-tools/{ADB}", "../escaped.txt"], "unsafe file path"),
])
def test_a_bad_download_is_refused_and_installs_nothing(home, names, message):
    with pytest.raises(RuntimeError, match=message):
        platform_tools.download(url=_zip(home, names))
    assert platform_tools.installed_adb() is None
    assert not (home / "escaped.txt").exists()


def test_the_download_lands_in_the_bioaudit_home_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_tools.Path, "home", lambda: tmp_path)
    assert platform_tools.install_dir() == tmp_path / "BioAudit" / "platform-tools"


def test_a_copy_downloaded_to_the_earlier_folder_is_still_found(home):
    earlier = home / "old" / "platform-tools"
    earlier.mkdir(parents=True)
    (earlier / ADB).write_text("binary")
    assert platform_tools.installed_adb() == str(earlier / ADB)
    assert adb.resolve_adb() == str(earlier / ADB)


def _scripted_adb(monkeypatch, *outputs):
    """An Adb whose `run` answers each call with the next (stdout, stderr)."""
    replies = list(outputs)
    monkeypatch.setattr(adb.Adb, "run",
                        lambda self, *args, **kw: adb.AdbResult(0, *replies.pop(0)))
    monkeypatch.setattr(adb.time, "sleep", lambda _s: None)
    return adb.Adb("adb")


def test_a_phone_waiting_for_allow_is_reported_not_hidden(monkeypatch):
    phone = _scripted_adb(monkeypatch, ("List of devices attached\n1b541277\tunauthorized\n\n", ""))
    assert phone.device_states() == {"1b541277": "unauthorized"}


def test_only_ready_phones_count_as_devices(monkeypatch):
    phone = _scripted_adb(monkeypatch, (
        "List of devices attached\nAAA\tdevice\nBBB\tunauthorized\nCCC\toffline\n", ""))
    assert phone.devices() == ["AAA"]


def test_a_just_started_adb_is_asked_again(monkeypatch):
    phone = _scripted_adb(
        monkeypatch,
        ("List of devices attached\n\n",
         "* daemon not running; starting now at tcp:5037\n* daemon started successfully\n"),
        ("List of devices attached\n1b541277\tdevice\n", ""),
    )
    assert phone.devices() == ["1b541277"]


def test_a_failed_download_says_so(home):
    with pytest.raises(RuntimeError, match="Could not download ADB"):
        platform_tools.download(url=(home / "missing.zip").as_uri())
