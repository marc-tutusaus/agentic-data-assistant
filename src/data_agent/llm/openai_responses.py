"""OpenAI adapter on the Responses API (official `openai` SDK).

OpenAI's reasoning models (GPT-5.x) only support function tools together with
reasoning on /v1/responses, so OpenAI gets its own native adapter. Other
OpenAI-compatible servers keep using Chat Completions (openai_compat.py).
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
    ToolCall,
    ToolResults,
    ToolSpec,
    Usage,
    UserText,
)

REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


class OpenAIResponsesClient:
    provider = "openai"

    def __init__(
        self,
        model: str = "gpt-5.6-luna",
        api_key: str | None = None,
        effort: str | None = "medium",
        max_output_tokens: int = 16000,
        client: Any = None,
    ):
        self.model = model
        self.effort = effort if model.startswith(REASONING_MODEL_PREFIXES) else None
        self.max_output_tokens = max_output_tokens
        self._client = client or openai.OpenAI(api_key=api_key)

    def _to_native(self, messages: list[Message]) -> list[Any]:
        items: list[Any] = []
        for m in messages:
            if isinstance(m, UserText):
                items.append({"role": "user", "content": m.text})
            elif isinstance(m, AssistantTurn):
                if m.provider == self.provider and m.native is not None:
                    items.extend(m.native)  # output items replayed as-is (keeps reasoning items)
                    continue
                if m.text:
                    items.append({"role": "assistant", "content": m.text})
                items.extend(
                    {
                        "type": "function_call",
                        "call_id": c.id,
                        "name": c.name,
                        "arguments": json.dumps(c.arguments or {}),
                    }
                    for c in m.tool_calls
                )
            elif isinstance(m, ToolResults):
                items.extend(
                    {
                        "type": "function_call_output",
                        "call_id": r.call_id,
                        "output": f"ERROR: {r.content}" if r.is_error else r.content,
                    }
                    for r in m.results
                )
        return items

    @staticmethod
    def _tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
                "strict": True,
            }
            for t in tools
        ]

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "instructions": system,
            "input": self._to_native(messages),
            "max_output_tokens": self.max_output_tokens,
        }
        if tools:
            params["tools"] = self._tools(tools)
        if self.effort:
            params["reasoning"] = {"effort": self.effort}
        try:
            response = self._client.responses.create(**params)
        except openai.APIConnectionError as e:
            raise LLMError(f"Cannot reach the OpenAI API: {e}") from e
        except openai.RateLimitError as e:
            raise LLMError("OpenAI rate limit reached, retry later") from e
        except openai.APIStatusError as e:
            raise LLMError(f"OpenAI API error {e.status_code}: {e.message}") from e

        text, calls, refused = [], [], False
        for item in response.output:
            if item.type == "message":
                for part in item.content:
                    if part.type == "output_text":
                        text.append(part.text)
                    elif part.type == "refusal":
                        refused = True
            elif item.type == "function_call":
                try:
                    args = json.loads(item.arguments or "{}")
                    calls.append(ToolCall(item.call_id, item.name, args if isinstance(args, dict) else None))
                except json.JSONDecodeError as e:
                    calls.append(ToolCall(item.call_id, item.name, None, f"invalid JSON arguments: {e}"))

        native = [item.model_dump(exclude_none=True) for item in response.output]
        incomplete = getattr(response, "incomplete_details", None)
        if refused or (incomplete and incomplete.reason == "content_filter"):
            stop = "refusal"
        elif calls:
            stop = "tool_use"
        elif incomplete and incomplete.reason == "max_output_tokens":
            stop = "max_tokens"
        else:
            stop = "end"

        u = response.usage
        cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
        usage = Usage(u.input_tokens or 0, u.output_tokens or 0, cached) if u else Usage()
        return LLMResponse(AssistantTurn("".join(text), calls, self.provider, native), stop, usage, response.model)
