"""Anti-CSRF for the UI server.

A request that changes data (any method but GET, HEAD, OPTIONS) must carry an Origin header naming
this server (127.0.0.1 or localhost on its port). A browser adds Origin to every cross-site POST and
a page of another site cannot forge it, so such a request is refused (E007). This comes on top of
the token cookie, which is SameSite=Strict."""

from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from fin.ui.security import allowed_hosts, refusal

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def origin_guard(
    port: int,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    origins = {f"http://{host}" for host in allowed_hosts(port)}  # NOSONAR: plain http on 127.0.0.1

    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if (
            request.method not in SAFE_METHODS
            and request.headers.get("origin", "").lower() not in origins
        ):
            return refusal("this request did not come from the fin UI page; nothing changed", 403)
        return await call_next(request)

    return middleware
