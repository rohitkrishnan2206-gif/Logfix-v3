"""Deterministic heuristic 'LLM' so the product runs and tests pass with no API keys.
It is NOT AI: it follows a fixed tool plan and assembles an answer from tool output.
The UI labels it 'heuristic (no LLM)'. Switch to Bedrock/Azure OpenAI for real reasoning."""
from __future__ import annotations

import json
import re
from typing import Any

from .base import LLMProvider, LLMResponse, ToolCall, ToolSpec

_WRAP = re.compile(r"<untrusted_tool_output>\s*(.*?)\s*</untrusted_tool_output>", re.S)
_STOP = set(
    "the a an is are was were why what when where how did does do my our your this that these those with from into "
    "for and or not any all show tell about have has been being can could would should please logs log error errors "
    "service services find look check there their them then than just over under last past minutes".split()
)


def _parse(content: str) -> Any:
    m = _WRAP.search(content or "")
    raw = m.group(1) if m else (content or "")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _results(messages: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in messages:
        if m["role"] == "tool":
            for r in m["results"]:
                out[r["name"]] = _parse(r["content"])
    return out


def _first_user(messages: list[dict]) -> str:
    for m in messages:
        if m["role"] == "user":
            return m["content"]
    return ""


def _last_user(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m["role"] == "user":
            return m["content"]
    return ""


def _keywords(text: str, n: int = 4) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9_\-]{3,}", text.lower()) if w not in _STOP]
    words.sort(key=len, reverse=True)
    return " ".join(words[:n])


def _bullets(text: str, n: int = 4) -> list[str]:
    """Prefer bullets under a Remediation/Steps heading; fall back to any bullets."""
    text = text or ""
    m = re.search(r"(?im)^#{1,3}\s*(remediation|steps)[^\n]*\n(.*)", text, re.S)
    if m:
        text = m.group(2)
    items = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^(?:[-*]|\d+[.)])\s+(.*)$", line)
        if m and len(m.group(1)) > 12:
            items.append(m.group(1))
    return items[:n]


class MockProvider(LLMProvider):
    name = "mock"
    model = "heuristic-v1"
    is_real = False

    def complete(self, system, messages, tools=None, max_tokens=2048, temperature=0.2) -> LLMResponse:
        tool_names = {t.name for t in (tools or [])}
        res = _results(messages)
        n = sum(1 for m in messages if m["role"] == "tool")

        def call(name: str, args: dict) -> LLMResponse:
            return LLMResponse(tool_calls=[ToolCall(id=f"mock-{n}-{name}", name=name, args=args)], stop_reason="tool_use")

        if "[MODE:investigate]" in system:
            return self._investigate(call, res, tool_names, messages)
        if "[MODE:code]" in system:
            return self._code(call, res, tool_names, messages)
        return self._chat(call, res, tool_names, messages)

    # ---- incident investigation ----
    def _investigate(self, call, res, tools, messages) -> LLMResponse:
        if "top_error_patterns" in tools and "top_error_patterns" not in res:
            return call("top_error_patterns", {"minutes": 60})
        pats = (res.get("top_error_patterns") or {}).get("patterns") or []
        top = pats[0] if pats else None
        if top and "search_logs" in tools and "search_logs" not in res:
            return call("search_logs", {"service": top["service"], "min_severity": "HIGH", "minutes": 60, "limit": 15})
        if top and "search_runbooks" in tools and "search_runbooks" not in res:
            return call("search_runbooks", {"query": top["sample"][:200]})
        logs = (res.get("search_logs") or {}).get("logs") or []
        rbs = (res.get("search_runbooks") or {}).get("results") or []
        if not top:
            body = {"summary": "No error patterns found in the lookback window.", "root_cause": "Unknown: no evidence collected.",
                    "confidence": "low", "evidence": [], "recommendations": ["Widen the time range or check the source connection."],
                    "runbooks": []}
            return LLMResponse(text=json.dumps(body), stop_reason="end_turn")
        rb_text = rbs[0]["snippet"] if rbs else ""
        recs = _bullets(rb_text) or ["Review the evidence logs and recent deployments for the affected service."]
        other = [p["service"] for p in pats[1:4] if p["service"] != top["service"]]
        cascade = f" Related failures also seen in: {', '.join(other)}." if other else ""
        body = {
            "summary": f"{top['service']} is failing: '{top['sample'][:140]}' occurred {top['count']} times.{cascade}",
            "root_cause": f"Most likely cause is the recurring {top['service']} error pattern"
                          + (f", consistent with runbook '{rbs[0]['title']}'." if rbs else "."),
            "confidence": "medium" if rbs and logs else "low",
            "evidence": [{"log_id": l["id"], "note": l["message"][:160]} for l in logs[:4]],
            "recommendations": recs,
            "runbooks": [r["title"] for r in rbs[:2]],
            "note": "Generated by the heuristic provider (no LLM). Configure Bedrock or Azure OpenAI for real reasoning.",
        }
        return LLMResponse(text=json.dumps(body), stop_reason="end_turn")

    # ---- chat ----
    def _chat(self, call, res, tools, messages) -> LLMResponse:
        q = _last_user(messages)
        if "top_error_patterns" in tools and "top_error_patterns" not in res:
            return call("top_error_patterns", {"minutes": 60})
        if "search_runbooks" in tools and "search_runbooks" not in res:
            pats0 = (res.get("top_error_patterns") or {}).get("patterns") or []
            return call("search_runbooks", {"query": pats0[0]["sample"][:200] if pats0 else (_keywords(q) or q[:100])})
        pats = (res.get("top_error_patterns") or {}).get("patterns") or []
        rbs = (res.get("search_runbooks") or {}).get("results") or []
        lines = []
        if pats:
            lines.append("Top error patterns in the last hour:")
            lines += [f"- {p['service']}: {p['sample'][:110]} ({p['count']}x, log #{p['sample_log_id']})" for p in pats[:3]]
        else:
            lines.append("I found no recent error patterns in the collected logs.")
        if rbs:
            lines.append(f"Relevant runbook: {rbs[0]['title']}")
            lines += [f"- {b}" for b in _bullets(rbs[0]["snippet"], 3)]
        lines.append("(Heuristic provider, no LLM. Configure Bedrock or Azure OpenAI for real answers.)")
        return LLMResponse(text="\n".join(lines), stop_reason="end_turn")

    # ---- repository assistant ----
    def _code(self, call, res, tools, messages) -> LLMResponse:
        q = _first_user(messages)
        kw = _keywords(q, 1) or "TODO"
        if "grep" in tools and "grep" not in res:
            return call("grep", {"pattern": re.escape(kw), "max_results": 10})
        matches = (res.get("grep") or {}).get("matches") or []
        if matches and "read_file" in tools and "read_file" not in res:
            m = matches[0]
            return call("read_file", {"path": m["path"], "start_line": max(1, m["line"] - 5), "end_line": m["line"] + 15})
        if not matches:
            return LLMResponse(text=f"No matches for '{kw}' in this repository. (Heuristic provider, no LLM.)", stop_reason="end_turn")
        lines = [f"Found {len(matches)} match(es) for '{kw}':"]
        lines += [f"- {m['path']}:{m['line']}  {m['text'][:100]}" for m in matches[:6]]
        lines.append("(Heuristic provider, no LLM: it can search and read but not reason about or propose changes.)")
        return LLMResponse(text="\n".join(lines), stop_reason="end_turn")
