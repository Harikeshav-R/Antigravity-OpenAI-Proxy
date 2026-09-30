# tests/test_api_routes.py
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from app.main import create_app
from app.auth.storage import Credentials

def test_models_endpoint():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    ids = [m["id"] for m in data["data"]]
    assert "claude-3.7-sonnet" in ids
    assert "gemini-3.8-flash" in ids
    assert "gemini-3-pro" in ids

def test_chat_completions_unauthorized(monkeypatch):
    app = create_app()
    monkeypatch.setattr(app.state.auth_manager, "get_credentials", AsyncMock(side_effect=RuntimeError("No credentials")))
    client = TestClient(app)
    resp = client.post("/v1/chat/completions", json={"model": "claude-3.7-sonnet", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 401

def test_chat_completions_non_streaming(monkeypatch):
    app = create_app()
    creds = Credentials("tok", "ref", "test-project", 9999999999999)
    monkeypatch.setattr(app.state.auth_manager, "get_credentials", AsyncMock(return_value=creds))

    async def mock_stream_chat(*args, **kwargs):
        yield {"content_delta": "Hello there!", "reasoning_delta": None, "tool_call_delta": None, "finish_reason": "stop", "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}

    monkeypatch.setattr(app.state.cca_client, "stream_chat", mock_stream_chat)

    client = TestClient(app)
    resp = client.post("/v1/chat/completions", json={"model": "claude-3.7-sonnet", "messages": [{"role": "user", "content": "hi"}], "stream": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Hello there!"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"]["total_tokens"] == 8

def test_chat_completions_streaming(monkeypatch):
    app = create_app()
    creds = Credentials("tok", "ref", "test-project", 9999999999999)
    monkeypatch.setattr(app.state.auth_manager, "get_credentials", AsyncMock(return_value=creds))

    async def mock_stream_chat(*args, **kwargs):
        yield {"content_delta": "Chunk 1", "finish_reason": None}
        yield {"content_delta": " Chunk 2", "finish_reason": "stop"}

    monkeypatch.setattr(app.state.cca_client, "stream_chat", mock_stream_chat)

    client = TestClient(app)
    resp = client.post("/v1/chat/completions", json={"model": "claude-3.7-sonnet", "messages": [{"role": "user", "content": "hi"}], "stream": True})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    text = resp.text
    assert "Chunk 1" in text
    assert "Chunk 2" in text
    assert "[DONE]" in text

def test_status_endpoint():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/status")
    assert resp.status_code == 200
    assert resp.json()["service"] == "antigravity-proxy"
