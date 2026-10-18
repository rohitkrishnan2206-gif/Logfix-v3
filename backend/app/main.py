"""FastAPI app factory: wires config, DB, secrets, KB, LLM, scheduler and routes."""
from __future__ import annotations

import json
import os
import secrets as pysecrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .api import auth as auth_api, chat as chat_api, code as code_api, ops as ops_api, pipeline as pipeline_api
from .config import Settings, load_settings
from .connectors import build_connector
from .context import AppContext
from .db import Database, now_iso
from .kb.seed import seed_kb
from .kb.store import KnowledgeBase
from .llm.factory import LLMManager
from .security import LoginLimiter, SecretBox, hash_password, load_jwt_secret
from .services.scheduler import Scheduler

VERSION = "2.0.0"
SAMPLE_REPO = Path(__file__).resolve().parents[2] / "sample_repo"


def bootstrap(ctx: AppContext) -> None:
    db = ctx.db
    if not db.one("SELECT 1 FROM users LIMIT 1"):
        pw = ctx.settings.admin_password
        generated = pw is None
        if generated:
            pw = pysecrets.token_urlsafe(12) + "A1"
            f = ctx.settings.data_dir / "initial_admin_password.txt"
            f.write_text(f"username: {ctx.settings.admin_user}\npassword: {pw}\n(You must change it at first login. Delete this file afterwards.)\n")
            try:
                os.chmod(f, 0o600)
            except OSError:
                pass
            print(f"\n*** First run: admin login is in {f} ***\n", flush=True)
        db.execute("INSERT INTO users(username,password_hash,role,must_change,created_at) VALUES(?,?,?,?,?)",
                   (ctx.settings.admin_user, hash_password(pw), "admin", int(generated), now_iso()))
        db.audit("system", "bootstrap.admin")
    seed_kb(ctx.kb)
    if not db.get_setting("seeded"):
        db.execute("INSERT OR IGNORE INTO sources(name,cloud,config,secrets_enc,interval_seconds,created_at) VALUES(?,?,?,?,?,?)",
                   ("Demo cloud (simulated)", "demo", json.dumps({"scenario": "quiet"}), ctx.secrets.encrypt({}), 30, now_iso()))
        if SAMPLE_REPO.is_dir():
            db.execute("INSERT OR IGNORE INTO repos(name,root_path,created_at) VALUES(?,?,?)", ("sample-payment-service", str(SAMPLE_REPO), now_iso()))
        db.set_setting("seeded", True)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_path)
    ctx = AppContext(settings=settings, db=db, secrets=SecretBox.from_settings(settings.data_dir, settings.secret_key),
                     jwt_secret=load_jwt_secret(settings.data_dir, settings.jwt_secret), limiter=LoginLimiter())
    ctx.kb = KnowledgeBase(db)
    ctx.llm = LLMManager(settings, db, ctx.secrets)
    ctx.connector_factory = build_connector
    ctx.scheduler = Scheduler(ctx)
    bootstrap(ctx)
    from .services import activity as _activity
    _activity.mark_interrupted(ctx)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not settings.disable_scheduler:
            ctx.scheduler.start()
        yield
        ctx.scheduler.stop()

    app = FastAPI(title="LogFix AI V2", version=VERSION, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.ctx = ctx
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith("/api/"):
            resp.headers["Content-Security-Policy"] = (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        elif request.url.path.endswith("report.html"):
            resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
        return resp

    for r in (auth_api.router, ops_api.router, pipeline_api.router, chat_api.router, code_api.router):
        app.include_router(r)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": VERSION, "llm": ctx.llm.safe_get().describe(), "scheduler": not settings.disable_scheduler}

    dist = settings.frontend_dist
    if dist:
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            f = (dist / path).resolve()
            if path and f.is_file() and dist.resolve() in f.parents:
                return FileResponse(f)
            if path.startswith("assets/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            return FileResponse(dist / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def root():
            return {"message": "LogFix AI V2 API is running. Frontend not built: see README (npm run build).", "docs": "/api/docs"}

    return app


app = None  # created lazily by __main__ / uvicorn factory


def get_app() -> FastAPI:
    return create_app()
