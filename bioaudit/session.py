"""Saved-session persistence, shared by the CLI and the desktop GUI.

Kept free of any GUI toolkit import so the CLI can sign in without pulling in PySide6,
and so the GUI's sign-in dialog (`gui/signin.py`) can reuse the same file format.

An account is now required everywhere: BioAudit has no local storage and no guest mode,
so a saved session is what lets a second launch skip typing a password again, not an
optional convenience layered on top of an offline mode.

On storing the session: signing in returns a refresh token, which is long-lived and can
mint new access tokens. Writing it to disk is therefore a real trade-off, so it is opt-in
through a checkbox that defaults to off. When the user does opt in, the file goes in their
home directory with permissions narrowed as far as the platform allows.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .api import ApiClient, ApiClientError, Session

SESSION_FILENAME = "session.json"
REFRESH_TOKEN_ENV = "BIOAUDIT_REFRESH_TOKEN"


@dataclass(frozen=True)
class SavedAccount:
    email: str
    base_url: str
    active: bool


def session_path(base_dir: str | os.PathLike) -> Path:
    return Path(base_dir) / SESSION_FILENAME


def _read(base_dir: str | os.PathLike) -> dict:
    """Load the saved-accounts file as `{"active": email|None, "accounts": {...}}`.

    Files written before multi-account support held one session at the top level; they
    are read as a single saved account so nobody is signed out by upgrading.
    """
    empty = {"active": None, "accounts": {}}
    path = session_path(base_dir)
    if not path.exists():
        return empty
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return empty
    if not isinstance(raw, dict):
        return empty

    if "accounts" in raw and isinstance(raw["accounts"], dict):
        accounts = {e: v for e, v in raw["accounts"].items()
                    if isinstance(v, dict) and isinstance(v.get("session"), dict)}
        active = raw.get("active")
        return {"active": active if active in accounts else None, "accounts": accounts}

    if isinstance(raw.get("session"), dict):
        email = raw.get("email") or "unknown"
        return {"active": email,
                "accounts": {email: {"base_url": raw.get("base_url"),
                                     "session": raw["session"]}}}
    return empty


def _write(base_dir: str | os.PathLike, data: dict) -> Optional[Path]:
    path = session_path(base_dir)
    try:
        if not data["accounts"]:
            path.unlink(missing_ok=True)
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        # Narrow to owner-only. This is the main protection on POSIX; on Windows the
        # call is accepted but the real control is the user's own profile directory.
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return path
    except OSError:
        return None


def save_session(base_dir: str | os.PathLike, client: ApiClient) -> Optional[Path]:
    """Remember this account and make it the active one.

    Other saved accounts are kept, which is what makes switching possible without a
    password. Returns the path written, or None if it could not be saved. A failure here
    is not worth interrupting the user for: they are already signed in for this run.
    """
    if client.session is None:
        return None

    email = client.account.email if client.account else "unknown"
    data = _read(base_dir)
    data["accounts"][email] = {
        "base_url": client.base_url,
        "session": client.session.to_dict(),
    }
    data["active"] = email
    return _write(base_dir, data)


def forget(base_dir: str | os.PathLike, email: str) -> None:
    """Drop one saved account. If it was the active one, nothing is active afterwards."""
    data = _read(base_dir)
    data["accounts"].pop(email, None)
    if data["active"] == email:
        data["active"] = None
    _write(base_dir, data)


def clear_session(base_dir: str | os.PathLike) -> None:
    """Forget the active account only; other saved accounts stay available to switch to."""
    active = _read(base_dir)["active"]
    if active is not None:
        forget(base_dir, active)


def list_accounts(base_dir: str | os.PathLike) -> list[SavedAccount]:
    data = _read(base_dir)
    return [SavedAccount(email=email, base_url=entry.get("base_url") or "",
                         active=(email == data["active"]))
            for email, entry in sorted(data["accounts"].items())]


def _restore(base_dir, email: str, default_base_url: str) -> Optional[ApiClient]:
    """Rebuild a signed-in client for one saved account, or drop the entry and return None.

    The stored access token has almost certainly expired, so this refreshes before
    handing the client back. If the refresh fails, for any reason from a revoked session
    (a password change or joining a team revokes tokens) to the server being down, the
    stored entry is useless and is removed, so the caller asks for the password again.
    """
    entry = _read(base_dir)["accounts"].get(email)
    if entry is None:
        return None

    try:
        client = ApiClient(entry.get("base_url") or default_base_url)
        client.session = Session.from_dict(entry["session"])
    except (KeyError, ValueError, TypeError):
        forget(base_dir, email)
        return None

    try:
        client.refresh()
        client.get_profile()
    except ApiClientError:
        forget(base_dir, email)
        return None

    return client


def restore_session(base_dir: str | os.PathLike, default_base_url: str) -> Optional[ApiClient]:
    """Rebuild a signed-in client for the active saved account, or return None.

    `BIOAUDIT_REFRESH_TOKEN` takes priority so a CI job can sign in without a saved
    session file. It is never written to disk, and a failed refresh returns None.
    """
    env_token = os.environ.get(REFRESH_TOKEN_ENV)
    if env_token:
        client = ApiClient(default_base_url)
        client.session = Session(id_token="", refresh_token=env_token)
        try:
            client.refresh()
            client.get_profile()
        except ApiClientError:
            return None
        return client
    active = _read(base_dir)["active"]
    if active is None:
        return None
    return _restore(base_dir, active, default_base_url)


def switch_to(base_dir: str | os.PathLike, email: str,
              default_base_url: str) -> Optional[ApiClient]:
    """Sign in as another saved account without a password.

    Returns None when the account is not saved or its token no longer works; in the
    second case the entry is removed and the caller should ask for the password.
    """
    client = _restore(base_dir, email, default_base_url)
    if client is not None:
        save_session(base_dir, client)
    return client


def default_base_dir() -> Path:
    """Where the session file lives (the GUI also writes exported reports alongside it).

    Anchored under the user's home directory, not the current working directory, so a
    double-clicked exe or a CLI invoked from a read-only folder can always write here.
    """
    base = Path(os.path.expanduser("~")) / "BioAudit"
    base.mkdir(parents=True, exist_ok=True)
    return base
