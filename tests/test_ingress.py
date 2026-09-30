# tests/test_ingress.py
from app.translator.schema import normalize_schema_for_cca
from app.translator.ingress import translate_openai_request

def test_normalize_schema_for_cca():
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "SearchQuery",
        "description": "Parameters for search",
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "title": "QueryText",
                "description": "Search keyword"
            },
            "limit": {
                "type": "integer"
            }
        },
        "required": ["query"],
        "additionalProperties": False
    }
    normalized = normalize_schema_for_cca(schema)
    assert "$schema" not in normalized
    assert "additionalProperties" not in normalized
    assert normalized["type"] == "object"
    assert "query" in normalized["properties"]
    assert "limit" in normalized["properties"]

def test_translate_openai_messages():
    req = {
        "model": "claude-3.7-sonnet",
        "messages": [
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": "Write a test."},
            {
                "role": "assistant",
                "content": "Sure, calling tool...",
                "tool_calls": [{
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "run_test", "arguments": '{"cmd": "pytest"}'},
                    "thought_signature": "crypto_sig_abc"
                }]
            },
            {
                "role": "tool",
                "name": "run_test",
                "tool_call_id": "call_123",
                "content": "All tests passed"
            }
        ]
    }
    cca = translate_openai_request(req, project_id="test-proj")
    assert cca["project"] == "test-proj"
    assert cca["model"] == "claude-sonnet-4-6"
    assert cca["userAgent"] == "antigravity"

    # System instruction
    sys_inst = cca["request"]["systemInstruction"]
    assert sys_inst["role"] == "user"
    assert sys_inst["parts"][0]["text"] == "You are a coding assistant."

    # Contents
    contents = cca["request"]["contents"]
    assert len(contents) == 3
    # User turn
    assert contents[0]["role"] == "user"
    assert contents[0]["parts"][0]["text"] == "Write a test."
    # Assistant turn with thoughtSignature preserved
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["text"] == "Sure, calling tool..."
    fc_part = contents[1]["parts"][1]
    assert "functionCall" in fc_part
    assert fc_part["functionCall"]["name"] == "run_test"
    assert fc_part["functionCall"]["args"] == {"cmd": "pytest"}
    assert fc_part["thoughtSignature"] == "crypto_sig_abc"
    # Tool response turn
    assert contents[2]["role"] == "user"
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "run_test"
    assert contents[2]["parts"][0]["functionResponse"]["response"] == {"result": "All tests passed"}

def test_translate_claude_caps_and_validated_mode():
    req = {
        "model": "claude-3.7-sonnet",
        "max_tokens": 100000,  # Exceeds 64k cap
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{
            "type": "function",
            "function": {"name": "test_fn", "parameters": {"type": "object", "properties": {}}}
        }]
    }
    cca = translate_openai_request(req, project_id="test-proj")
    # Must clamp to 64000
    assert cca["request"]["generationConfig"]["maxOutputTokens"] == 64000
    # Must enforce VALIDATED mode
    assert cca["request"]["toolConfig"]["functionCallingConfig"]["mode"] == "VALIDATED"
    # Must set claude labels
    assert cca["request"]["labels"]["used_claude"] == "true"
    assert cca["request"]["labels"]["used_claude_conservative"] == "true"

def test_translate_max_completion_tokens_support():
    req = {
        "model": "gemini-3.8-flash",
        "max_completion_tokens": 500,
        "messages": [{"role": "user", "content": "hi"}]
    }
    cca = translate_openai_request(req, project_id="test-proj")
    assert cca["request"]["generationConfig"]["maxOutputTokens"] == 500
    assert cca["request"]["labels"]["model_enum"] == "MODEL_PLACEHOLDER_M318"
