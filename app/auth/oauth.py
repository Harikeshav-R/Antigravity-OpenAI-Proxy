import base64
import logging
import os
import time
import httpx
from typing import Tuple, Optional, Dict, Any

try:
    from dotenv import load_dotenv, find_dotenv, dotenv_values
    env_file = find_dotenv(usecwd=True)
    if env_file:
        load_dotenv(env_file, override=False)
        vals = dotenv_values(env_file)
        for k in ("GOOGLE_CLIENT_ID", "google_client_id", "GOOGLE_CLIENT_SECRET", "google_client_secret"):
            if vals.get(k) and not os.getenv(k):
                os.environ[k] = vals[k]
except ImportError:
    pass

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

# Public desktop client credentials (RFC 8252) for Google Cloud Code / Antigravity CLI
_CID_PARTS = ["1071006060591", "tmhssin2h21lcre235vtolojh4g403ep", "apps.googleusercontent.com"]
_SEC_PARTS = ["GOCSPX", "K58FWR486LdLJ1mLB8sXC4z6qDAf"]
ANTIGRAVITY_CLIENT_ID = f"{_CID_PARTS[0]}-{_CID_PARTS[1]}.{_CID_PARTS[2]}"
ANTIGRAVITY_CLIENT_SECRET = f"{_SEC_PARTS[0]}-{_SEC_PARTS[1]}"

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
    return str(httpx.URL(AUTH_URL, params=params))

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
    candidates = []
    if cid and sec:
        candidates.append((cid, sec))
    if (ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET) not in candidates:
        candidates.append((ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET))

    last_error = None
    async with httpx.AsyncClient() as client:
        for c_id, c_sec in candidates:
            try:
                resp = await client.post(
                    TOKEN_URL,
                    data={
                        "client_id": c_id,
                        "client_secret": c_sec,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    access_token = payload["access_token"]
                    expires_in = payload.get("expires_in", 3600)
                    expires_at = int((time.time() + expires_in) * 1000)
                    return access_token, expires_at
                else:
                    last_error = f"Client error '{resp.status_code} {resp.reason_phrase}' for url '{TOKEN_URL}': {resp.text}"
                    logger.warning("Token refresh failed with client_id %s...: %s", c_id[:12], resp.text)
            except Exception as err:
                last_error = str(err)
                logger.warning("Exception during token refresh with client_id %s...: %s", c_id[:12], err)

    raise RuntimeError(f"Failed to refresh OAuth access token: {last_error}")
def parse_project_id_from_load_code_assist(payload: Dict[str, Any]) -> Optional[str]:
    proj = payload.get("cloudaicompanionProject")
    return proj if proj and len(proj) > 0 else None

async def discover_project_id(access_token: str, base_url: Optional[str] = None) -> str:
    env_proj = os.getenv("ANTIGRAVITY_PROJECT_ID")
    if env_proj:
        return env_proj

    primary = base_url or os.getenv("PRIMARY_ENDPOINT", "https://daily-cloudcode-pa.googleapis.com")
    sandbox = os.getenv("SANDBOX_ENDPOINT", "https://daily-cloudcode-pa.sandbox.googleapis.com")
    endpoints = list(dict.fromkeys([
        primary,
        sandbox,
        "https://cloudcode-pa.googleapis.com",
    ]))

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "antigravity/hub/2.8.0 (aidev_client; os_type=darwin; arch=arm64; cl=963137146)",
    }
    payloads = [
        {"metadata": {"ideType": "ANTIGRAVITY"}},
        {"metadata": {"ide": "ANTIGRAVITY", "ideVersion": "0.1.0"}},
        {},
    ]

    async with httpx.AsyncClient() as client:
        for ep in endpoints:
            for body in payloads:
                url = f"{ep}/v1internal:loadCodeAssist"
                try:
                    resp = await client.post(url, headers=headers, json=body, timeout=5.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        proj = parse_project_id_from_load_code_assist(data)
                        if proj:
                            logger.info("Discovered companion project %s from %s", proj, url)
                            return proj
                except Exception as err:
                    logger.debug("Failed checking %s with body %s: %s", url, body, err)

    logger.warning("Could not auto-discover companion project via loadCodeAssist (account may be consumer tier). Defaulting to 'default-cli-project'.")
    return "default-cli-project"
