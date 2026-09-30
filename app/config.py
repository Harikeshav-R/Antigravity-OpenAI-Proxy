from dataclasses import dataclass
import os
from typing import Optional, Dict

@dataclass(frozen=True)
class WireModelProfile:
    wire_model_id: str
    is_claude: bool
    max_output_tokens: int
    model_enum: Optional[str] = None

ANTIGRAVITY_PROFILES: Dict[str, WireModelProfile] = {
    "claude-3.7-sonnet": WireModelProfile("claude-sonnet-4-6", is_claude=True, max_output_tokens=64000),
    "claude-sonnet-4-6": WireModelProfile("claude-sonnet-4-6", is_claude=True, max_output_tokens=64000),
    "claude-opus-4-6": WireModelProfile("claude-opus-4-6-thinking", is_claude=True, max_output_tokens=64000),
    "gemini-3.8-flash": WireModelProfile("gemini-3-flash-agent", is_claude=False, max_output_tokens=65536, model_enum="MODEL_PLACEHOLDER_M132"),
    "gemini-3-flash": WireModelProfile("gemini-3-flash-agent", is_claude=False, max_output_tokens=65536, model_enum="MODEL_PLACEHOLDER_M132"),
    "gemini-3.1-pro": WireModelProfile("gemini-pro-agent", is_claude=False, max_output_tokens=65535, model_enum="MODEL_PLACEHOLDER_M16"),
    "gemini-3-pro": WireModelProfile("gemini-pro-agent", is_claude=False, max_output_tokens=65535, model_enum="MODEL_PLACEHOLDER_M16"),
    "gemini-2.5-pro": WireModelProfile("gemini-2.5-pro", is_claude=False, max_output_tokens=65536),
    "gemini-2.5-flash": WireModelProfile("gemini-2.5-flash", is_claude=False, max_output_tokens=65536),
    "gemini-2.0-flash": WireModelProfile("gemini-2.0-flash", is_claude=False, max_output_tokens=65536),
    "gemini-2.0-flash-lite": WireModelProfile("gemini-2.0-flash-lite", is_claude=False, max_output_tokens=65536),
    "gemini-2.0-pro-exp": WireModelProfile("gemini-2.0-pro-exp", is_claude=False, max_output_tokens=65536),
    "gemini-1.5-pro": WireModelProfile("gemini-1.5-pro", is_claude=False, max_output_tokens=65536),
    "gemini-1.5-flash": WireModelProfile("gemini-1.5-flash", is_claude=False, max_output_tokens=65536),
}

def get_wire_model_profile(model_name: str) -> WireModelProfile:
    norm = model_name.lower().replace("openai/", "").replace("google/", "").replace("anthropic/", "")
    if norm in ANTIGRAVITY_PROFILES:
        return ANTIGRAVITY_PROFILES[norm]
    is_claude = "claude" in norm
    max_tokens = 64000 if is_claude else 65536
    return WireModelProfile(wire_model_id=norm, is_claude=is_claude, max_output_tokens=max_tokens)

class ProxyConfig:
    def __init__(self, credentials_path: Optional[str] = None):
        self.credentials_path = credentials_path or os.getenv("CREDENTIALS_PATH", "credentials.json")
        self.primary_endpoint = os.getenv("PRIMARY_ENDPOINT", "https://daily-cloudcode-pa.googleapis.com")
        self.sandbox_endpoint = os.getenv("SANDBOX_ENDPOINT", "https://daily-cloudcode-pa.sandbox.googleapis.com")
        self.port = int(os.getenv("PORT", "8000"))
        self.user_agent = "antigravity/hub/2.8.0 (aidev_client; os_type=darwin; arch=arm64; cl=963137146)"
