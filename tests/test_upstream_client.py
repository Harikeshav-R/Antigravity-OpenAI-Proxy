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

@pytest.mark.asyncio
async def test_client_raises_upstream_quota_exhausted_on_429():
    from app.client import UpstreamQuotaExhaustedError
    config = ProxyConfig()
    client = CloudCodeAssistClient(config)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text='{"error": {"code": 429, "message": "Resource has been exhausted (e.g. check quota)."}}')

    mock_transport = httpx.MockTransport(handler)
    with pytest.raises(UpstreamQuotaExhaustedError) as exc_info:
        async for _ in client.stream_chat_with_transport(
            cca_payload={"model": "claude-sonnet-4-6"},
            access_token="test_tok",
            is_claude=True,
            transport=mock_transport,
            base_backoff_ms=5
        ):
            pass
    assert exc_info.value.status_code == 429
    assert "429" in str(exc_info.value)

@pytest.mark.asyncio
async def test_client_raises_upstream_quota_exhausted_on_403_quota():
    from app.client import UpstreamQuotaExhaustedError
    config = ProxyConfig()
    client = CloudCodeAssistClient(config)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text='{"error": "RESOURCE_EXHAUSTED: daily limit reached"}')

    mock_transport = httpx.MockTransport(handler)
    with pytest.raises(UpstreamQuotaExhaustedError) as exc_info:
        async for _ in client.stream_chat_with_transport(
            cca_payload={"model": "claude-sonnet-4-6"},
            access_token="test_tok",
            is_claude=True,
            transport=mock_transport,
            base_backoff_ms=5
        ):
            pass
    assert exc_info.value.status_code == 403

@pytest.mark.asyncio
async def test_client_raises_runtime_error_on_normal_403():
    from app.client import UpstreamQuotaExhaustedError
    config = ProxyConfig()
    client = CloudCodeAssistClient(config)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text='{"error": "PermissionDenied: permission cloudaicompanion.generateChat denied"}')

    mock_transport = httpx.MockTransport(handler)
    with pytest.raises(RuntimeError) as exc_info:
        async for _ in client.stream_chat_with_transport(
            cca_payload={"model": "claude-sonnet-4-6"},
            access_token="test_tok",
            is_claude=True,
            transport=mock_transport,
            base_backoff_ms=5
        ):
            pass
    assert not isinstance(exc_info.value, UpstreamQuotaExhaustedError)

@pytest.mark.asyncio
async def test_client_preserves_grounding_and_late_usage_events():
    config = ProxyConfig()
    client = CloudCodeAssistClient(config)

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"response": {"modelVersion": "gemini-3.8-flash-001", "candidates": [{"content": {"parts": [{"text": "Python 3.13 was released."}]}}]}}\n\n'
            'data: {"response": {"candidates": [{"finishReason": "STOP"}]}}\n\n'
            'data: {"response": {"candidates": [{"groundingMetadata": {"webSearchQueries": ["Python 3.13"], "groundingChunks": [{"web": {"uri": "https://python.org", "title": "Python"}}]}}]}}\n\n'
            'data: {"response": {"candidates": [], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20, "totalTokenCount": 30}}}\n\n'
        )
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    mock_transport = httpx.MockTransport(handler)
    chunks = []
    async for item in client.stream_chat_with_transport(
        cca_payload={"model": "gemini-3.8-flash"},
        access_token="test_tok",
        is_claude=False,
        transport=mock_transport,
        base_backoff_ms=5,
    ):
        chunks.append(item)

    # Must have yielded text, finish reason, grounding metadata, and usage
    texts = [c.get("content_delta") for c in chunks if c.get("content_delta")]
    assert "Python 3.13 was released." in texts

    grounding_items = [c.get("grounding_metadata") for c in chunks if c.get("grounding_metadata")]
    assert len(grounding_items) == 1
    assert grounding_items[0]["webSearchQueries"] == ["Python 3.13"]

    model_versions = [c.get("model_version") for c in chunks if c.get("model_version")]
    assert "gemini-3.8-flash-001" in model_versions

    raw_finishes = [c.get("raw_finish_reason") for c in chunks if c.get("raw_finish_reason")]
    assert "STOP" in raw_finishes

    usages = [c.get("usage") for c in chunks if c.get("usage")]
    assert len(usages) == 1
    assert usages[0] == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}

@pytest.mark.asyncio
async def test_client_treats_metadata_only_google_search_as_meaningful():
    config = ProxyConfig()
    client = CloudCodeAssistClient(config)

    attempt = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        body = (
            'data: {"response": {"candidates": [{"finishReason": "STOP", "groundingMetadata": {"webSearchQueries": ["query"], "groundingChunks": [{"web": {"uri": "https://example.com"}}]}}]}}\n\n'
        )
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    mock_transport = httpx.MockTransport(handler)
    chunks = []
    async for item in client.stream_chat_with_transport(
        cca_payload={"request": {"tools": [{"googleSearch": {}}]}},
        access_token="test_tok",
        is_claude=False,
        transport=mock_transport,
        base_backoff_ms=5,
    ):
        chunks.append(item)

    # Should not retry; single attempt was meaningful
    assert attempt == 1
    assert len(chunks) == 1
    assert chunks[0].get("grounding_metadata") is not None

@pytest.mark.asyncio
async def test_client_raises_on_in_band_quota_error_after_partial_text():
    from app.client import UpstreamQuotaExhaustedError
    config = ProxyConfig()
    client = CloudCodeAssistClient(config)

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"response": {"candidates": [{"content": {"parts": [{"text": "Partial text"}]}}]}}\n\n'
            'data: {"error": {"code": 429, "message": "Resource has been exhausted (quota)."}}\n\n'
        )
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    mock_transport = httpx.MockTransport(handler)
    with pytest.raises(UpstreamQuotaExhaustedError) as exc_info:
        async for _ in client.stream_chat_with_transport(
            cca_payload={"model": "gemini-3.8-flash"},
            access_token="test_tok",
            is_claude=False,
            transport=mock_transport,
            base_backoff_ms=5,
        ):
            pass
    assert exc_info.value.status_code == 429

@pytest.mark.asyncio
async def test_client_raises_on_prompt_feedback_block():
    from app.client import UpstreamResponseError
    config = ProxyConfig()
    client = CloudCodeAssistClient(config)

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"response": {"promptFeedback": {"blockReason": "SAFETY"}}}\n\n'
        )
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    mock_transport = httpx.MockTransport(handler)
    with pytest.raises(UpstreamResponseError) as exc_info:
        async for _ in client.stream_chat_with_transport(
            cca_payload={"model": "gemini-3.8-flash"},
            access_token="test_tok",
            is_claude=False,
            transport=mock_transport,
            base_backoff_ms=5,
        ):
            pass
    assert "SAFETY" in str(exc_info.value)

@pytest.mark.asyncio
async def test_iter_sse_json_spec_compliance():
    from app.client import iter_sse_json, UpstreamResponseError

    async def gen_lines():
        # Covers: data:{...} (no space), CRLF, comments, multiline data, [DONE], empty line, unterminated final event
        lines = [
            ": keep-alive comment\r\n",
            "event: message\r\n",
            'data:{"key1": "val1"}\r\n',
            "\r\n",
            "data: {\n",
            'data: "key2": "val2"\n',
            "data: }\n",
            "\n",
            "data: [DONE]\n",
            "\n",
            "data: \n",
            "\n",
            'data: {"final": true}',  # unterminated event at EOF
        ]
        for l in lines:
            yield l

    results = []
    async for payload in iter_sse_json(gen_lines()):
        results.append(payload)

    assert len(results) == 3
    assert results[0] == {"key1": "val1"}
    assert results[1] == {"key2": "val2"}
    assert results[2] == {"final": True}

    # Malformed JSON raises UpstreamResponseError
    async def bad_json_lines():
        yield "data: {bad-json}\n"
        yield "\n"

    with pytest.raises(UpstreamResponseError, match="Malformed upstream SSE JSON"):
        async for _ in iter_sse_json(bad_json_lines()):
            pass

    # Non-dict JSON raises UpstreamResponseError
    async def array_json_lines():
        yield "data: [1, 2, 3]\n"
        yield "\n"

    with pytest.raises(UpstreamResponseError, match="must be a JSON object"):
        async for _ in iter_sse_json(array_json_lines()):
            pass
