"""Provider-neutral LLM interface used by the agent framework."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class LLMError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # auth | throttle | transient | config | unknown
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.kind in ("throttle", "transient")


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema (type=object)


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str = ""


class LLMProvider(ABC):
    name: str = ""
    model: str = ""
    is_real: bool = True  # False for the heuristic mock; shown in the UI

    @abstractmethod
    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """messages use the internal format:
        {"role":"user","content":str}
        {"role":"assistant","content":str,"tool_calls":[{"id","name","args"}]}
        {"role":"tool","results":[{"id","name","content":str}]}"""

    def complete_stream(self, system, messages, tools=None, max_tokens: int = 2048, temperature: float = 0.2,
                        on_text=None) -> LLMResponse:
        """Like complete(), but calls on_text(delta) as text arrives. Providers without real streaming
        fall back to one call and emit the text in small chunks (so the UI still renders progressively)."""
        resp = self.complete(system, messages, tools, max_tokens, temperature)
        if on_text and resp.text:
            for i in range(0, len(resp.text), 24):
                on_text(resp.text[i:i + 24])
        return resp

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "model": self.model, "is_real": self.is_real}
