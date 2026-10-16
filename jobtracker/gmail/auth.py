"""OAuth 2.0 (installed-app flow) with an atomically cached, auto-refreshed token."""
from __future__ import annotations

import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_credentials(credentials_path: Path, token_path: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except (ValueError, OSError):
            creds = None  # corrupt / partial token file — fall through to a fresh flow
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None  # refresh token revoked or expired — re-authenticate
    if not creds or not creds.valid:
        if not credentials_path.exists():
            raise FileNotFoundError(
                f"{credentials_path} not found. Create an OAuth client (Desktop app) in Google "
                "Cloud Console -> APIs & Services -> Credentials, download the JSON and save it "
                "there. See docs/setup.md.")
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = token_path.with_suffix(".tmp")
    tmp.write_text(creds.to_json(), encoding="utf-8")
    os.replace(tmp, token_path)
    return creds
