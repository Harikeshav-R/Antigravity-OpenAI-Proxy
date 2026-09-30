# tests/test_upstream_client.py
import pytest
import httpx
from app.config import ProxyConfig
from app.client import CloudCodeAssistClient

@pytest.mark.asyncio
async def test_client_retries_on_empty_200_stream():
    # Simulate empty response: first attempt returns 200 with STOP and 0 text,
    # second attempt returns 200 with text content
    config = ProxyConfig()
    client = CloudCodeAssistClient(config)

    attempt = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            # Empty stream
            body = 'data: {"response": {"candidates": [{"finishReason": "STOP"}]}}\n\n'
            return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})
        else:
            # Meaningful stream
            body = (
                'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Hello!"}]}}]}}\n\n'
                'data: {"response": {"candidates": [{"finishReason": "STOP"}]}}\n\n'
            )
            return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    mock_transport = httpx.MockTransport(handler)
    chunks = []
    async for item in client.stream_chat_with_transport(
        cca_payload={"model": "claude-sonnet-4-6"},
        access_token="test_tok",
        is_claude=True,
        transport=mock_transport,
        base_backoff_ms=10  # fast test backoff
    ):
        chunks.append(item)

    # Must have retried and completed attempt 2
    assert attempt == 2
    texts = [c.get("content_delta") for c in chunks if c.get("content_delta")]
    assert "Hello!" in texts

@pytest.mark.asyncio
async def test_client_endpoint_failover_on_5xx():
    config = ProxyConfig()
    client = CloudCodeAssistClient(config)

    endpoints_called = []
    def handler(request: httpx.Request) -> httpx.Response:
        endpoints_called.append(str(request.url))
        if "sandbox" not in str(request.url):
            return httpx.Response(502, text="Bad Gateway")
        else:
            body = 'data: {"response": {"candidates": [{"content": {"parts": [{"text": "From sandbox"}]}}, {"finishReason": "STOP"}]}}\n\n'
            return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    mock_transport = httpx.MockTransport(handler)
    chunks = []
    async for item in client.stream_chat_with_transport(
        cca_payload={"model": "claude-sonnet-4-6"},
        access_token="test_tok",
        is_claude=True,
        transport=mock_transport,
        base_backoff_ms=10
    ):
        chunks.append(item)

    assert any("daily-cloudcode-pa.googleapis.com" in u and "sandbox" not in u for u in endpoints_called)
    assert any("sandbox.googleapis.com" in u for u in endpoints_called)
    texts = [c.get("content_delta") for c in chunks if c.get("content_delta")]
    assert "From sandbox" in texts

@pytest.mark.asyncio
async def test_client_raises_when_all_retries_empty():
    config = ProxyConfig()
    client = CloudCodeAssistClient(config)

    def handler(request: httpx.Request) -> httpx.Response:
        body = 'data: {"response": {"candidates": [{"finishReason": "STOP"}]}}\n\n'
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    mock_transport = httpx.MockTransport(handler)
    with pytest.raises(RuntimeError, match="empty-stream|Empty response"):
        async for _ in client.stream_chat_with_transport(
            cca_payload={"model": "claude-sonnet-4-6"},
            access_token="test_tok",
            is_claude=True,
            transport=mock_transport,
            base_backoff_ms=5
        ):
            pass
