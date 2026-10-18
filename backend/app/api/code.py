"""Repository coding assistant API."""
from __future__ import annotations

from pathlib import Path

import json
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..agents.code_agent import ask_repo
from ..agents.code_tools import apply_proposal
from ..context import AppContext
from ..db import now_iso
from ..services import github as gh
from .deps import get_ctx, require

router = APIRouter(prefix="/api")

BLOCKED_PREFIXES = ("/etc", "/proc", "/sys", "/dev", "/boot", "/root", "/var/lib", "/usr", "/bin", "/sbin", "/lib")


class RepoIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    root_path: str = Field(min_length=1, max_length=500)


def validate_repo_path(raw: str, allowed_roots: list[str]) -> Path:
    p = Path(raw).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(400, "Path is not a directory on the server")
    if len(p.parts) < 3 or str(p).startswith(BLOCKED_PREFIXES):
        raise HTTPException(400, "That directory is not allowed")
    if allowed_roots:
        if not any(str(p) == r or str(p).startswith(r.rstrip("/") + "/") for r in (str(Path(a).resolve()) for a in allowed_roots)):
            raise HTTPException(400, "Directory is outside LOGFIX_REPO_ROOTS")
    return p


PUBLIC_COLS = ("id,name,root_path,created_at,kind,url,branch,last_synced_at,last_commit,sync_status,sync_error,pr_comments,allow_pr,"
               "(token_enc IS NOT NULL AND token_enc!='') AS has_token,(webhook_secret_enc IS NOT NULL AND webhook_secret_enc!='') AS has_webhook")


def _webhook_url(ctx: AppContext, request: Request, rid: int) -> str:
    base = ctx.settings.public_url or str(request.base_url).rstrip("/")
    return f"{base}/api/webhooks/github/{rid}"


@router.get("/repos")
def repos(request: Request, _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    rows = ctx.db.query(f"SELECT {PUBLIC_COLS} FROM repos ORDER BY id")
    for r in rows:
        r["webhook_url"] = _webhook_url(ctx, request, r["id"]) if r["kind"] == "github" else None
        if r["kind"] == "github":
            r["root_path"] = None   # server path is not useful to the browser
    return rows


@router.post("/repos", status_code=201)
def add_repo(body: RepoIn, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    p = validate_repo_path(body.root_path, ctx.settings.repo_roots)
    if ctx.db.one("SELECT 1 FROM repos WHERE name=?", (body.name,)):
        raise HTTPException(409, "Repository name exists")
    rid = ctx.db.execute("INSERT INTO repos(name,root_path,created_at) VALUES(?,?,?)", (body.name, str(p), now_iso()))
    ctx.db.audit(admin["username"], "repo.add", body.name, {"path": str(p)})
    return {"id": rid}


@router.delete("/repos/{rid}")
def del_repo(rid: int, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    row = ctx.db.one("SELECT kind FROM repos WHERE id=?", (rid,))
    ctx.db.execute("DELETE FROM repos WHERE id=?", (rid,))
    if row and row["kind"] == "github":
        import shutil
        shutil.rmtree(gh.clone_dir(ctx, rid), ignore_errors=True)
    ctx.db.audit(admin["username"], "repo.delete", str(rid))
    return {"ok": True}


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=4000)


@router.post("/repos/{rid}/ask")
def ask(rid: int, body: AskIn, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    try:
        return ask_repo(ctx, rid, body.question, user["username"])
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/proposals")
def proposals(status: str = "", _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    if status:
        return ctx.db.query("SELECT * FROM proposals WHERE status=? ORDER BY id DESC LIMIT 200", (status,))
    return ctx.db.query("SELECT * FROM proposals ORDER BY id DESC LIMIT 200")


@router.post("/proposals/{pid}/apply")
def apply(pid: int, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    try:
        return apply_proposal(ctx, pid, admin["username"])
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # noqa: BLE001  PathError etc.
        raise HTTPException(400, str(e)) from e


@router.post("/proposals/{pid}/reject")
def reject(pid: int, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    pr = ctx.db.one("SELECT * FROM proposals WHERE id=?", (pid,))
    if not pr or pr["status"] != "pending":
        raise HTTPException(404, "No pending proposal with that id")
    ctx.db.execute("UPDATE proposals SET status='rejected', decided_by=?, decided_at=? WHERE id=?", (user["username"], now_iso(), pid))
    ctx.db.audit(user["username"], "proposal.reject", str(pid))
    return {"ok": True}


# ---------------- linked GitHub repositories ----------------
class GithubRepoIn(BaseModel):
    url: str = Field(min_length=10, max_length=300)
    name: str | None = Field(default=None, max_length=80)
    branch: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9/_.-]*$")
    token: str | None = Field(default=None, max_length=500)
    allow_pr: bool = False
    pr_comments: bool = False


@router.post("/repos/github", status_code=201)
def link_github(body: GithubRepoIn, request: Request, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    try:
        host, owner, name = gh.parse_repo_url(body.url)
    except gh.GitHubError as e:
        raise HTTPException(400, str(e)) from e
    display = body.name or f"{owner}/{name}"
    if ctx.db.one("SELECT 1 FROM repos WHERE name=?", (display,)):
        raise HTTPException(409, "A repository with this name is already linked")
    secret = gh.new_webhook_secret()
    rid = ctx.db.execute(
        "INSERT INTO repos(name,root_path,created_at,kind,url,branch,token_enc,webhook_secret_enc,sync_status,allow_pr,pr_comments) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (display, "", now_iso(), "github", f"https://{host}/{owner}/{name}", body.branch,
         ctx.secrets.encrypt({"token": body.token}) if body.token else None, ctx.secrets.encrypt({"secret": secret}),
         "pending", int(body.allow_pr), int(body.pr_comments)))
    ctx.db.audit(admin["username"], "repo.link", display, {"url": body.url, "token": bool(body.token)})
    gh.sync_async(ctx, rid, admin["username"])
    return {"id": rid, "name": display, "sync_status": "syncing", "webhook_url": _webhook_url(ctx, request, rid),
            "webhook_secret": secret, "webhook_note": "Shown once. Add it in GitHub: Settings > Webhooks > Content type application/json, events: Pushes and Pull requests."}


class RepoPatch(BaseModel):
    branch: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9/_.-]*$")
    token: str | None = Field(default=None, max_length=500)
    allow_pr: bool | None = None
    pr_comments: bool | None = None


@router.patch("/repos/{rid}")
def patch_repo(rid: int, body: RepoPatch, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    row = ctx.db.one("SELECT * FROM repos WHERE id=? AND kind='github'", (rid,))
    if not row:
        raise HTTPException(404, "Linked repository not found")
    if body.branch is not None:
        ctx.db.execute("UPDATE repos SET branch=? WHERE id=?", (body.branch, rid))
    if body.token:
        ctx.db.execute("UPDATE repos SET token_enc=? WHERE id=?", (ctx.secrets.encrypt({"token": body.token}), rid))
    if body.allow_pr is not None:
        ctx.db.execute("UPDATE repos SET allow_pr=? WHERE id=?", (int(body.allow_pr), rid))
    if body.pr_comments is not None:
        ctx.db.execute("UPDATE repos SET pr_comments=? WHERE id=?", (int(body.pr_comments), rid))
    ctx.db.audit(admin["username"], "repo.update", row["name"], {k: ("***" if k == "token" else v) for k, v in body.model_dump(exclude_none=True).items()})
    return {"ok": True}


@router.post("/repos/{rid}/sync", status_code=202)
def sync(rid: int, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    row = ctx.db.one("SELECT sync_status FROM repos WHERE id=? AND kind='github'", (rid,))
    if not row:
        raise HTTPException(404, "Linked repository not found")
    if row["sync_status"] == "syncing":
        return {"sync_status": "syncing"}
    ctx.db.execute("UPDATE repos SET sync_status='syncing' WHERE id=?", (rid,))
    gh.sync_async(ctx, rid, user["username"])
    return {"sync_status": "syncing"}


@router.post("/repos/{rid}/webhook/rotate")
def rotate_secret(rid: int, request: Request, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    if not ctx.db.one("SELECT 1 FROM repos WHERE id=? AND kind='github'", (rid,)):
        raise HTTPException(404, "Linked repository not found")
    secret = gh.new_webhook_secret()
    ctx.db.execute("UPDATE repos SET webhook_secret_enc=? WHERE id=?", (ctx.secrets.encrypt({"secret": secret}), rid))
    ctx.db.audit(admin["username"], "repo.webhook_rotate", str(rid))
    return {"webhook_url": _webhook_url(ctx, request, rid), "webhook_secret": secret}


@router.post("/webhooks/github/{rid}", include_in_schema=False)
async def github_webhook(rid: int, request: Request, ctx: AppContext = Depends(get_ctx)):
    """Public endpoint, protected by the HMAC signature only."""
    if int(request.headers.get("content-length") or 0) > 5_000_000:
        raise HTTPException(413, "Payload too large")
    body = await request.body()
    if len(body) > 5_000_000:
        raise HTTPException(413, "Payload too large")
    row = ctx.db.one("SELECT * FROM repos WHERE id=? AND kind='github'", (rid,))
    if not row:
        raise HTTPException(404, "Unknown repository")
    if not gh.verify_signature(gh.webhook_secret(ctx, row), body, request.headers.get("x-hub-signature-256")):
        ctx.db.audit("webhook", "webhook.rejected", str(rid), "bad signature")
        raise HTTPException(401, "Invalid signature")
    event = request.headers.get("x-github-event", "")
    delivery = request.headers.get("x-github-delivery") or ""
    if delivery:
        with ctx.db.connect() as c:
            fresh = c.execute("INSERT OR IGNORE INTO webhook_deliveries(delivery_id,repo_id,event,received_at) VALUES(?,?,?,?)",
                              (delivery, rid, event, now_iso())).rowcount
        if not fresh:
            return {"status": "duplicate", "ignored": True}
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "Body is not JSON")
    if event == "ping":
        return {"status": "pong"}
    if event == "push":
        ref = payload.get("ref", "")
        if row["branch"] and ref != f"refs/heads/{row['branch']}":
            return {"status": "ignored", "reason": f"push to {ref}, tracking {row['branch']}"}
        if row["sync_status"] != "syncing":
            ctx.db.execute("UPDATE repos SET sync_status='syncing' WHERE id=?", (rid,))
            gh.sync_async(ctx, rid, "webhook")
        return {"status": "sync_started"}
    if event == "pull_request":
        if payload.get("action") not in ("opened", "synchronize", "reopened", "ready_for_review"):
            return {"status": "ignored", "reason": f"action {payload.get('action')}"}
        pr = payload.get("pull_request") or {}
        if pr.get("draft"):
            return {"status": "ignored", "reason": "draft"}
        from ..agents.pr_review import start_review
        rv = start_review(ctx, rid, int(pr.get("number") or payload.get("number") or 0), str(pr.get("title", ""))[:200],
                          str((pr.get("head") or {}).get("sha", "")), str((pr.get("user") or {}).get("login", "")))
        return {"status": "review_started", "review_id": rv}
    return {"status": "ignored", "reason": f"event {event}"}


@router.get("/repos/{rid}/reviews")
def reviews(rid: int, _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    rows = ctx.db.query("SELECT id,pr_number,title,head_sha,author,status,result,comment_url,error,created_at,finished_at FROM reviews "
                        "WHERE repo_id=? ORDER BY id DESC LIMIT 30", (rid,))
    for r in rows:
        r["result"] = json.loads(r["result"]) if r["result"] else None
    return rows


class ReviewIn(BaseModel):
    number: int = Field(ge=1, le=10_000_000)


@router.post("/repos/{rid}/reviews", status_code=202)
def manual_review(rid: int, body: ReviewIn, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    row = ctx.db.one("SELECT * FROM repos WHERE id=? AND kind='github'", (rid,))
    if not row:
        raise HTTPException(404, "Linked repository not found")
    try:
        meta = gh._api(row, gh.get_token(ctx, row), "GET", f"/pulls/{body.number}").json()
    except gh.GitHubError as e:
        raise HTTPException(502, str(e)) from e
    from ..agents.pr_review import start_review
    rv = start_review(ctx, rid, body.number, str(meta.get("title", ""))[:200], str((meta.get("head") or {}).get("sha", "")),
                      str((meta.get("user") or {}).get("login", "")))
    return {"review_id": rv, "status": "running"}


@router.post("/proposals/{pid}/pr")
def open_pr(pid: int, admin: dict = Depends(require("admin")), ctx: AppContext = Depends(get_ctx)):
    try:
        return gh.open_pr_for_proposal(ctx, pid, admin["username"])
    except gh.GitHubError as e:
        raise HTTPException(409, str(e)) from e
