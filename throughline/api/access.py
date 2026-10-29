"""Opt-in authenticated shared workspace. No header-based identity or implicit role grants."""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import secrets
import threading
import uuid
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from throughline.queries._exec import one

from .deps import DatabaseUnavailable, connection

actor_context = contextvars.ContextVar("throughline_actor", default=None)
COOKIE = "throughline_session"
SAFE = {"GET", "HEAD", "OPTIONS"}
ROLES = {"viewer": 0, "editor": 1, "admin": 2}
_HASH_SLOTS = threading.BoundedSemaphore(2)


def password_hash(password: str, salt: str | None = None) -> str:
    if not 15 <= len(password) <= 128:
        raise ValueError("Use a password with 15 to 128 characters.")
    salt = salt or secrets.token_hex(16)
    with _HASH_SLOTS:
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**17, r=8, p=1, maxmem=256 * 1024**2)
    return f"scrypt${salt}${digest.hex()}"


_DUMMY = "scrypt$" + "00" * 16 + "$" + "00" * 64


def password_matches(password: str, encoded: str) -> bool:
    try:
        scheme, salt, _ = encoded.split("$")
        return scheme == "scrypt" and hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def validate_origin(origin: str) -> str:
    parsed = urlsplit(origin)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and local)) or not parsed.hostname:
        raise ValueError("Team mode requires THROUGHLINE_PUBLIC_URL with HTTPS (HTTP is allowed only on loopback).")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("THROUGHLINE_PUBLIC_URL must be an origin without credentials, path, query or fragment.")
    host = parsed.hostname
    if any(char.isspace() for char in host):
        raise ValueError("The public hostname cannot contain whitespace.")
    host = f"[{host}]" if ":" in host else host.encode("idna").decode("ascii")
    port = parsed.port
    if port and port != (443 if parsed.scheme == "https" else 80):
        host += f":{port}"
    return f"{parsed.scheme}://{host}"


def matches_public_host(host, public_url):
    if not host or any(char in host for char in "/?#@"):
        return False
    try:
        return validate_origin(urlsplit(public_url).scheme + "://" + host) == public_url
    except ValueError:
        return False


def required_role(path: str, method: str) -> str:
    # System APIs, host files, credentials, job logs and arbitrary SQL are admin-only.
    if path.startswith(("/api/console", "/api/operate", "/api/export", "/api/ai/", "/api/pm/", "/api/access")):
        return "admin"
    if "/artifact/" in path or path in {"/api/docs", "/api/openapi.json", "/api/redoc"}:
        return "admin"
    if path == "/api/find/graph":
        return "viewer"
    if path.startswith(("/api/projects/", "/api/story/", "/api/curate/")):
        return "viewer" if method in SAFE else "editor"
    if path == "/api/ask":
        return "editor"
    if method in SAFE and (
        path in {"/api/overview", "/api/providers", "/api/find", "/api/timeline"}
        or path.startswith(("/api/detail/", "/api/find/", "/api/timeline/"))
    ):
        return "viewer"
    return "admin"


def audit(conn, action, entity="session", entity_id=None):
    context = actor_context.get() or ("anonymous", None)
    one(
        conn,
        """INSERT INTO access_audit(actor, request_id, action, entity_type, entity_id)
        VALUES (%s,%s,%s,%s,%s) RETURNING id""",
        (*context, action, entity, entity_id),
    )
    conn.commit()


def audit_request(settings, action, path):
    with connection(settings) as conn:
        audit(conn, action, "http", path[:300])


def resolve_session(settings, token):
    if not token or len(token) > 100:
        return None
    with connection(settings) as conn:
        user = one(
            conn,
            """UPDATE access_sessions s SET last_seen_at=now() FROM access_users u
            WHERE s.token_hash=%s AND s.user_id=u.id AND u.enabled
              AND s.expires_at>now() AND s.last_seen_at>now()-interval '30 minutes'
            RETURNING u.id,u.username,u.display_name,u.role,s.csrf_token""",
            (token_hash(token),),
        )
        conn.commit()
        return user


class AccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = request.app.state.settings
        request.state.user = None
        if settings.auth_mode != "team":
            return await call_next(request)
        request_id = uuid.uuid4().hex
        context = actor_context.set(("anonymous", request_id))
        try:
            path = request.url.path
            if path != "/api/health" and not matches_public_host(request.headers.get("host"), settings.public_url):
                return JSONResponse({"detail": "Unexpected host."}, status_code=400)
            if path.startswith("/api/"):
                if request.method not in SAFE:
                    if (
                        request.headers.get("origin") != settings.public_url
                        or request.headers.get("x-throughline-request") != "1"
                    ):
                        return JSONResponse({"detail": "Same-origin application request required."}, status_code=403)
                if path not in {"/api/auth/login", "/api/auth/session", "/api/health"}:
                    user = await run_in_threadpool(resolve_session, settings, request.cookies.get(COOKIE))
                    if not user:
                        return JSONResponse({"detail": "Sign in to continue."}, status_code=401)
                    request.state.user = user
                    actor_context.set((f"user:{user['id']}", request_id))
                    if request.method not in SAFE and not hmac.compare_digest(
                        request.headers.get("x-csrf-token", ""), user["csrf_token"]
                    ):
                        await run_in_threadpool(audit_request, settings, "csrf_denied", path)
                        return JSONResponse({"detail": "Invalid session request token."}, status_code=403)
                    if (
                        path not in {"/api/auth/logout", "/api/auth/password"}
                        and ROLES[user["role"]] < ROLES[required_role(path, request.method)]
                    ):
                        await run_in_threadpool(audit_request, settings, "role_denied", path)
                        return JSONResponse({"detail": "Your role does not allow this action."}, status_code=403)
            if request.state.user and request.method not in SAFE:
                await run_in_threadpool(audit_request, settings, request.method + "_started", path)
            response = await call_next(request)
            if request.state.user and request.method not in SAFE:
                await run_in_threadpool(audit_request, settings, request.method + "_" + str(response.status_code), path)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; object-src 'none'"
            )
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["X-Request-ID"] = request_id
            return response
        except DatabaseUnavailable:
            return JSONResponse({"detail": "Workspace database unavailable."}, status_code=503)
        finally:
            actor_context.reset(context)


def require_team(request):
    if request.app.state.settings.auth_mode != "team":
        raise HTTPException(404, "Account management requires team mode.")
