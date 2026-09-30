from fastapi import APIRouter
from app.config import ANTIGRAVITY_PROFILES

router = APIRouter()

@router.get("/v1/models")
async def list_models():
    models = []
    for model_id in ANTIGRAVITY_PROFILES.keys():
        models.append({
            "id": model_id,
            "object": "model",
            "created": 1727650000,
            "owned_by": "google-antigravity"
        })
    return {"object": "list", "data": models}
