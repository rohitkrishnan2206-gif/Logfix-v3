"""Chat sessions and the knowledge base."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agents.chat import create_session, send_message, stream_message
from ..context import AppContext
from .deps import get_ctx, require

router = APIRouter(prefix="/api")


class MsgIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _own_session(ctx: AppContext, sid: int, user: dict) -> dict:
    s = ctx.db.one("SELECT * FROM chat_sessions WHERE id=?", (sid,))
    if not s or (s["user_id"] != user["id"] and user["role"] != "admin"):
        raise HTTPException(404, "Chat not found")
    return s


@router.get("/chat/sessions")
def sessions(user: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    return ctx.db.query("SELECT * FROM chat_sessions WHERE user_id=? ORDER BY id DESC LIMIT 50", (user["id"],))


@router.post("/chat/sessions", status_code=201)
def new_session(user: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    return {"id": create_session(ctx, user["id"])}


@router.get("/chat/sessions/{sid}")
def get_session(sid: int, user: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    s = _own_session(ctx, sid, user)
    msgs = ctx.db.query("SELECT id,role,content,trace,created_at FROM chat_messages WHERE session_id=? ORDER BY id", (sid,))
    for m in msgs:
        m["trace"] = json.loads(m["trace"]) if m["trace"] else None
    return {**s, "messages": msgs}


@router.post("/chat/sessions/{sid}/messages")
def post_message(sid: int, body: MsgIn, user: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    _own_session(ctx, sid, user)
    return send_message(ctx, sid, body.text, user["username"])


class StreamIn(BaseModel):
    text: str | None = Field(default=None, max_length=4000)
    regenerate: bool = False


@router.post("/chat/sessions/{sid}/stream")
def stream(sid: int, body: StreamIn, user: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    _own_session(ctx, sid, user)
    if not body.regenerate and not (body.text and body.text.strip()):
        raise HTTPException(422, "text is required")
    gen = stream_message(ctx, sid, body.text.strip() if body.text else None, user["username"], body.regenerate)
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


class RenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=80)


@router.patch("/chat/sessions/{sid}")
def rename_session(sid: int, body: RenameIn, user: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    _own_session(ctx, sid, user)
    ctx.db.execute("UPDATE chat_sessions SET title=? WHERE id=?", (body.title.strip(), sid))
    return {"ok": True}


@router.delete("/chat/sessions/{sid}")
def delete_session(sid: int, user: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    _own_session(ctx, sid, user)
    ctx.db.execute("DELETE FROM chat_sessions WHERE id=?", (sid,))
    return {"ok": True}


# ---- knowledge base ----
class DocIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=10, max_length=200_000)


@router.get("/kb/docs")
def docs(_: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    return ctx.kb.list_docs()


@router.get("/kb/docs/{did}")
def doc(did: int, _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    d = ctx.kb.get_doc(did)
    if not d:
        raise HTTPException(404, "Document not found")
    return d


@router.post("/kb/docs", status_code=201)
def add_doc(body: DocIn, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    did = ctx.kb.add_doc(body.title, body.content, source=f"upload:{user['username']}")
    ctx.db.audit(user["username"], "kb.add", body.title)
    return {"id": did}


@router.delete("/kb/docs/{did}")
def del_doc(did: int, user: dict = Depends(require("analyst")), ctx: AppContext = Depends(get_ctx)):
    ctx.kb.delete_doc(did)
    ctx.db.audit(user["username"], "kb.delete", str(did))
    return {"ok": True}


@router.get("/kb/search")
def kb_search(q: str, _: dict = Depends(require("viewer")), ctx: AppContext = Depends(get_ctx)):
    return {"method": "BM25 keyword ranking (not vector embeddings)", "results": ctx.kb.search(q, k=5)}
