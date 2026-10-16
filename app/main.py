from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import ProxyConfig
from app.auth.manager import AuthManager
from app.client import CloudCodeAssistClient
from app.routes.models import router as models_router
from app.routes.chat import router as chat_router
from app.routes.auth_routes import router as auth_router
from app.routes.search import router as search_router, SearchAuthError
from app.search.service import SearchService, SearchServiceError
from fastapi import Request
from fastapi.responses import JSONResponse

def create_app() -> FastAPI:
    config = ProxyConfig()
    auth_manager = AuthManager(config.credentials_path, pool_strategy=config.pool_strategy)
    cca_client = CloudCodeAssistClient(config)
    search_service = SearchService(config, auth_manager, cca_client)

    app = FastAPI(title="Antigravity OpenAI Proxy")
    app.state.config = config
    app.state.auth_manager = auth_manager
    app.state.cca_client = cca_client
    app.state.search_service = search_service

    @app.exception_handler(SearchAuthError)
    async def search_auth_handler(request: Request, exc: SearchAuthError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.msg}},
            headers=exc.headers,
        )

    @app.exception_handler(SearchServiceError)
    async def search_service_handler(request: Request, exc: SearchServiceError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(models_router)
    app.include_router(chat_router)
    app.include_router(auth_router)
    app.include_router(search_router)
    return app

app = create_app()
