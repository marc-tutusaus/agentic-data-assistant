"""The agent loop: model -> tool calls -> results -> model, until it answers."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from ..llm import LLMClient, Message, ToolResult, ToolResults, Usage, UserText, estimate_cost
from .prompts import build_system_prompt
from .tools import Toolbox

log = logging.getLogger("data_agent")


@dataclass
class StepTrace:
    step: int
    tool: str
    arguments: dict[str, Any] | None
    ok: bool
    duration_ms: int
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class AgentResult:
    answer: str
    stop: str  # answered | refused | max_steps | max_tokens
    steps: list[StepTrace]
    usage: Usage
    provider: str
    model: str
    duration_ms: int
    cost_usd: float | None = None

    @property
    def sql(self) -> list[str]:
        return [s.detail["sql"] for s in self.steps if "sql" in s.detail]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["sql"] = self.sql
        return d


class DataAgent:
    def __init__(
        self, llm: LLMClient, toolbox: Toolbox, max_steps: int = 12, on_step: Callable[[StepTrace], None] | None = None
    ):
        self.llm = llm
        self.toolbox = toolbox
        self.max_steps = max_steps
        self.on_step = on_step  # e.g. live progress in the CLI

    def ask(self, question: str) -> AgentResult:
        started = time.perf_counter()
        system = build_system_prompt()
        tools = self.toolbox.specs()
        messages: list[Message] = [UserText(question)]
        steps: list[StepTrace] = []
        usage = Usage()
        model = self.llm.model

        for _ in range(self.max_steps):
            response = self.llm.complete(system, messages, tools)
            usage += response.usage
            model = response.model or model
            messages.append(response.turn)

            if response.stop_reason == "refusal":
                return self._result(
                    "The model declined to answer this request.", "refused", steps, usage, model, started
                )
            if not response.turn.tool_calls:
                stop = "max_tokens" if response.stop_reason == "max_tokens" else "answered"
                return self._result(response.turn.text, stop, steps, usage, model, started)

            results = []
            for call in response.turn.tool_calls:
                t0 = time.perf_counter()
                if call.parse_error:
                    output_content, is_error, meta = f"Invalid tool arguments: {call.parse_error}", True, {}
                else:
                    out = self.toolbox.execute(call.name, call.arguments)
                    output_content, is_error, meta = out.content, out.is_error, out.meta
                trace = StepTrace(
                    len(steps) + 1,
                    call.name,
                    call.arguments,
                    not is_error,
                    int((time.perf_counter() - t0) * 1000),
                    meta,
                    output_content if is_error else None,
                )
                steps.append(trace)
                if self.on_step:
                    self.on_step(trace)
                log.info(
                    "step %s %s ok=%s %sms %s",
                    trace.step,
                    call.name,
                    trace.ok,
                    trace.duration_ms,
                    json.dumps(call.arguments, ensure_ascii=False)[:300],
                )
                results.append(ToolResult(call.id, output_content, is_error))
            messages.append(ToolResults(results))

        # Out of steps: ask for the best answer with what was gathered. Tools stay
        # declared (the history contains tool calls) but any new call is ignored.
        messages.append(
            UserText(
                "You have reached the step limit. Do not call more tools. Answer now with the "
                "data you already have, and say clearly what is missing."
            )
        )
        response = self.llm.complete(system, messages, tools)
        usage += response.usage
        answer = response.turn.text or "I could not finish the analysis within the step limit."
        return self._result(answer, "max_steps", steps, usage, model, started)

    def _result(
        self, answer: str, stop: str, steps: list[StepTrace], usage: Usage, model: str, started: float
    ) -> AgentResult:
        result = AgentResult(
            answer=answer.strip(),
            stop=stop,
            steps=steps,
            usage=usage,
            provider=self.llm.provider,
            model=model,
            duration_ms=int((time.perf_counter() - started) * 1000),
            cost_usd=estimate_cost(model, usage.input_tokens, usage.output_tokens),
        )
        log.info(
            "done stop=%s steps=%s tokens_in=%s tokens_out=%s cost=%s",
            stop,
            len(steps),
            usage.input_tokens,
            usage.output_tokens,
            result.cost_usd,
        )
        return result
