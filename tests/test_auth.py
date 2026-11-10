"""get_creds: only a rejected token means re-authorize; a network failure is an error."""
import pytest
from google.auth.exceptions import RefreshError, TransportError

from gchat_mcp import google_api


class StaleCreds:
    valid, expired, refresh_token = False, True, "r"

    def __init__(self, exc):
        self.exc = exc

    def refresh(self, request):
        raise self.exc


@pytest.fixture
def auth_dir(tmp_path, monkeypatch):
    (tmp_path / "token.json").write_text("{}")
    return tmp_path


def use(monkeypatch, exc):
    monkeypatch.setattr(google_api.Credentials, "from_authorized_user_file",
                        classmethod(lambda cls, *a, **k: StaleCreds(exc)))


def test_network_failure_on_refresh_is_not_auth_required(auth_dir, monkeypatch):
    use(monkeypatch, TransportError("no network"))
    with pytest.raises(TransportError):
        google_api.get_creds(str(auth_dir), interactive=False, log=lambda *a: None)


def test_rejected_token_is_auth_required(auth_dir, monkeypatch):
    use(monkeypatch, RefreshError("invalid_grant"))
    with pytest.raises(google_api.AuthRequired):
        google_api.get_creds(str(auth_dir), interactive=False, log=lambda *a: None)
