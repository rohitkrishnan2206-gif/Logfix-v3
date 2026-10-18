"""Repository tools. Read-only except propose_edit, which only STORES a proposal.
Applying a proposal is a separate, admin-only, human-approved action."""
from __future__ import annotations

import difflib
import fnmatch
import os
import re
import subprocess
from pathlib import Path

from ..context import AppContext
from ..db import now_iso
from ..llm.base import ToolSpec
from ..redact import redact
from .framework import AgentContext, Tool

MAX_FILE_BYTES = 1_000_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".idea", ".pytest_cache"}
SENSITIVE = re.compile(
    r"(^\.env(\..*)?$|\.pem$|\.key$|\.p12$|\.pfx$|^id_(rsa|dsa|ecdsa|ed25519)|credentials|secrets?\.(json|ya?ml|toml)$|"
    r"\.tfstate|^\.netrc$|^\.npmrc$|^\.pypirc$|^secret\.key$|^jwt_secret$)",
    re.I,
)


class PathError(Exception):
    pass


def resolve_safe(root: str | Path, rel: str, must_exist: bool = True) -> Path:
    if rel is None or "\x00" in rel:
        raise PathError("invalid path")
    if os.path.isabs(rel) or re.match(r"^[A-Za-z]:[\\/]", rel):
        raise PathError("absolute paths are not allowed")
    parts = Path(rel.replace("\\", "/")).parts
    if ".." in parts:
        raise PathError("'..' is not allowed")
    if ".git" in parts:
        raise PathError("git internals are not accessible")
    root_p = Path(root).resolve()
    target = (root_p / rel).resolve()
    try:
        target.relative_to(root_p)
    except ValueError:
        raise PathError("path escapes the repository (symlink?)") from None
    if SENSITIVE.search(target.name):
        raise PathError("sensitive file: access refused")
    if must_exist and not target.exists():
        raise PathError("file not found")
    return target


def _is_text(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return b"\x00" not in f.read(4096)
    except OSError:
        return False


def _walk(root: Path):
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS)
        for f in sorted(fn):
            p = Path(dp) / f
            if SENSITIVE.search(f) or p.is_symlink():
                continue
            yield p


def _root(actx: AgentContext) -> Path:
    return Path(actx.extra["repo_root"])


def list_files(args, actx):
    root = _root(actx)
    base = resolve_safe(root, args.get("path") or ".") if args.get("path") not in (None, "", ".") else root
    if not base.is_dir():
        return {"error": "not a directory"}
    out = [str(p.relative_to(root)) for p in _walk(base)]
    n = max(1, min(int(args.get("max") or 200), 500))
    return {"files": out[:n], "truncated": len(out) > n}


def read_file(args, actx):
    root = _root(actx)
    p = resolve_safe(root, args.get("path", ""))
    if not p.is_file():
        return {"error": "not a file"}
    if p.stat().st_size > MAX_FILE_BYTES:
        return {"error": "file too large (>1MB)"}
    if not _is_text(p):
        return {"error": "binary file"}
    lines = p.read_text(errors="replace").splitlines()
    s = max(1, int(args.get("start_line") or 1))
    e = min(len(lines), int(args.get("end_line") or s + 199), s + 399)
    body = "\n".join(f"{i}: {lines[i - 1]}" for i in range(s, e + 1))
    return {"path": str(p.relative_to(root)), "start_line": s, "end_line": e, "total_lines": len(lines), "content": redact(body)}


def grep(args, actx):
    root = _root(actx)
    try:
        rx = re.compile(str(args.get("pattern", "")), re.I)
    except re.error as e:
        return {"error": f"bad regex: {e}"}
    glob = args.get("glob")
    mx = max(1, min(int(args.get("max_results") or 30), 100))
    matches = []
    for p in _walk(root):
        if glob and not fnmatch.fnmatch(p.name, glob):
            continue
        try:
            if p.stat().st_size > MAX_FILE_BYTES or not _is_text(p):
                continue
            for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if rx.search(line):
                    matches.append({"path": str(p.relative_to(root)), "line": i, "text": redact(line.strip())[:200]})
                    if len(matches) >= mx:
                        return {"matches": matches, "truncated": True}
        except OSError:
            continue
    return {"matches": matches, "truncated": False}


def git_log(args, actx):
    root = _root(actx)
    n = max(1, min(int(args.get("max") or 10), 30))
    try:
        r = subprocess.run(["git", "-C", str(root), "log", f"-{n}", "--pretty=format:%h|%an|%ad|%s", "--date=short"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return {"error": "git not available"}
    if r.returncode != 0:
        return {"error": "not a git repository"}
    return {"commits": [dict(zip(("hash", "author", "date", "subject"), l.split("|", 3))) for l in r.stdout.splitlines()]}


def make_diff(path: str, old: str, new: str, content: str) -> str:
    after = content.replace(old, new, 1)
    return "".join(difflib.unified_diff(content.splitlines(True), after.splitlines(True), f"a/{path}", f"b/{path}", n=3))


def propose_edit(args, actx):
    root = _root(actx)
    path, old, new = args.get("path", ""), args.get("old_text", ""), args.get("new_text", "")
    p = resolve_safe(root, path)
    if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES or not _is_text(p):
        return {"error": "cannot edit this file"}
    content = p.read_text()
    if not old:
        return {"error": "old_text is required"}
    n = content.count(old)
    if n != 1:
        return {"error": f"old_text must appear exactly once in the file (found {n})"}
    diff = make_diff(str(p.relative_to(root)), old, new, content)
    pid = actx.app.db.execute(
        "INSERT INTO proposals(repo_id,path,old_text,new_text,rationale,diff,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (actx.extra["repo_id"], str(p.relative_to(root)), old, new, str(args.get("rationale", ""))[:1000], diff,
         actx.user, now_iso()),
    )
    return {"proposal_id": pid, "status": "pending", "note": "Saved for human review. Nothing was changed on disk."}


def apply_proposal(actx: AppContext, proposal_id: int, user: str) -> dict:
    """Admin action. Re-validates against the CURRENT file so a stale proposal can't clobber newer edits."""
    db = actx.db
    pr = db.one("SELECT * FROM proposals WHERE id=?", (proposal_id,))
    if not pr:
        raise ValueError("proposal not found")
    if pr["status"] != "pending":
        raise ValueError(f"proposal is {pr['status']}")
    repo = db.one("SELECT * FROM repos WHERE id=?", (pr["repo_id"],))
    p = resolve_safe(repo["root_path"], pr["path"])
    content = p.read_text()
    if content.count(pr["old_text"]) != 1:
        db.execute("UPDATE proposals SET status='stale', decided_by=?, decided_at=? WHERE id=?", (user, now_iso(), proposal_id))
        raise ValueError("file changed since the proposal was made; proposal marked stale")
    tmp = p.with_name(p.name + ".logfix.tmp")
    tmp.write_text(content.replace(pr["old_text"], pr["new_text"], 1))
    os.replace(tmp, p)  # atomic
    db.execute("UPDATE proposals SET status='applied', decided_by=?, decided_at=? WHERE id=?", (user, now_iso(), proposal_id))
    db.audit(user, "proposal.apply", str(proposal_id), {"path": pr["path"]})
    return {"status": "applied", "path": pr["path"]}


def _spec(name, desc, props, req=None):
    return ToolSpec(name, desc, {"type": "object", "properties": props, "required": req or []})


CODE_TOOLS: list[Tool] = [
    Tool(_spec("list_files", "List files in the repository (optionally under a sub-path).",
               {"path": {"type": "string"}, "max": {"type": "integer"}}), list_files),
    Tool(_spec("read_file", "Read a text file with line numbers. Use start_line/end_line for large files.",
               {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, ["path"]), read_file),
    Tool(_spec("grep", "Case-insensitive regex search across the repository.",
               {"pattern": {"type": "string"}, "glob": {"type": "string", "description": "file name glob, e.g. *.py"},
                "max_results": {"type": "integer"}}, ["pattern"]), grep),
    Tool(_spec("git_log", "Recent commits.", {"max": {"type": "integer"}}), git_log),
    Tool(_spec("propose_edit",
               "Propose a code change as an exact text replacement. old_text must match exactly once. "
               "This only saves a proposal for human review; it does not change any file.",
               {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"},
                "rationale": {"type": "string"}}, ["path", "old_text", "new_text"]), propose_edit),
]
