"""Claude adapter (official `anthropic` SDK, Messages API with tool use)."""

from __future__ import annotations

from typing import Any

import anthropic

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

# Models that accept server-side refusal fallbacks (fallbacks="default").
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


class AnthropicClient:
    provider = "anthropic"

    def __init__(
        self,
        model: str = "claude-opus-5-5",
        api_key: str | None = None,
        effort: str | None = "medium",
        fallbacks: bool = True,
        max_tokens: int = 16000,
        client: Any = None,
    ):
        self.model = model
        self.effort = effort
        self.fallbacks = fallbacks and model in FALLBACK_MODELS
        self.max_tokens = max_tokens
        self._client = client or anthropic.Anthropic(api_key=api_key)

    # ── request translation ─────────────────────────────────────────────────
    def _to_native(self, messages: list[Message]) -> list[dict[str, Any]]:
        native: list[dict[str, Any]] = []
        for m in messages:
            if isinstance(m, UserText):
                if native and native[-1]["role"] == "user" and isinstance(native[-1]["content"], list):
                    # text after tool results joins the same user turn
                    native[-1]["content"].append({"type": "text", "text": m.text})
                else:
                    native.append({"role": "user", "content": m.text})
            elif isinstance(m, AssistantTurn):
                if m.provider == self.provider and m.native is not None:
                    content = m.native  # replay unchanged (keeps thinking blocks valid)
                else:
                    content = ([{"type": "text", "text": m.text}] if m.text else []) + [
                        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments or {}}
                        for c in m.tool_calls
                    ]
                native.append({"role": "assistant", "content": content})
            elif isinstance(m, ToolResults):
                # all results of one turn go back in a single user message
                native.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r.call_id,
                                "content": r.content,
                                "is_error": r.is_error,
                            }
                            for r in m.results
                        ],
                    }
                )
        return native

    @staticmethod
    def _tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters, "strict": True} for t in tools
        ]

    # ── call ────────────────────────────────────────────────────────────────
    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": self._to_native(messages),
            # system prompt + tools repeat on every agent step: cache them
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            params["tools"] = self._tools(tools)
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        try:
            if self.fallbacks:
                response = self._client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
            else:
                response = self._client.messages.create(**params)
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Cannot reach the Anthropic API: {e}") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Anthropic rate limit reached, retry later") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e

        stop = STOP_REASONS.get(response.stop_reason or "", "end")
        if stop == "refusal":
            # check the stop reason before reading content
            return LLMResponse(
                AssistantTurn("", [], self.provider, response.content), "refusal", self._usage(response), response.model
            )

        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"]
        return LLMResponse(
            AssistantTurn(text, calls, self.provider, response.content), stop, self._usage(response), response.model
        )

    @staticmethod
    def _usage(response: Any) -> Usage:
        u = response.usage
        return Usage(u.input_tokens or 0, u.output_tokens or 0, getattr(u, "cache_read_input_tokens", 0) or 0)
