"""Azure OpenAI chat-completions provider with tool calling (API key or Managed Identity)."""
from __future__ import annotations

import json
import time

import httpx

from .base import LLMError, LLMProvider, LLMResponse, ToolCall, ToolSpec


def to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            msg: dict = {"role": "assistant", "content": m.get("content") or None}
            if m.get("tool_calls"):
                msg["tool_calls"] = [
                    {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
                    for tc in m["tool_calls"]
                ]
            out.append(msg)
        elif m["role"] == "tool":
            for r in m["results"]:
                out.append({"role": "tool", "tool_call_id": r["id"], "content": r["content"]})
    return out


def parse_openai_response(data: dict) -> LLMResponse:
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message", {})
    calls = []
    for tc in msg.get("tool_calls") or []:
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append(ToolCall(id=tc["id"], name=tc["function"]["name"], args=args))
    u = data.get("usage", {})
    return LLMResponse(
        text=msg.get("content") or "",
        tool_calls=calls,
        usage={"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0)},
        stop_reason=choice.get("finish_reason", ""),
    )


def parse_openai_stream(lines, on_text=None) -> LLMResponse:
    """Consume an SSE stream of chat.completion.chunk lines."""
    text, calls, usage, finish = [], {}, {}, ""
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            d = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if d.get("usage"):
            usage = {"input": d["usage"].get("prompt_tokens", 0), "output": d["usage"].get("completion_tokens", 0)}
        ch = (d.get("choices") or [{}])[0]
        delta = ch.get("delta") or {}
        if delta.get("content"):
            text.append(delta["content"])
            if on_text:
                on_text(delta["content"])
        for tc in delta.get("tool_calls") or []:
            slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            slot["args"] += fn.get("arguments") or ""
        if ch.get("finish_reason"):
            finish = ch["finish_reason"]
    out = []
    for i in sorted(calls):
        c = calls[i]
        try:
            args = json.loads(c["args"] or "{}")
        except json.JSONDecodeError:
            args = {}
        out.append(ToolCall(id=c["id"] or f"call-{i}", name=c["name"], args=args))
    return LLMResponse(text="".join(text), tool_calls=out, usage=usage, stop_reason=finish)


class AzureOpenAIProvider(LLMProvider):
    name = "azure_openai"

    def __init__(self, endpoint: str, deployment: str, api_version: str, api_key: str | None = None,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        if not endpoint:
            raise LLMError("config", "Azure OpenAI endpoint is not set")
        self.endpoint = endpoint.rstrip("/")
        self.model = deployment
        self.api_version = api_version
        self.api_key = api_key
        self._http = httpx.Client(transport=transport, timeout=120)
        self._sleep = sleep

    def _auth_header(self) -> dict:
        if self.api_key:
            return {"api-key": self.api_key}
        try:  # Managed Identity / developer login via azure-identity (optional dependency)
            from azure.identity import DefaultAzureCredential

            tok = DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default")
            return {"Authorization": f"Bearer {tok.token}"}
        except ImportError as e:
            raise LLMError("config", "No API key set and azure-identity is not installed") from e
        except Exception as e:  # noqa: BLE001
            raise LLMError("auth", f"Managed identity login failed: {e}") from e

    def complete(self, system, messages, tools=None, max_tokens=2048, temperature=0.2) -> LLMResponse:
        body: dict = {"messages": to_openai_messages(system, messages), "max_tokens": max_tokens, "temperature": temperature}
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
        url = f"{self.endpoint}/openai/deployments/{self.model}/chat/completions?api-version={self.api_version}"
        last: LLMError | None = None
        for i in range(3):
            try:
                r = self._http.post(url, json=body, headers=self._auth_header())
                if r.status_code == 200:
                    return parse_openai_response(r.json())
                if r.status_code in (401, 403):
                    last = LLMError("auth", f"HTTP {r.status_code}: {r.text[:200]}")
                elif r.status_code == 429:
                    last = LLMError("throttle", "Azure OpenAI rate limit (429)")
                elif r.status_code >= 500:
                    last = LLMError("transient", f"HTTP {r.status_code}")
                elif r.status_code == 404:
                    last = LLMError("config", "Deployment not found: check the deployment name and endpoint")
                else:
                    last = LLMError("unknown", f"HTTP {r.status_code}: {r.text[:200]}")
            except httpx.TransportError as e:
                last = LLMError("transient", f"Network error: {e}")
            if not last.retryable or i == 2:
                raise last
            self._sleep(1.0 * (2**i))
        raise last  # pragma: no cover

    def complete_stream(self, system, messages, tools=None, max_tokens=2048, temperature=0.2, on_text=None) -> LLMResponse:
        body: dict = {"messages": to_openai_messages(system, messages), "max_tokens": max_tokens, "temperature": temperature,
                      "stream": True, "stream_options": {"include_usage": True}}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                             for t in tools]
        url = f"{self.endpoint}/openai/deployments/{self.model}/chat/completions?api-version={self.api_version}"
        try:
            with self._http.stream("POST", url, json=body, headers=self._auth_header()) as r:
                if r.status_code != 200:
                    r.read()
                    kind = {401: "auth", 403: "auth", 429: "throttle", 404: "config"}.get(r.status_code, "transient" if r.status_code >= 500 else "unknown")
                    raise LLMError(kind, f"HTTP {r.status_code}: {r.text[:200]}")
                return parse_openai_stream(r.iter_lines(), on_text)
        except httpx.TransportError as e:
            raise LLMError("transient", f"Network error: {e}") from e
