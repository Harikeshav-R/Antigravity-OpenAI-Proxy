# tests/test_config.py
from app.config import get_wire_model_profile, ProxyConfig

def test_wire_model_profile_claude_sonnet():
    profile = get_wire_model_profile("claude-3.7-sonnet")
    assert profile.wire_model_id == "claude-sonnet-4-6"
    assert profile.is_claude is True
    assert profile.max_output_tokens == 64000
    assert profile.model_enum is None

def test_wire_model_profile_claude_opus():
    profile = get_wire_model_profile("claude-opus-4-6")
    assert profile.wire_model_id == "claude-opus-4-6-thinking"
    assert profile.is_claude is True
    assert profile.max_output_tokens == 64000

    profile_dot = get_wire_model_profile("claude-opus-4.6")
    assert profile_dot.wire_model_id == "claude-opus-4-6-thinking"
    assert profile_dot.is_claude is True

def test_wire_model_profile_claude_sonnet_dot():
    profile = get_wire_model_profile("claude-sonnet-4.6")
    assert profile.wire_model_id == "claude-sonnet-4-6"
    assert profile.is_claude is True
def test_wire_model_profile_gemini_flash():
    profile = get_wire_model_profile("gemini-3.8-flash")
    assert profile.wire_model_id == "gemini-3-flash-agent"
    assert profile.is_claude is False
    assert profile.model_enum == "MODEL_PLACEHOLDER_M132"
    assert profile.max_output_tokens == 65536

def test_wire_model_profile_gemini_pro():
    profile = get_wire_model_profile("gemini-3-pro")
    assert profile.wire_model_id == "gemini-pro-agent"
    assert profile.is_claude is False
    assert profile.model_enum == "MODEL_PLACEHOLDER_M16"
    assert profile.max_output_tokens == 65535

def test_wire_model_profile_gemini_31_pro():
    profile = get_wire_model_profile("gemini-3.1-pro")
    assert profile.wire_model_id in ("gemini-3.1-pro", "gemini-pro-agent")
    assert profile.is_claude is False
    assert profile.max_output_tokens == 65535

def test_wire_model_profile_gemini_25():
    profile = get_wire_model_profile("gemini-2.5-flash")
    assert profile.wire_model_id == "gemini-2.5-flash"
    assert profile.is_claude is False
    assert profile.max_output_tokens == 65536

def test_wire_model_profile_prefix_stripping():
    profile = get_wire_model_profile("openai/claude-3.7-sonnet")
    assert profile.wire_model_id == "claude-sonnet-4-6"
    profile2 = get_wire_model_profile("google/gemini-3.8-flash")
    assert profile2.wire_model_id == "gemini-3-flash-agent"

def test_default_config_paths():
    config = ProxyConfig(credentials_path="/tmp/test_cred.json")
    assert config.credentials_path == "/tmp/test_cred.json"
    assert config.primary_endpoint == "https://daily-cloudcode-pa.googleapis.com"
    assert config.sandbox_endpoint == "https://daily-cloudcode-pa.sandbox.googleapis.com"
    assert config.port == 8000
    assert "antigravity/hub/2.8.0" in config.user_agent
