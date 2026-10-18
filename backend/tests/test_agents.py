import json
from app.agents.framework import AgentContext, Tool, run_agent
from app.agents.investigator import ground, parse_result, run_investigation
from app.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall, ToolSpec
from app.services.collector import collect_source
from tests.conftest import inject


class Scripted(LLMProvider):
    name, model = "scripted", "s"

    def __init__(self, steps):
        self.steps, self.i, self.tools_seen = steps, 0, []

    def complete(self, system, messages, tools=None, max_tokens=0, temperature=0):
        self.tools_seen.append(bool(tools))
        s = self.steps[min(self.i, len(self.steps) - 1)]
        self.i += 1
        if isinstance(s, Exception):
            raise s
        return s


def echo_tool():
    return Tool(ToolSpec("echo", "d", {"type": "object", "properties": {}}), lambda a, c: {"echo": a})


def boom_tool():
    def f(a, c):
        raise RuntimeError("kaput")
    return Tool(ToolSpec("boom", "d", {"type": "object", "properties": {}}), f)


def test_loop_records_trace(env):
    llm = Scripted([LLMResponse(tool_calls=[ToolCall("1", "echo", {"x": 1})]), LLMResponse(text="done")])
    r = run_agent(llm, "sys", [{"role": "user", "content": "hi"}], [echo_tool()], AgentContext(app=env.ctx))
    assert r.text == "done" and [t["type"] for t in r.trace] == ["llm", "tool", "llm"] and r.trace[1]["args"] == {"x": 1}


def test_unknown_and_failing_tools_reported_not_fatal(env):
    llm = Scripted([LLMResponse(tool_calls=[ToolCall("1", "nope", {}), ToolCall("2", "boom", {})]), LLMResponse(text="ok")])
    r = run_agent(llm, "s", [{"role": "user", "content": "x"}], [boom_tool()], AgentContext(app=env.ctx))
    tools = [t for t in r.trace if t["type"] == "tool"]
    assert r.text == "ok" and not tools[0]["ok"] and not tools[1]["ok"] and "kaput" in tools[1]["error"]


def test_step_cap_forces_final_answer_without_tools(env):
    llm = Scripted([LLMResponse(tool_calls=[ToolCall("1", "echo", {})])] * 3 + [LLMResponse(text="forced")])
    r = run_agent(llm, "s", [{"role": "user", "content": "x"}], [echo_tool()], AgentContext(app=env.ctx), max_steps=3)
    assert r.forced_final and r.text == "forced" and llm.tools_seen[-1] is False


def test_llm_error_returned_not_raised(env):
    r = run_agent(Scripted([LLMError("auth", "denied")]), "s", [{"role": "user", "content": "x"}], [echo_tool()], AgentContext(app=env.ctx))
    assert r.error.startswith("auth") and r.trace[-1]["type"] == "error"


def test_grounding_flags_invented_citations():
    res = ground({"confidence": "high", "evidence": [{"log_id": 1, "note": "a"}, {"log_id": 999, "note": "b"}, {"log_id": "x"}]}, {1})
    assert res["grounding"] == {"verified": 1, "total": 3} and res["confidence"] == "medium"
    assert [e["verified"] for e in res["evidence"]] == [True, False, False]


def test_parse_result_handles_prose_and_json():
    assert parse_result('Sure: {"summary": "s"} thanks')["summary"] == "s"
    assert parse_result("just prose")["parse_error"] is True


def test_full_investigation_with_mock_provider(env):
    inject(env)
    inc = env.client.get("/api/incidents").json()[0]
    inv_id = run_investigation(env.ctx, inc["id"], "tester")
    inv = env.client.get(f"/api/investigations/{inv_id}").json()
    assert inv["status"] == "done" and inv["provider"] == "mock"
    g = inv["result"]["grounding"]
    assert g["total"] > 0 and g["verified"] == g["total"]
    assert {"top_error_patterns", "search_logs", "search_runbooks"} <= {t.get("name") for t in inv["trace"]}
    assert env.client.get(f"/api/incidents/{inc['id']}").json()["summary"]


def test_investigation_failure_is_recorded(env):
    inject(env)
    inc = env.client.get("/api/incidents").json()[0]
    env.ctx.llm.override = Scripted([LLMError("auth", "bad creds")])
    inv_id = run_investigation(env.ctx, inc["id"], "t")
    inv = env.client.get(f"/api/investigations/{inv_id}").json()
    assert inv["status"] == "failed" and "bad creds" in inv["error"]


def test_report_escapes_html(env):
    inject(env)
    inc = env.client.get("/api/incidents").json()[0]
    env.ctx.db.execute("UPDATE incidents SET title=? WHERE id=?", ("<script>alert(1)</script>", inc["id"]))
    html = env.client.get(f"/api/incidents/{inc['id']}/report.html").text
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


def test_chat_roundtrip_persists_history(env):
    inject(env)
    sid = env.client.post("/api/chat/sessions").json()["id"]
    r = env.client.post(f"/api/chat/sessions/{sid}/messages", json={"text": "what is failing?"}).json()
    assert "payment-service" in r["answer"] and r["trace"]
    s = env.client.get(f"/api/chat/sessions/{sid}").json()
    assert [m["role"] for m in s["messages"]] == ["user", "assistant"]
