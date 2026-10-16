import asyncio
import logging
import secrets
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from app.config import ProxyConfig
from app.search.models import SearchRequest, SearchResponse
from app.search.service import SearchService, SearchServiceError

logger = logging.getLogger(__name__)

router = APIRouter()

class SearchAuthError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message}, headers=headers)
        self.code = code
        self.msg = message

def verify_search_api_key(request: Request) -> None:
    config: ProxyConfig = request.app.state.config
    configured_key = config.search_api_key
    if not configured_key:
        raise SearchAuthError(
            status_code=503,
            code="search_disabled",
            message="SEARCH_API_KEY is not configured",
        )

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise SearchAuthError(
            status_code=401,
            code="unauthorized",
            message="Invalid search API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header[7:].strip()
    if not token:
        raise SearchAuthError(
            status_code=401,
            code="unauthorized",
            message="Invalid search API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not secrets.compare_digest(token.encode("utf-8"), configured_key.encode("utf-8")):
        raise SearchAuthError(
            status_code=401,
            code="unauthorized",
            message="Invalid search API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def _disconnect_watcher(request: Request) -> None:
    while True:
        await asyncio.sleep(0.1)
        if await request.is_disconnected():
            return
@router.post("/v1/search", response_model=SearchResponse)
async def search_endpoint(
    request: Request,
    body: SearchRequest,
    _auth: None = Depends(verify_search_api_key),
):
    search_service: SearchService = request.app.state.search_service

    search_task = asyncio.create_task(search_service.search(body))
    watcher_task = asyncio.create_task(_disconnect_watcher(request))
    done, pending = await asyncio.wait(
        [search_task, watcher_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    try:
        if watcher_task in done:
            search_task.cancel()
            try:
                await search_task
            except (asyncio.CancelledError, Exception):
                pass
            return JSONResponse(
                status_code=499,
                content={"error": {"code": "client_closed_request", "message": "Client disconnected"}},
            )

        watcher_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(watcher_task), timeout=0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass

        return await search_task

    except SearchServiceError as e:
        return JSONResponse(
            status_code=e.status_code,
            content={"error": {"code": e.code, "message": e.message}},
        )
    finally:
        for t in (search_task, watcher_task):
            if not t.done():
                t.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(t), timeout=0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
