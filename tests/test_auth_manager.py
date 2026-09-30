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
    monkeypatch.delenv("google_client_id", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID is required"):
        get_authorization_url("http://localhost:8000/oauth-callback")

def test_get_oauth_client_credentials_case_insensitive(monkeypatch):
    from app.auth.oauth import get_oauth_client_credentials
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("google_client_id", "lowercase-client-id")
    monkeypatch.setenv("google_client_secret", "lowercase-secret")
    cid, sec = get_oauth_client_credentials()
    assert cid == "lowercase-client-id"
    assert sec == "lowercase-secret"
def test_get_authorization_url_with_client_id():
    from app.auth.oauth import get_authorization_url
    url = get_authorization_url("http://localhost:8000/oauth-callback", client_id="test-client-id")
    assert "client_id=test-client-id" in url
    assert "client_id=client_id=" not in url
    assert "redirect_uri=redirect_uri=" not in url
    assert "response_type=response_type=" not in url
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

@pytest.mark.asyncio
async def test_discover_project_id_success(monkeypatch):
    import httpx
    from app.auth.oauth import discover_project_id
    monkeypatch.delenv("ANTIGRAVITY_PROJECT_ID", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"cloudaicompanionProject": "discovered-project-999"})

    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: orig(transport=transport))
    proj = await discover_project_id("test_token")
    assert proj == "discovered-project-999"

@pytest.mark.asyncio
async def test_discover_project_id_fallback_on_403(monkeypatch):
    import httpx
    from app.auth.oauth import discover_project_id
    monkeypatch.delenv("ANTIGRAVITY_PROJECT_ID", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": 403, "message": "The caller does not have permission"}})

    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: orig(transport=transport))
    proj = await discover_project_id("test_token")
    # Should fall back cleanly without raising
    assert proj == "default-cli-project"

@pytest.mark.asyncio
async def test_discover_project_id_prefers_env(monkeypatch):
    from app.auth.oauth import discover_project_id
    monkeypatch.setenv("ANTIGRAVITY_PROJECT_ID", "my-env-project")
    proj = await discover_project_id("test_token")
    assert proj == "my-env-project"

@pytest.mark.asyncio
async def test_refresh_access_token_uses_default_antigravity_client_when_unset(monkeypatch):
    import httpx
    from app.auth.oauth import refresh_access_token, ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("google_client_id", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("google_client_secret", raising=False)

    captured_data = {}
    def handler(request: httpx.Request) -> httpx.Response:
        from urllib.parse import parse_qs
        captured_data.update(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "refreshed_abc", "expires_in": 3600})

    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: orig(transport=transport))

    token, expires_at = await refresh_access_token("test_refresh_token")
    assert token == "refreshed_abc"
    assert captured_data.get("client_id") == [ANTIGRAVITY_CLIENT_ID]
    assert captured_data.get("client_secret") == [ANTIGRAVITY_CLIENT_SECRET]

@pytest.mark.asyncio
async def test_refresh_access_token_fallback_when_custom_client_unauthorized(monkeypatch):
    import httpx
    from app.auth.oauth import refresh_access_token, ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "custom-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "custom-secret")

    attempts = []
    def handler(request: httpx.Request) -> httpx.Response:
        from urllib.parse import parse_qs
        data = parse_qs(request.content.decode())
        cid = data.get("client_id", [""])[0]
        attempts.append(cid)
        if cid == "custom-client-id":
            return httpx.Response(401, json={"error": "unauthorized_client", "error_description": "Unauthorized"})
        elif cid == ANTIGRAVITY_CLIENT_ID:
            return httpx.Response(200, json={"access_token": "fallback_token", "expires_in": 1800})
        return httpx.Response(400, json={"error": "bad_request"})

    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: orig(transport=transport))

    token, expires_at = await refresh_access_token("test_refresh_token")
    assert token == "fallback_token"
    assert attempts == ["custom-client-id", ANTIGRAVITY_CLIENT_ID]
