import os
import pytest
from app.agents.code_tools import PathError, resolve_safe, apply_proposal
from tests.conftest import make_user


@pytest.fixture()
def repo(env):
    root = env.tmp / "proj"; (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def f():\n    return 1\n")
    (root / ".env").write_text("SECRET=abc")
    (root / "key.pem").write_text("-----BEGIN PRIVATE KEY-----")
    (root / "bin.dat").write_bytes(b"\x00\x01\x02")
    (env.tmp / "outside.txt").write_text("outside")
    os.symlink(env.tmp / "outside.txt", root / "link.txt")
    rid = env.client.post("/api/repos", json={"name": "proj", "root_path": str(root)}).json()["id"]
    return type("R", (), {"root": root, "id": rid})


@pytest.mark.parametrize("bad", ["../outside.txt", "/etc/passwd", "src/../../outside.txt", ".git/config", "C:\\Windows\\x", ".env", "key.pem", "link.txt"])
def test_path_safety(repo, bad):
    with pytest.raises(PathError):
        resolve_safe(repo.root, bad)


def test_ok_path(repo):
    assert resolve_safe(repo.root, "src/a.py").name == "a.py"


def test_repo_path_validation(env):
    assert env.client.post("/api/repos", json={"name": "x", "root_path": "/etc"}).status_code == 400
    assert env.client.post("/api/repos", json={"name": "y", "root_path": "/nonexistent/dir"}).status_code == 400


def test_agent_tools_refuse_secrets_and_scope(env, repo):
    from app.agents.code_tools import read_file, grep, list_files
    from app.agents.framework import AgentContext
    a = AgentContext(app=env.ctx, extra={"repo_root": str(repo.root), "repo_id": repo.id})
    with pytest.raises(PathError):
        read_file({"path": ".env"}, a)
    assert read_file({"path": "bin.dat"}, a)["error"] == "binary file"
    files = list_files({}, a)["files"]
    assert "src/a.py" in files and ".env" not in files and "key.pem" not in files and "link.txt" not in files
    assert grep({"pattern": "SECRET|outside|PRIVATE"}, a)["matches"] == []


def test_ask_reads_code(env, repo):
    r = env.client.post(f"/api/repos/{repo.id}/ask", json={"question": "find the return value"}).json()
    assert r["error"] is None and "a.py" in r["answer"]


def _propose(env, repo, old="return 1", new="return 2"):
    from app.agents.code_tools import propose_edit
    from app.agents.framework import AgentContext
    a = AgentContext(app=env.ctx, user="admin", extra={"repo_root": str(repo.root), "repo_id": repo.id})
    return propose_edit({"path": "src/a.py", "old_text": old, "new_text": new, "rationale": "r"}, a)


def test_propose_does_not_modify_files_until_apply(env, repo):
    r = _propose(env, repo)
    assert r["status"] == "pending" and "return 1" in (repo.root / "src" / "a.py").read_text()
    pr = env.client.get("/api/proposals").json()[0]
    assert "-    return 1" in pr["diff"] and "+    return 2" in pr["diff"]
    assert env.client.post(f"/api/proposals/{pr['id']}/apply").json()["status"] == "applied"
    assert "return 2" in (repo.root / "src" / "a.py").read_text()
    assert env.client.post(f"/api/proposals/{pr['id']}/apply").status_code == 409  # not re-appliable


def test_propose_requires_unique_match(env, repo):
    assert "found 0" in _propose(env, repo, old="nothing here")["error"]
    (repo.root / "src" / "a.py").write_text("x\nx\n")
    assert "found 2" in _propose(env, repo, old="x")["error"]


def test_stale_proposal_not_applied(env, repo):
    _propose(env, repo)
    (repo.root / "src" / "a.py").write_text("def f():\n    return 99\n")
    pid = env.client.get("/api/proposals").json()[0]["id"]
    assert env.client.post(f"/api/proposals/{pid}/apply").status_code == 409
    assert env.client.get("/api/proposals").json()[0]["status"] == "stale"
    assert "return 99" in (repo.root / "src" / "a.py").read_text()


def test_only_admin_applies_analyst_can_reject(env, repo):
    from fastapi.testclient import TestClient
    _propose(env, repo)
    pid = env.client.get("/api/proposals").json()[0]["id"]
    ana = make_user(env, "ana", "analyst")
    c = TestClient(env.app)
    assert c.post(f"/api/proposals/{pid}/apply", headers=ana).status_code == 403
    assert c.post(f"/api/proposals/{pid}/reject", headers=ana).status_code == 200
    assert "return 1" in (repo.root / "src" / "a.py").read_text()
