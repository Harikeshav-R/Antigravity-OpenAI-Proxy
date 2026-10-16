import asyncio
import json
import time
import pytest
import httpx
from app.config import ProxyConfig
from app.auth.storage import Credentials, CredentialStorage
from app.auth.manager import AuthManager
from app.client import CloudCodeAssistClient
from app.search.models import SearchRequest
from app.search.service import (
    SearchService,
    SearchServiceError,
    validate_search_model,
    build_search_payload,
)

def test_validate_search_model():
    # Valid gemini models
    p_flash = validate_search_model("gemini-3.8-flash")
    assert p_flash.wire_model_id == "gemini-3.8-flash-high"
    p_25 = validate_search_model("gemini-2.5-flash")
    assert p_25.wire_model_id == "gemini-2.5-flash"

    # Claude rejected
    with pytest.raises(SearchServiceError) as exc_info:
        validate_search_model("claude-3.7-sonnet")
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "search_model_unavailable"

    # GPT-OSS rejected
    with pytest.raises(SearchServiceError) as exc_info:
        validate_search_model("gpt-oss-120b")
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "search_model_unavailable"

    # Unknown model rejected
    with pytest.raises(SearchServiceError) as exc_info:
        validate_search_model("random-model-xyz")
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "search_model_unavailable"

def test_build_search_payload():
    req = SearchRequest(query="site:python.org Python release")
    payload = build_search_payload(req, project_id="proj-123", model="gemini-3.8-flash-high")
    assert payload["project"] == "proj-123"
    assert payload["model"] == "gemini-3.8-flash-high"
    assert payload["userAgent"] == "antigravity"

    inner = payload["request"]
    assert inner["tools"] == [{"googleSearch": {}}]
    assert "toolConfig" not in inner
    assert len(inner["contents"]) == 1
    assert inner["contents"][0]["role"] == "user"
    assert inner["contents"][0]["parts"][0]["text"] == "site:python.org Python release"

@pytest.mark.asyncio
async def test_search_service_missing_credentials(tmp_path):
    cred_file = str(tmp_path / "empty_creds.json")
    with open(cred_file, "w") as f:
        f.write("[]")

    config = ProxyConfig(credentials_path=cred_file)
    auth_mgr = AuthManager(credentials_path=cred_file)
    cca_client = CloudCodeAssistClient(config)
    service = SearchService(config, auth_mgr, cca_client)

    with pytest.raises(SearchServiceError) as exc_info:
        await service.search(SearchRequest(query="test query"))
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "search_credentials_unavailable"
    assert "No Antigravity account configured" in exc_info.value.message

@pytest.mark.asyncio
async def test_search_service_failover_on_429_preserves_only_second_account_data(tmp_path):
    cred_file = str(tmp_path / "creds.json")
    acc1 = Credentials("tok-1", "ref-1", "project-1", int(time.time() * 1000 + 3600000), email="acc1@example.com")
    acc2 = Credentials("tok-2", "ref-2", "project-2", int(time.time() * 1000 + 3600000), email="acc2@example.com")
    storage = CredentialStorage(cred_file)
    storage.save_all([acc1, acc2])

    config = ProxyConfig(credentials_path=cred_file)
    auth_mgr = AuthManager(credentials_path=cred_file)
    cca_client = CloudCodeAssistClient(config)

    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        auth_header = request.headers.get("Authorization", "")
        calls.append(auth_header)
        if "tok-1" in auth_header:
            # First account returns partial text and then 429 quota exhaustion
            body = (
                'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Acc1 partial text"}]}}]}}\n\n'
                'data: {"error": {"code": 429, "message": "RESOURCE_EXHAUSTED"}}\n\n'
            )
            return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})
        elif "tok-2" in auth_header:
            # Second account succeeds with real citations
            body = (
                'data: {"response": {"modelVersion": "gemini-3.8-flash-001", "candidates": [{"content": {"parts": [{"text": "Acc2 answer."}]}}]}}\n\n'
                'data: {"response": {"candidates": [{"finishReason": "STOP", "groundingMetadata": {"webSearchQueries": ["q"], "groundingChunks": [{"web": {"uri": "https://example.com/2", "title": "T2"}}], "groundingSupports": [{"segment": {"text": "Acc2 answer."}, "groundingChunkIndices": [0]}]}}]}}\n\n'
            )
            return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})
        return httpx.Response(500, text="Unexpected")

    cca_client._transport = httpx.MockTransport(handler)
    service = SearchService(config, auth_mgr, cca_client)

    resp = await service.search(SearchRequest(query="test query"))
    assert len(calls) == 2
    assert "Bearer tok-1" in calls[0]
    assert "Bearer tok-2" in calls[1]

    # Verify acc1 partial text did NOT leak into acc2 answer
    assert resp.answer == "Acc2 answer."
    assert "Acc1" not in resp.answer
    assert len(resp.results) == 1
    assert resp.results[0].url == "https://example.com/2"

@pytest.mark.asyncio
async def test_search_service_quota_flavored_403_and_non_quota_403(tmp_path):
    cred_file = str(tmp_path / "creds.json")
    acc1 = Credentials("tok-1", "ref-1", "project-1", int(time.time() * 1000 + 3600000), email="acc1@example.com")
    acc2 = Credentials("tok-2", "ref-2", "project-2", int(time.time() * 1000 + 3600000), email="acc2@example.com")
    storage = CredentialStorage(cred_file)
    storage.save_all([acc1, acc2])

    config = ProxyConfig(credentials_path=cred_file)
    auth_mgr = AuthManager(credentials_path=cred_file)
    cca_client = CloudCodeAssistClient(config)

    # 1. Quota-flavored 403 fails over to acc2
    calls = []
    def quota_403_handler(request: httpx.Request) -> httpx.Response:
        auth_header = request.headers.get("Authorization", "")
        calls.append(auth_header)
        if "tok-1" in auth_header:
            return httpx.Response(403, text='{"error": "RESOURCE_EXHAUSTED quota exceeded"}')
        else:
            body = 'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Acc2 ok"}]}, "finishReason": "STOP"}]}}\n\n'
            return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    cca_client._transport = httpx.MockTransport(quota_403_handler)
    service = SearchService(config, auth_mgr, cca_client)
    resp = await service.search(SearchRequest(query="test query"))
    assert resp.answer == "Acc2 ok"
    assert len(calls) == 2

    # 2. Non-quota 403 on acc2 raises 502 without treating as quota
    def non_quota_403_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text='{"error": "PermissionDenied: cloudaicompanion.generateChat denied"}')

    cca_client._transport = httpx.MockTransport(non_quota_403_handler)
    with pytest.raises(SearchServiceError) as exc_info:
        await service.search(SearchRequest(query="test query"))
    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "search_upstream_error"

@pytest.mark.asyncio
async def test_search_service_all_accounts_exhausted(tmp_path):
    cred_file = str(tmp_path / "creds.json")
    acc1 = Credentials("tok-1", "ref-1", "project-1", int(time.time() * 1000 + 3600000), email="acc1@example.com")
    storage = CredentialStorage(cred_file)
    storage.save_all([acc1])

    config = ProxyConfig(credentials_path=cred_file)
    auth_mgr = AuthManager(credentials_path=cred_file)
    cca_client = CloudCodeAssistClient(config)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text='{"error": "Quota exhausted"}')

    cca_client._transport = httpx.MockTransport(handler)
    service = SearchService(config, auth_mgr, cca_client)

    with pytest.raises(SearchServiceError) as exc_info:
        await service.search(SearchRequest(query="test query"))
    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "search_quota_exhausted"

@pytest.mark.asyncio
async def test_search_service_deadline_timeout(tmp_path, monkeypatch):
    cred_file = str(tmp_path / "creds.json")
    acc1 = Credentials("tok-1", "ref-1", "project-1", int(time.time() * 1000 + 3600000))
    storage = CredentialStorage(cred_file)
    storage.save_all([acc1])

    monkeypatch.setenv("SEARCH_TIMEOUT_SECONDS", "0.1")
    config = ProxyConfig(credentials_path=cred_file)
    auth_mgr = AuthManager(credentials_path=cred_file)
    cca_client = CloudCodeAssistClient(config)

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1.0)
        return httpx.Response(200, text="data: {}\n\n")

    cca_client._transport = httpx.MockTransport(slow_handler)
    service = SearchService(config, auth_mgr, cca_client)

    with pytest.raises(SearchServiceError) as exc_info:
        await service.search(SearchRequest(query="test query"))
    assert exc_info.value.status_code == 504
    assert exc_info.value.code == "search_timeout"

@pytest.mark.asyncio
async def test_search_service_concurrent_expired_tokens_share_refresh_lock(tmp_path, monkeypatch):
    cred_file = str(tmp_path / "creds.json")
    # Expired credential (expires_at in the past)
    acc = Credentials("expired-tok", "ref-tok", "proj-1", expires_at=int(time.time() * 1000 - 10000), email="acc@test.com")
    storage = CredentialStorage(cred_file)
    storage.save_all([acc])

    config = ProxyConfig(credentials_path=cred_file)
    auth_mgr = AuthManager(credentials_path=cred_file)
    cca_client = CloudCodeAssistClient(config)

    refresh_count = 0
    async def mock_refresh(ref_tok):
        nonlocal refresh_count
        refresh_count += 1
        await asyncio.sleep(0.05)  # simulate network delay
        return "new-refreshed-tok", int(time.time() * 1000 + 3600000)

    monkeypatch.setattr(auth_mgr, "_perform_refresh", mock_refresh)

    def handler(request: httpx.Request) -> httpx.Response:
        body = 'data: {"response": {"candidates": [{"content": {"parts": [{"text": "OK"}]}, "finishReason": "STOP"}]}}\n\n'
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    cca_client._transport = httpx.MockTransport(handler)
    service = SearchService(config, auth_mgr, cca_client)

    # Launch two concurrent searches
    r1, r2 = await asyncio.gather(
        service.search(SearchRequest(query="q1")),
        service.search(SearchRequest(query="q2")),
    )
    assert r1.answer == "OK"
    assert r2.answer == "OK"
    # Token refresh should have been called only ONCE due to lock
    assert refresh_count == 1
