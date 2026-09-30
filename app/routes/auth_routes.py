import logging
from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from app.auth.oauth import get_authorization_url, exchange_code_for_tokens, discover_project_id
from app.auth.storage import Credentials

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/status")
async def status(request: Request):
    auth_manager = request.app.state.auth_manager
    pool_status = auth_manager.get_pool_status()
    total_accounts = len(pool_status)
    active_accounts = sum(1 for acc in pool_status if not acc.get("is_cooling_down"))
    authenticated = total_accounts > 0
    first_email = pool_status[0]["email"] if pool_status else None
    first_project_id = pool_status[0]["project_id"] if pool_status else None

    return {
        "status": "ok",
        "service": "antigravity-proxy",
        "authenticated": authenticated,
        "total_accounts": total_accounts,
        "active_accounts": active_accounts,
        "email": first_email,
        "project_id": first_project_id,
        "accounts": pool_status
    }

@router.get("/login", response_class=HTMLResponse)
async def login(request: Request):
    auth_manager = request.app.state.auth_manager
    pool_status = auth_manager.get_pool_status()
    total_accounts = len(pool_status)

    host = request.headers.get("host", "localhost:8000")
    proto = "https" if request.url.is_secure else "http"
    redirect_uri = f"{proto}://{host}/oauth-callback"

    oauth_url = None
    config_err = None
    try:
        oauth_url = get_authorization_url(redirect_uri)
    except RuntimeError as err:
        config_err = str(err)

    accounts_html = ""
    if pool_status:
        accounts_html += f"<h3>Connected Accounts ({total_accounts})</h3><div style='text-align:left;border:1px solid #dadce0;border-radius:8px;padding:12px 16px;margin:20px 0;'>"
        for acc in pool_status:
            acc_name = acc.get("email") or acc.get("project_id") or "Unknown"
            if acc.get("is_cooling_down"):
                badge = f"<span style='background:#fce8e6;color:#c5221f;padding:2px 8px;border-radius:12px;font-size:0.8em;font-weight:bold;'>Cooldown ({acc.get('cooldown_remaining_seconds', 0)}s)</span>"
            else:
                badge = "<span style='background:#e6f4ea;color:#137333;padding:2px 8px;border-radius:12px;font-size:0.8em;font-weight:bold;'>Active</span>"
            accounts_html += f"<div style='display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #f1f3f4;'><div><strong>{acc_name}</strong><br/><small style='color:#5f6368;'>Project: {acc.get('project_id') or 'N/A'}</small></div><div>{badge}</div></div>"
        accounts_html += "</div>"
    else:
        accounts_html = "<p style='color:#5f6368;'>No Google accounts currently connected to the pool.</p>"

    btn_text = "Add Another Google Account" if total_accounts > 0 else "Sign in with Google"

    action_html = ""
    if oauth_url:
        action_html = f"""
        <div style="margin:30px 0;">
            <a href="{oauth_url}" style="background:#1a73e8;color:white;padding:12px 24px;text-decoration:none;border-radius:6px;font-weight:bold;">{btn_text}</a>
        </div>
        """
    else:
        action_html = f"""
        <div style="margin:30px 0;">
            <p style="color:#d93025;background:#fce8e6;padding:12px;border-radius:6px;">{config_err}</p>
            <p style="font-size:0.9em;color:#666;">Set <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> in your environment, or mount a valid <code>credentials.json</code> directly.</p>
        </div>
        """

    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html>
        <head><title>Antigravity Proxy Login</title></head>
        <body style="font-family:system-ui,-apple-system,sans-serif;max-width:640px;margin:60px auto;text-align:center;line-height:1.6;color:#202124;">
            <h2>Google Antigravity Proxy</h2>
            <p>Pool multiple Google accounts with Cloud Code Assist / Antigravity subscriptions for round-robin load balancing and quota failover.</p>
            {accounts_html}
            {action_html}
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
    auth_manager.add_or_update_account(creds)
    return RedirectResponse(url="/status")

@router.get("/v1/accounts")
async def get_accounts(request: Request):
    auth_manager = request.app.state.auth_manager
    pool_status = auth_manager.get_pool_status()
    return {
        "total_accounts": len(pool_status),
        "active_accounts": sum(1 for acc in pool_status if not acc.get("is_cooling_down")),
        "accounts": pool_status
    }

@router.delete("/v1/accounts/{identifier}")
async def delete_account(request: Request, identifier: str):
    auth_manager = request.app.state.auth_manager
    removed = auth_manager.remove_account(identifier)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Account '{identifier}' not found in pool")
    return {"status": "ok", "deleted": identifier}
