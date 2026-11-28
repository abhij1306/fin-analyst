"""Local-only protections of the UI server (docs/web-ui-plan.md).

- It listens on 127.0.0.1 only (`fin.ui.server.serve`).
- A request whose Host header is not localhost or 127.0.0.1 on the server's port is refused, so a
  web page cannot reach the server through a DNS name it controls (DNS rebinding).
- A random token is made at start and put in the URL that is opened. The first request with
  `?token=` sets it as an HttpOnly, SameSite=Strict cookie and redirects without it. Every other
  request needs that cookie. There is no CORS, so another site cannot read answers.
"""

import secrets
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from fin.audit.errors import UiRequestRefused

COOKIE = "fin_ui_token"
TOKEN_PARAM = "token"  # noqa: S105 (the query parameter's name)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def allowed_hosts(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


def refusal(message: str, status: int) -> JSONResponse:
    error = UiRequestRefused(message)
    return JSONResponse(
        {"error": {"code": error.code, "message": error.message}}, status_code=status
    )


def protect(
    token: str, port: int
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """The middleware function: Host check, token hand-off, cookie check."""
    hosts = allowed_hosts(port)

    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.headers.get("host", "").lower() not in hosts:
            return refusal("this server answers only on 127.0.0.1 and localhost", 400)
        offered = request.query_params.get(TOKEN_PARAM)
        if offered is not None:
            if not secrets.compare_digest(offered.encode(), token.encode()):
                return refusal(
                    "the token in the link is wrong; open the link `fin ui` printed", 401
                )
            rest = [(k, v) for k, v in request.query_params.multi_items() if k != TOKEN_PARAM]
            target = request.url.path + (f"?{urlencode(rest)}" if rest else "")
            response = RedirectResponse(target, status_code=303)
            response.set_cookie(  # NOSONAR: plain http on 127.0.0.1; Safari drops Secure cookies
                COOKIE, token, httponly=True, samesite="strict", path="/"
            )
            return response
        if not secrets.compare_digest(request.cookies.get(COOKIE, "").encode(), token.encode()):
            return refusal("open the link `fin ui` printed (it carries the token)", 401)
        return await call_next(request)

    return middleware
