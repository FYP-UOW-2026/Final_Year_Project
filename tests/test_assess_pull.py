"""The device assessment must work from a package name alone.

Without --apk, build_assess should pull the installed base APK off the device and
feed its real exported-component list to the IPC and response oracles, so the
headline runtime checks fire -- rather than probing a blank manifest and finding
nothing. A fake Adb scripts a VulnDemo-like device so the whole path runs with no
hardware.
"""

from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("androguard", reason="pulled-APK parsing needs androguard")

from bioaudit import core
from bioaudit.adb import AdbResult
from bioaudit.config import Config

_VULNDEMO = pathlib.Path(__file__).resolve().parents[1] / "sample-app" / "dist" / "vulndemo.apk"


class FakeDevice:
    """Scripts just enough adb for build_assess to run against a VulnDemo-like app.

    pull_base_apk hands back the real forged APK, so the manifest the oracles read
    is the genuine one. The shell probes return the observable results a vulnerable
    device would: the exported activity launches, the provider answers a valid id
    differently from an invalid one, and a token sits in logcat.
    """

    PKG = "com.bioaudit.vulndemo"

    def __init__(self, apk_path: str):
        self._apk = apk_path
        self.serial = "fake-serial"

    def require_device(self) -> str:
        return self.serial

    def is_installed(self, package: str) -> bool:
        return package == self.PKG

    def pull_base_apk(self, package: str, dest_dir: str):
        return self._apk if package == self.PKG else None

    def shell(self, *args: str, timeout: int = 60) -> AdbResult:
        a = list(args)
        if a[:2] == ["am", "start"]:
            # Exported activity opens without authentication.
            return AdbResult(0, "Starting: Intent { ... }", "")
        if a[:1] == ["content"]:
            uri = a[a.index("--uri") + 1]
            seg = uri.rsplit("/", 1)[-1]
            if seg == "admin":
                return AdbResult(0, "Row: 0 status=valid", "")
            return AdbResult(0, "No result found.", "")
        if a[:1] == ["logcat"]:
            return AdbResult(0, "I/BioAuthDemo: auth session token=SDF9sd8f7sdKJHkjh"
                                "324kjhKJH234ZZaa11bb22cc33==", "")
        return AdbResult(0, "", "")


def test_assess_without_apk_pulls_and_fires_oracles():
    if not _VULNDEMO.is_file():
        pytest.skip("sample-app/dist/vulndemo.apk not built")

    fake = FakeDevice(str(_VULNDEMO))
    run = core.build_assess(fake.PKG, apk=None, cfg=Config(), adb=fake)
    core.process_findings(run)

    categories = {f.category for f in run.findings}

    # The two component-probing detectors, which a blank manifest would have starved:
    assert "exported-auth-bypass" in categories, "IPC oracle should fire on the pulled manifest"
    assert "auth-state-oracle" in categories, "response oracle should fire on the pulled manifest"
    # And the static checks run too, because the pulled APK carries the DEX:
    assert "boolean-only-auth" in categories
    assert "debuggable-release" in categories


def test_assess_without_apk_falls_back_when_pull_fails():
    """If the APK can't be pulled, probing continues against a bare manifest rather
    than erroring -- the old behaviour, preserved as the fallback."""
    fake = FakeDevice(str(_VULNDEMO))
    fake.pull_base_apk = lambda package, dest_dir: None  # simulate a failed pull

    run = core.build_assess(fake.PKG, apk=None, cfg=Config(), adb=fake)
    core.process_findings(run)
    # No crash, and the passive logcat observer still fires without any manifest.
    assert "logcat-leak" in {f.category for f in run.findings}
