import asyncio
import time
import pytest
import httpx
from fastapi.testclient import TestClient
from app.main import create_app
from app.auth.storage import Credentials, CredentialStorage
from app.auth.manager import AuthManager
from app.client import CloudCodeAssistClient
from app.search.service import SearchService

@pytest.fixture
def test_setup(tmp_path, monkeypatch):
    cred_file = str(tmp_path / "creds.json")
    acc = Credentials("tok-1", "ref-1", "project-1", int(time.time() * 1000 + 3600000), email="acc@test.com")
    storage = CredentialStorage(cred_file)
    storage.save_all([acc])

    monkeypatch.setenv("SEARCH_API_KEY", "test-search-key")
    monkeypatch.setenv("CREDENTIALS_PATH", cred_file)
    app = create_app()

    return app, cred_file

def test_search_route_disabled_when_key_unset(tmp_path, monkeypatch):
    cred_file = str(tmp_path / "creds.json")
    with open(cred_file, "w") as f:
        f.write("[]")
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    app = create_app()
    client = TestClient(app)

    resp = client.post(
        "/v1/search",
        json={"query": "test"},
        headers={"Authorization": "Bearer test-key"},
    )
    assert resp.status_code == 503
    data = resp.json()
    assert data["error"]["code"] == "search_disabled"
    assert "SEARCH_API_KEY is not configured" in data["error"]["message"]

def test_search_route_unauthorized_missing_or_invalid_bearer(test_setup):
    app, _ = test_setup
    client = TestClient(app)

    # Missing header
    r1 = client.post("/v1/search", json={"query": "test"})
    assert r1.status_code == 401
    assert r1.headers.get("www-authenticate") == "Bearer"
    assert r1.json()["error"]["code"] == "unauthorized"

    # Wrong header prefix
    r2 = client.post("/v1/search", json={"query": "test"}, headers={"Authorization": "Basic 12345"})
    assert r2.status_code == 401
    assert r2.headers.get("www-authenticate") == "Bearer"
    assert r2.json()["error"]["code"] == "unauthorized"

    # Wrong key
    r3 = client.post("/v1/search", json={"query": "test"}, headers={"Authorization": "Bearer wrong-key"})
    assert r3.status_code == 401
    assert r3.headers.get("www-authenticate") == "Bearer"
    assert r3.json()["error"]["code"] == "unauthorized"

def test_search_route_validation_422(test_setup):
    app, _ = test_setup
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-search-key"}

    # Empty query
    r1 = client.post("/v1/search", json={"query": ""}, headers=headers)
    assert r1.status_code == 422

    # Whitespace-only query
    r2 = client.post("/v1/search", json={"query": "   "}, headers=headers)
    assert r2.status_code == 422

    # Boolean max_results
    r3 = client.post("/v1/search", json={"query": "valid", "max_results": True}, headers=headers)
    assert r3.status_code == 422

    # Out of range max_results
    r4 = client.post("/v1/search", json={"query": "valid", "max_results": 25}, headers=headers)
    assert r4.status_code == 422

    # Extra fields forbidden
    r5 = client.post("/v1/search", json={"query": "valid", "search_depth": "deep"}, headers=headers)
    assert r5.status_code == 422

    # include_raw_content=True forbidden
    r6 = client.post("/v1/search", json={"query": "valid", "include_raw_content": True}, headers=headers)
    assert r6.status_code == 422

def test_search_route_success_with_grounding_and_slicing(test_setup):
    app, _ = test_setup

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"response": {"modelVersion": "gemini-3.8-flash-001", "candidates": [{"content": {"parts": [{"text": "Python 3.13 was released on Oct 7, 2024. "}]}}]}}\n\n'
            'data: {"response": {"candidates": [{"content": {"parts": [{"thought": true, "text": "hidden reasoning text"}]}}]}}\n\n'
            'data: {"response": {"candidates": [{"content": {"parts": [{"text": "It includes experimental free threading."}]}}]}}\n\n'
            'data: {"response": {"candidates": [{"finishReason": "STOP", "groundingMetadata": {"webSearchQueries": ["python 3.13 release"], "groundingChunks": [{"web": {"uri": "https://python.org/release", "title": "Release Notes"}}, {"web": {"uri": "https://python.org/pep703", "title": "PEP 703"}}], "groundingSupports": [{"segment": {"text": "Python 3.13 was released on Oct 7, 2024."}, "groundingChunkIndices": [0]}, {"segment": {"text": "It includes experimental free threading."}, "groundingChunkIndices": [1]}]}}]}}\n\n'
            'data: {"response": {"candidates": [], "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 30, "totalTokenCount": 50}}}\n\n'
        )
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    app.state.cca_client._transport = httpx.MockTransport(handler)
    client = TestClient(app)

    # Request max_results=1
    resp = client.post(
        "/v1/search",
        json={"query": "python 3.13 release", "max_results": 1},
        headers={"Authorization": "Bearer test-search-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "python 3.13 release"
    assert "Python 3.13 was released on Oct 7, 2024." in data["answer"]
    assert "hidden reasoning text" not in data["answer"]
    assert data["model"] == "gemini-3.8-flash-001"
    assert data["search_queries"] == ["python 3.13 release"]
    assert data["usage"] == {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50}

    # Sliced to 1 result
    assert len(data["results"]) == 1
    assert data["results"][0]["url"] == "https://python.org/release"
    assert data["results"][0]["title"] == "Release Notes"
    assert data["results"][0]["content"] == "Grounded answer excerpt (model-generated, not page text): Python 3.13 was released on Oct 7, 2024."

    # Citations match sliced results
    assert len(data["citations"]) == 1
    assert data["citations"][0]["url"] == "https://python.org/release"

def test_search_route_quota_exhausted_returns_429(test_setup):
    app, _ = test_setup

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text='{"error": "Quota exhausted"}')

    app.state.cca_client._transport = httpx.MockTransport(handler)
    client = TestClient(app)

    resp = client.post(
        "/v1/search",
        json={"query": "test query"},
        headers={"Authorization": "Bearer test-search-key"},
    )
    assert resp.status_code == 429
    data = resp.json()
    assert data["error"]["code"] == "search_quota_exhausted"
    assert "All Antigravity accounts are quota-exhausted" in data["error"]["message"]

def test_search_route_upstream_error_returns_502(test_setup):
    app, _ = test_setup

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    app.state.cca_client._transport = httpx.MockTransport(handler)
    client = TestClient(app)

    resp = client.post(
        "/v1/search",
        json={"query": "test query"},
        headers={"Authorization": "Bearer test-search-key"},
    )
    assert resp.status_code == 502
    data = resp.json()
    assert data["error"]["code"] == "search_upstream_error"
    assert "Google grounded search failed" in data["error"]["message"]

def test_search_route_timeout_returns_504(test_setup, monkeypatch):
    app, _ = test_setup
    app.state.config.search_timeout_seconds = 0.1

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1.0)
        return httpx.Response(200, text="data: {}\n\n")

    app.state.cca_client._transport = httpx.MockTransport(slow_handler)
    client = TestClient(app)

    resp = client.post(
        "/v1/search",
        json={"query": "test query"},
        headers={"Authorization": "Bearer test-search-key"},
    )
    assert resp.status_code == 504
    data = resp.json()
    assert data["error"]["code"] == "search_timeout"
    assert "Grounded search timed out" in data["error"]["message"]

def test_chat_completions_still_operates_normally(test_setup):
    app, _ = test_setup

    async def mock_stream_chat(*args, **kwargs):
        yield {
            "content_delta": "Hello from normal chat!",
            "reasoning_delta": None,
            "tool_call_delta": None,
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }

    app.state.cca_client.stream_chat = mock_stream_chat
    client = TestClient(app)

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "claude-3.7-sonnet", "messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Hello from normal chat!"

@pytest.mark.asyncio
async def test_search_route_client_disconnect_cancels_search(test_setup):
    app, _ = test_setup

    search_cancelled = False
    async def blocking_stream_chat(*args, **kwargs):
        nonlocal search_cancelled
        try:
            # Block indefinitely
            while True:
                await asyncio.sleep(0.1)
                yield {"content_delta": "tick"}
        except asyncio.CancelledError:
            search_cancelled = True
            raise

    app.state.cca_client.stream_chat = blocking_stream_chat

    # Simulate ASGI request lifecycle with disconnect
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/search",
        "headers": [
            (b"content-type", b"application/json"),
            (b"authorization", b"Bearer test-search-key"),
        ],
        "query_string": b"",
    }

    disconnect_sent = False
    async def receive():
        nonlocal disconnect_sent
        if not disconnect_sent:
            disconnect_sent = True
            return {
                "type": "http.request",
                "body": b'{"query": "disconnect test"}',
                "more_body": False,
            }
        return {"type": "http.disconnect"}

    messages = []
    async def send(message):
        messages.append(message)

    await app(scope, receive, send)

    # Search should have been cancelled by the disconnect watcher
    assert search_cancelled is True
    # Confirm 499 response was sent
    assert any(m.get("status") == 499 for m in messages if m.get("type") == "http.response.start")
