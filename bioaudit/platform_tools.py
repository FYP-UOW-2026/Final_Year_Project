"""Google's Android platform-tools, downloaded on request so phone testing needs no setup.

Assessing a phone needs `adb`. Rather than asking every user to install it and edit their
PATH, the app can fetch Google's own platform-tools into its data folder, and
`adb.resolve_adb` finds that copy. It is downloaded from Google rather than shipped
inside BioAudit because Google's prebuilt adb comes under the Android SDK terms, which
restrict redistributing it: each user takes their own copy from Google, under those terms.

An adb the machine already has (on PATH or in an Android SDK) is still found first, so
nothing changes for anyone who set it up themselves.

Kept free of any GUI import, so the CLI could use it too.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

_PLATFORM = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
DOWNLOAD_URL = (
    f"https://dl.google.com/android/repository/platform-tools-latest-{_PLATFORM}.zip"
)
TERMS_URL = "https://developer.android.com/studio/terms"
_ADB_NAME = "adb.exe" if sys.platform == "win32" else "adb"
# The archive is about 7 MB; anything far larger is not what was asked for.
_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024


def install_dir() -> Path:
    """Where the downloaded platform-tools live: the BioAudit folder in the user's home.

    The same folder as the saved session and reports. Not AppData: the Microsoft Store
    build of Python quietly redirects writes there into its own package folder, which put
    adb somewhere nobody would think to look.
    """
    return Path.home() / "BioAudit" / "platform-tools"


def _earlier_install_dir() -> Path:
    """Where downloads went before, so a copy fetched then is still found."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "BioAudit" / "platform-tools"


def installed_adb() -> Optional[str]:
    """The downloaded adb, if there is one."""
    for folder in (install_dir(), _earlier_install_dir()):
        path = folder / _ADB_NAME
        if path.is_file():
            return str(path)
    return None


def download(on_progress: Optional[Callable[[str], None]] = None,
             url: str = DOWNLOAD_URL) -> str:
    """Fetch and unpack platform-tools, replacing any earlier copy. Returns adb's path.

    Raises RuntimeError with a message fit to show the user when it cannot.
    """
    report = on_progress or (lambda _message: None)
    target = install_dir()
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=target.parent, prefix="platform-tools-") as work:
        archive = Path(work) / "platform-tools.zip"
        try:
            with urllib.request.urlopen(url, timeout=60) as response, open(archive, "wb") as out:
                total = int(response.headers.get("Content-Length") or 0)
                received = 0
                while chunk := response.read(256 * 1024):
                    received += len(chunk)
                    if received > _MAX_DOWNLOAD_BYTES:
                        raise RuntimeError("The download was far larger than expected.")
                    out.write(chunk)
                    of_total = f" of {total / 1e6:.1f}" if total else ""
                    report(f"Downloading ADB ({received / 1e6:.1f}{of_total} MB)")
        except OSError as exc:
            raise RuntimeError(f"Could not download ADB from Google: {exc}") from exc

        report("Unpacking ADB")
        unpacked = Path(work) / "unpacked"
        try:
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
                if f"platform-tools/{_ADB_NAME}" not in names:
                    raise RuntimeError("The download did not contain adb.")
                root = unpacked.resolve()
                for name in names:
                    # Refuse anything that would land outside the folder (a "zip slip").
                    if not (unpacked / name).resolve().is_relative_to(root):
                        raise RuntimeError("The download contained an unsafe file path.")
                zf.extractall(unpacked)
        except zipfile.BadZipFile as exc:
            raise RuntimeError("The download was not a valid zip file.") from exc

        adb = unpacked / "platform-tools" / _ADB_NAME
        if sys.platform != "win32":
            adb.chmod(adb.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(unpacked / "platform-tools"), str(target))

    return str(target / _ADB_NAME)
