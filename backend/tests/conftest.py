import re
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def env(tmp_path):
    s = Settings(data_dir=tmp_path / "data", disable_scheduler=True, admin_password="AdminPass123")
    app = create_app(s)
    app.state.ctx.db.set_setting("detection", {"auto_pipeline": False})  # tests opt in explicitly
    c = TestClient(app)
    tok = c.post("/api/auth/login", json={"username": "admin", "password": "AdminPass123"}).json()["token"]
    c.headers.update({"Authorization": f"Bearer {tok}"})
    return type("Env", (), {"client": c, "ctx": app.state.ctx, "app": app, "tmp": tmp_path, "settings": s})


def make_user(env, name, role):
    r = env.client.post("/api/users", json={"username": name, "password": "UserPass1234", "role": role})
    assert r.status_code == 201, r.text
    env.ctx.db.execute("UPDATE users SET must_change=0 WHERE username=?", (name,))
    tok = env.client.post("/api/auth/login", json={"username": name, "password": "UserPass1234"}, headers={"Authorization": ""}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def inject(env, scenario="connection_pool"):
    sid = env.client.get("/api/sources").json()[0]["id"]
    r = env.client.post(f"/api/sources/{sid}/demo/inject", json={"scenario": scenario})
    assert r.status_code == 200, r.text
    return sid, r.json()
