# tests/test_egress.py
from app.translator.sanitizers import PlanningLeakFilter
from app.translator.egress import (
    format_openai_sse_chunk,
    build_openai_completion_response,
    parse_cca_chunk,
)

def test_planning_leak_filter_strips_json_leak():
    f = PlanningLeakFilter(tool_names={"search", "bash"})
    # First chunk starts with leaked planning JSON
    c1 = f.filter_text('{"thought": "planning...", "command": "ls"}')
    assert c1 == ""
    # Second chunk contains actual text
    c2 = f.filter_text("Hello, world!")
    assert c2 == "Hello, world!"
    # Subsequent text passes directly
    c3 = f.filter_text(" How can I help?")
    assert c3 == " How can I help?"

def test_planning_leak_filter_preserves_valid_json_output():
    # If the model is outputting a regular JSON response that is NOT a leak
    f = PlanningLeakFilter(tool_names={"search"})
    c1 = f.filter_text('{"status": "ok", "items": [1, 2, 3]}')
    # Because it contains neither "thought" nor tool names nor "_i", it is preserved
    assert c1 == '{"status": "ok", "items": [1, 2, 3]}'

def test_planning_leak_filter_flush():
    f = PlanningLeakFilter(tool_names=set())
    # Incomplete chunk that stayed in buffer
    f.filter_text('{"some_key":')
    flushed = f.flush()
    assert flushed == '{"some_key":'

def test_format_openai_sse_chunk():
    chunk = format_openai_sse_chunk(
        chat_id="chatcmpl-123",
        model="claude-3.7-sonnet",
        content_delta="Hello",
        reasoning_delta=None,
        tool_call_delta=None,
        finish_reason=None
    )
    assert chunk["id"] == "chatcmpl-123"
    assert chunk["choices"][0]["delta"]["content"] == "Hello"
    assert chunk["choices"][0]["finish_reason"] is None

def test_build_openai_completion_response():
    resp = build_openai_completion_response(
        chat_id="chatcmpl-123",
        model="claude-3.7-sonnet",
        content="Final answer",
        reasoning="My thoughts",
        tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
        finish_reason="stop",
        usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
    )
    assert resp["id"] == "chatcmpl-123"
    assert resp["choices"][0]["message"]["content"] == "Final answer"
    assert resp["choices"][0]["message"]["reasoning_content"] == "My thoughts"
    assert len(resp["choices"][0]["message"]["tool_calls"]) == 1

def test_parse_cca_chunk_with_thought_signature():
    chunk = {
        "response": {
            "candidates": [{
                "content": {
                    "parts": [{
                        "functionCall": {"name": "test_tool", "args": {"x": 1}},
                        "thoughtSignature": "sig_xyz_123"
                    }]
                },
                "finishReason": "STOP"
            }],
            "usageMetadata": {
                "promptTokenCount": 15,
                "candidatesTokenCount": 25,
                "totalTokenCount": 40
            }
        }
    }
    filter_leak = PlanningLeakFilter(set())
    text, reasoning, tool_calls, finish, usage, thought_sig = parse_cca_chunk(chunk, filter_leak)
    assert text is None
    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "test_tool"
    assert tool_calls[0]["thought_signature"] == "sig_xyz_123"
    assert finish == "stop"
    assert usage["total_tokens"] == 40
    assert thought_sig == "sig_xyz_123"

def test_parse_cca_chunk_with_thought_and_text():
    chunk = {
        "response": {
            "candidates": [{
                "content": {
                    "parts": [{
                        "thought": True,
                        "text": "I am thinking about..."
                    }]
                }
            }]
        }
    }
    filter_leak = PlanningLeakFilter(set())
    text, reasoning, tool_calls, finish, usage, thought_sig = parse_cca_chunk(chunk, filter_leak)
    assert text is None
    assert reasoning == "I am thinking about..."

def test_parse_cca_chunk_max_tokens_mapped_to_length():
    chunk = {
        "response": {
            "candidates": [{
                "finishReason": "MAX_TOKENS"
            }]
        }
    }
    filter_leak = PlanningLeakFilter(set())
    _, _, _, finish, _, _ = parse_cca_chunk(chunk, filter_leak)
    assert finish == "length"

def test_parse_cca_chunk_parallel_tool_calls_indices():
    chunk = {
        "response": {
            "candidates": [{
                "content": {
                    "parts": [
                        {"functionCall": {"name": "tool_a", "args": {"a": 1}}},
                        {"functionCall": {"name": "tool_b", "args": {"b": 2}}}
                    ]
                }
            }]
        }
    }
    filter_leak = PlanningLeakFilter(set())
    _, _, tool_calls, _, _, _ = parse_cca_chunk(chunk, filter_leak)
    assert len(tool_calls) == 2
    assert tool_calls[0]["index"] == 0
    assert tool_calls[1]["index"] == 1
