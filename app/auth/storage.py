from dataclasses import dataclass, asdict
import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class Credentials:
    access_token: str
    refresh_token: str
    project_id: str
    expires_at: int
    email: Optional[str] = None

    def is_expired(self, skew_ms: int = 60000) -> bool:
        if self.expires_at == 0:
            return True
        return (time.time() * 1000) + skew_ms >= self.expires_at

class CredentialStorage:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> Optional[Credentials]:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return Credentials(
                        access_token=data.get("access_token", data.get("token", "")),
                        refresh_token=data.get("refresh_token", data.get("refreshToken", "")),
                        project_id=data.get("project_id", data.get("projectId", "")),
                        expires_at=int(data.get("expires_at", data.get("expiresAt", 0))),
                        email=data.get("email"),
                    )
            except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as err:
                logger.warning("Failed to load credentials from %s: %s", self.path, err)

        env_ref = os.getenv("ANTIGRAVITY_REFRESH_TOKEN")
        env_proj = os.getenv("ANTIGRAVITY_PROJECT_ID")
        env_acc = os.getenv("ANTIGRAVITY_ACCESS_TOKEN", "")
        if env_ref and env_proj:
            return Credentials(access_token=env_acc, refresh_token=env_ref, project_id=env_proj, expires_at=0)
        return None

    def save(self, credentials: Credentials) -> None:
        target_dir = os.path.dirname(os.path.abspath(self.path))
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(asdict(credentials), f, indent=2)
