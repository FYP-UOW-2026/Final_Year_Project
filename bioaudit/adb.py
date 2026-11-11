"""Thin wrapper around the `adb` CLI. No root is assumed anywhere.

Every runtime observation the tool makes goes through this class: shell commands,
component probing, logcat capture, screenshots, and backups.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from .masking import mask_serial


class AdbError(RuntimeError):
    pass


def resolve_adb(adb_path: str = "adb") -> str:
    """Locate the adb binary.

    Honour an explicit path from config. Otherwise prefer adb on PATH, then fall
    back to the standard Android SDK locations — a machine can have the SDK's adb
    without it being on PATH (or with a stale PATH entry pointing at a moved
    platform-tools folder)."""
    if adb_path and adb_path != "adb":
        return adb_path  # explicitly configured; respect it
    if shutil.which("adb"):
        return "adb"
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
        os.path.expandvars(r"%USERPROFILE%\AppData\Local\Android\Sdk\platform-tools\adb.exe"),
        os.path.expandvars(r"%ANDROID_HOME%\platform-tools\adb.exe"),
        os.path.expandvars(r"%ANDROID_SDK_ROOT%\platform-tools\adb.exe"),
        os.path.expandvars(r"%ProgramFiles%\Android\android-sdk\platform-tools\adb.exe"),
        os.path.expanduser("~/Android/Sdk/platform-tools/adb"),          # Linux/macOS
        os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),  # macOS
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    # Last, the copy BioAudit downloads for people with no adb of their own.
    from .platform_tools import installed_adb
    return installed_adb() or "adb"  # else give up; "adb binary not found" will follow


def adb_available(adb_path: str = "adb") -> bool:
    """Whether any adb can be found, so a caller can offer to download one if not."""
    return resolve_adb(adb_path) != "adb" or shutil.which("adb") is not None


def _parse_devices(output: str) -> dict[str, str]:
    """`adb devices` output -> {serial: state}, skipping its header and server messages."""
    states = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.startswith(("*", "List of devices")):
            states[parts[0]] = parts[1]
    return states


@dataclass
class AdbResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Adb:
    def __init__(self, adb_path: str = "adb", serial: Optional[str] = None):
        self.adb_path = resolve_adb(adb_path)
        self.serial = serial

    def _base(self) -> list[str]:
        cmd = [self.adb_path]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    def run(self, *args: str, timeout: int = 60) -> AdbResult:
        kwargs = dict(
            capture_output=True,
            text=True,
            timeout=timeout,
            # A windowed (no-console) frozen build has no valid stdin/console;
            # redirect stdin and suppress console-window flashes, and decode
            # defensively since logcat can carry non-UTF-8 bytes.
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
        )
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(self._base() + list(args), **kwargs)
        except FileNotFoundError as exc:
            raise AdbError(f"adb binary not found at '{self.adb_path}'") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"adb command timed out: {' '.join(args)}") from exc
        # capture_output should yield strings, but guard against None so callers
        # can always .splitlines() safely.
        return AdbResult(proc.returncode, proc.stdout or "", proc.stderr or "")

    def shell(self, *args: str, timeout: int = 60) -> AdbResult:
        return self.run("shell", *args, timeout=timeout)

    # --- device discovery -------------------------------------------------

    def device_states(self) -> dict[str, str]:
        """Every phone adb can see, by serial, with its state.

        "device" means ready. "unauthorized" means the phone has not yet allowed this
        computer: its "Allow USB debugging?" prompt is waiting, which is what a new adb
        (such as a fresh download) always meets first. "offline" means the connection
        dropped. The first call after adb's server starts can come back empty, because the
        server has not finished looking for phones, so that case is asked once more.
        """
        res = self.run("devices")
        states = _parse_devices(res.stdout)
        if not states and "daemon started" in res.stdout + res.stderr:
            time.sleep(2)
            states = _parse_devices(self.run("devices").stdout)
        return states

    def devices(self) -> list[str]:
        """Serials of the phones that are ready to use."""
        return [serial for serial, state in self.device_states().items() if state == "device"]

    def require_device(self) -> str:
        """Return the target serial, erroring if the selection is ambiguous."""
        serials = self.devices()
        if not serials:
            raise AdbError("no authorized device connected (check USB debugging)")
        if self.serial:
            if self.serial not in serials:
                raise AdbError(f"configured serial '{mask_serial(self.serial)}' not connected")
            return self.serial
        if len(serials) > 1:
            raise AdbError("multiple devices connected; set device.serial: "
                           f"{[mask_serial(s) for s in serials]}")
        self.serial = serials[0]
        return self.serial

    # --- app inspection ---------------------------------------------------

    def is_installed(self, package: str) -> bool:
        res = self.shell("pm", "list", "packages", package)
        return any(line.strip() == f"package:{package}" for line in res.stdout.splitlines())

    def list_packages(self, third_party_only: bool = True) -> list[str]:
        """Return installed package names, sorted. Third-party (user-installed)
        apps by default; pass third_party_only=False to include system apps."""
        args = ["pm", "list", "packages"]
        if third_party_only:
            args.append("-3")
        res = self.shell(*args)
        pkgs = [
            line.strip()[len("package:"):]
            for line in res.stdout.splitlines()
            if line.strip().startswith("package:")
        ]
        return sorted(pkgs)

    def dump_manifest(self, package: str) -> str:
        """Best-effort manifest dump for an installed app (no root)."""
        return self.shell("dumpsys", "package", package).stdout

    def pull_base_apk(self, package: str, dest_dir: str) -> Optional[str]:
        """Pull an installed app's base APK off the device (no root required).

        `pm path` lists the on-device APK path(s); a user-installed app's base.apk
        under /data/app is world-readable, so `adb pull` fetches it without root.
        This is what lets `assess` build a real exported-component list (and read the
        DEX) from just a package name, with no --apk supplied.

        Returns the local path to the pulled APK, or None if the path could not be
        found or the pull failed (e.g. a system app with a protected path).
        """
        res = self.shell("pm", "path", package)
        remote = None
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line.startswith("package:"):
                continue
            path = line[len("package:"):].strip()
            if path.endswith("base.apk"):
                remote = path
                break
            if remote is None:
                remote = path  # fall back to the first split if none is named base.apk
        if not remote:
            return None
        local = os.path.join(dest_dir, "base.apk")
        pulled = self.run("pull", remote, local)
        if not pulled.ok or not os.path.isfile(local):
            return None
        return local

    # --- device/app scope info --------------------------------------------

    def device_model(self) -> str:
        return self.shell("getprop", "ro.product.model").stdout.strip()

    def android_version(self) -> str:
        return self.shell("getprop", "ro.build.version.release").stdout.strip()

    def app_version_name(self, package: str) -> Optional[str]:
        """Best-effort versionName from the manifest dump (no root)."""
        match = re.search(r"versionName=(\S+)", self.dump_manifest(package))
        return match.group(1) if match else None
