import json
import time
import uuid
from typing import Optional, Dict, Any, List, Tuple
from app.translator.sanitizers import PlanningLeakFilter

def format_openai_sse_chunk(
    chat_id: str,
    model: str,
    content_delta: Optional[str] = None,
    reasoning_delta: Optional[str] = None,
    tool_call_delta: Optional[List[Dict[str, Any]]] = None,
    finish_reason: Optional[str] = None,
    usage: Optional[Dict[str, int]] = None
) -> Dict[str, Any]:
    delta: Dict[str, Any] = {}
    if content_delta is not None:
        delta["content"] = content_delta
    if reasoning_delta is not None:
        delta["reasoning_content"] = reasoning_delta
    if tool_call_delta is not None:
        delta["tool_calls"] = tool_call_delta

    choice: Dict[str, Any] = {
        "index": 0,
        "delta": delta,
        "finish_reason": finish_reason
    }

    payload: Dict[str, Any] = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [choice]
    }
    if usage:
        payload["usage"] = usage
    return payload

def build_openai_completion_response(
    chat_id: str,
    model: str,
    content: str,
    reasoning: Optional[str],
    tool_calls: Optional[List[Dict[str, Any]]],
    finish_reason: str,
    usage: Dict[str, int]
) -> Dict[str, Any]:
    message: Dict[str, Any] = {
        "role": "assistant",
        "content": content
    }
    if reasoning:
        message["reasoning_content"] = reasoning
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": chat_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason
        }],
        "usage": usage
    }

def parse_cca_chunk(
    chunk: Dict[str, Any],
    leak_filter: PlanningLeakFilter
) -> Tuple[Optional[str], Optional[str], Optional[List[Dict[str, Any]]], Optional[str], Optional[Dict[str, int]], Optional[str]]:
    candidate = chunk.get("response", {}).get("candidates", [{}])[0]
    parts = candidate.get("content", {}).get("parts", [])

    content_delta: Optional[str] = None
    reasoning_delta: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    thought_sig: Optional[str] = None

    for part in parts:
        sig = part.get("thoughtSignature")
        if sig:
            thought_sig = sig

        if "thought" in part:
            thought_val = part.get("thought")
            thought_text = part.get("text", "") if not isinstance(thought_val, str) else thought_val
            reasoning_delta = (reasoning_delta or "") + thought_text
        elif "text" in part:
            filtered = leak_filter.filter_text(part["text"])
            if filtered:
                content_delta = (content_delta or "") + filtered
        elif "functionCall" in part:
            fn = part["functionCall"]
            tc_index = len(tool_calls) if tool_calls is not None else 0
            tc: Dict[str, Any] = {
                "index": tc_index,
                "id": fn.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                "type": "function",
                "function": {
                    "name": fn.get("name"),
                    "arguments": json.dumps(fn.get("args", {}))
                }
            }
            if sig:
                tc["thought_signature"] = sig
            if tool_calls is None:
                tool_calls = []
            tool_calls.append(tc)

    finish_reason: Optional[str] = None
    raw_finish = candidate.get("finishReason")
    if raw_finish:
        if raw_finish == "STOP":
            finish_reason = "stop"
        elif raw_finish == "TOOL_CALL":
            finish_reason = "tool_calls"
        elif raw_finish == "MAX_TOKENS":
            finish_reason = "length"
        else:
            finish_reason = raw_finish.lower()
    usage: Optional[Dict[str, int]] = None
    usage_meta = chunk.get("response", {}).get("usageMetadata")
    if usage_meta:
        prompt_tokens = usage_meta.get("promptTokenCount", 0)
        completion_tokens = usage_meta.get("candidatesTokenCount", 0)
        total_tokens = usage_meta.get("totalTokenCount", prompt_tokens + completion_tokens)
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens
        }

    return content_delta, reasoning_delta, tool_calls, finish_reason, usage, thought_sig
