import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.errors import _envelope

COOKIE = "wm_csrf"
HEADER = "X-CSRF-Token"
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def set_csrf_cookie(response: Response, secure: bool) -> None:
    # Readable by JS on purpose (double submit); SameSite=Lax like the session.
    response.set_cookie(
        COOKIE, secrets.token_urlsafe(32), max_age=8 * 3600, httponly=False,
        samesite="lax", secure=secure,
    )  # fmt: skip


class CsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in _UNSAFE and not request.url.path.startswith("/auth/"):
            cookie, header = request.cookies.get(COOKIE), request.headers.get(HEADER)
            if not cookie or not header or not secrets.compare_digest(cookie, header):
                return _envelope(request, 403, "CSRF_FAILED", "CSRF token mismatch")
        return await call_next(request)
