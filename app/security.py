"""Request controls shared by browser actions and upload routes."""
import hashlib
import hmac
from tempfile import SpooledTemporaryFile

from fastapi import Form, HTTPException, Request
from starlette.responses import JSONResponse

from app.auth import SESSION_COOKIE
from app.config import get_settings


def csrf_token(request: Request) -> str:
    session = request.cookies.get(SESSION_COOKIE, "")
    if not session:
        return ""
    return hmac.new(session.encode(), b"pv-csrf-v1", hashlib.sha256).hexdigest()


def require_csrf(request: Request, csrf: str = Form("")) -> None:
    expected = csrf_token(request)
    if not expected or not hmac.compare_digest(expected, csrf):
        raise HTTPException(403, "Invalid CSRF token. Reload the page and try again.")


class RequestSizeLimitMiddleware:
    """Bound the body before multipart parsing, including chunked requests."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        limit = get_settings().max_upload_bytes + 1024 * 1024  # multipart overhead
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, 400)(scope, receive, send)
        if length > limit:
            return await JSONResponse({"detail": "Request body too large"}, 413)(scope, receive, send)
        with SpooledTemporaryFile(max_size=1024 * 1024) as body:
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > limit:
                    return await JSONResponse({"detail": "Request body too large"}, 413)(scope, receive, send)
                body.write(chunk)
                if not message.get("more_body", False):
                    break
            body.seek(0)
            remaining = size

            async def bounded_receive():
                nonlocal remaining
                if remaining < 0:
                    return await receive()
                chunk = body.read(64 * 1024)
                remaining -= len(chunk)
                more = remaining > 0
                if not more:
                    remaining = -1
                return {"type": "http.request", "body": chunk, "more_body": more}

            await self.app(scope, bounded_receive, send)
