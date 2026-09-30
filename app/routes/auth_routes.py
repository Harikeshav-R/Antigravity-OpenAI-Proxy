import logging
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from app.auth.oauth import get_authorization_url, exchange_code_for_tokens, discover_project_id
from app.auth.storage import Credentials

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/status")
async def status(request: Request):
    auth_manager = request.app.state.auth_manager
    authenticated = False
    email = None
    project_id = None
    try:
        creds = await auth_manager.get_credentials()
        authenticated = True
        email = creds.email
        project_id = creds.project_id
    except RuntimeError as err:
        logger.debug("Proxy not currently authenticated: %s", err)

    return {
        "status": "ok",
        "service": "antigravity-proxy",
        "authenticated": authenticated,
        "email": email,
        "project_id": project_id
    }

@router.get("/login", response_class=HTMLResponse)
async def login(request: Request):
    host = request.headers.get("host", "localhost:8000")
    proto = "https" if request.url.is_secure else "http"
    redirect_uri = f"{proto}://{host}/oauth-callback"
    try:
        oauth_url = get_authorization_url(redirect_uri)
    except RuntimeError as err:
        return HTMLResponse(content=f"""
        <!DOCTYPE html>
        <html>
            <head><title>Antigravity Proxy Login - Configuration Required</title></head>
            <body style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:80px auto;text-align:center;line-height:1.6;">
                <h2>OAuth Configuration Required</h2>
                <p style="color:#d93025;background:#fce8e6;padding:12px;border-radius:6px;">{err}</p>
                <p style="font-size:0.9em;color:#666;">Set <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> in your environment, or mount a valid <code>credentials.json</code> directly.</p>
            </body>
        </html>
        """, status_code=200)

    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html>
        <head><title>Antigravity Proxy Login</title></head>
        <body style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:80px auto;text-align:center;line-height:1.6;">
            <h2>Google Antigravity Proxy</h2>
            <p>Sign in with your Google Account that holds an active Cloud Code Assist or Antigravity subscription.</p>
            <div style="margin:40px 0;">
                <a href="{oauth_url}" style="background:#1a73e8;color:white;padding:12px 24px;text-decoration:none;border-radius:6px;font-weight:bold;">Sign in with Google</a>
            </div>
            <p style="font-size:0.9em;color:#666;">Or mount <code>credentials.json</code> to your container / set <code>ANTIGRAVITY_REFRESH_TOKEN</code>.</p>
        </body>
    </html>
    """)

@router.get("/oauth-callback")
async def oauth_callback(request: Request, code: str = Query(...)):
    host = request.headers.get("host", "localhost:8000")
    proto = "https" if request.url.is_secure else "http"
    redirect_uri = f"{proto}://{host}/oauth-callback"

    auth_manager = request.app.state.auth_manager
    access_token, refresh_token, expires_at, email = await exchange_code_for_tokens(code, redirect_uri)
    project_id = await discover_project_id(access_token)

    creds = Credentials(
        access_token=access_token,
        refresh_token=refresh_token,
        project_id=project_id,
        expires_at=expires_at,
        email=email
    )
    auth_manager.save_new_credentials(creds)
    return RedirectResponse(url="/status")
