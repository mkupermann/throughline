"""Account/session APIs for the opt-in shared workspace."""

import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from throughline.api.access import (
    _DUMMY,
    COOKIE,
    actor_context,
    audit,
    password_hash,
    password_matches,
    require_team,
    resolve_session,
    token_hash,
)
from throughline.api.deps import connection
from throughline.queries._exec import one, rows

router = APIRouter(tags=["workspace access"])


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._@-]*$")
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize(cls, value):
        return value.lower()


class NewUser(Login):
    display_name: str = Field(min_length=1, max_length=120)
    role: Literal["viewer", "editor", "admin"] = "viewer"

    @field_validator("display_name")
    @classmethod
    def visible_name(cls, value):
        if not value.strip():
            raise ValueError("Display name cannot be blank.")
        return value.strip()


class UserUpdate(BaseModel):
    role: Literal["viewer", "editor", "admin"]
    enabled: bool


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=15, max_length=128)


def identity(user):
    return {k: user[k] for k in ("id", "username", "display_name", "role")}


@router.get("/auth/session")
def session(request: Request):
    settings = request.app.state.settings
    if settings.auth_mode != "team":
        return {"mode": "local", "user": None, "csrf_token": None}
    user = resolve_session(settings, request.cookies.get(COOKIE))
    return {
        "mode": "team",
        "user": identity(user) if user else None,
        "csrf_token": user["csrf_token"] if user else None,
    }


def rate_limit(conn, username, host):
    # Atomic, database-shared limits cannot be bypassed by another web worker.
    blocked = False
    for label, value, limit in (("account", username, 10), ("client", host, 30)):
        attempt = one(
            conn,
            """INSERT INTO access_attempts(key,attempts) VALUES (%s,1)
            ON CONFLICT(key) DO UPDATE SET
                attempts=CASE WHEN access_attempts.window_start<now()-interval '15 minutes' THEN 1 ELSE access_attempts.attempts+1 END,
                window_start=CASE WHEN access_attempts.window_start<now()-interval '15 minutes' THEN now() ELSE access_attempts.window_start END
            RETURNING attempts""",
            (token_hash(label + ":" + value),),
        )
        blocked |= attempt["attempts"] > limit
    one(conn, "DELETE FROM access_attempts WHERE window_start<now()-interval '1 day'")
    conn.commit()
    # Rate counters must survive a later rejection, but the commit ends SET LOCAL.
    # Restore the authenticated actor for subsequent writes in this request.
    context = actor_context.get()
    if context:
        one(
            conn,
            "SELECT set_config('throughline.actor', %s, true), set_config('throughline.request_id', %s, true)",
            context,
        )
    if blocked:
        raise HTTPException(429, "Too many sign-in attempts. Try again in 15 minutes.", headers={"Retry-After": "900"})


@router.post("/auth/login")
def login(body: Login, request: Request, response: Response):
    require_team(request)
    settings = request.app.state.settings
    with connection(settings) as conn:
        rate_limit(conn, body.username, request.client.host if request.client else "unknown")
        user = one(conn, "SELECT * FROM access_users WHERE username=%s", (body.username,))
        verified = password_matches(body.password, user["password_hash"] if user else _DUMMY)
        if not verified or not user or not user["enabled"]:
            audit(conn, "login_failed")
            raise HTTPException(401, "Invalid username or password.")
        one(conn, "DELETE FROM access_sessions WHERE expires_at<now() OR last_seen_at<now()-interval '30 minutes'")
        previous = request.cookies.get(COOKIE)
        if previous:
            one(conn, "DELETE FROM access_sessions WHERE token_hash=%s", (token_hash(previous),))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        one(
            conn,
            """INSERT INTO access_sessions(token_hash,user_id,csrf_token,expires_at)
            VALUES (%s,%s,%s,now()+interval '8 hours')""",
            (token_hash(token), user["id"], csrf),
        )
        context = actor_context.set((f"user:{user['id']}", actor_context.get()[1]))
        try:
            audit(conn, "login_succeeded")
        finally:
            actor_context.reset(context)
    response.set_cookie(
        COOKIE,
        token,
        max_age=8 * 3600,
        httponly=True,
        secure=settings.public_url.startswith("https:"),
        samesite="strict",
        path="/",
    )
    return {"mode": "team", "user": identity(user), "csrf_token": csrf}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    require_team(request)
    with connection(request.app.state.settings) as conn:
        one(conn, "DELETE FROM access_sessions WHERE token_hash=%s", (token_hash(request.cookies.get(COOKIE, "")),))
        audit(conn, "logout")
    response.delete_cookie(COOKIE, path="/")
    return {"signed_out": True}


@router.post("/auth/password")
def change_password(body: PasswordChange, request: Request, response: Response):
    require_team(request)
    with connection(request.app.state.settings) as conn:
        rate_limit(
            conn, "password:" + request.state.user["username"], request.client.host if request.client else "unknown"
        )
        user = one(conn, "SELECT * FROM access_users WHERE id=%s FOR UPDATE", (request.state.user["id"],))
        if not password_matches(body.current_password, user["password_hash"]):
            raise HTTPException(403, "Current password is incorrect.")
        one(
            conn, "UPDATE access_users SET password_hash=%s WHERE id=%s", (password_hash(body.new_password), user["id"])
        )
        one(conn, "DELETE FROM access_sessions WHERE user_id=%s", (user["id"],))
        conn.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"signed_out": True}


@router.get("/access/users")
def users(request: Request):
    require_team(request)
    with connection(request.app.state.settings) as conn:
        return {
            "users": rows(conn, "SELECT id,username,display_name,role,enabled,created_at FROM access_users ORDER BY id")
        }


@router.post("/access/users", status_code=201)
def add_user(body: NewUser, request: Request):
    require_team(request)
    try:
        hashed = password_hash(body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with connection(request.app.state.settings) as conn:
        result = one(
            conn,
            """INSERT INTO access_users(username,display_name,password_hash,role)
            VALUES (%s,%s,%s,%s) ON CONFLICT(username) DO NOTHING RETURNING id,username,display_name,role,enabled""",
            (body.username, body.display_name.strip(), hashed, body.role),
        )
        if not result:
            raise HTTPException(409, "This username is already in use.")
        conn.commit()
        return result


@router.put("/access/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, request: Request):
    require_team(request)
    with connection(request.app.state.settings) as conn:
        one(conn, "SELECT pg_advisory_xact_lock(1349913)")
        target = one(conn, "SELECT id,role,enabled FROM access_users WHERE id=%s FOR UPDATE", (user_id,))
        if not target:
            raise HTTPException(404, "Account not found.")
        if target["role"] == "admin" and target["enabled"] and (body.role != "admin" or not body.enabled):
            others = one(
                conn, "SELECT count(*) AS n FROM access_users WHERE enabled AND role='admin' AND id<>%s", (user_id,)
            )
            if not others["n"]:
                raise HTTPException(409, "Keep at least one active administrator.")
        result = one(
            conn,
            "UPDATE access_users SET role=%s,enabled=%s WHERE id=%s RETURNING id,username,display_name,role,enabled",
            (body.role, body.enabled, user_id),
        )
        one(conn, "DELETE FROM access_sessions WHERE user_id=%s", (user_id,))
        conn.commit()
        return result


@router.get("/access/audit")
def audit_history(request: Request, before: int | None = Query(None, gt=0), limit: int = Query(50, ge=1, le=200)):
    require_team(request)
    with connection(request.app.state.settings) as conn:
        return {
            "events": rows(
                conn,
                """SELECT a.*, u.display_name AS actor_name FROM access_audit a
            LEFT JOIN access_users u ON a.actor='user:' || u.id::text
            WHERE (%s::bigint IS NULL OR a.id<%s)
            ORDER BY a.id DESC LIMIT %s""",
                (before, before, limit),
            )
        }
