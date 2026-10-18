"""Tool-using agent loop with an execution trace.

Safety properties:
- tools are plain read-only functions chosen by the caller (the model can't add any)
- tool output is wrapped as untrusted data and size-capped
- hard caps on steps and wall-clock time; at the cap we force a final answer without tools
- every log id the model cites can be checked against ids the tools actually returned
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ..context import AppContext
from ..db import utcnow
from ..llm.base import LLMError, LLMProvider, ToolSpec

MAX_TOOL_CHARS = 12000
CLOSE_TAG = "</untrusted_tool_output>"


@dataclass
class Tool:
    spec: ToolSpec
    fn: Callable[[dict, "AgentContext"], Any]


@dataclass
class AgentContext:
    app: AppContext
    anchor: datetime = field(default_factory=utcnow)   # "now" for relative time windows
    user: str | None = None
    seen_log_ids: set[int] = field(default_factory=set)
    extra: dict[str, Any] = field(default_factory=dict)  # e.g. repo for code tools


@dataclass
class AgentResult:
    text: str
    trace: list[dict]
    usage: dict[str, int]
    steps: int
    forced_final: bool = False
    error: str | None = None


def wrap_tool_output(payload: Any) -> str:
    s = json.dumps(payload, default=str, ensure_ascii=False)
    if len(s) > MAX_TOOL_CHARS:
        s = s[:MAX_TOOL_CHARS] + '..."truncated"'
    s = s.replace(CLOSE_TAG, "[tag removed]")
    return f"<untrusted_tool_output>\n{s}\n{CLOSE_TAG}"


def _preview(v: Any, n: int = 400) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "..."


def run_agent(
    llm: LLMProvider,
    system: str,
    messages: list[dict],
    tools: list[Tool],
    actx: AgentContext,
    max_steps: int = 8,
    max_tokens: int = 2048,
    deadline_seconds: float = 180.0,
    on_event: Callable[[dict], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> AgentResult:
    by_name = {t.spec.name: t for t in tools}
    specs = [t.spec for t in tools]
    msgs = list(messages)
    trace: list[dict] = []
    usage = {"input": 0, "output": 0}
    started = time.monotonic()
    step_no = 0

    def add_usage(u: dict) -> None:
        usage["input"] += u.get("input", 0)
        usage["output"] += u.get("output", 0)

    def llm_step(use_tools: bool):
        nonlocal step_no
        step_no += 1
        t0 = time.monotonic()
        if on_event:
            resp = llm.complete_stream(system, msgs, specs if use_tools else None, max_tokens=max_tokens,
                                       on_text=lambda d: on_event({"type": "delta", "text": d}))
            if resp.tool_calls and resp.text:
                on_event({"type": "discard_text"})   # that text was narration before a tool call, not the answer
        else:
            resp = llm.complete(system, msgs, specs if use_tools else None, max_tokens=max_tokens)
        add_usage(resp.usage)
        trace.append({
            "step": step_no, "type": "llm", "ms": int((time.monotonic() - t0) * 1000),
            "text": _preview(resp.text, 300), "tool_calls": [c.name for c in resp.tool_calls],
            "usage": resp.usage,
        })
        return resp

    try:
        for _ in range(max_steps):
            if time.monotonic() - started > deadline_seconds:
                break
            if cancelled and cancelled():
                return AgentResult("", trace, usage, step_no, error="cancelled")
            resp = llm_step(True)
            if not resp.tool_calls:
                return AgentResult(resp.text, trace, usage, step_no)
            msgs.append({
                "role": "assistant", "content": resp.text,
                "tool_calls": [{"id": c.id, "name": c.name, "args": c.args} for c in resp.tool_calls],
            })
            results = []
            for c in resp.tool_calls:
                step_no += 1
                t0 = time.monotonic()
                tool = by_name.get(c.name)
                if on_event:
                    on_event({"type": "step", "name": c.name, "args": c.args, "status": "running"})
                ok, err = True, None
                if tool is None:
                    ok, err, out = False, f"unknown tool '{c.name}'", {"error": f"unknown tool '{c.name}'"}
                else:
                    try:
                        out = tool.fn(c.args or {}, actx)
                    except Exception as e:  # noqa: BLE001  tool failures go back to the model
                        ok, err, out = False, f"{type(e).__name__}: {e}", {"error": f"{type(e).__name__}: {e}"}
                trace.append({
                    "step": step_no, "type": "tool", "name": c.name, "args": c.args,
                    "ms": int((time.monotonic() - t0) * 1000), "ok": ok, "error": err,
                    "result": _preview(out),
                })
                results.append({"id": c.id, "name": c.name, "content": wrap_tool_output(out)})
                if on_event:
                    on_event({"type": "step", "name": c.name, "status": "ok" if ok else "error", "ms": int((time.monotonic() - t0) * 1000), "error": err})
            msgs.append({"role": "tool", "results": results})

        # Cap reached without a final answer: force one, with no tools.
        msgs.append({"role": "user", "content": "Stop using tools. Give your final answer now using only the evidence gathered."})
        resp = llm_step(False)
        return AgentResult(resp.text, trace, usage, step_no, forced_final=True)
    except LLMError as e:
        trace.append({"step": step_no + 1, "type": "error", "error": e.message, "kind": e.kind})
        return AgentResult("", trace, usage, step_no, error=f"{e.kind}: {e.message}")
