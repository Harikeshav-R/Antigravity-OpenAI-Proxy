from dataclasses import dataclass, asdict
import json
import logging
import os
import time
from typing import Optional, List

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

    def load_all(self) -> List[Credentials]:
        accounts: List[Credentials] = []
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                accounts.append(Credentials(
                                    access_token=item.get("access_token", item.get("token", "")),
                                    refresh_token=item.get("refresh_token", item.get("refreshToken", "")),
                                    project_id=item.get("project_id", item.get("projectId", "")),
                                    expires_at=int(item.get("expires_at", item.get("expiresAt", 0))),
                                    email=item.get("email"),
                                ))
                    elif isinstance(data, dict):
                        accounts.append(Credentials(
                            access_token=data.get("access_token", data.get("token", "")),
                            refresh_token=data.get("refresh_token", data.get("refreshToken", "")),
                            project_id=data.get("project_id", data.get("projectId", "")),
                            expires_at=int(data.get("expires_at", data.get("expiresAt", 0))),
                            email=data.get("email"),
                        ))
                    if accounts:
                        return accounts
            except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as err:
                logger.warning("Failed to load credentials from %s: %s", self.path, err)

        env_ref = os.getenv("ANTIGRAVITY_REFRESH_TOKEN")
        env_proj = os.getenv("ANTIGRAVITY_PROJECT_ID")
        env_acc = os.getenv("ANTIGRAVITY_ACCESS_TOKEN", "")
        if env_ref and env_proj:
            return [Credentials(access_token=env_acc, refresh_token=env_ref, project_id=env_proj, expires_at=0)]
        return []

    def load(self) -> Optional[Credentials]:
        accounts = self.load_all()
        return accounts[0] if accounts else None

    def save_all(self, accounts: List[Credentials]) -> None:
        target_dir = os.path.dirname(os.path.abspath(self.path))
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
        tmp_path = f"{self.path}.tmp.{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump([asdict(acc) for acc in accounts], f, indent=2)
        os.replace(tmp_path, self.path)

    def save(self, credentials: Credentials) -> None:
        accounts = self.load_all()
        matched_idx = -1
        for idx, acc in enumerate(accounts):
            if credentials.email and acc.email and acc.email.lower() == credentials.email.lower():
                matched_idx = idx
                break
            if credentials.project_id and acc.project_id == credentials.project_id:
                if not credentials.email or not acc.email or credentials.email.lower() == acc.email.lower():
                    matched_idx = idx
                    break

        if matched_idx >= 0:
            accounts[matched_idx] = credentials
        else:
            accounts.append(credentials)
        self.save_all(accounts)
