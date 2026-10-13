"""Provider-neutral types for the agent <-> LLM conversation.

The agent loop only speaks these types. Each adapter translates them to its
provider's native request format and back, so swapping providers never
touches the agent code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

StopReason = Literal["end", "tool_use", "max_tokens", "refusal"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (object, additionalProperties: false)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None
    parse_error: str | None = None  # set when the model produced invalid JSON arguments


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class UserText:
    text: str


@dataclass(frozen=True)
class AssistantTurn:
    text: str
    tool_calls: list[ToolCall]
    provider: str
    # The provider's own message, replayed verbatim to the same provider
    # (e.g. Claude thinking blocks must be echoed back unchanged).
    native: Any = None


@dataclass(frozen=True)
class ToolResults:
    results: list[ToolResult]


Message = UserText | AssistantTurn | ToolResults


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
        )


@dataclass
class LLMResponse:
    turn: AssistantTurn
    stop_reason: StopReason
    usage: Usage = field(default_factory=Usage)
    model: str = ""


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse: ...


class LLMError(RuntimeError):
    """A provider call failed (network, auth, rate limit...)."""
