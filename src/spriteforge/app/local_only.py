"""Localhost-only guard: the studio has no accounts, so any website the user visits could try to
drive it through the browser. Requests must name a loopback host (DNS rebinding), and requests
that change state must come from the studio's own origin (cross-site forms and fetches)."""

from urllib.parse import urlsplit

from fastapi.responses import JSONResponse

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def host_name(host: str) -> str:
    return (urlsplit("//" + host).hostname or "") if host else ""


def refusal(method: str, headers) -> str | None:
    """Why this request is refused, or None. `headers` is a case-insensitive mapping."""
    host = headers.get("host", "")
    if host_name(host) not in LOCAL_HOSTS:
        return "Local host required"
    if method in SAFE_METHODS:
        return None
    origin = headers.get("origin")
    if origin is not None:
        parts = urlsplit(origin)
        # Compared with the Host header (not the bind address) so the Vite dev proxy works.
        if parts.scheme not in {"http", "https"} or parts.netloc != host:
            return "Same-origin request required"
    elif headers.get("sec-fetch-site") == "cross-site":
        return "Same-origin request required"
    return None


class LocalOnly:
    """Pure ASGI middleware, so streaming responses (SSE) pass through untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            from starlette.datastructures import Headers

            reason = refusal(scope["method"], Headers(scope=scope))
            if reason:
                await JSONResponse({"detail": reason}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)
