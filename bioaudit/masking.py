"""Hide device serials wherever they are *displayed*.

A hardware serial identifies one physical phone, so it should not end up in a screen
recording, a screenshot or a shared CI log. This only affects what a person sees: the
real serial is still what gets passed to `adb -s`, so nothing about device selection
changes. Emulator serials carry nothing personal and are left readable.

Controlled by `privacy.mask_serial` in config.yaml (default on).
"""

from __future__ import annotations

from typing import Iterable, Optional

# Module-level on purpose: adb.py builds error strings with no config in reach, and
# threading a flag through every call site would be noise. Config.load sets it.
ENABLED = True


def set_enabled(value: bool) -> None:
    global ENABLED
    ENABLED = bool(value)


def mask_serial(serial: Optional[str], enabled: Optional[bool] = None) -> str:
    """`R3CY10CJBCZ` -> `R3…CZ`. Emulators and empty values pass through."""
    if not serial:
        return ""
    if not (ENABLED if enabled is None else enabled):
        return serial
    if serial.startswith("emulator-"):
        return serial
    if len(serial) <= 4:
        return "…"
    return f"{serial[:2]}…{serial[-2:]}"


def mask_text(text: str, serials: Iterable[str], enabled: Optional[bool] = None) -> str:
    """Replace every known serial inside free text (error messages, logs)."""
    for s in sorted({s for s in serials if s}, key=len, reverse=True):
        text = text.replace(s, mask_serial(s, enabled))
    return text
