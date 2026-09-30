import json
import logging
import random
import time
import uuid
from typing import Dict, Any, List
from app.config import get_wire_model_profile
from app.translator.schema import normalize_schema_for_cca

logger = logging.getLogger(__name__)

def random_signed_decimal_session_id() -> str:
    # 63-bit random negative decimal string
    val = random.randint(1, 9_000_000_000_000_000_000)
    return f"-{val}"

def translate_openai_request(req: Dict[str, Any], project_id: str) -> Dict[str, Any]:
    model_name = req.get("model", "claude-3.7-sonnet")
    effort = req.get("reasoning_effort")
    profile = get_wire_model_profile(model_name, reasoning_effort=effort)

    contents: List[Dict[str, Any]] = []
    system_parts: List[Dict[str, str]] = []

    for msg in req.get("messages", []):
        role = msg.get("role")
        content = msg.get("content", "")

        if role == "system":
            if isinstance(content, str) and content.strip():
                system_parts.append({"text": content})
        elif role == "user":
            if isinstance(content, str):
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif isinstance(content, list):
                parts = []
                for p in content:
                    if p.get("type") == "text":
                        parts.append({"text": p.get("text", "")})
                    elif p.get("type") == "image_url":
                        url = p.get("image_url", {}).get("url", "")
                        if url.startswith("data:"):
                            mime = url.split(";")[0].replace("data:", "")
                            data = url.split("base64,")[1]
                            parts.append({"inlineData": {"mimeType": mime, "data": data}})
                contents.append({"role": "user", "parts": parts})
        elif role == "assistant":
            parts = []
            if isinstance(content, str) and content.strip():
                parts.append({"text": content})
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                raw_args = fn.get("arguments", "{}")
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except (json.JSONDecodeError, TypeError) as err:
                        logger.debug("Failed to decode tool arguments JSON: %s", err)
                        args = {}
                elif isinstance(raw_args, dict):
                    args = raw_args
                else:
                    args = {}

                part_item: Dict[str, Any] = {
                    "functionCall": {
                        "name": fn.get("name"),
                        "args": args,
                    }
                }
                if tc.get("id"):
                    part_item["functionCall"]["id"] = tc.get("id")

                # Preserve thoughtSignature
                sig = (
                    tc.get("thought_signature") or
                    tc.get("thoughtSignature") or
                    msg.get("thought_signature") or
                    msg.get("thoughtSignature")
                )
                if sig:
                    part_item["thoughtSignature"] = sig

                parts.append(part_item)

            contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            tool_name = msg.get("name", "tool")
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": tool_name,
                        "response": {"result": content}
                    }
                }]
            })

    generation_config: Dict[str, Any] = {}
    if "temperature" in req:
        generation_config["temperature"] = req["temperature"]
    if "top_p" in req:
        generation_config["topP"] = req["top_p"]

    max_tokens = req.get("max_tokens") if req.get("max_tokens") is not None else req.get("max_completion_tokens")
    if max_tokens is not None:
        generation_config["maxOutputTokens"] = min(int(max_tokens), profile.max_output_tokens)
    else:
        generation_config["maxOutputTokens"] = profile.max_output_tokens

    labels: Dict[str, str] = {
        "trajectory_id": str(uuid.uuid4()),
        "last_step_index": "1",
    }
    if profile.is_claude:
        labels["used_claude"] = "true"
        labels["used_claude_conservative"] = "true"
    if profile.model_enum:
        labels["model_enum"] = profile.model_enum

    agent_id = str(uuid.uuid4())
    req_id = f"agent/{agent_id}/{int(time.time() * 1000)}/{labels['trajectory_id']}/2"

    inner_request: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": generation_config,
        "labels": labels,
        "sessionId": random_signed_decimal_session_id(),
    }

    if system_parts:
        inner_request["systemInstruction"] = {
            "role": "user",
            "parts": system_parts
        }

    # Tools
    tools = req.get("tools")
    if tools:
        declarations = []
        for t in tools:
            fn = t.get("function", {})
            raw_params = fn.get("parameters", {"type": "object", "properties": {}})
            norm_params = normalize_schema_for_cca(raw_params)
            declarations.append({
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "parameters": norm_params
            })
        inner_request["tools"] = [{"functionDeclarations": declarations}]

    if profile.is_claude:
        inner_request["toolConfig"] = {"functionCallingConfig": {"mode": "VALIDATED"}}

    return {
        "project": project_id,
        "model": profile.wire_model_id,
        "userAgent": "antigravity",
        "requestType": "agent",
        "requestId": req_id,
        "request": inner_request
    }
