"""Auth, user admin, LLM/detection settings and audit log."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..context import AppContext
from ..db import now_iso
from ..llm.base import LLMError
from ..security import (check_password_policy, create_token, hash_password, verify_password)
from ..services.detection import get_detection_settings
from ..services.notify import validate_webhook
from .deps import current_user, get_ctx, require

router = APIRouter(prefix="/api")


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePwIn(BaseModel):
    old_password: str
    new_password: str


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str
    role: str = Field(pattern="^(admin|analyst|viewer)$")


class UserPatch(BaseModel):
    role: str | None = Field(default=None, pattern="^(admin|analyst|viewer)$")
    disabled: bool | None = None
    password: str | None = None


def public_user(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "role": u["role"], "must_change": bool(u["must_change"]),
            "disabled": bool(u.get("disabled", 0))}


@router.post("/auth/login")
def login(body: LoginIn, ctx: AppContext = Depends(get_ctx)):
    key = body.username.lower()
    if ctx.limiter.locked(key):
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
    u = ctx.db.one("SELECT * FROM users WHERE username=?", (body.username,))
    ok = bool(u) and not u["disabled"] and verify_password(body.password, u["password_hash"])
    if not u:  # keep timing similar for unknown users
        verify_password(body.password, "pbkdf2$1000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    if not ok:
        ctx.limiter.fail(key)
        ctx.db.audit(body.username, "login.failed")
        raise HTTPException(401, "Invalid username or password")
    ctx.limiter.reset(key)
    ctx.db.audit(u["username"], "login")
    return {"token": create_token(ctx.jwt_secret, u["id"], u["role"], ctx.settings.token_ttl_minutes), "user": public_user(u)}


@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return public_user(user)


@router.post("/auth/change-password")
def change_password(body: ChangePwIn, user: dict = Depends(current_user), ctx: AppContext = Depends(get_ctx)):
    full = ctx.db.one("SELECT * FROM users WHERE id=?", (user["id"],))
    if not verify_password(body.old_password, full["password_hash"]):
        raise HTTPException(400, "Current password is wrong")
    err = check_password_policy(body.new_password)
    if err:
        raise HTTPException(400, err)
    if body.new_password == body.old_password:
        raise HTTPException(400, "New password must differ from the old one")
    ctx.db.execute("UPDATE users SET password_hash=?, must_change=0 WHERE id=?", (hash_password(body.new_password), user["id"]))
    ctx.db.audit(user["username"], "password.change")
    return {"ok": True}


# ---- users (admin) ----
@router.get("/users")
def list_users(_: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    return [public_user(u) for u in ctx.db.query("SELECT id,username,role,disabled,must_change FROM users ORDER BY id")]


@router.post("/users", status_code=201)
def create_user(body: UserIn, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    err = check_password_policy(body.password)
    if err:
        raise HTTPException(400, err)
    if ctx.db.one("SELECT 1 FROM users WHERE username=?", (body.username,)):
        raise HTTPException(409, "Username already exists")
    uid = ctx.db.execute("INSERT INTO users(username,password_hash,role,must_change,created_at) VALUES(?,?,?,1,?)",
                         (body.username, hash_password(body.password), body.role, now_iso()))
    ctx.db.audit(admin["username"], "user.create", body.username, {"role": body.role})
    return {"id": uid}


@router.patch("/users/{uid}")
def patch_user(uid: int, body: UserPatch, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    u = ctx.db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        raise HTTPException(404, "User not found")
    losing_admin = u["role"] == "admin" and ((body.role and body.role != "admin") or body.disabled)
    if losing_admin:
        others = ctx.db.one("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND disabled=0 AND id!=?", (uid,))["n"]
        if others == 0:
            raise HTTPException(400, "Cannot remove the last active admin")
    if body.role:
        ctx.db.execute("UPDATE users SET role=? WHERE id=?", (body.role, uid))
    if body.disabled is not None:
        ctx.db.execute("UPDATE users SET disabled=? WHERE id=?", (int(body.disabled), uid))
    if body.password:
        err = check_password_policy(body.password)
        if err:
            raise HTTPException(400, err)
        ctx.db.execute("UPDATE users SET password_hash=?, must_change=1 WHERE id=?", (hash_password(body.password), uid))
    ctx.db.audit(admin["username"], "user.update", u["username"], body.model_dump(exclude={"password"}, exclude_none=True))
    return {"ok": True}


# ---- settings ----
class LLMIn(BaseModel):
    provider: str | None = None
    bedrock_model_id: str | None = None
    aws_region: str | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str | None = None
    azure_api_key: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None


@router.get("/settings/llm")
def get_llm(_: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    return {**ctx.llm.public_config(), "active": ctx.llm.safe_get().describe()}


@router.put("/settings/llm")
def put_llm(body: LLMIn, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    data = body.model_dump()
    secrets = {k: data.pop(k) for k in ("azure_api_key", "access_key_id", "secret_access_key")}
    try:
        ctx.llm.update(data, secrets)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    ctx.db.audit(admin["username"], "settings.llm", None, {k: v for k, v in data.items() if v})
    return get_llm(admin, ctx)


@router.post("/settings/llm/test")
def test_llm(_: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    try:
        prov = ctx.llm.get()
        r = prov.complete("You are a health check.", [{"role": "user", "content": "Reply with the single word OK."}], None, max_tokens=20)
        return {"ok": True, "reply": r.text[:100], **prov.describe()}
    except LLMError as e:
        return {"ok": False, "error": f"{e.kind}: {e.message}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


class DetectionIn(BaseModel):
    lookback_minutes: int | None = Field(default=None, ge=5, le=1440)
    thresholds: dict[str, int] | None = None
    auto_investigate: bool | None = None
    auto_pipeline: bool | None = None
    auto_per_hour: int | None = Field(default=None, ge=0, le=100)
    webhook_url: str | None = None


@router.get("/settings/detection")
def get_detection(_: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    return get_detection_settings(ctx.db)


@router.put("/settings/detection")
def put_detection(body: DetectionIn, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    data = body.model_dump(exclude_none=True)
    if "webhook_url" in data:
        err = validate_webhook(data["webhook_url"])
        if err:
            raise HTTPException(400, err)
    for k, v in (data.get("thresholds") or {}).items():
        if k not in ("MEDIUM", "HIGH", "CRITICAL") or v < 1:
            raise HTTPException(400, "thresholds must be MEDIUM/HIGH/CRITICAL with values >= 1")
    cur = ctx.db.get_setting("detection", {}) or {}
    cur.update(data)
    ctx.db.set_setting("detection", cur)
    ctx.db.audit(admin["username"], "settings.detection", None, {k: ("***" if k == "webhook_url" else v) for k, v in data.items()})
    return get_detection_settings(ctx.db)


@router.get("/audit")
def audit(limit: int = 200, _: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    return ctx.db.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),))
