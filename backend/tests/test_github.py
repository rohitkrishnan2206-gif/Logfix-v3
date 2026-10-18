import hashlib
import hmac
import json
import subprocess
import time

import httpx
import pytest

from app.agents import pr_review
from app.services import github as gh


def sh(*a, cwd=None):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True,
                   env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/tmp", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def remote(tmp_path):
    work, bare = tmp_path / "work", tmp_path / "remote.git"
    work.mkdir()
    sh("git", "init", "-q", "-b", "main", cwd=work)
    (work / "app.py").write_text("def pay(x):\n    return x * 2\n")
    (work / ".env").write_text("SECRET=abc")
    sh("git", "add", ".", cwd=work)
    sh("git", "commit", "-q", "-m", "init", cwd=work)
    sh("git", "clone", "-q", "--bare", str(work), str(bare))
    return {"work": work, "bare": bare}


@pytest.fixture
def linked(env, remote, monkeypatch):
    monkeypatch.setattr(gh, "sync_async", lambda *a, **k: None)
    r = env.client.post("/api/repos/github", json={"url": "https://github.com/acme/demo", "token": "ghp_SECRETTOKEN123", "allow_pr": True})
    assert r.status_code == 201, r.text
    d = r.json()
    monkeypatch.setattr(gh, "remote_url", lambda row: str(remote["bare"]))
    return {**d, "remote": remote}


def sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def hook(env, linked, event, payload, delivery="d1", secret=None):
    body = json.dumps(payload).encode()
    return env.client.post(f"/api/webhooks/github/{linked['id']}", content=body, headers={
        "X-GitHub-Event": event, "X-GitHub-Delivery": delivery, "Content-Type": "application/json",
        "X-Hub-Signature-256": sign(secret or linked["webhook_secret"], body), "Authorization": ""})


# ---- URL rules ----
@pytest.mark.parametrize("url", ["http://github.com/a/b", "ssh://git@github.com/a/b", "file:///etc/passwd", "https://evil.com/a/b",
                                 "https://user:pw@github.com/a/b", "https://github.com/a", "https://github.com/a/b/c", "https://localhost/a/b",
                                 "https://github.com/../b"])
def test_bad_urls_rejected(env, url):
    assert env.client.post("/api/repos/github", json={"url": url}).status_code == 400


def test_url_parsing():
    assert gh.parse_repo_url("https://github.com/acme/demo.git") == ("github.com", "acme", "demo")


def test_only_admin_can_link(env):
    from conftest import make_user
    h = make_user(env, "ana", "analyst")
    assert env.client.post("/api/repos/github", json={"url": "https://github.com/a/b"}, headers=h).status_code == 403


def test_secrets_not_exposed(env, linked):
    t = env.client.get("/api/repos").text
    assert "ghp_SECRETTOKEN123" not in t and "token_enc" not in t and "webhook_secret_enc" not in t
    r = next(x for x in env.client.get("/api/repos").json() if x["id"] == linked["id"])
    assert r["has_token"] and r["has_webhook"] and r["webhook_url"].endswith(f"/api/webhooks/github/{linked['id']}")
    raw = env.ctx.db.one("SELECT token_enc FROM repos WHERE id=?", (linked["id"],))["token_enc"]
    assert "ghp_SECRETTOKEN123" not in raw


# ---- sync ----
def test_sync_clone_then_fast_forward(env, linked):
    r = gh.sync_repo(env.ctx, linked["id"], allow_file=True)
    assert r["status"] == "ready" and r["branch"] == "main"
    row = env.ctx.db.one("SELECT * FROM repos WHERE id=?", (linked["id"],))
    assert row["sync_status"] == "ready" and row["last_commit"] == r["commit"]
    # new commit upstream -> second sync picks it up
    w = linked["remote"]["work"]
    (w / "new.py").write_text("x = 1\n")
    sh("git", "add", ".", cwd=w); sh("git", "commit", "-q", "-m", "more", cwd=w); sh("git", "push", "-q", str(linked["remote"]["bare"]), "main", cwd=w)
    r2 = gh.sync_repo(env.ctx, linked["id"], allow_file=True)
    assert r2["commit"] != r["commit"] and (gh.clone_dir(env.ctx, linked["id"]) / "new.py").exists()


def test_sync_failure_is_recorded_and_token_hidden(env, linked, monkeypatch):
    monkeypatch.setattr(gh, "remote_url", lambda row: "/nonexistent/ghp_SECRETTOKEN123.git")
    with pytest.raises(gh.GitHubError) as e:
        gh.sync_repo(env.ctx, linked["id"], allow_file=True)
    assert "ghp_SECRETTOKEN123" not in str(e.value)
    row = env.ctx.db.one("SELECT sync_status,sync_error FROM repos WHERE id=?", (linked["id"],))
    assert row["sync_status"] == "error" and "ghp_SECRETTOKEN123" not in row["sync_error"]


def test_ask_requires_ready_then_works_and_blocks_env(env, linked):
    r = env.client.post(f"/api/repos/{linked['id']}/ask", json={"question": "how does pay work?"})
    assert r.status_code == 404 and "not ready" in r.text
    gh.sync_repo(env.ctx, linked["id"], allow_file=True)
    r = env.client.post(f"/api/repos/{linked['id']}/ask", json={"question": "pay"})
    assert r.status_code == 200 and "app.py" in json.dumps(r.json()["trace"])
    from app.agents.code_tools import PathError, resolve_safe
    with pytest.raises(PathError):
        resolve_safe(gh.clone_dir(env.ctx, linked["id"]), ".env")


def test_token_goes_via_env_not_argv():
    env = gh._env("tok123")
    assert "tok123" not in json.dumps([v for k, v in env.items() if k.startswith("GIT_CONFIG_VALUE") and "Authorization" not in v])
    assert env["GIT_CONFIG_VALUE_0"] == "/dev/null" or "hooksPath" in env["GIT_CONFIG_KEY_0"]


# ---- webhook ----
def test_webhook_signature_required(env, linked):
    assert hook(env, linked, "ping", {}, secret="wrong").status_code == 401
    assert env.client.post(f"/api/webhooks/github/{linked['id']}", content=b"{}", headers={"X-GitHub-Event": "ping", "Authorization": ""}).status_code == 401
    assert hook(env, linked, "ping", {}).json()["status"] == "pong"
    assert env.client.post("/api/webhooks/github/9999", content=b"{}", headers={"Authorization": ""}).status_code == 404


def test_webhook_duplicate_delivery_ignored(env, linked):
    assert hook(env, linked, "ping", {}, delivery="same").json()["status"] == "pong"
    assert hook(env, linked, "ping", {}, delivery="same").json()["status"] == "duplicate"


def test_push_triggers_sync_only_for_tracked_branch(env, linked, monkeypatch):
    called = []
    monkeypatch.setattr(gh, "sync_async", lambda ctx, rid, user="system": called.append(rid))
    env.client.patch(f"/api/repos/{linked['id']}", json={"branch": "main"})
    assert hook(env, linked, "push", {"ref": "refs/heads/feature"}, "p1").json()["status"] == "ignored"
    assert hook(env, linked, "push", {"ref": "refs/heads/main"}, "p2").json()["status"] == "sync_started"
    assert called == [linked["id"]]


PATCH = "@@ -1,2 +1,4 @@\n def pay(x):\n-    return x\n+    api_key = \"sk_live_abcdefghijk123\"\n+    print(x)\n+    return eval(x)\n"


def wait_review(env, rid, n=100):
    for _ in range(n):
        rows = env.client.get(f"/api/repos/{rid}/reviews").json()
        if rows and rows[0]["status"] != "running":
            return rows[0]
        time.sleep(0.1)
    raise AssertionError("review did not finish")


def test_pull_request_event_runs_review(env, linked):
    posted = []

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json=[{"filename": "app.py", "status": "modified", "additions": 3, "deletions": 1, "patch": PATCH}])
        posted.append(json.loads(req.content)["body"])
        return httpx.Response(201, json={"html_url": "https://github.com/acme/demo/pull/7#c1"})
    gh._http_transport = httpx.MockTransport(handler)
    try:
        env.client.patch(f"/api/repos/{linked['id']}", json={"pr_comments": True})
        r = hook(env, linked, "pull_request", {"action": "opened", "number": 7, "pull_request": {"number": 7, "title": "Fix pay", "head": {"sha": "abc"}, "user": {"login": "dev"}}}, "pr1")
        assert r.json()["status"] == "review_started"
        rv = wait_review(env, linked["id"])
    finally:
        gh._http_transport = None
    assert rv["status"] == "done", rv
    res = rv["result"]
    assert res["verdict"] == "request_changes" and res["risk"] == "high"
    kinds = " ".join(f["comment"] for f in res["findings"])
    assert "secret" in kinds.lower() and "eval" in kinds.lower() and "Debug output" in kinds
    assert next(f for f in res["findings"] if "eval" in f["comment"])["line"] == 4
    assert rv["comment_url"] and "sk_live_abcdefghijk123" not in posted[0] and "LogFix AI review of #7" in posted[0]


def test_review_comment_not_posted_by_default(env, linked):
    posted = []

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json=[{"filename": "a.py", "status": "added", "additions": 1, "deletions": 0, "patch": "@@ -0,0 +1 @@\n+x = 1\n"}])
        posted.append(1)
        return httpx.Response(201, json={})
    gh._http_transport = httpx.MockTransport(handler)
    try:
        hook(env, linked, "pull_request", {"action": "opened", "pull_request": {"number": 8, "title": "t", "head": {"sha": "a"}, "user": {"login": "d"}}}, "pr2")
        rv = wait_review(env, linked["id"])
    finally:
        gh._http_transport = None
    assert rv["status"] == "done" and not posted and rv["result"]["verdict"] == "approve"


def test_draft_and_closed_prs_ignored(env, linked):
    assert hook(env, linked, "pull_request", {"action": "closed", "pull_request": {}}, "c1").json()["status"] == "ignored"
    assert hook(env, linked, "pull_request", {"action": "opened", "pull_request": {"draft": True, "number": 1}}, "c2").json()["reason"] == "draft"


def test_review_failure_is_recorded(env, linked):
    gh._http_transport = httpx.MockTransport(lambda r: httpx.Response(404, text="not found"))
    try:
        hook(env, linked, "pull_request", {"action": "opened", "pull_request": {"number": 9, "title": "t", "head": {"sha": "a"}, "user": {"login": "d"}}}, "pr3")
        rv = wait_review(env, linked["id"])
    finally:
        gh._http_transport = None
    assert rv["status"] == "failed" and "404" in rv["error"]


def test_rule_findings_line_numbers():
    f = pr_review.rule_findings([{"path": "x.py", "patch": "@@ -10,2 +10,3 @@\n ctx\n+# TODO fix\n-old\n+ok\n"}])
    assert len(f) == 1 and f[0]["line"] == 11


# ---- PR creation ----
def make_proposal(env, rid):
    return env.ctx.db.execute(
        "INSERT INTO proposals(repo_id,path,old_text,new_text,rationale,diff,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (rid, "app.py", "return x * 2", "return x * 3", "fix multiplier", "diff", "admin", "2026-01-01T00:00:00Z"))


def test_open_pr_pushes_branch_without_touching_clone(env, linked):
    gh.sync_repo(env.ctx, linked["id"], allow_file=True)
    pid = make_proposal(env, linked["id"])
    out = gh.open_pr_for_proposal(env.ctx, pid, "admin", allow_file=True)
    branches = subprocess.run(["git", "branch", "--list"], cwd=linked["remote"]["bare"], capture_output=True, text=True).stdout
    assert out["branch"] in branches
    shown = subprocess.run(["git", "show", f"{out['branch']}:app.py"], cwd=linked["remote"]["bare"], capture_output=True, text=True).stdout
    assert "x * 3" in shown
    assert "x * 2" in (gh.clone_dir(env.ctx, linked["id"]) / "app.py").read_text()   # local clone unchanged
    assert env.ctx.db.one("SELECT status FROM proposals WHERE id=?", (pid,))["status"] == "applied"
    log = subprocess.run(["git", "log", "-1", "--format=%an|%s", out["branch"]], cwd=linked["remote"]["bare"], capture_output=True, text=True).stdout
    assert log.startswith("LogFix AI|")


def test_open_pr_guards(env, linked):
    gh.sync_repo(env.ctx, linked["id"], allow_file=True)
    pid = make_proposal(env, linked["id"])
    env.client.patch(f"/api/repos/{linked['id']}", json={"allow_pr": False})
    r = env.client.post(f"/api/proposals/{pid}/pr")
    assert r.status_code == 409 and "switched off" in r.text
    env.client.patch(f"/api/repos/{linked['id']}", json={"allow_pr": True})
    env.ctx.db.execute("UPDATE proposals SET old_text='nope' WHERE id=?", (pid,))
    with pytest.raises(gh.GitHubError, match="stale"):
        gh.open_pr_for_proposal(env.ctx, pid, "admin", allow_file=True)
    assert env.ctx.db.one("SELECT status FROM proposals WHERE id=?", (pid,))["status"] == "stale"


def test_rotate_secret_invalidates_old(env, linked):
    new = env.client.post(f"/api/repos/{linked['id']}/webhook/rotate").json()["webhook_secret"]
    assert hook(env, linked, "ping", {}, "r1").status_code == 401
    assert hook(env, linked, "ping", {}, "r2", secret=new).status_code == 200


def test_delete_removes_clone(env, linked):
    gh.sync_repo(env.ctx, linked["id"], allow_file=True)
    d = gh.clone_dir(env.ctx, linked["id"])
    assert d.exists()
    env.client.delete(f"/api/repos/{linked['id']}")
    assert not d.exists()
