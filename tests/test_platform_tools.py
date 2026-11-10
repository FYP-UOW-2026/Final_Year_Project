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


def test_a_failed_download_says_so(home):
    with pytest.raises(RuntimeError, match="Could not download ADB"):
        platform_tools.download(url=(home / "missing.zip").as_uri())
