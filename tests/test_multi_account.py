import asyncio
import json
import time
import pytest
from app.auth.storage import Credentials, CredentialStorage
from app.auth.manager import AuthManager

@pytest.mark.asyncio
async def test_sticky_latch_rotation_default(tmp_path):
    cred_file = tmp_path / "credentials.json"
    accounts_data = [
        {"email": "acc1@example.com", "access_token": "tok1", "refresh_token": "ref1", "project_id": "proj1", "expires_at": int(time.time() * 1000) + 3600000},
        {"email": "acc2@example.com", "access_token": "tok2", "refresh_token": "ref2", "project_id": "proj2", "expires_at": int(time.time() * 1000) + 3600000},
        {"email": "acc3@example.com", "access_token": "tok3", "refresh_token": "ref3", "project_id": "proj3", "expires_at": int(time.time() * 1000) + 3600000},
    ]
    cred_file.write_text(json.dumps(accounts_data))

    manager = AuthManager(credentials_path=str(cred_file))  # default sticky

    # Sequential requests must reuse acc1 for KV cache retention
    first = await manager.get_next_available_credentials()
    second = await manager.get_next_available_credentials()
    third = await manager.get_next_available_credentials()

    assert first.email == "acc1@example.com"
    assert second.email == "acc1@example.com"
    assert third.email == "acc1@example.com"

    # Mark acc1 as exhausted
    manager.mark_quota_exhausted(first, cooldown_seconds=1)

    # Next request switches and latches onto acc2
    fourth = await manager.get_next_available_credentials()
    fifth = await manager.get_next_available_credentials()
    assert fourth.email == "acc2@example.com"
    assert fifth.email == "acc2@example.com"

    # Wait for acc1 cooldown to expire
    await asyncio.sleep(1.05)

    # In sticky mode, must STAY on acc2 even though acc1 is now recovered
    sixth = await manager.get_next_available_credentials()
    assert sixth.email == "acc2@example.com"

    # Mark acc2 as exhausted
    manager.mark_quota_exhausted(fourth, cooldown_seconds=600)

    # Switches to acc3 (or recovered acc1)
    seventh = await manager.get_next_available_credentials()
    assert seventh.email in {"acc3@example.com", "acc1@example.com"}

@pytest.mark.asyncio
async def test_round_robin_rotation(tmp_path):
    cred_file = tmp_path / "credentials.json"
    accounts_data = [
        {"email": "acc1@example.com", "access_token": "tok1", "refresh_token": "ref1", "project_id": "proj1", "expires_at": int(time.time() * 1000) + 3600000},
        {"email": "acc2@example.com", "access_token": "tok2", "refresh_token": "ref2", "project_id": "proj2", "expires_at": int(time.time() * 1000) + 3600000},
        {"email": "acc3@example.com", "access_token": "tok3", "refresh_token": "ref3", "project_id": "proj3", "expires_at": int(time.time() * 1000) + 3600000},
    ]
    cred_file.write_text(json.dumps(accounts_data))

    manager = AuthManager(credentials_path=str(cred_file), pool_strategy="round_robin")
    
    first = await manager.get_next_available_credentials()
    second = await manager.get_next_available_credentials()
    third = await manager.get_next_available_credentials()
    fourth = await manager.get_next_available_credentials()

    assert first.email == "acc1@example.com"
    assert second.email == "acc2@example.com"
    assert third.email == "acc3@example.com"
    assert fourth.email == "acc1@example.com"
@pytest.mark.asyncio
async def test_quota_cooldown_skips_exhausted_account(tmp_path):
    cred_file = tmp_path / "credentials.json"
    accounts_data = [
        {"email": "acc1@example.com", "access_token": "tok1", "refresh_token": "ref1", "project_id": "proj1", "expires_at": int(time.time() * 1000) + 3600000},
        {"email": "acc2@example.com", "access_token": "tok2", "refresh_token": "ref2", "project_id": "proj2", "expires_at": int(time.time() * 1000) + 3600000},
    ]
    cred_file.write_text(json.dumps(accounts_data))

    manager = AuthManager(credentials_path=str(cred_file))
    pool = manager.get_account_pool()
    assert len(pool) == 2

    # Mark acc1 as exhausted
    manager.mark_quota_exhausted(pool[0], cooldown_seconds=600)

    # Successive calls should only return acc2
    res1 = await manager.get_next_available_credentials()
    res2 = await manager.get_next_available_credentials()
    assert res1.email == "acc2@example.com"
    assert res2.email == "acc2@example.com"

@pytest.mark.asyncio
async def test_all_accounts_in_cooldown_raises_error(tmp_path):
    cred_file = tmp_path / "credentials.json"
    accounts_data = [
        {"email": "acc1@example.com", "access_token": "tok1", "refresh_token": "ref1", "project_id": "proj1", "expires_at": int(time.time() * 1000) + 3600000},
    ]
    cred_file.write_text(json.dumps(accounts_data))

    manager = AuthManager(credentials_path=str(cred_file))
    pool = manager.get_account_pool()
    manager.mark_quota_exhausted(pool[0], cooldown_seconds=600)

    with pytest.raises(RuntimeError, match="All Antigravity accounts in pool are quota-exhausted"):
        await manager.get_next_available_credentials()

@pytest.mark.asyncio
async def test_exclude_ids_skips_specified_accounts(tmp_path):
    cred_file = tmp_path / "credentials.json"
    accounts_data = [
        {"email": "acc1@example.com", "access_token": "tok1", "refresh_token": "ref1", "project_id": "proj1", "expires_at": int(time.time() * 1000) + 3600000},
        {"email": "acc2@example.com", "access_token": "tok2", "refresh_token": "ref2", "project_id": "proj2", "expires_at": int(time.time() * 1000) + 3600000},
    ]
    cred_file.write_text(json.dumps(accounts_data))

    manager = AuthManager(credentials_path=str(cred_file))
    res = await manager.get_next_available_credentials(exclude_ids={"acc1@example.com"})
    assert res.email == "acc2@example.com"

@pytest.mark.asyncio
async def test_per_account_independent_locks(tmp_path, monkeypatch):
    cred_file = tmp_path / "credentials.json"
    # Both accounts expired
    accounts_data = [
        {"email": "acc1@example.com", "access_token": "tok1", "refresh_token": "ref1", "project_id": "proj1", "expires_at": 1000},
        {"email": "acc2@example.com", "access_token": "tok2", "refresh_token": "ref2", "project_id": "proj2", "expires_at": 1000},
    ]
    cred_file.write_text(json.dumps(accounts_data))

    manager = AuthManager(credentials_path=str(cred_file))

    order = []
    async def mock_refresh(ref_token: str):
        if ref_token == "ref1":
            await asyncio.sleep(0.1)
            order.append("acc1")
            return "new_tok1", int((time.time() + 3600) * 1000)
        else:
            await asyncio.sleep(0.01)
            order.append("acc2")
            return "new_tok2", int((time.time() + 3600) * 1000)

    monkeypatch.setattr(manager, "_perform_refresh", mock_refresh)

    # Request acc1 and acc2 concurrently
    t1 = asyncio.create_task(manager.get_next_available_credentials(exclude_ids={"acc2@example.com"}))
    t2 = asyncio.create_task(manager.get_next_available_credentials(exclude_ids={"acc1@example.com"}))

    r1, r2 = await asyncio.gather(t1, t2)
    assert r1.email == "acc1@example.com"
    assert r2.email == "acc2@example.com"
    assert r1.access_token == "new_tok1"
    assert r2.access_token == "new_tok2"
    # acc2 finished first because acc1 was delayed and their locks are independent
    assert order == ["acc2", "acc1"]

@pytest.mark.asyncio
async def test_add_or_update_and_remove_account(tmp_path):
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text("[]")

    manager = AuthManager(credentials_path=str(cred_file))
    assert manager.get_account_pool() == []

    acc1 = Credentials(access_token="tok1", refresh_token="ref1", project_id="proj1", expires_at=1000, email="acc1@example.com")
    manager.add_or_update_account(acc1)
    assert len(manager.get_account_pool()) == 1

    # Update acc1
    acc1_mod = Credentials(access_token="tok1_mod", refresh_token="ref1", project_id="proj1", expires_at=2000, email="acc1@example.com")
    manager.add_or_update_account(acc1_mod)
    assert len(manager.get_account_pool()) == 1
    assert manager.get_account_pool()[0].access_token == "tok1_mod"

    # Add acc2
    acc2 = Credentials(access_token="tok2", refresh_token="ref2", project_id="proj2", expires_at=1000, email="acc2@example.com")
    manager.add_or_update_account(acc2)
    assert len(manager.get_account_pool()) == 2

    # Remove acc1
    removed = manager.remove_account("acc1@example.com")
    assert removed is True
    assert len(manager.get_account_pool()) == 1
    assert manager.get_account_pool()[0].email == "acc2@example.com"

    # Remove non-existent
    assert manager.remove_account("nonexistent") is False

@pytest.mark.asyncio
async def test_get_pool_status(tmp_path):
    cred_file = tmp_path / "credentials.json"
    accounts_data = [
        {"email": "active@example.com", "access_token": "tok1", "refresh_token": "ref1", "project_id": "proj1", "expires_at": int(time.time() * 1000) + 3600000},
        {"email": "cooldown@example.com", "access_token": "tok2", "refresh_token": "ref2", "project_id": "proj2", "expires_at": int(time.time() * 1000) + 3600000},
    ]
    cred_file.write_text(json.dumps(accounts_data))

    manager = AuthManager(credentials_path=str(cred_file))
    pool = manager.get_account_pool()
    manager.mark_quota_exhausted(pool[1], cooldown_seconds=300)

    status = manager.get_pool_status()
    assert len(status) == 2
    assert status[0]["email"] == "active@example.com"
    assert status[0]["is_cooling_down"] is False
    assert status[0]["cooldown_remaining_seconds"] == 0
    assert status[0]["is_expired"] is False

    assert status[1]["email"] == "cooldown@example.com"
    assert status[1]["is_cooling_down"] is True
    assert status[1]["cooldown_remaining_seconds"] > 0

def test_chat_completions_non_streaming_quota_failover(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.client import UpstreamQuotaExhaustedError

    cred_file = tmp_path / "credentials.json"
    accounts_data = [
        {"email": "acc1@example.com", "access_token": "tok1", "refresh_token": "ref1", "project_id": "proj1", "expires_at": int(time.time() * 1000) + 3600000},
        {"email": "acc2@example.com", "access_token": "tok2", "refresh_token": "ref2", "project_id": "proj2", "expires_at": int(time.time() * 1000) + 3600000},
    ]
    cred_file.write_text(json.dumps(accounts_data))

    monkeypatch.setenv("CREDENTIALS_PATH", str(cred_file))
    app = create_app()

    async def mock_stream_chat(*args, **kwargs):
        token = kwargs.get("access_token")
        if token == "tok1":
            raise UpstreamQuotaExhaustedError("Google Cloud Code Assist 429: quota exceeded", status_code=429)
        elif token == "tok2":
            yield {"content_delta": "Success from acc2!", "finish_reason": "stop"}

    monkeypatch.setattr(app.state.cca_client, "stream_chat", mock_stream_chat)

    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "claude-3.7-sonnet", "messages": [{"role": "user", "content": "hi"}], "stream": False}
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"] == "Success from acc2!"

    # Verify /status reflects acc1 in cooldown and acc2 active
    resp_status = client.get("/status")
    assert resp_status.status_code == 200
    status_data = resp_status.json()
    assert status_data["total_accounts"] == 2
    assert status_data["active_accounts"] == 1
    acc1_status = next(s for s in status_data["accounts"] if s["email"] == "acc1@example.com")
    acc2_status = next(s for s in status_data["accounts"] if s["email"] == "acc2@example.com")
    assert acc1_status["is_cooling_down"] is True
    assert acc1_status["cooldown_remaining_seconds"] > 0
    assert acc2_status["is_cooling_down"] is False
    assert acc2_status["cooldown_remaining_seconds"] == 0

def test_chat_completions_non_streaming_all_accounts_exhausted(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.client import UpstreamQuotaExhaustedError

    cred_file = tmp_path / "credentials.json"
    accounts_data = [
        {"email": "acc1@example.com", "access_token": "tok1", "refresh_token": "ref1", "project_id": "proj1", "expires_at": int(time.time() * 1000) + 3600000},
        {"email": "acc2@example.com", "access_token": "tok2", "refresh_token": "ref2", "project_id": "proj2", "expires_at": int(time.time() * 1000) + 3600000},
    ]
    cred_file.write_text(json.dumps(accounts_data))

    monkeypatch.setenv("CREDENTIALS_PATH", str(cred_file))
    app = create_app()

    async def mock_stream_chat(*args, **kwargs):
        if False:
            yield {}
        raise UpstreamQuotaExhaustedError("Google Cloud Code Assist 429: quota exceeded", status_code=429)

    monkeypatch.setattr(app.state.cca_client, "stream_chat", mock_stream_chat)

    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "claude-3.7-sonnet", "messages": [{"role": "user", "content": "hi"}], "stream": False}
    )

    assert resp.status_code == 429
    assert "quota-exhausted" in resp.json().get("detail", "")

def test_chat_completions_streaming_quota_failover(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.client import UpstreamQuotaExhaustedError

    cred_file = tmp_path / "credentials.json"
    accounts_data = [
        {"email": "acc1@example.com", "access_token": "tok1", "refresh_token": "ref1", "project_id": "proj1", "expires_at": int(time.time() * 1000) + 3600000},
        {"email": "acc2@example.com", "access_token": "tok2", "refresh_token": "ref2", "project_id": "proj2", "expires_at": int(time.time() * 1000) + 3600000},
    ]
    cred_file.write_text(json.dumps(accounts_data))

    monkeypatch.setenv("CREDENTIALS_PATH", str(cred_file))
    app = create_app()

    async def mock_stream_chat(*args, **kwargs):
        token = kwargs.get("access_token")
        if token == "tok1":
            raise UpstreamQuotaExhaustedError("Google Cloud Code Assist 429: quota exceeded", status_code=429)
        elif token == "tok2":
            yield {"content_delta": "Streaming chunk from acc2", "finish_reason": None}
            yield {"content_delta": "", "finish_reason": "stop"}

    monkeypatch.setattr(app.state.cca_client, "stream_chat", mock_stream_chat)

    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "claude-3.7-sonnet", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    )

    assert resp.status_code == 200
    assert "Streaming chunk from acc2" in resp.text
    assert "[DONE]" in resp.text

def test_chat_completions_streaming_all_accounts_exhausted(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.client import UpstreamQuotaExhaustedError

    cred_file = tmp_path / "credentials.json"
    accounts_data = [
        {"email": "acc1@example.com", "access_token": "tok1", "refresh_token": "ref1", "project_id": "proj1", "expires_at": int(time.time() * 1000) + 3600000},
    ]
    cred_file.write_text(json.dumps(accounts_data))

    monkeypatch.setenv("CREDENTIALS_PATH", str(cred_file))
    app = create_app()

    async def mock_stream_chat(*args, **kwargs):
        if False:
            yield {}
        raise UpstreamQuotaExhaustedError("Google Cloud Code Assist 429: quota exceeded", status_code=429)

    monkeypatch.setattr(app.state.cca_client, "stream_chat", mock_stream_chat)

    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "claude-3.7-sonnet", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    )

    assert resp.status_code == 200
    assert "insufficient_quota" in resp.text
    assert "429" in resp.text
    assert "[DONE]" in resp.text

def test_status_endpoint_multi_account(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app

    cred_file = tmp_path / "credentials.json"
    accounts_data = [
        {"email": "dev1@gmail.com", "access_token": "tok1", "refresh_token": "ref1", "project_id": "proj1", "expires_at": int(time.time() * 1000) + 3600000},
        {"email": "dev2@gmail.com", "access_token": "tok2", "refresh_token": "ref2", "project_id": "proj2", "expires_at": int(time.time() * 1000) + 3600000},
    ]
    cred_file.write_text(json.dumps(accounts_data))

    monkeypatch.setenv("CREDENTIALS_PATH", str(cred_file))
    app = create_app()

    # Mark dev2 as exhausted
    app.state.auth_manager.mark_quota_exhausted(app.state.auth_manager.get_account_pool()[1], cooldown_seconds=450)

    client = TestClient(app)
    resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "ok"
    assert data["service"] == "antigravity-proxy"
    assert data["authenticated"] is True
    assert data["total_accounts"] == 2
    assert data["active_accounts"] == 1
    assert len(data["accounts"]) == 2
    assert data["accounts"][0]["email"] == "dev1@gmail.com"
    assert data["accounts"][0]["is_cooling_down"] is False
    assert data["accounts"][1]["email"] == "dev2@gmail.com"
    assert data["accounts"][1]["is_cooling_down"] is True
    assert data["accounts"][1]["cooldown_remaining_seconds"] > 0

def test_login_page_renders_account_list(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app

    cred_file = tmp_path / "credentials.json"
    accounts_data = [
        {"email": "connected1@gmail.com", "access_token": "tok1", "refresh_token": "ref1", "project_id": "proj1", "expires_at": int(time.time() * 1000) + 3600000},
    ]
    cred_file.write_text(json.dumps(accounts_data))

    monkeypatch.setenv("CREDENTIALS_PATH", str(cred_file))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    app = create_app()

    client = TestClient(app)
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "connected1@gmail.com" in resp.text
    assert "Add Another Google Account" in resp.text

def test_accounts_rest_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app

    cred_file = tmp_path / "credentials.json"
    accounts_data = [
        {"email": "acc1@gmail.com", "access_token": "tok1", "refresh_token": "ref1", "project_id": "proj1", "expires_at": int(time.time() * 1000) + 3600000},
        {"email": "acc2@gmail.com", "access_token": "tok2", "refresh_token": "ref2", "project_id": "proj2", "expires_at": int(time.time() * 1000) + 3600000},
    ]
    cred_file.write_text(json.dumps(accounts_data))

    monkeypatch.setenv("CREDENTIALS_PATH", str(cred_file))
    app = create_app()
    client = TestClient(app)

    # GET /v1/accounts
    resp = client.get("/v1/accounts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_accounts"] == 2
    assert data["active_accounts"] == 2

    # DELETE /v1/accounts/{identifier}
    resp_del = client.delete("/v1/accounts/acc1@gmail.com")
    assert resp_del.status_code == 200
    assert resp_del.json()["status"] == "ok"

    # Verify pool now has 1 account
    resp_after = client.get("/v1/accounts")
    assert resp_after.json()["total_accounts"] == 1
    assert resp_after.json()["accounts"][0]["email"] == "acc2@gmail.com"

    # DELETE non-existent account returns 404
    resp_not_found = client.delete("/v1/accounts/nonexistent@gmail.com")
    assert resp_not_found.status_code == 404

def test_oauth_callback_adds_account_to_pool(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from fastapi.testclient import TestClient
    from app.main import create_app
    import app.routes.auth_routes as auth_routes

    cred_file = tmp_path / "credentials.json"
    cred_file.write_text("[]")

    monkeypatch.setenv("CREDENTIALS_PATH", str(cred_file))
    app = create_app()

    monkeypatch.setattr(
        auth_routes,
        "exchange_code_for_tokens",
        AsyncMock(return_value=("new_acc_tok", "new_ref_tok", int(time.time() * 1000) + 3600000, "new_user@gmail.com"))
    )
    monkeypatch.setattr(
        auth_routes,
        "discover_project_id",
        AsyncMock(return_value="discovered-proj-123")
    )

    client = TestClient(app, follow_redirects=False)
    resp = client.get("/oauth-callback?code=mock_oauth_code")
    assert resp.status_code == 307
    assert resp.headers["location"] == "/status"

    # Verify account was added to pool
    pool = app.state.auth_manager.get_account_pool()
    assert len(pool) == 1
    assert pool[0].email == "new_user@gmail.com"
    assert pool[0].project_id == "discovered-proj-123"
