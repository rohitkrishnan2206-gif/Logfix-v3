import json

import httpx

from app.llm.azure_openai import parse_openai_stream
from app.llm.bedrock import consume_bedrock_stream
from conftest import inject


def parse_sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        ev, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
        if ev:
            out.append((ev, data))
    return out


def new_chat(env):
    return env.client.post("/api/chat/sessions").json()["id"]


def test_stream_events_and_persistence(env):
    inject(env)
    sid = new_chat(env)
    r = env.client.post(f"/api/chat/sessions/{sid}/stream", json={"text": "what is failing?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = parse_sse(r.text)
    kinds = [e for e, _ in ev]
    assert kinds[0] == "user_message" and kinds[-1] == "done"
    assert "step" in kinds and "delta" in kinds and "answer" in kinds
    ans = next(d for e, d in ev if e == "answer")
    assert ans["text"] and ans["trace"]
    streamed = "".join(d["text"] for e, d in ev if e == "delta")
    assert streamed.strip() == ans["text"].strip()
    msgs = env.client.get(f"/api/chat/sessions/{sid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_regenerate_replaces_last_answer(env):
    inject(env)
    sid = new_chat(env)
    env.client.post(f"/api/chat/sessions/{sid}/stream", json={"text": "what is failing?"})
    r = env.client.post(f"/api/chat/sessions/{sid}/stream", json={"regenerate": True})
    assert "answer" in [e for e, _ in parse_sse(r.text)]
    msgs = env.client.get(f"/api/chat/sessions/{sid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_stream_requires_text_and_ownership(env):
    sid = new_chat(env)
    assert env.client.post(f"/api/chat/sessions/{sid}/stream", json={}).status_code == 422
    assert env.client.post("/api/chat/sessions/9999/stream", json={"text": "x"}).status_code == 404
    assert env.client.post(f"/api/chat/sessions/{sid}/stream", json={"text": "x"}, headers={"Authorization": ""}).status_code == 401


def test_rename(env):
    sid = new_chat(env)
    assert env.client.patch(f"/api/chat/sessions/{sid}", json={"title": "My triage"}).status_code == 200
    assert env.client.get(f"/api/chat/sessions/{sid}").json()["title"] == "My triage"


def test_llm_failure_is_reported_in_stream(env):
    from app.llm.base import LLMError, LLMProvider

    class Broken(LLMProvider):
        name, model, is_real = "broken", "b", True
        def complete(self, *a, **k): raise LLMError("auth", "bad key")

    env.ctx.llm.safe_get = lambda: Broken()
    sid = new_chat(env)
    ev = parse_sse(env.client.post(f"/api/chat/sessions/{sid}/stream", json={"text": "hi"}).text)
    ans = next(d for e, d in ev if e == "answer")
    assert "bad key" in ans["text"] and ans["error"]
    assert ev[-1][0] == "done"


def test_tool_narration_is_discarded_from_stream(env):
    from app.llm.base import LLMProvider, LLMResponse, ToolCall

    class Narrator(LLMProvider):
        name, model, is_real = "n", "n", True
        def __init__(self): self.n = 0
        def complete(self, system, messages, tools=None, *a, **k):
            self.n += 1
            if self.n == 1:
                return LLMResponse(text="Let me look...", tool_calls=[ToolCall("1", "top_error_patterns", {"minutes": 60})])
            return LLMResponse(text="Final answer.")

    env.ctx.llm.safe_get = lambda: Narrator()
    sid = new_chat(env)
    ev = parse_sse(env.client.post(f"/api/chat/sessions/{sid}/stream", json={"text": "hi"}).text)
    kinds = [e for e, _ in ev]
    assert "discard_text" in kinds
    assert next(d for e, d in ev if e == "answer")["text"] == "Final answer."


def test_parse_openai_stream():
    lines = ['data: {"choices":[{"delta":{"content":"Hel"}}]}', 'data: {"choices":[{"delta":{"content":"lo"}}]}',
             'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"search_logs","arguments":"{\\"q\\":"}}]}}]}',
             'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"x\\"}"}}]},"finish_reason":"tool_calls"}]}',
             'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":7}}', "data: [DONE]"]
    got = []
    r = parse_openai_stream(lines, got.append)
    assert r.text == "Hello" and got == ["Hel", "lo"]
    assert r.tool_calls[0].name == "search_logs" and r.tool_calls[0].args == {"q": "x"}
    assert r.usage == {"input": 5, "output": 7}


def test_consume_bedrock_stream():
    ev = [{"contentBlockDelta": {"delta": {"text": "Hi "}}}, {"contentBlockDelta": {"delta": {"text": "there"}}}, {"contentBlockStop": {}},
          {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "t1", "name": "search_logs"}}}},
          {"contentBlockDelta": {"delta": {"toolUse": {"input": "{\"q\":"}}}}, {"contentBlockDelta": {"delta": {"toolUse": {"input": "\"y\"}"}}}},
          {"contentBlockStop": {}}, {"messageStop": {"stopReason": "tool_use"}},
          {"metadata": {"usage": {"inputTokens": 3, "outputTokens": 4}}}]
    got = []
    r = consume_bedrock_stream(iter(ev), got.append)
    assert r.text == "Hi there" and r.tool_calls[0].args == {"q": "y"} and r.usage == {"input": 3, "output": 4}


def test_bedrock_stream_error_event():
    import pytest
    from app.llm.base import LLMError
    with pytest.raises(LLMError):
        consume_bedrock_stream(iter([{"throttlingException": {"message": "slow down"}}]))
