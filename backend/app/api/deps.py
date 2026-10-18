from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from ..context import AppContext
from ..security import decode_token, role_allows

PASSWORD_CHANGE_OK_PATHS = {"/api/auth/me", "/api/auth/change-password"}


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def current_user(request: Request, ctx: AppContext = Depends(get_ctx)) -> dict:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "Not authenticated")
    payload = decode_token(ctx.jwt_secret, auth[7:].strip())
    if not payload:
        raise HTTPException(401, "Invalid or expired token")
    # Live lookup: disabling a user or changing a role takes effect immediately.
    user = ctx.db.one("SELECT id,username,role,disabled,must_change FROM users WHERE id=?", (int(payload["sub"]),))
    if not user or user["disabled"]:
        raise HTTPException(401, "Account disabled or removed")
    if user["must_change"] and request.url.path not in PASSWORD_CHANGE_OK_PATHS:
        raise HTTPException(403, "password_change_required")
    return user


def require(role: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        if not role_allows(user["role"], role):
            raise HTTPException(403, f"Requires {role} role")
        return user

    return dep
