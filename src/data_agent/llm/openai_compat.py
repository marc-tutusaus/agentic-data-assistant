"""Adapter for any OpenAI-compatible Chat Completions API.

One adapter covers OpenAI, Google Gemini, Mistral, Ollama and LM Studio:
they all expose `/chat/completions` with function calling, only the base URL
and the API key change.
"""

from __future__ import annotations

import json
from typing import Any

import openai

from .base import (
    AssistantTurn,
    LLMError,
    LLMResponse,
    Message,
    StopReason,
    ToolCall,
    ToolResults,
    ToolSpec,
    Usage,
    UserText,
)

STOP_REASONS: dict[str, StopReason] = {
    "stop": "end",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class OpenAICompatClient:
    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 16000,
        client: Any = None,
    ):
        self.provider = provider
        self.model = model
        self.max_tokens = max_tokens
        self._client = client or openai.OpenAI(api_key=api_key or "not-needed", base_url=base_url)

    def _to_native(self, system: str, messages: list[Message]) -> list[dict[str, Any]]:
        native: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if isinstance(m, UserText):
                native.append({"role": "user", "content": m.text})
            elif isinstance(m, AssistantTurn):
                msg: dict[str, Any] = {"role": "assistant", "content": m.text or None}
                if m.tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments or {})},
                        }
                        for c in m.tool_calls
                    ]
                native.append(msg)
            elif isinstance(m, ToolResults):
                for r in m.results:
                    native.append(
                        {
                            "role": "tool",
                            "tool_call_id": r.call_id,
                            "content": f"ERROR: {r.content}" if r.is_error else r.content,
                        }
                    )
        return native

    @staticmethod
    def _tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in tools
        ]

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_native(system, messages),
            # OpenAI requires max_completion_tokens (GPT-5.x rejects max_tokens and spends part of it on
            # reasoning); other OpenAI-compatible servers only understand max_tokens.
            ("max_completion_tokens" if self.provider == "openai" else "max_tokens"): self.max_tokens,
        }
        if tools:
            params["tools"] = self._tools(tools)
        try:
            response = self._client.chat.completions.create(**params)
        except openai.APIConnectionError as e:
            raise LLMError(f"Cannot reach {self.provider}: {e}") from e
        except openai.RateLimitError as e:
            raise LLMError(f"{self.provider} rate limit reached, retry later") from e
        except openai.APIStatusError as e:
            raise LLMError(f"{self.provider} API error {e.status_code}: {e.message}") from e

        choice = response.choices[0]
        calls = []
        for tc in choice.message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
                calls.append(
                    ToolCall(
                        tc.id,
                        tc.function.name,
                        args if isinstance(args, dict) else None,
                        None if isinstance(args, dict) else "arguments must be a JSON object",
                    )
                )
            except json.JSONDecodeError as e:
                calls.append(ToolCall(tc.id, tc.function.name, None, f"invalid JSON arguments: {e}"))

        stop = STOP_REASONS.get(choice.finish_reason or "", "end")
        if calls:  # some local servers report "stop" even when they return tool calls
            stop = "tool_use"
        u = response.usage
        usage = Usage(getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0) if u else Usage()
        return LLMResponse(
            AssistantTurn(choice.message.content or "", calls, self.provider), stop, usage, response.model or self.model
        )
