import logging
import os
import time
import httpx
from typing import Tuple, Optional, Dict, Any

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

def get_oauth_client_credentials(
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None
) -> Tuple[str, str]:
    cid = (
        client_id
        or os.getenv("GOOGLE_CLIENT_ID")
        or os.getenv("google_client_id")
        or ""
    ).strip()
    sec = (
        client_secret
        or os.getenv("GOOGLE_CLIENT_SECRET")
        or os.getenv("google_client_secret")
        or ""
    ).strip()
    return cid, sec

def get_authorization_url(redirect_uri: str, state: Optional[str] = None, client_id: Optional[str] = None) -> str:
    cid, _ = get_oauth_client_credentials(client_id=client_id)
    if not cid:
        raise RuntimeError("GOOGLE_CLIENT_ID is required for OAuth login. Set it in environment variables or .env.")

    params = {
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    if state:
        params["state"] = state
    query = "&".join(f"{k}={httpx.URL('', params={k: v}).query.decode()}" for k, v in params.items())
    return f"{AUTH_URL}?{query}"

async def exchange_code_for_tokens(
    code: str,
    redirect_uri: str,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None
) -> Tuple[str, str, int, Optional[str]]:
    cid, sec = get_oauth_client_credentials(client_id, client_secret)
    if not cid or not sec:
        raise RuntimeError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required for OAuth token exchange.")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": cid,
                "client_secret": sec,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        access_token = payload["access_token"]
        refresh_token = payload.get("refresh_token", "")
        expires_in = payload.get("expires_in", 3600)
        expires_at = int((time.time() + expires_in) * 1000)

        email = None
        try:
            user_resp = await client.get(
                "https://www.googleapis.com/oauth2/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=5.0
            )
            if user_resp.status_code == 200:
                email = user_resp.json().get("email")
        except httpx.HTTPError as err:
            logger.warning("Could not fetch user profile email during OAuth exchange: %s", err)

        return access_token, refresh_token, expires_at, email

async def refresh_access_token(
    refresh_token: str,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None
) -> Tuple[str, int]:
    cid, sec = get_oauth_client_credentials(client_id, client_secret)
    if not cid or not sec:
        raise RuntimeError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required to refresh OAuth access tokens.")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": cid,
                "client_secret": sec,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        access_token = payload["access_token"]
        expires_in = payload.get("expires_in", 3600)
        expires_at = int((time.time() + expires_in) * 1000)
        return access_token, expires_at

def parse_project_id_from_load_code_assist(payload: Dict[str, Any]) -> Optional[str]:
    proj = payload.get("cloudaicompanionProject")
    return proj if proj and len(proj) > 0 else None

async def discover_project_id(access_token: str, base_url: str = "https://daily-cloudcode-pa.googleapis.com") -> str:
    url = f"{base_url}/v1internal:loadCodeAssist"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "antigravity/hub/2.8.0 (aidev_client; os_type=darwin; arch=arm64; cl=963137146)",
    }
    body = {"metadata": {"ideType": "ANTIGRAVITY"}}
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=body, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        proj = parse_project_id_from_load_code_assist(data)
        if not proj:
            raise RuntimeError(f"Could not discover cloudaicompanionProject from loadCodeAssist: {data}")
        return proj
