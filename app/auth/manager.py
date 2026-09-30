import asyncio
import logging
from typing import Optional
from app.auth.storage import CredentialStorage, Credentials
from app.auth.oauth import refresh_access_token

logger = logging.getLogger(__name__)

class AuthManager:
    def __init__(self, credentials_path: str):
        self.storage = CredentialStorage(credentials_path)
        self._cached: Optional[Credentials] = None
        self._lock = asyncio.Lock()

    async def _perform_refresh(self, refresh_token: str):
        return await refresh_access_token(refresh_token)

    async def get_credentials(self) -> Credentials:
        if self._cached is None:
            self._cached = self.storage.load()
            if not self._cached:
                raise RuntimeError("No Antigravity credentials configured. Use /login or mount credentials.json.")

        if not self._cached.is_expired():
            return self._cached

        async with self._lock:
            # Double-check inside lock
            if not self._cached.is_expired():
                return self._cached

            new_token, expires_at = await self._perform_refresh(self._cached.refresh_token)
            self._cached.access_token = new_token
            self._cached.expires_at = expires_at
            try:
                self.storage.save(self._cached)
            except OSError as err:
                logger.warning("Failed to persist refreshed credentials to %s: %s", self.storage.path, err)
            return self._cached

    def save_new_credentials(self, credentials: Credentials) -> None:
        self._cached = credentials
        self.storage.save(credentials)
