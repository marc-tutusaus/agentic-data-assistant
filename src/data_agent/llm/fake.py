"""Scripted LLM for tests and offline demos: no network, no API key."""

from __future__ import annotations

import itertools
from collections.abc import Callable

from .base import AssistantTurn, LLMResponse, Message, ToolCall, ToolSpec, Usage

Step = Callable[[list[Message]], LLMResponse]
_ids = itertools.count(1)


def tool(name: str, **arguments) -> Step:
    """Script step: the model calls one tool."""

    def step(_messages: list[Message]) -> LLMResponse:
        call = ToolCall(f"call_{next(_ids)}", name, arguments)
        return LLMResponse(AssistantTurn("", [call], "fake"), "tool_use", Usage(100, 20), "fake-model")

    return step


def say(text: str) -> Step:
    """Script step: the model answers with text."""

    def step(_messages: list[Message]) -> LLMResponse:
        return LLMResponse(AssistantTurn(text, [], "fake"), "end", Usage(100, 50), "fake-model")

    return step


class ScriptedLLM:
    provider = "fake"
    model = "fake-model"

    def __init__(self, *steps: Step):
        self._steps = list(steps)
        self.calls: list[dict] = []  # what the agent sent, for assertions

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        self.calls.append({"system": system, "messages": list(messages), "tools": [t.name for t in tools]})
        if not self._steps:
            raise AssertionError("ScriptedLLM ran out of steps")
        return self._steps.pop(0)(messages)
