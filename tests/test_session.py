"""Saved-accounts file: save, list, switch, forget, legacy format. Offline, no server."""

import json
import os
import stat

import pytest

from bioaudit import session as sess
from bioaudit.api import ApiClient, ApiClientError, Session


class _Account:
    def __init__(self, email):
        self.email = email


def _client(email, token="r-token"):
    c = ApiClient("http://x/api")
    c.session = Session(id_token="i-" + email, refresh_token=token + email, expires_at=1.0, uid="u")
    c.account = _Account(email)
    return c


def _fake_restore(monkeypatch, fail=()):
    """Make refresh/get_profile succeed, except for emails in `fail`."""
    def refresh(self):
        if self.session.id_token[2:] in fail:
            raise ApiClientError("revoked", status=401)

    def profile(self):
        self.account = _Account(self.session.id_token[2:])

    monkeypatch.setattr(ApiClient, "refresh", refresh)
    monkeypatch.setattr(ApiClient, "get_profile", profile)


def test_save_keeps_other_accounts_and_marks_latest_active(tmp_path):
    sess.save_session(tmp_path, _client("a@x.com"))
    sess.save_session(tmp_path, _client("b@x.com"))
    accounts = sess.list_accounts(tmp_path)
    assert [(a.email, a.active) for a in accounts] == [("a@x.com", False), ("b@x.com", True)]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_file_is_owner_only(tmp_path):
    path = sess.save_session(tmp_path, _client("a@x.com"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_switch_changes_active_without_password(tmp_path, monkeypatch):
    sess.save_session(tmp_path, _client("a@x.com"))
    sess.save_session(tmp_path, _client("b@x.com"))
    _fake_restore(monkeypatch)

    client = sess.switch_to(tmp_path, "a@x.com", "http://x/api")

    assert client.account.email == "a@x.com"
    assert [a.email for a in sess.list_accounts(tmp_path) if a.active] == ["a@x.com"]


def test_revoked_token_drops_only_that_entry(tmp_path, monkeypatch):
    sess.save_session(tmp_path, _client("a@x.com"))
    sess.save_session(tmp_path, _client("b@x.com"))
    _fake_restore(monkeypatch, fail={"a@x.com"})

    assert sess.switch_to(tmp_path, "a@x.com", "http://x/api") is None
    assert [a.email for a in sess.list_accounts(tmp_path)] == ["b@x.com"]


def test_switch_to_unknown_account_returns_none(tmp_path):
    assert sess.switch_to(tmp_path, "nobody@x.com", "http://x/api") is None


def test_clear_session_forgets_active_only(tmp_path):
    sess.save_session(tmp_path, _client("a@x.com"))
    sess.save_session(tmp_path, _client("b@x.com"))
    sess.clear_session(tmp_path)
    assert [a.email for a in sess.list_accounts(tmp_path)] == ["a@x.com"]
    assert sess.restore_session(tmp_path, "http://x/api") is None


def test_forgetting_last_account_removes_file(tmp_path):
    sess.save_session(tmp_path, _client("a@x.com"))
    sess.forget(tmp_path, "a@x.com")
    assert not sess.session_path(tmp_path).exists()


def test_restore_uses_active_account(tmp_path, monkeypatch):
    sess.save_session(tmp_path, _client("a@x.com"))
    sess.save_session(tmp_path, _client("b@x.com"))
    _fake_restore(monkeypatch)
    assert sess.restore_session(tmp_path, "http://x/api").account.email == "b@x.com"


def test_legacy_single_account_file_still_reads(tmp_path, monkeypatch):
    legacy = {"base_url": "http://old/api", "email": "old@x.com",
              "session": _client("old@x.com").session.to_dict()}
    sess.session_path(tmp_path).write_text(json.dumps(legacy))
    _fake_restore(monkeypatch)

    assert [(a.email, a.active) for a in sess.list_accounts(tmp_path)] == [("old@x.com", True)]
    assert sess.restore_session(tmp_path, "http://x/api").account.email == "old@x.com"


def test_corrupt_file_is_treated_as_empty(tmp_path):
    sess.session_path(tmp_path).write_text("{not json")
    assert sess.list_accounts(tmp_path) == []
    assert sess.restore_session(tmp_path, "http://x/api") is None
