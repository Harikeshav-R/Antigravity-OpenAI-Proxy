#!/usr/bin/env python3
"""
Imports Antigravity CLI credentials from the system keyring into credentials.json.
Supports macOS Keychain.
"""
import base64
import json
import os
import subprocess
import sys

def main():
    target_path = os.getenv("CREDENTIALS_PATH", "credentials.json")
    print(f"Reading Antigravity CLI credentials from macOS Keychain...")

    try:
        raw = subprocess.check_output(
            ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception as e:
        print(f"Error: Could not retrieve 'gemini/antigravity' item from macOS Keychain: {e}")
        print("Ensure you have logged into Antigravity CLI on this machine.")
        sys.exit(1)

    if raw.startswith("go-keyring-base64:"):
        raw = raw[len("go-keyring-base64:"):]

    try:
        data = json.loads(base64.b64decode(raw).decode())
    except Exception as e:
        print(f"Error decoding keychain payload: {e}")
        sys.exit(1)

    token_info = data.get("token", {})
    refresh_token = token_info.get("refresh_token")
    access_token = token_info.get("access_token", "")

    if not refresh_token:
        print("Error: No refresh token found in Antigravity CLI credentials.")
        sys.exit(1)

    email = None
    if "id_token" in data:
        parts = data["id_token"].split(".")
        if len(parts) >= 2:
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            try:
                email = json.loads(base64.b64decode(padded).decode()).get("email")
            except Exception:
                pass

    expiry_str = token_info.get("expiry")
    expires_at = 0
    if expiry_str:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(expiry_str)
            expires_at = int(dt.timestamp() * 1000)
        except Exception:
            expires_at = 0

    project_id = os.getenv("ANTIGRAVITY_PROJECT_ID")
    if not project_id and access_token:
        try:
            import asyncio
            from app.auth.oauth import discover_project_id
            project_id = asyncio.run(discover_project_id(access_token))
        except Exception:
            project_id = "default-cli-project"
    elif not project_id:
        project_id = "default-cli-project"

    new_cred = {
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "project_id": project_id,
        "expires_at": expires_at
    }

    # Load existing accounts to avoid overwriting multi-account pools
    existing = []
    if os.path.exists(target_path):
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                content = json.load(f)
                if isinstance(content, list):
                    existing = content
                elif isinstance(content, dict):
                    existing = [content]
        except Exception:
            existing = []

    # Update matching or append
    matched = False
    for i, acc in enumerate(existing):
        if email and acc.get("email") == email:
            existing[i] = new_cred
            matched = True
            break

    if not matched:
        existing.append(new_cred)

    target_dir = os.path.dirname(os.path.abspath(target_path))
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)

    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)

    print(f"Successfully imported Antigravity CLI account ({email or 'default'}) into {target_path}!")

if __name__ == "__main__":
    main()
