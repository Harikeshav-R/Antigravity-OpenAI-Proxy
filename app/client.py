import asyncio
import json
import logging
from typing import Dict, Any, AsyncIterator, Optional, Set
import httpx
from app.config import ProxyConfig
from app.translator.sanitizers import PlanningLeakFilter
from app.translator.egress import parse_cca_chunk

logger = logging.getLogger(__name__)

class CloudCodeAssistClient:
    def __init__(self, config: ProxyConfig):
        self.config = config
        self.max_empty_retries = 3

    async def stream_chat(
        self,
        cca_payload: Dict[str, Any],
        access_token: str,
        is_claude: bool,
        tool_names: Optional[Set[str]] = None,
        base_backoff_ms: int = 1000
    ) -> AsyncIterator[Dict[str, Any]]:
        async for chunk in self.stream_chat_with_transport(
            cca_payload=cca_payload,
            access_token=access_token,
            is_claude=is_claude,
            tool_names=tool_names,
            transport=None,
            base_backoff_ms=base_backoff_ms
        ):
            yield chunk

    async def stream_chat_with_transport(
        self,
        cca_payload: Dict[str, Any],
        access_token: str,
        is_claude: bool,
        tool_names: Optional[Set[str]] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        base_backoff_ms: int = 1000
    ) -> AsyncIterator[Dict[str, Any]]:
        endpoints = [
            f"{self.config.primary_endpoint}/v1internal:streamGenerateContent?alt=sse",
            f"{self.config.sandbox_endpoint}/v1internal:streamGenerateContent?alt=sse",
        ]

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": self.config.user_agent,
        }
        if is_claude:
            headers["anthropic-beta"] = "interleaved-thinking-2025-05-14"

        last_error = None

        for endpoint_url in endpoints:
            try:
                for empty_attempt in range(self.max_empty_retries + 1):
                    if empty_attempt > 0:
                        delay_s = (base_backoff_ms * (2 ** (empty_attempt - 1))) / 1000.0
                        await asyncio.sleep(delay_s)

                    leak_filter = PlanningLeakFilter(tool_names or set())
                    received_meaningful = False
                    buffered_chunks = []

                    client_kwargs: Dict[str, Any] = {"timeout": 300.0}
                    if transport:
                        client_kwargs["transport"] = transport

                    async with httpx.AsyncClient(**client_kwargs) as client:
                        async with client.stream("POST", endpoint_url, headers=headers, json=cca_payload) as resp:
                            if resp.status_code >= 500 or resp.status_code == 404:
                                err_text = await resp.aread()
                                last_error = RuntimeError(f"Endpoint {endpoint_url} returned {resp.status_code}: {err_text.decode()}")
                                break  # Fail over to next endpoint

                            if resp.status_code != 200:
                                err_text = await resp.aread()
                                raise RuntimeError(f"Cloud Code Assist returned {resp.status_code}: {err_text.decode()}")

                            async for line in resp.aiter_lines():
                                if not line.startswith("data: "):
                                    continue
                                data_str = line[6:].strip()
                                if not data_str:
                                    continue
                                try:
                                    raw_chunk = json.loads(data_str)
                                except (json.JSONDecodeError, TypeError, ValueError) as err:
                                    logger.debug("Failed to decode SSE data line as JSON: %s", err)
                                    continue

                                content, reasoning, tool_calls, finish, usage, thought_sig = parse_cca_chunk(raw_chunk, leak_filter)
                                if (content and content.strip()) or reasoning or tool_calls:
                                    received_meaningful = True

                                chunk_dict = {
                                    "content_delta": content,
                                    "reasoning_delta": reasoning,
                                    "tool_call_delta": tool_calls,
                                    "finish_reason": finish,
                                    "usage": usage,
                                    "thought_signature": thought_sig,
                                }
                                buffered_chunks.append(chunk_dict)

                            # Flush any remaining buffer in leak_filter
                            tail = leak_filter.flush()
                            if tail:
                                received_meaningful = True
                                buffered_chunks.append({"content_delta": tail})

                    # If we received meaningful content, yield and return
                    if received_meaningful:
                        for c in buffered_chunks:
                            yield c
                        return

                    if empty_attempt == self.max_empty_retries:
                        last_error = RuntimeError(f"Exhausted empty-stream retries on {endpoint_url}")
                        break
            except (httpx.ConnectError, httpx.TimeoutException) as conn_err:
                last_error = conn_err
                continue

        if last_error:
            raise last_error
