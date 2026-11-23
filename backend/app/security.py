"""Small, explicit authentication and abuse-control boundary for the API."""

import logging
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request, WebSocket, status

from app.config import settings
from app.db.redis import get_redis_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    key_fingerprint: str
    role: str


def _presented_api_key(value: str | None) -> str | None:
    if not value:
        return None
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value.strip()


def _authenticate(value: str | None) -> Principal:
    if not settings.api_key_auth_enabled:
        return Principal(key_fingerprint="development", role="admin")

    key = _presented_api_key(value)
    if not key:
        # A public deployment serves keyless visitors at a fixed, bounded role.
        # A key that is presented must still be valid: a wrong key is an error,
        # not a silent downgrade to anonymous.
        if settings.public_access_role != "none":
            return Principal(key_fingerprint="anonymous", role=settings.public_access_role)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key required")

    for configured_key, role in settings.api_keys.items():
        if secrets.compare_digest(key, configured_key):
            fingerprint = f"{configured_key[:4]}***{configured_key[-4:]}" if len(configured_key) >= 8 else "***"
            return Principal(key_fingerprint=fingerprint, role=role)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


async def require_principal(request: Request) -> Principal:
    return _authenticate(request.headers.get("authorization") or request.headers.get("x-api-key"))


async def require_viewer(request: Request) -> Principal:
    return await require_principal(request)


async def require_operator(request: Request) -> Principal:
    principal = await require_principal(request)
    if principal.role not in {"operator", "admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator role required")
    return principal


async def require_admin(request: Request) -> Principal:
    principal = await require_principal(request)
    if principal.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role required")
    return principal


def client_address(request: Request) -> str:
    """The address rate limits are counted against.

    Behind a reverse proxy every request arrives from the proxy, so the
    configured header carries the visitor's address instead. Its last
    comma-separated entry is used: a proxy that appends to a list adds the
    address it saw at the end, while anything earlier came from the client.
    """
    if settings.client_ip_header:
        forwarded = request.headers.get(settings.client_ip_header)
        if forwarded:
            address = forwarded.split(",")[-1].strip()
            if address:
                return address
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request) -> None:
    """Bound API abuse with a fixed-window Redis counter.

    Authentication is the primary boundary. This intentionally fails closed in
    production when the limiter cannot reach Redis.
    """
    client_host = client_address(request)
    route = request.scope.get("route")
    route_path = getattr(route, "path", request.url.path)
    bucket = f"rate:{client_host}:{route_path}"
    try:
        pipe = get_redis_client().pipeline()
        # The first request opens the window; INCR keeps its expiry. SET NX EX
        # rather than EXPIRE NX, which needs Redis 7 and which not every
        # hosted Redis accepts -- and a limiter error fails production closed.
        pipe.set(bucket, 0, ex=60, nx=True)
        pipe.incr(bucket)
        _, count = pipe.execute()
    except Exception as exc:
        logger.warning("rate limiter unavailable", exc_info=exc)
        if settings.environment == "production":
            raise HTTPException(status_code=503, detail="Request limiter unavailable") from exc
        return

    if count > settings.rate_limit_per_minute:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": "60"},
        )


def websocket_principal(websocket: WebSocket) -> Principal:
    """Authenticate a browser WebSocket before accepting it.

    Browsers cannot set arbitrary handshake headers. The query parameter is
    accepted only for this transport and must be protected by TLS in production.
    """
    value = websocket.headers.get("authorization") or websocket.headers.get("x-api-key")
    value = value or websocket.query_params.get("api_key")
    return _authenticate(value)
