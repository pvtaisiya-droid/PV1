from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx


GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
DEFAULT_MICROSOFT_SCOPES = "offline_access User.Read Mail.ReadWrite Mail.Send"
OUTLOOK_ATTACHMENT_SIMPLE_LIMIT_BYTES = 3 * 1024 * 1024
_TOKEN_CACHE: dict[str, dict] = {}


class OutlookConfigurationError(RuntimeError):
    pass


class OutlookAuthorizationError(RuntimeError):
    pass


class OutlookGraphError(RuntimeError):
    pass


class OutlookAttachmentTooLargeError(OutlookGraphError):
    pass


def microsoft_config() -> dict[str, str]:
    return {
        "client_id": os.getenv("MICROSOFT_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("MICROSOFT_CLIENT_SECRET", "").strip(),
        "tenant_id": os.getenv("MICROSOFT_TENANT_ID", "").strip() or "common",
        "redirect_uri": os.getenv("MICROSOFT_REDIRECT_URI", "").strip(),
        "scopes": os.getenv("MICROSOFT_SCOPES", DEFAULT_MICROSOFT_SCOPES).strip()
        or DEFAULT_MICROSOFT_SCOPES,
    }


def is_configured() -> bool:
    config = microsoft_config()
    return bool(config["client_id"] and config["client_secret"] and config["redirect_uri"])


def is_authorized(user_key: str = "default") -> bool:
    token_data = _TOKEN_CACHE.get(user_key)
    return bool(
        token_data
        and (token_data.get("refresh_token") or token_data.get("access_token"))
    )


def require_configured() -> dict[str, str]:
    config = microsoft_config()
    missing = [
        name
        for name, value in {
            "MICROSOFT_CLIENT_ID": config["client_id"],
            "MICROSOFT_CLIENT_SECRET": config["client_secret"],
            "MICROSOFT_REDIRECT_URI": config["redirect_uri"],
        }.items()
        if not value
    ]
    if missing:
        raise OutlookConfigurationError(
            "Microsoft Graph is not configured. Missing: " + ", ".join(missing)
        )
    return config


def get_auth_url(state: str | None = None) -> str:
    config = require_configured()
    params = {
        "client_id": config["client_id"],
        "response_type": "code",
        "redirect_uri": config["redirect_uri"],
        "response_mode": "query",
        "scope": config["scopes"],
    }
    if state:
        params["state"] = state
    return (
        f"https://login.microsoftonline.com/{config['tenant_id']}/oauth2/v2.0/authorize?"
        + urlencode(params)
    )


def handle_callback(
    *,
    code: str,
    state: str | None = None,
    user_key: str = "default",
) -> dict:
    config = require_configured()
    response = httpx.post(
        token_url(config),
        data={
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config["redirect_uri"],
            "scope": config["scopes"],
        },
        timeout=30,
    )
    token_data = parse_token_response(response)
    token_data["state"] = state
    _TOKEN_CACHE[user_key] = token_data
    return token_data


def get_access_token(user_key: str = "default") -> str:
    token_data = _TOKEN_CACHE.get(user_key)
    if not token_data:
        raise OutlookAuthorizationError("Microsoft authorization is required.")
    if token_data.get("access_token") and token_data.get("expires_at", 0) > time.time() + 60:
        return token_data["access_token"]
    return refresh_access_token(user_key=user_key)


def refresh_access_token(user_key: str = "default") -> str:
    config = require_configured()
    token_data = _TOKEN_CACHE.get(user_key)
    refresh_token = token_data.get("refresh_token") if token_data else None
    if not refresh_token:
        raise OutlookAuthorizationError("Microsoft refresh token is not available.")
    response = httpx.post(
        token_url(config),
        data={
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "redirect_uri": config["redirect_uri"],
            "scope": config["scopes"],
        },
        timeout=30,
    )
    refreshed = parse_token_response(response)
    if "refresh_token" not in refreshed:
        refreshed["refresh_token"] = refresh_token
    _TOKEN_CACHE[user_key] = refreshed
    return refreshed["access_token"]


def create_outlook_draft(
    *,
    subject: str,
    body: str,
    to_recipients: list[str],
    cc_recipients: list[str] | None = None,
    user_key: str = "default",
) -> dict:
    access_token = get_access_token(user_key=user_key)
    payload = {
        "subject": subject,
        "body": {
            "contentType": "Text",
            "content": body,
        },
        "toRecipients": recipient_payload(to_recipients),
        "ccRecipients": recipient_payload(cc_recipients or []),
    }
    response = httpx.post(
        f"{GRAPH_BASE_URL}/me/messages",
        headers=graph_headers(access_token),
        json=payload,
        timeout=30,
    )
    return parse_graph_response(response)


def add_attachment_to_draft(
    *,
    message_id: str,
    file_path: str | Path,
    attachment_name: str | None = None,
    user_key: str = "default",
) -> dict:
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        raise OutlookGraphError("Attachment file was not found.")
    size = path.stat().st_size
    if size > OUTLOOK_ATTACHMENT_SIMPLE_LIMIT_BYTES:
        raise OutlookAttachmentTooLargeError(
            "Файл сверки больше 3 МБ. Для MVP поддерживаются вложения до 3 МБ."
        )
    access_token = get_access_token(user_key=user_key)
    payload = {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": attachment_name or path.name,
        "contentBytes": base64.b64encode(path.read_bytes()).decode("ascii"),
    }
    response = httpx.post(
        f"{GRAPH_BASE_URL}/me/messages/{message_id}/attachments",
        headers=graph_headers(access_token),
        json=payload,
        timeout=30,
    )
    return parse_graph_response(response)


def send_outlook_message(
    *,
    message_id: str,
    user_key: str = "default",
) -> None:
    access_token = get_access_token(user_key=user_key)
    response = httpx.post(
        f"{GRAPH_BASE_URL}/me/messages/{message_id}/send",
        headers=graph_headers(access_token),
        timeout=30,
    )
    if response.status_code not in {202, 204}:
        raise OutlookGraphError(graph_error_text(response))


def get_message_web_link_if_available(message: dict | None) -> str | None:
    if not message:
        return None
    return message.get("webLink")


def token_url(config: dict[str, str]) -> str:
    return f"https://login.microsoftonline.com/{config['tenant_id']}/oauth2/v2.0/token"


def parse_token_response(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        raise OutlookAuthorizationError(graph_error_text(response))
    data = response.json()
    expires_in = int(data.get("expires_in") or 3600)
    data["expires_at"] = time.time() + expires_in
    return data


def parse_graph_response(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        raise OutlookGraphError(graph_error_text(response))
    if not response.content:
        return {}
    return response.json()


def graph_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def graph_error_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or f"Microsoft Graph error {response.status_code}"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return error.get("message") or error.get("code") or str(error)
    return str(payload)


def recipient_payload(emails: list[str]) -> list[dict]:
    return [
        {"emailAddress": {"address": email}}
        for email in emails
        if (email or "").strip()
    ]
