"""GitHub integration: clone/sync a repository by URL, verify webhooks, review pull requests, open fix PRs.

Security notes
- Only https URLs on an allowlisted host (github.com by default; add GitHub Enterprise hosts with LOGFIX_GIT_HOSTS).
- The token is stored encrypted and passed to git through environment config (never in the URL, argv or .git/config).
- Git hooks and the file:// protocol are disabled; clones are shallow.
- Webhooks are accepted only with a valid HMAC-SHA256 signature; deliveries are de-duplicated.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..agents.code_tools import resolve_safe
from ..context import AppContext
from ..db import now_iso
from ..redact import redact

GIT_TIMEOUT = 240
ALLOWED_HOSTS = {"github.com"}
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,100}$")
_http_transport: httpx.BaseTransport | None = None   # tests inject a MockTransport here


class GitHubError(Exception):
    pass


def allowed_hosts() -> set[str]:
    extra = {h.strip().lower() for h in os.getenv("LOGFIX_GIT_HOSTS", "").split(",") if h.strip()}
    return ALLOWED_HOSTS | extra


def parse_repo_url(url: str) -> tuple[str, str, str]:
    """Return (host, owner, repo). Raises GitHubError for anything that is not a plain https repo URL."""
    u = urlparse((url or "").strip())
    if u.scheme != "https" or not u.hostname:
        raise GitHubError("Repository URL must start with https://")
    if u.username or u.password:
        raise GitHubError("Do not put credentials in the URL; use the token field")
    host = u.hostname.lower()
    if host not in allowed_hosts():
        raise GitHubError(f"Host '{host}' is not allowed. Allowed: {', '.join(sorted(allowed_hosts()))} (extend with LOGFIX_GIT_HOSTS)")
    parts = [p for p in u.path.split("/") if p]
    if len(parts) != 2:
        raise GitHubError("URL must look like https://github.com/owner/repo")
    owner, name = parts[0], re.sub(r"\.git$", "", parts[1])
    if not _NAME.match(owner) or not _NAME.match(name) or owner in (".", "..") or name in (".", ".."):
        raise GitHubError("Invalid owner or repository name")
    return host, owner, name


def api_base(host: str) -> str:
    return "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"


def clone_dir(actx: AppContext, repo_id: int) -> Path:
    return actx.settings.data_dir / "repos" / str(repo_id)


def _env(token: str | None) -> dict:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "SYSTEMROOT", "TMPDIR")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo", "GIT_CONFIG_NOSYSTEM": "1"})
    cfgs = [("core.hooksPath", os.devnull), ("protocol.file.allow", "never"), ("core.fsmonitor", "false"), ("advice.detachedHead", "false")]
    if token:
        b = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cfgs.append(("http.extraheader", f"Authorization: Basic {b}"))
    env["GIT_CONFIG_COUNT"] = str(len(cfgs))
    for i, (k, v) in enumerate(cfgs):
        env[f"GIT_CONFIG_KEY_{i}"] = k
        env[f"GIT_CONFIG_VALUE_{i}"] = v
    return env


def git(args: list[str], cwd: Path | None = None, token: str | None = None, timeout: int = GIT_TIMEOUT, allow_file: bool = False) -> str:
    env = _env(token)
    if allow_file:  # tests only: lets git read a local bare repo
        for i in range(int(env["GIT_CONFIG_COUNT"])):
            if env[f"GIT_CONFIG_KEY_{i}"] == "protocol.file.allow":
                env[f"GIT_CONFIG_VALUE_{i}"] = "always"
    try:
        p = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise GitHubError(f"git {args[0]} timed out after {timeout}s") from e
    except FileNotFoundError as e:
        raise GitHubError("git is not installed on the server") from e
    if p.returncode != 0:
        msg = (p.stderr or p.stdout or "").strip()
        if token:
            msg = msg.replace(token, "***")
        raise GitHubError(f"git {args[0]} failed: {redact(msg)[:400]}")
    return p.stdout


def remote_url(row: dict) -> str:
    return row["url"]


def get_token(actx: AppContext, row: dict) -> str | None:
    return actx.secrets.decrypt(row.get("token_enc")).get("token") or None


def sync_repo(actx: AppContext, repo_id: int, allow_file: bool = False) -> dict:
    """Clone on first use, otherwise fetch and fast-forward to origin/<branch>. Never blocks the request thread."""
    db = actx.db
    row = db.one("SELECT * FROM repos WHERE id=?", (repo_id,))
    if not row or row["kind"] != "github":
        raise GitHubError("not a linked GitHub repository")
    dest = clone_dir(actx, repo_id)
    token, branch = get_token(actx, row), row["branch"] or None
    db.execute("UPDATE repos SET sync_status='syncing', sync_error=NULL WHERE id=?", (repo_id,))
    try:
        if (dest / ".git").is_dir():
            git(["fetch", "--depth", "50", "--prune", "origin"], dest, token, allow_file=allow_file)
            ref = f"origin/{branch}" if branch else "origin/HEAD"
            if not branch:
                git(["remote", "set-head", "origin", "-a"], dest, token, allow_file=allow_file)
            git(["reset", "--hard", ref], dest, token)
            git(["clean", "-fdx"], dest, token)
        else:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            dest.parent.mkdir(parents=True, exist_ok=True)
            args = ["clone", "--depth", "50", "--no-tags"]
            if branch:
                args += ["--branch", branch]
            git([*args, "--", remote_url(row), str(dest)], None, token, allow_file=allow_file)
        head = git(["rev-parse", "HEAD"], dest).strip()
        cur_branch = git(["rev-parse", "--abbrev-ref", "HEAD"], dest).strip()
        db.execute("UPDATE repos SET root_path=?, sync_status='ready', sync_error=NULL, last_synced_at=?, last_commit=?, branch=COALESCE(branch,?) WHERE id=?",
                   (str(dest), now_iso(), head, cur_branch, repo_id))
        return {"status": "ready", "commit": head, "branch": cur_branch}
    except Exception as e:  # noqa: BLE001
        msg = redact(str(e))[:500]
        db.execute("UPDATE repos SET sync_status='error', sync_error=? WHERE id=?", (msg, repo_id))
        raise GitHubError(msg) from e


def sync_async(actx: AppContext, repo_id: int, user: str = "system") -> None:
    from ..services import activity

    def work():
        with activity.Tracked(actx, "code_assistant", user=user) as t:
            try:
                r = sync_repo(actx, repo_id)
                t.ok(f"synced repo #{repo_id} at {r['commit'][:8]} ({r['branch']})")
            except Exception as e:  # noqa: BLE001
                t.fail(str(e), summary=f"sync of repo #{repo_id} failed")

    threading.Thread(target=work, daemon=True, name=f"sync-{repo_id}").start()


# ---------------- webhooks ----------------
def new_webhook_secret() -> str:
    return secrets.token_urlsafe(32)


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def webhook_secret(actx: AppContext, row: dict) -> str:
    return actx.secrets.decrypt(row.get("webhook_secret_enc")).get("secret", "")


# ---------------- REST API ----------------
def _client(token: str | None) -> httpx.Client:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "logfix-ai"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return httpx.Client(headers=h, timeout=30, transport=_http_transport)


def _api(row: dict, token: str | None, method: str, path: str, **kw) -> httpx.Response:
    host, owner, name = parse_repo_url(row["url"])
    with _client(token) as c:
        r = c.request(method, f"{api_base(host)}/repos/{owner}/{name}{path}", **kw)
    if r.status_code >= 300:
        raise GitHubError(f"GitHub API {r.status_code}: {redact(r.text)[:200]}")
    return r


def fetch_pr_files(row: dict, token: str | None, number: int, max_files: int = 60) -> list[dict]:
    r = _api(row, token, "GET", f"/pulls/{number}/files", params={"per_page": max_files})
    return [{"path": f["filename"], "status": f.get("status"), "additions": f.get("additions", 0),
             "deletions": f.get("deletions", 0), "patch": f.get("patch") or ""} for f in r.json()]


def post_pr_comment(row: dict, token: str | None, number: int, body: str) -> str:
    r = _api(row, token, "POST", f"/issues/{number}/comments", json={"body": redact(body)[:60000]})
    return r.json().get("html_url", "")


def create_pull_request(row: dict, token: str | None, head: str, base: str, title: str, body: str) -> dict:
    r = _api(row, token, "POST", "/pulls", json={"title": title[:200], "head": head, "base": base, "body": redact(body)[:20000]})
    d = r.json()
    return {"number": d.get("number"), "url": d.get("html_url")}


# ---------------- proposal -> branch -> PR ----------------
def open_pr_for_proposal(actx: AppContext, proposal_id: int, user: str, allow_file: bool = False) -> dict:
    db = actx.db
    pr = db.one("SELECT * FROM proposals WHERE id=?", (proposal_id,))
    if not pr:
        raise GitHubError("proposal not found")
    if pr["status"] != "pending":
        raise GitHubError(f"proposal is {pr['status']}")
    row = db.one("SELECT * FROM repos WHERE id=?", (pr["repo_id"],))
    if row["kind"] != "github":
        raise GitHubError("pull requests can only be opened for linked GitHub repositories")
    if not row["allow_pr"]:
        raise GitHubError("Pull request creation is switched off for this repository. Enable it in the repository settings.")
    token = get_token(actx, row)
    if not token:
        raise GitHubError("A token with write access is required to push a branch")
    repo_dir = clone_dir(actx, row["id"])
    if not (repo_dir / ".git").is_dir():
        raise GitHubError("Repository is not synced yet")
    base = row["branch"] or git(["rev-parse", "--abbrev-ref", "HEAD"], repo_dir).strip()
    branch = f"logfix/fix-{proposal_id}-{secrets.token_hex(3)}"
    wt = repo_dir.parent / f"{row['id']}-wt-{proposal_id}"
    try:
        git(["fetch", "--depth", "50", "origin", base], repo_dir, token, allow_file=allow_file)
        git(["worktree", "add", "-b", branch, str(wt), f"origin/{base}"], repo_dir, allow_file=allow_file)
        target = resolve_safe(wt, pr["path"])
        content = target.read_text()
        if content.count(pr["old_text"]) != 1:
            db.execute("UPDATE proposals SET status='stale', decided_by=?, decided_at=? WHERE id=?", (user, now_iso(), proposal_id))
            raise GitHubError("The file changed upstream since the proposal was made; proposal marked stale")
        target.write_text(content.replace(pr["old_text"], pr["new_text"], 1))
        git(["add", "--", pr["path"]], wt)
        git(["-c", "user.name=LogFix AI", "-c", "user.email=logfix-ai@users.noreply.github.com", "commit", "-m",
             f"LogFix AI: {(pr['rationale'] or 'proposed fix')[:70]}\n\nProposal #{proposal_id}, approved by {user}."], wt)
        git(["push", "origin", f"{branch}:{branch}"], wt, token, allow_file=allow_file)
        out = {"branch": branch, "base": base}
        if row["url"] and not allow_file:
            created = create_pull_request(row, token, branch, base, f"LogFix AI: {(pr['rationale'] or 'proposed fix')[:80]}",
                                          f"Proposed by LogFix AI and approved by **{user}**.\n\n**Why:** {pr['rationale'] or 'n/a'}\n\n```diff\n{pr['diff'][:6000]}\n```\n\n"
                                          "_Review carefully. LogFix did not run tests on this change._")
            out.update(created)
        db.execute("UPDATE proposals SET status='applied', decided_by=?, decided_at=?, pr_url=? WHERE id=?",
                   (user, now_iso(), out.get("url") or f"branch:{branch}", proposal_id))
        db.audit(user, "proposal.pr", str(proposal_id), out)
        return out
    finally:
        try:
            git(["worktree", "remove", "--force", str(wt)], repo_dir)
        except GitHubError:
            shutil.rmtree(wt, ignore_errors=True)
