import json
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from tests.conftest import inject, make_user


def test_unauthenticated_and_bad_token(env):
    c = TestClient(env.app)
    assert c.get("/api/sources").status_code == 401
    assert c.get("/api/sources", headers={"Authorization": "Bearer junk"}).status_code == 401
    assert c.get("/api/health").status_code == 200


def test_login_failure_and_lockout(env):
    c = TestClient(env.app)
    for _ in range(5):
        assert c.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "admin", "password": "AdminPass123"}).status_code == 429


def test_role_enforcement(env):
    viewer, analyst = make_user(env, "vic", "viewer"), make_user(env, "ana", "analyst")
    c = TestClient(env.app)
    assert c.get("/api/incidents", headers=viewer).status_code == 200
    assert c.post("/api/sources", headers=viewer, json={"name": "x", "cloud": "demo"}).status_code == 403
    assert c.get("/api/users", headers=analyst).status_code == 403
    assert c.get("/api/audit", headers=analyst).status_code == 403
    assert c.put("/api/settings/llm", headers=analyst, json={"provider": "mock"}).status_code == 403
    sid = env.client.get("/api/sources").json()[0]["id"]
    assert c.post(f"/api/sources/{sid}/collect", headers=viewer).status_code == 403
    assert c.post(f"/api/sources/{sid}/collect", headers=analyst).status_code == 200
    assert c.post("/api/proposals/1/apply", headers=analyst).status_code == 403


def test_disabled_user_loses_access_immediately(env):
    h = make_user(env, "bob", "viewer")
    uid = [u for u in env.client.get("/api/users").json() if u["username"] == "bob"][0]["id"]
    c = TestClient(env.app)
    assert c.get("/api/overview", headers=h).status_code == 200
    env.client.patch(f"/api/users/{uid}", json={"disabled": True})
    assert c.get("/api/overview", headers=h).status_code == 401


def test_cannot_remove_last_admin(env):
    aid = env.client.get("/api/users").json()[0]["id"]
    assert env.client.patch(f"/api/users/{aid}", json={"role": "viewer"}).status_code == 400
    assert env.client.patch(f"/api/users/{aid}", json={"disabled": True}).status_code == 400


def test_first_run_generated_password_forces_change(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "d", disable_scheduler=True))
    c = TestClient(app)
    pw = [l for l in (tmp_path / "d" / "initial_admin_password.txt").read_text().splitlines() if l.startswith("password:")][0].split(": ")[1]
    tok = c.post("/api/auth/login", json={"username": "admin", "password": pw}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    r = c.get("/api/sources", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "password_change_required"
    assert c.post("/api/auth/change-password", headers=h, json={"old_password": pw, "new_password": "short"}).status_code == 400
    assert c.post("/api/auth/change-password", headers=h, json={"old_password": pw, "new_password": "BrandNewPass9"}).status_code == 200
    assert c.get("/api/sources", headers=h).status_code == 200


def test_secrets_encrypted_at_rest_and_never_returned(env):
    r = env.client.post("/api/sources", json={"name": "az", "cloud": "azure", "secrets": {"client_secret": "TOPSECRET-123", "junk": "x"},
                                              "config": {"workspace_id": "w"}})
    assert r.status_code == 201 and "TOPSECRET" not in r.text and r.json()["secrets_set"] == ["client_secret"]
    raw = env.ctx.db.one("SELECT secrets_enc FROM sources WHERE name='az'")["secrets_enc"]
    assert "TOPSECRET" not in raw and env.ctx.secrets.decrypt(raw)["client_secret"] == "TOPSECRET-123"
    assert "TOPSECRET" not in env.client.get("/api/sources").text


def test_blank_secret_on_patch_keeps_stored(env):
    sid = env.client.post("/api/sources", json={"name": "az", "cloud": "azure", "secrets": {"client_secret": "keepme"}}).json()["id"]
    env.client.patch(f"/api/sources/{sid}", json={"secrets": {"client_secret": ""}, "interval_seconds": 60})
    raw = env.ctx.db.one("SELECT secrets_enc FROM sources WHERE id=?", (sid,))["secrets_enc"]
    assert env.ctx.secrets.decrypt(raw)["client_secret"] == "keepme"


def test_interval_validation(env):
    assert env.client.post("/api/sources", json={"name": "a", "cloud": "demo", "interval_seconds": 5}).status_code == 400
    assert env.client.post("/api/sources", json={"name": "b", "cloud": "demo", "interval_seconds": 60}).status_code == 201


def test_source_test_reports_connector_errors(env):
    sid = env.client.post("/api/sources", json={"name": "az", "cloud": "azure"}).json()["id"]
    r = env.client.post(f"/api/sources/{sid}/test").json()
    assert r["ok"] is False and r["kind"] == "config"


def test_collect_error_is_recorded_on_source(env):
    sid = env.client.post("/api/sources", json={"name": "az", "cloud": "azure"}).json()["id"]
    r = env.client.post(f"/api/sources/{sid}/collect").json()
    assert r["error"].startswith("config")
    assert env.client.get("/api/sources").json()[-1]["last_status"] == "error"


def test_audit_trail_records_admin_actions(env):
    env.client.post("/api/sources", json={"name": "a", "cloud": "demo"})
    acts = [a["action"] for a in env.client.get("/api/audit").json()]
    assert "source.create" in acts and "login" in acts


def test_logs_search_filters_and_like_escaping(env):
    inject(env)
    r = env.client.get("/api/logs", params={"severity": "HIGH", "q": "pool exhausted"}).json()
    assert r["total"] > 0 and all(l["severity"] == "HIGH" for l in r["logs"])
    assert env.client.get("/api/logs", params={"q": "%"}).json()["total"] == 0  # % is literal, not a wildcard


def test_overview_and_report(env):
    inject(env)
    o = env.client.get("/api/overview").json()
    assert o["errors_last_hour"] > 0 and len(o["timeline"]) == 13 and o["llm"]["is_real"] is False
    iid = env.client.get("/api/incidents").json()[0]["id"]
    assert env.client.get(f"/api/incidents/{iid}/report.html").status_code == 200


def test_spa_fallback_does_not_serve_outside_dist(tmp_path):
    dist = tmp_path / "dist"; dist.mkdir(); (dist / "index.html").write_text("<html>app</html>"); (tmp_path / "secret.txt").write_text("nope")
    app = create_app(Settings(data_dir=tmp_path / "d", disable_scheduler=True, admin_password="AdminPass123", frontend_dist=dist))
    c = TestClient(app)
    assert "app" in c.get("/some/route").text
    assert "nope" not in c.get("/..%2fsecret.txt").text
    assert c.get("/api/nothing").status_code == 404
    assert c.get("/").headers["content-security-policy"].startswith("default-src 'self'")
