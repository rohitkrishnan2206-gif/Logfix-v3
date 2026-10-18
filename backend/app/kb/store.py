"""Knowledge base: documents -> chunks in SQLite, BM25 index rebuilt when they change."""
from __future__ import annotations

import re
import threading

from ..db import Database, now_iso
from ..redact import redact
from .bm25 import BM25

CHUNK_MAX = 1400


def chunk_markdown(text: str) -> list[str]:
    """Split on headings, then merge small sections so a short runbook stays one chunk."""
    parts = re.split(r"(?m)^(?=#{1,3} )", text.strip())
    parts = [p.strip() for p in parts if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in parts:
        if cur and len(cur) + len(p) + 2 > CHUNK_MAX:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    out: list[str] = []
    for c in chunks:  # hard split anything still too long
        while len(c) > CHUNK_MAX * 1.5:
            cut = c.rfind("\n", 0, CHUNK_MAX)
            cut = cut if cut > 200 else CHUNK_MAX
            out.append(c[:cut])
            c = c[cut:].lstrip()
        out.append(c)
    return out


class KnowledgeBase:
    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()
        self._index: BM25 | None = None
        self._meta: dict[int, dict] = {}

    def add_doc(self, title: str, content: str, source: str = "manual") -> int:
        content = redact(content)
        doc_id = self.db.execute(
            "INSERT INTO kb_docs(title,source,content,created_at) VALUES(?,?,?,?)", (title, source, content, now_iso())
        )
        with self.db.connect() as c:
            for i, ch in enumerate(chunk_markdown(content)):
                c.execute("INSERT INTO kb_chunks(doc_id,ord,text) VALUES(?,?,?)", (doc_id, i, ch))
        self._invalidate()
        return doc_id

    def delete_doc(self, doc_id: int) -> bool:
        self.db.execute("DELETE FROM kb_docs WHERE id=?", (doc_id,))
        self._invalidate()
        return True

    def list_docs(self) -> list[dict]:
        return self.db.query(
            "SELECT d.id,d.title,d.source,d.created_at,LENGTH(d.content) AS size,"
            "(SELECT COUNT(*) FROM kb_chunks c WHERE c.doc_id=d.id) AS chunks FROM kb_docs d ORDER BY d.id"
        )

    def get_doc(self, doc_id: int) -> dict | None:
        return self.db.one("SELECT * FROM kb_docs WHERE id=?", (doc_id,))

    def _invalidate(self) -> None:
        with self._lock:
            self._index = None

    def _ensure(self) -> BM25:
        with self._lock:
            if self._index is None:
                rows = self.db.query(
                    "SELECT c.id,c.doc_id,c.text,d.title FROM kb_chunks c JOIN kb_docs d ON d.id=c.doc_id"
                )
                self._meta = {r["id"]: r for r in rows}
                # titles count toward relevance
                self._index = BM25([(r["id"], f"{r['title']} {r['title']} {r['text']}") for r in rows])
            return self._index

    def search(self, query: str, k: int = 3) -> list[dict]:
        idx = self._ensure()
        out, seen_docs = [], set()
        for chunk_id, score in idx.search(query, k=k * 3):
            m = self._meta[chunk_id]
            if m["doc_id"] in seen_docs:
                continue
            seen_docs.add(m["doc_id"])
            out.append({"doc_id": m["doc_id"], "title": m["title"], "snippet": m["text"][:1500], "score": round(score, 3)})
            if len(out) >= k:
                break
        return out
