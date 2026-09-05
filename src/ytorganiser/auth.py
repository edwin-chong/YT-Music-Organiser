"""OAuth for the YouTube Data API v3 (installed-app flow)."""
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube"]


def _paths():
    client_secret_file = os.environ.get("YOUTUBE_CLIENT_SECRET_FILE", "client_secret.json")
    token_file = os.environ.get("YOUTUBE_TOKEN_FILE", "token.json")
    return client_secret_file, token_file


def get_credentials() -> Credentials:
    client_secret_file, token_file = _paths()
    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(client_secret_file):
                raise FileNotFoundError(
                    f"Missing {client_secret_file}. Download an OAuth 'Desktop app' "
                    "client secret from the Google Cloud Console and place it here "
                    "(see README)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_file, "w") as f:
            f.write(creds.to_json())

    return creds


def get_service():
    return build("youtube", "v3", credentials=get_credentials())
