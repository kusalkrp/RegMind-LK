"""
API key authentication middleware.

All MCP tool endpoints require a valid API key via X-API-Key header
(or api_key query parameter). The /health path is public.

Keys are stored hashed (SHA-256) in the api_keys table.
Valid keys are cached in-process for 5 minutes to avoid a DB hit per request.

To create a new key:
    key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    INSERT INTO api_keys (name, key_hash) VALUES ('client-name', '<hash>');
"""

import hashlib
import logging

from cachetools import TTLCache
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Cache valid key_hash → caller_id for 5 minutes
_key_cache: TTLCache = TTLCache(maxsize=1024, ttl=300)

# Paths that bypass authentication
_PUBLIC_PATHS: frozenset[str] = frozenset({"/health", "/health/"})


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that enforces API key authentication.
    Attach to the FastMCP Starlette app before starting the server.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        raw_key = (
            request.headers.get("X-API-Key")
            or request.query_params.get("api_key")
        )

        if not raw_key:
            logger.warning(
                "auth_missing_key",
                extra={"path": request.url.path, "ip": request.client.host if request.client else "unknown"},
            )
            return JSONResponse(
                {"error": "Missing API key. Provide X-API-Key header."},
                status_code=401,
            )

        key_hash = _hash_key(raw_key)

        # Fast path: cached
        if key_hash in _key_cache:
            request.state.caller_id = _key_cache[key_hash]
            return await call_next(request)

        # Slow path: DB lookup
        from sltda_mcp.database import acquire

        try:
            async with acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT id::text AS id
                       FROM api_keys
                       WHERE key_hash = $1 AND is_active = TRUE""",
                    key_hash,
                )
                if row:
                    # Update last_used_at asynchronously (non-blocking best-effort)
                    await conn.execute(
                        "UPDATE api_keys SET last_used_at = NOW() WHERE key_hash = $1",
                        key_hash,
                    )
        except Exception as exc:
            logger.error("auth_db_error: %s", exc)
            return JSONResponse({"error": "Authentication service unavailable"}, status_code=503)

        if not row:
            logger.warning(
                "auth_invalid_key",
                extra={"key_prefix": raw_key[:8] + "...", "path": request.url.path},
            )
            return JSONResponse({"error": "Invalid or inactive API key"}, status_code=401)

        caller_id = row["id"]
        _key_cache[key_hash] = caller_id
        request.state.caller_id = caller_id
        return await call_next(request)
