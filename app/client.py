import asyncio
import json
import logging
from typing import Dict, Any, AsyncIterator, Optional, Set
import httpx
from app.config import ProxyConfig
from app.translator.sanitizers import PlanningLeakFilter
from app.translator.egress import parse_cca_chunk

logger = logging.getLogger(__name__)

class UpstreamQuotaExhaustedError(RuntimeError):
    def __init__(self, message: str, status_code: int = 429):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
class UpstreamResponseError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code

async def iter_sse_json(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, object]]:
    data_lines: list[str] = []

    def _flush_data() -> dict[str, object] | None:
        nonlocal data_lines
        if not data_lines:
            return None
        joined = "\n".join(data_lines)
        data_lines = []
        if not joined or joined.strip() == "[DONE]":
            return None
        try:
            payload = json.loads(joined)
        except Exception as e:
            raise UpstreamResponseError(f"Malformed upstream SSE JSON: {e}", status_code=502) from e

        if not isinstance(payload, dict):
            raise UpstreamResponseError(
                f"Upstream SSE payload must be a JSON object, got {type(payload).__name__}",
                status_code=502,
            )
        return payload

    async for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if not line:
            item = _flush_data()
            if item is not None:
                yield item
            continue

        if line.startswith(":"):
            continue

        if line.startswith("data:"):
            val = line[5:]
            if val.startswith(" "):
                val = val[1:]
            data_lines.append(val)
            continue

        continue

    item = _flush_data()
    if item is not None:
        yield item
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
            transport=getattr(self, "_transport", None),
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

        has_google_search = False
        req_tools = cca_payload.get("request", {}).get("tools", [])
        if not req_tools and "tools" in cca_payload:
            req_tools = cca_payload.get("tools", [])
        if isinstance(req_tools, list):
            for t in req_tools:
                if isinstance(t, dict) and "googleSearch" in t:
                    has_google_search = True
                    break

        for endpoint_url in endpoints:
            try:
                for empty_attempt in range(self.max_empty_retries + 1):
                    if empty_attempt > 0:
                        delay_s = (base_backoff_ms * (2 ** (empty_attempt - 1))) / 1000.0
                        await asyncio.sleep(delay_s)

                    leak_filter = PlanningLeakFilter(tool_names or set())
                    received_meaningful = False
                    buffered_chunks = []
                    current_thought_sig = None

                    client_kwargs: Dict[str, Any] = {"timeout": 300.0}
                    if transport:
                        client_kwargs["transport"] = transport

                    async with httpx.AsyncClient(**client_kwargs) as client:
                        async with client.stream("POST", endpoint_url, headers=headers, json=cca_payload) as resp:
                            if resp.status_code >= 500 or resp.status_code == 404:
                                err_text = await resp.aread()
                                last_error = UpstreamResponseError(
                                    f"Endpoint {endpoint_url} returned {resp.status_code}: {err_text.decode()}",
                                    status_code=resp.status_code,
                                )
                                break  # Fail over to next endpoint

                            if resp.status_code == 429:
                                err_text = await resp.aread()
                                raise UpstreamQuotaExhaustedError(
                                    f"Google Cloud Code Assist 429: {err_text.decode()}",
                                    status_code=429,
                                )

                            if resp.status_code == 403:
                                err_text = await resp.aread()
                                body_str = err_text.decode()
                                if "RESOURCE_EXHAUSTED" in body_str or "quota" in body_str.lower():
                                    raise UpstreamQuotaExhaustedError(
                                        f"Google Cloud Code Assist quota exceeded (403): {body_str}",
                                        status_code=403,
                                    )
                                raise UpstreamResponseError(
                                    f"Cloud Code Assist returned 403: {body_str}",
                                    status_code=403,
                                )

                            if resp.status_code != 200:
                                err_text = await resp.aread()
                                raise UpstreamResponseError(
                                    f"Cloud Code Assist returned {resp.status_code}: {err_text.decode()}",
                                    status_code=resp.status_code,
                                )

                            async for raw_chunk in iter_sse_json(resp.aiter_lines()):
                                if "error" in raw_chunk:
                                    err = raw_chunk["error"]
                                    if isinstance(err, dict):
                                        err_code = err.get("code")
                                        err_msg = str(err.get("message", ""))
                                        err_status = str(err.get("status", ""))
                                    else:
                                        err_code = None
                                        err_msg = str(err)
                                        err_status = ""
                                    err_str = f"{err_msg} {err_status}".lower()
                                    if err_code == 429 or "resource_exhausted" in err_str or "quota" in err_str:
                                        raise UpstreamQuotaExhaustedError(
                                            f"Google Cloud Code Assist in-band quota error: {err_msg or err}",
                                            status_code=429 if err_code != 403 else 403,
                                        )
                                    status_code = err_code if isinstance(err_code, int) and err_code >= 400 else 502
                                    raise UpstreamResponseError(
                                        f"Google Cloud Code Assist in-band error: {err_msg or err}",
                                        status_code=status_code,
                                    )

                                resp_obj = raw_chunk.get("response") if isinstance(raw_chunk.get("response"), dict) else raw_chunk
                                prompt_feedback = resp_obj.get("promptFeedback") if isinstance(resp_obj, dict) else None
                                candidates = resp_obj.get("candidates") if isinstance(resp_obj, dict) else None

                                if prompt_feedback and not candidates:
                                    block_reason = prompt_feedback.get("blockReason", "UNKNOWN") if isinstance(prompt_feedback, dict) else "UNKNOWN"
                                    raise UpstreamResponseError(
                                        f"Prompt blocked by upstream safety/policy: {block_reason} ({prompt_feedback})",
                                        status_code=400,
                                    )

                                content, reasoning, tool_calls, finish, usage, thought_sig = parse_cca_chunk(raw_chunk, leak_filter)
                                if thought_sig:
                                    current_thought_sig = thought_sig

                                if tool_calls and current_thought_sig:
                                    for tc in tool_calls:
                                        if not tc.get("thought_signature"):
                                            tc["thought_signature"] = current_thought_sig

                                candidate = (candidates or [{}])[0]
                                grounding_meta = candidate.get("groundingMetadata")
                                model_version = resp_obj.get("modelVersion") if isinstance(resp_obj, dict) else None
                                raw_finish = candidate.get("finishReason")

                                if (content and content.strip()) or reasoning or tool_calls:
                                    received_meaningful = True
                                elif has_google_search and grounding_meta:
                                    if (
                                        grounding_meta.get("groundingChunks")
                                        or grounding_meta.get("webSearchQueries")
                                        or grounding_meta.get("groundingSupports")
                                    ):
                                        received_meaningful = True

                                chunk_dict = {
                                    "content_delta": content,
                                    "reasoning_delta": reasoning,
                                    "tool_call_delta": tool_calls,
                                    "finish_reason": finish,
                                    "usage": usage,
                                    "thought_signature": thought_sig or current_thought_sig,
                                }
                                if grounding_meta is not None:
                                    chunk_dict["grounding_metadata"] = grounding_meta
                                if model_version is not None:
                                    chunk_dict["model_version"] = model_version
                                if raw_finish is not None:
                                    chunk_dict["raw_finish_reason"] = raw_finish

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
                        last_error = UpstreamResponseError(
                            f"Exhausted empty-stream retries on {endpoint_url}",
                            status_code=502,
                        )
                        break
            except (httpx.ConnectError, httpx.TimeoutException) as conn_err:
                last_error = conn_err
                continue

        if last_error:
            raise last_error
