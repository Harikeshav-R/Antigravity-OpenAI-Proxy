from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import ProxyConfig
from app.auth.manager import AuthManager
from app.client import CloudCodeAssistClient
from app.routes.models import router as models_router
from app.routes.chat import router as chat_router
from app.routes.auth_routes import router as auth_router

def create_app() -> FastAPI:
    config = ProxyConfig()
    auth_manager = AuthManager(config.credentials_path, pool_strategy=config.pool_strategy)
    cca_client = CloudCodeAssistClient(config)

    app = FastAPI(title="Antigravity OpenAI Proxy")
    app.state.config = config
    app.state.auth_manager = auth_manager
    app.state.cca_client = cca_client

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
    return app

app = create_app()
