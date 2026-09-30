# tests/test_auth_manager.py
import asyncio
import time
import pytest
from app.auth.storage import Credentials, CredentialStorage
from app.auth.manager import AuthManager
from app.auth.oauth import parse_project_id_from_load_code_assist

def test_credentials_expiry():
    # Expired token
    c1 = Credentials(access_token="tok", refresh_token="ref", project_id="p", expires_at=1000)
    assert c1.is_expired() is True
    # Future token
    c2 = Credentials(access_token="tok", refresh_token="ref", project_id="p", expires_at=int(time.time() * 1000) + 120000)
    assert c2.is_expired() is False
    # Skew check: token expiring in 30 seconds is expired under 60s skew
    c3 = Credentials(access_token="tok", refresh_token="ref", project_id="p", expires_at=int(time.time() * 1000) + 30000)
    assert c3.is_expired(skew_ms=60000) is True

def test_parse_project_id_from_load_code_assist():
    payload = {"cloudaicompanionProject": "project-antigravity-999"}
    assert parse_project_id_from_load_code_assist(payload) == "project-antigravity-999"
    assert parse_project_id_from_load_code_assist({}) is None
def test_get_authorization_url_requires_client_id(monkeypatch):
    from app.auth.oauth import get_authorization_url
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID is required"):
        get_authorization_url("http://localhost:8000/oauth-callback")

def test_get_authorization_url_with_client_id():
    from app.auth.oauth import get_authorization_url
    url = get_authorization_url("http://localhost:8000/oauth-callback", client_id="test-client-id")
    assert "client_id=test-client-id" in url
    assert "response_type=code" in url

@pytest.mark.asyncio
async def test_auth_manager_returns_valid_token(tmp_path):
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text('{"access_token": "valid_tok", "refresh_token": "ref_tok", "project_id": "proj-1", "expires_at": 9999999999999}')

    manager = AuthManager(credentials_path=str(cred_file))
    creds = await manager.get_credentials()
    assert creds.access_token == "valid_tok"
    assert creds.project_id == "proj-1"

@pytest.mark.asyncio
async def test_auth_manager_single_flight_refresh_concurrent(tmp_path, monkeypatch):
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text('{"access_token": "expired_tok", "refresh_token": "ref_tok", "project_id": "proj-1", "expires_at": 1000}')

    manager = AuthManager(credentials_path=str(cred_file))
    call_count = 0

    async def mock_refresh(refresh_token: str):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return "new_access_tok", int((time.time() + 3600) * 1000)

    monkeypatch.setattr(manager, "_perform_refresh", mock_refresh)

    # Launch 10 concurrent requests
    tasks = [manager.get_credentials() for _ in range(10)]
    results = await asyncio.gather(*tasks)

    # Must invoke refresh exactly ONCE
    assert call_count == 1
    for r in results:
        assert r.access_token == "new_access_tok"

    # Verify file was updated
    storage = CredentialStorage(str(cred_file))
    saved = storage.load()
    assert saved is not None
    assert saved.access_token == "new_access_tok"
