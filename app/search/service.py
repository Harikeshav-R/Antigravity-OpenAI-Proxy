import asyncio
import logging
from typing import Mapping, Optional, Set
import httpx
from app.config import ProxyConfig, WireModelProfile, get_wire_model_profile, ANTIGRAVITY_PROFILES
from app.auth.manager import AuthManager
from app.client import CloudCodeAssistClient, UpstreamQuotaExhaustedError, UpstreamResponseError
from app.translator.ingress import translate_openai_request
from app.search.models import SearchRequest, SearchResponse
from app.search.grounding import GroundingAccumulator

logger = logging.getLogger(__name__)

class SearchServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

def validate_search_model(search_model: str) -> WireModelProfile:
    profile = get_wire_model_profile(search_model)
    known_wire_ids = {p.wire_model_id for p in ANTIGRAVITY_PROFILES.values()}
    if profile.wire_model_id not in known_wire_ids or not profile.wire_model_id.startswith("gemini-"):
        raise SearchServiceError(
            503,
            "search_model_unavailable",
            "SEARCH_MODEL must select a configured Gemini model",
        )
    return profile

def build_search_payload(request: SearchRequest, project_id: str, model: str) -> dict[str, object]:
    openai_req: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": request.query}],
        "max_tokens": 4096,
    }
    payload = translate_openai_request(openai_req, project_id=project_id)
    inner_req = payload.get("request")
    if isinstance(inner_req, dict):
        inner_req["tools"] = [{"googleSearch": {}}]
        inner_req.pop("toolConfig", None)
    return payload

class SearchService:
    def __init__(
        self,
        config: ProxyConfig,
        auth_manager: AuthManager,
        cca_client: CloudCodeAssistClient,
    ) -> None:
        self.config = config
        self.auth_manager = auth_manager
        self.cca_client = cca_client

    async def search(self, request: SearchRequest) -> SearchResponse:
        profile = validate_search_model(self.config.search_model)
        try:
            return await asyncio.wait_for(
                self._execute_search(request, profile),
                timeout=self.config.search_timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise SearchServiceError(504, "search_timeout", "Grounded search timed out")

    async def _execute_search(
        self, request: SearchRequest, profile: WireModelProfile
    ) -> SearchResponse:
        pool = self.auth_manager.get_account_pool()
        if not pool:
            raise SearchServiceError(
                503,
                "search_credentials_unavailable",
                "No Antigravity account configured",
            )

        pool_size = max(1, len(pool))
        tried_ids: Set[str] = set()

        for attempt in range(pool_size):
            try:
                creds = await self.auth_manager.get_next_available_credentials(exclude_ids=tried_ids)
            except RuntimeError as e:
                err_msg = str(e).lower()
                if "quota-exhausted" in err_msg or "excluded" in err_msg:
                    raise SearchServiceError(
                        429,
                        "search_quota_exhausted",
                        "All Antigravity accounts are quota-exhausted",
                    )
                if "refresh" in err_msg:
                    raise SearchServiceError(
                        503,
                        "search_credentials_unavailable",
                        "Antigravity credentials could not be refreshed",
                    )
                raise SearchServiceError(
                    503,
                    "search_credentials_unavailable",
                    "Antigravity credentials could not be refreshed",
                )

            acc_id = self.auth_manager.account_id(creds)
            payload = build_search_payload(request, project_id=creds.project_id, model=profile.wire_model_id)
            accumulator = GroundingAccumulator(wire_model=profile.wire_model_id)

            try:
                async for item in self.cca_client.stream_chat(
                    cca_payload=payload,
                    access_token=creds.access_token,
                    is_claude=False,
                    tool_names=set(),
                ):
                    accumulator.feed(item)

                return accumulator.finish(query=request.query, max_results=request.max_results)

            except UpstreamQuotaExhaustedError:
                tried_ids.add(acc_id)
                self.auth_manager.mark_quota_exhausted(creds)
                logger.warning("Search account %s quota exhausted. Failing over to next pool account.", acc_id)
                continue
            except UpstreamResponseError as e:
                logger.warning("Upstream response error during search: %s", e)
                raise SearchServiceError(
                    502,
                    "search_upstream_error",
                    "Google grounded search failed",
                )
            except httpx.HTTPError as e:
                logger.warning("HTTP error during search: %s", e)
                raise SearchServiceError(
                    502,
                    "search_upstream_error",
                    "Google grounded search failed",
                )

        raise SearchServiceError(
            429,
            "search_quota_exhausted",
            "All Antigravity accounts are quota-exhausted",
        )
