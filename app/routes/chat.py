import json
import logging
import uuid
from typing import Dict, Any, List
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from app.config import get_wire_model_profile
from app.translator.ingress import translate_openai_request
from app.translator.egress import format_openai_sse_chunk, build_openai_completion_response

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    auth_manager = request.app.state.auth_manager
    cca_client = request.app.state.cca_client

    try:
        creds = await auth_manager.get_credentials()
    except Exception as e:
        logger.warning("Failed to obtain Antigravity credentials: %s", e)
        raise HTTPException(status_code=401, detail=str(e))

    body = await request.json()
    model_name = body.get("model", "claude-3.7-sonnet")
    stream = body.get("stream", False)
    chat_id = f"chatcmpl-{uuid.uuid4().hex}"

    profile = get_wire_model_profile(model_name)
    cca_payload = translate_openai_request(body, project_id=creds.project_id)
    tool_names = {t.get("function", {}).get("name", "") for t in body.get("tools", [])}

    if stream:
        async def sse_generator():
            try:
                async for item in cca_client.stream_chat(
                    cca_payload=cca_payload,
                    access_token=creds.access_token,
                    is_claude=profile.is_claude,
                    tool_names=tool_names
                ):
                    out = format_openai_sse_chunk(
                        chat_id=chat_id,
                        model=model_name,
                        content_delta=item.get("content_delta"),
                        reasoning_delta=item.get("reasoning_delta"),
                        tool_call_delta=item.get("tool_call_delta"),
                        finish_reason=item.get("finish_reason"),
                        usage=item.get("usage")
                    )
                    yield f"data: {json.dumps(out)}\n\n"
            except Exception as e:
                logger.error("Error in streaming chat completion: %s", e, exc_info=True)
                err_payload = {"error": {"message": str(e), "type": "upstream_error"}}
                yield f"data: {json.dumps(err_payload)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse_generator(), media_type="text/event-stream")

    # Non-streaming: assemble in-memory
    accumulated_text = ""
    accumulated_reasoning = ""
    accumulated_tool_calls: List[Dict[str, Any]] = []
    finish_reason = "stop"
    final_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    try:
        async for item in cca_client.stream_chat(
            cca_payload=cca_payload,
            access_token=creds.access_token,
            is_claude=profile.is_claude,
            tool_names=tool_names
        ):
            if item.get("content_delta"):
                accumulated_text += item["content_delta"]
            if item.get("reasoning_delta"):
                accumulated_reasoning += item["reasoning_delta"]
            if item.get("tool_call_delta"):
                accumulated_tool_calls.extend(item["tool_call_delta"])
            if item.get("finish_reason"):
                finish_reason = item["finish_reason"]
            if item.get("usage"):
                final_usage = item["usage"]
    except Exception as e:
        logger.error("Error in non-streaming chat completion: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail=str(e))

    resp_obj = build_openai_completion_response(
        chat_id=chat_id,
        model=model_name,
        content=accumulated_text,
        reasoning=accumulated_reasoning or None,
        tool_calls=accumulated_tool_calls or None,
        finish_reason=finish_reason,
        usage=final_usage
    )
    return JSONResponse(content=resp_obj)
