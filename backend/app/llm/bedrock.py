"""Amazon Bedrock (Converse API) provider with tool use."""
from __future__ import annotations

import time
from typing import Any

from .base import LLMError, LLMProvider, LLMResponse, ToolCall, ToolSpec


def to_bedrock_messages(messages: list[dict]) -> list[dict]:
    """Internal -> Converse format. Converse requires strictly alternating roles and
    tool results to be sent as user messages, so consecutive same-role turns are merged."""
    out: list[dict] = []

    def push(role: str, blocks: list[dict]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        if m["role"] == "user":
            push("user", [{"text": m["content"] or "(empty)"}])
        elif m["role"] == "assistant":
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"text": m["content"]})
            for tc in m.get("tool_calls", []):
                blocks.append({"toolUse": {"toolUseId": tc["id"], "name": tc["name"], "input": tc["args"]}})
            if blocks:
                push("assistant", blocks)
        elif m["role"] == "tool":
            push(
                "user",
                [
                    {"toolResult": {"toolUseId": r["id"], "content": [{"text": r["content"] or "(empty)"}]}}
                    for r in m["results"]
                ],
            )
    return out


def from_bedrock_response(resp: dict) -> LLMResponse:
    blocks = resp.get("output", {}).get("message", {}).get("content", [])
    text = "".join(b["text"] for b in blocks if "text" in b)
    calls = [
        ToolCall(id=b["toolUse"]["toolUseId"], name=b["toolUse"]["name"], args=b["toolUse"].get("input") or {})
        for b in blocks
        if "toolUse" in b
    ]
    u = resp.get("usage", {})
    return LLMResponse(
        text=text,
        tool_calls=calls,
        usage={"input": u.get("inputTokens", 0), "output": u.get("outputTokens", 0)},
        stop_reason=resp.get("stopReason", ""),
    )


def map_error(exc: Exception) -> LLMError:
    code = ""
    r = getattr(exc, "response", None)
    if isinstance(r, dict):
        code = r.get("Error", {}).get("Code", "")
    msg = f"{code or type(exc).__name__}: {str(exc)[:300]}"
    if code in ("ThrottlingException", "TooManyRequestsException", "ServiceQuotaExceededException"):
        return LLMError("throttle", msg)
    if code in ("AccessDeniedException", "UnrecognizedClientException", "ExpiredTokenException"):
        return LLMError("auth", msg)
    if code in ("ModelTimeoutException", "ServiceUnavailableException", "InternalServerException", "ModelNotReadyException") or type(
        exc
    ).__name__ in ("EndpointConnectionError", "ReadTimeoutError", "ConnectTimeoutError"):
        return LLMError("transient", msg)
    if code in ("ValidationException", "ResourceNotFoundException"):
        return LLMError("config", msg + " (check the model ID / inference profile for your region)")
    return LLMError("unknown", msg)


class BedrockProvider(LLMProvider):
    name = "bedrock"

    def __init__(self, model_id: str, region: str, creds: dict | None = None, client: Any = None, sleep=time.sleep):
        self.model = model_id
        self.region = region
        self._sleep = sleep
        if client is None:
            import boto3
            from botocore.config import Config

            kw: dict[str, Any] = {
                "region_name": region,
                "config": Config(retries={"max_attempts": 1, "mode": "standard"}, read_timeout=120, connect_timeout=10),
            }
            creds = creds or {}
            if creds.get("access_key_id") and creds.get("secret_access_key"):
                kw["aws_access_key_id"] = creds["access_key_id"]
                kw["aws_secret_access_key"] = creds["secret_access_key"]
                if creds.get("session_token"):
                    kw["aws_session_token"] = creds["session_token"]
            client = boto3.client("bedrock-runtime", **kw)
        self.client = client

    def _kwargs(self, system, messages, tools, max_tokens, temperature) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "modelId": self.model,
            "messages": to_bedrock_messages(messages),
            "system": [{"text": system}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if tools:
            kwargs["toolConfig"] = {
                "tools": [
                    {"toolSpec": {"name": t.name, "description": t.description, "inputSchema": {"json": t.parameters}}}
                    for t in tools
                ]
            }
        return kwargs

    def complete(self, system, messages, tools=None, max_tokens=2048, temperature=0.2) -> LLMResponse:
        kwargs = self._kwargs(system, messages, tools, max_tokens, temperature)
        last: LLMError | None = None
        for i in range(3):
            try:
                return from_bedrock_response(self.client.converse(**kwargs))
            except Exception as e:  # noqa: BLE001
                last = map_error(e)
                if not last.retryable or i == 2:
                    raise last from e
                self._sleep(1.0 * (2**i))
        raise last  # pragma: no cover


    def complete_stream(self, system, messages, tools=None, max_tokens=2048, temperature=0.2, on_text=None) -> LLMResponse:
        kwargs = self._kwargs(system, messages, tools, max_tokens, temperature)
        try:
            resp = self.client.converse_stream(**kwargs)
            return consume_bedrock_stream(resp.get("stream") or [], on_text)
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001
            raise map_error(e) from e


def consume_bedrock_stream(events, on_text=None) -> LLMResponse:
    import json as _json
    from .base import ToolCall
    text, calls, cur, usage, stop = [], [], None, {}, ""
    for ev in events:
        if "contentBlockStart" in ev:
            tu = (ev["contentBlockStart"].get("start") or {}).get("toolUse")
            cur = {"id": tu["toolUseId"], "name": tu["name"], "buf": ""} if tu else None
        elif "contentBlockDelta" in ev:
            d = ev["contentBlockDelta"].get("delta") or {}
            if "text" in d:
                text.append(d["text"])
                if on_text:
                    on_text(d["text"])
            elif "toolUse" in d and cur is not None:
                cur["buf"] += d["toolUse"].get("input", "")
        elif "contentBlockStop" in ev:
            if cur is not None:
                try:
                    args = _json.loads(cur["buf"] or "{}")
                except _json.JSONDecodeError:
                    args = {}
                calls.append(ToolCall(id=cur["id"], name=cur["name"], args=args))
                cur = None
        elif "messageStop" in ev:
            stop = ev["messageStop"].get("stopReason", "")
        elif "metadata" in ev:
            u = ev["metadata"].get("usage") or {}
            usage = {"input": u.get("inputTokens", 0), "output": u.get("outputTokens", 0)}
        else:
            for k in ev:
                if k.endswith("Exception"):
                    raise LLMError("transient", f"{k}: {ev[k].get('message', '')}")
    return LLMResponse(text="".join(text), tool_calls=calls, usage=usage, stop_reason=stop)
