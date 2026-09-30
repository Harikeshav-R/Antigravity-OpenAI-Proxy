import asyncio
import logging
import time
from typing import Optional, List, Dict, Set, Any
from app.auth.storage import CredentialStorage, Credentials
from app.auth.oauth import refresh_access_token

logger = logging.getLogger(__name__)

class AuthManager:
    def __init__(self, credentials_path: str):
        self.storage = CredentialStorage(credentials_path)
        self._accounts: List[Credentials] = []
        self._locks: Dict[str, asyncio.Lock] = {}
        self._cooldowns: Dict[str, float] = {}
        self._round_robin_idx: int = 0
        self._pool_lock = asyncio.Lock()
        self._initialized: bool = False

    def _account_id(self, acc: Credentials) -> str:
        return acc.email or acc.project_id or (acc.refresh_token[:8] if acc.refresh_token else "")

    def _get_lock(self, acc_id: str) -> asyncio.Lock:
        if acc_id not in self._locks:
            self._locks[acc_id] = asyncio.Lock()
        return self._locks[acc_id]

    def _ensure_loaded(self) -> None:
        if not self._initialized:
            self._accounts = self.storage.load_all()
            self._initialized = True

    @property
    def _cached(self) -> Optional[Credentials]:
        self._ensure_loaded()
        return self._accounts[0] if self._accounts else None

    @_cached.setter
    def _cached(self, val: Optional[Credentials]) -> None:
        self._ensure_loaded()
        if val is None:
            self._accounts = []
        else:
            if self._accounts:
                self._accounts[0] = val
            else:
                self._accounts = [val]

    async def _perform_refresh(self, refresh_token: str):
        return await refresh_access_token(refresh_token)

    def get_account_pool(self) -> List[Credentials]:
        self._ensure_loaded()
        return list(self._accounts)

    async def get_next_available_credentials(self, exclude_ids: Optional[Set[str]] = None) -> Credentials:
        self._ensure_loaded()
        if not self._accounts:
            raise RuntimeError("No Antigravity credentials configured. Use /login or mount credentials.json.")

        exclude = exclude_ids or set()
        now = time.time()

        eligible = [
            acc for acc in self._accounts
            if self._account_id(acc) not in exclude
        ]
        if not eligible:
            raise RuntimeError("All Antigravity accounts in pool are excluded")

        active_accounts = [
            acc for acc in eligible
            if now >= self._cooldowns.get(self._account_id(acc), 0.0)
        ]

        if not active_accounts:
            soonest_acc = min(eligible, key=lambda a: self._cooldowns.get(self._account_id(a), 0.0))
            if now >= self._cooldowns.get(self._account_id(soonest_acc), 0.0):
                selected = soonest_acc
            else:
                raise RuntimeError("All Antigravity accounts in pool are quota-exhausted")
        else:
            idx = self._round_robin_idx % len(active_accounts)
            selected = active_accounts[idx]
            self._round_robin_idx = (idx + 1) % len(active_accounts)

        if not selected.is_expired():
            return selected

        acc_id = self._account_id(selected)
        lock = self._get_lock(acc_id)
        async with lock:
            if not selected.is_expired():
                return selected
            new_token, expires_at = await self._perform_refresh(selected.refresh_token)
            selected.access_token = new_token
            selected.expires_at = expires_at
            try:
                self.storage.save_all(self._accounts)
            except OSError as err:
                logger.warning("Failed to persist refreshed credentials to %s: %s", self.storage.path, err)
            return selected

    async def get_credentials(self) -> Credentials:
        return await self.get_next_available_credentials()

    def mark_quota_exhausted(self, account: Credentials, cooldown_seconds: int = 600) -> None:
        acc_id = self._account_id(account)
        self._cooldowns[acc_id] = time.time() + cooldown_seconds

    def add_or_update_account(self, account: Credentials) -> None:
        self._ensure_loaded()
        matched_idx = -1
        for idx, acc in enumerate(self._accounts):
            if account.email and acc.email and acc.email.lower() == account.email.lower():
                matched_idx = idx
                break
            if account.project_id and acc.project_id == account.project_id:
                if not account.email or not acc.email or account.email.lower() == acc.email.lower():
                    matched_idx = idx
                    break
        if matched_idx >= 0:
            self._accounts[matched_idx] = account
        else:
            self._accounts.append(account)
        self.storage.save_all(self._accounts)

    def save_new_credentials(self, credentials: Credentials) -> None:
        self.add_or_update_account(credentials)

    def remove_account(self, identifier: str) -> bool:
        self._ensure_loaded()
        target_idx = -1
        for idx, acc in enumerate(self._accounts):
            if (acc.email and acc.email.lower() == identifier.lower()) or \
               (acc.project_id and acc.project_id == identifier) or \
               (self._account_id(acc) == identifier):
                target_idx = idx
                break
        if target_idx >= 0:
            removed = self._accounts.pop(target_idx)
            rem_id = self._account_id(removed)
            self._locks.pop(rem_id, None)
            self._cooldowns.pop(rem_id, None)
            self.storage.save_all(self._accounts)
            return True
        return False

    def get_pool_status(self) -> List[Dict[str, Any]]:
        self._ensure_loaded()
        now = time.time()
        status_list = []
        for acc in self._accounts:
            acc_id = self._account_id(acc)
            cooldown_until = self._cooldowns.get(acc_id, 0.0)
            is_cooling = now < cooldown_until
            rem = max(0, int(cooldown_until - now)) if is_cooling else 0
            status_list.append({
                "email": acc.email,
                "project_id": acc.project_id,
                "is_cooling_down": is_cooling,
                "cooldown_remaining_seconds": rem,
                "is_expired": acc.is_expired(),
            })
        return status_list
