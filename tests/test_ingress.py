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

def test_normalize_schema_list_type_and_nullable():
    schema = {
        "type": "object",
        "properties": {
            "vec_weight": {
                "type": ["number", "null"],
                "description": "weight"
            },
            "mode": {
                "type": ["string", "null"]
            },
            "tag": {
                "type": "null"
            }
        }
    }
    norm = normalize_schema_for_cca(schema)
    assert norm["properties"]["vec_weight"]["type"] == "number"
    assert norm["properties"]["vec_weight"]["nullable"] is True
    assert norm["properties"]["mode"]["type"] == "string"
    assert norm["properties"]["mode"]["nullable"] is True
    assert norm["properties"]["tag"]["type"] == "string"
    assert norm["properties"]["tag"]["nullable"] is True

def test_normalize_schema_claude_flatten_and_strip_unions():
    schema = {
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {
                    "anyOf": [
                        {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "action": {"type": "string"}}
                        },
                        {
                            "type": "object",
                            "properties": {"content": {"type": "string"}}
                        }
                    ]
                }
            },
            "notify": {
                "anyOf": [
                    {"type": "boolean"},
                    {"type": "array", "items": {"type": "string"}}
                ]
            }
        }
    }
    norm_claude = normalize_schema_for_cca(schema, is_claude=True)
    assert norm_claude["properties"]["operations"]["items"]["type"] == "object"
    assert "name" in norm_claude["properties"]["operations"]["items"]["properties"]
    assert "content" in norm_claude["properties"]["operations"]["items"]["properties"]
    assert "anyOf" not in norm_claude["properties"]["operations"]["items"]
    assert "anyOf" not in norm_claude["properties"]["notify"]
    assert norm_claude["properties"]["notify"]["type"] == "boolean"

def test_translate_flat_tool_definitions():
    req = {
        "model": "gemini-3.8-flash",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "name": "flat_tool",
                "description": "flat tool description",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "val": {"type": ["integer", "null"]}
                    }
                }
            }
        ]
    }
    cca = translate_openai_request(req, project_id="test-proj")
    decls = cca["request"]["tools"][0]["functionDeclarations"]
    assert len(decls) == 1
    assert decls[0]["name"] == "flat_tool"
    assert decls[0]["description"] == "flat tool description"
    assert decls[0]["parameters"]["properties"]["val"]["type"] == "integer"
    assert decls[0]["parameters"]["properties"]["val"]["nullable"] is True
def test_translate_uses_cached_thought_signature():
    from app.translator.signatures import cache_thought_signature
    cache_thought_signature(call_id="call_cached_999", function_name="my_tool", signature="sig_crypto_secret")

    req = {
        "model": "gemini-3.8-flash",
        "messages": [
            {"role": "user", "content": "run tool"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_cached_999",
                        "type": "function",
                        "function": {"name": "my_tool", "arguments": "{}"}
                    }
                ]
            },
            {"role": "tool", "name": "my_tool", "content": "result"}
        ]
    }
    cca = translate_openai_request(req, project_id="test-proj")
    model_turn = cca["request"]["contents"][1]
    fc_part = model_turn["parts"][0]
    assert fc_part["thoughtSignature"] == "sig_crypto_secret"


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

def test_translate_reasoning_effort_support():
    req = {
        "model": "gemini-3.8-flash",
        "reasoning_effort": "low",
        "messages": [{"role": "user", "content": "hi"}]
    }
    cca = translate_openai_request(req, project_id="test-proj")
    assert cca["model"] == "gemini-3.8-flash-low"
    assert cca["request"]["labels"]["model_enum"] == "MODEL_PLACEHOLDER_M320"
