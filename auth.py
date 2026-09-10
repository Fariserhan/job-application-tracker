import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Resolve next to this file so the CLI works from any working directory.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")
CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")


def _save_token(creds: Credentials):
    """Atomic write so a crash mid-save can never corrupt token.json."""
    tmp = TOKEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as token_file:
        token_file.write(creds.to_json())
    os.replace(tmp, TOKEN_PATH)


def get_credentials() -> Credentials:
    creds = None
    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        except (ValueError, OSError):
            creds = None  # corrupt/partial token file — fall through to a fresh OAuth flow

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None  # refresh token revoked/expired — re-authenticate
        if not creds or not creds.valid:
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(
                    f"{CREDENTIALS_PATH} not found. Download it from Google Cloud Console "
                    "(APIs & Services > Credentials > OAuth client ID > Desktop app)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        _save_token(creds)

    return creds
