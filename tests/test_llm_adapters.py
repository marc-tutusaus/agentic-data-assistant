"""Adapter tests with stubbed SDK clients: request shape in, neutral types out."""

import json
from types import SimpleNamespace as NS

from data_agent.llm import AssistantTurn, ToolCall, ToolResult, ToolResults, ToolSpec, UserText
from data_agent.llm.anthropic_client import FALLBACK_BETA, AnthropicClient
from data_agent.llm.factory import create_llm
from data_agent.llm.openai_compat import OpenAICompatClient

TOOLS = [
    ToolSpec(
        "run_sql",
        "Run SQL",
        {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
            "additionalProperties": False,
        },
    )
]


class Recorder:
    """Records the kwargs of the last create() call and returns a canned response."""

    def __init__(self, response):
        self.response, self.kwargs = response, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


# ── Anthropic ───────────────────────────────────────────────────────────────
def anthropic_stub(content, stop_reason="tool_use"):
    rec = Recorder(
        NS(
            content=content,
            stop_reason=stop_reason,
            model="claude-opus-5-5",
            usage=NS(input_tokens=1200, output_tokens=80, cache_read_input_tokens=1000),
        )
    )
    return rec, NS(messages=rec, beta=NS(messages=rec))


def test_anthropic_request_and_tool_call_parsing():
    rec, sdk = anthropic_stub(
        [
            NS(type="thinking", thinking=""),
            NS(type="text", text="Let me check."),
            NS(type="tool_use", id="toolu_1", name="run_sql", input={"sql": "SELECT 1"}),
        ]
    )
    llm = AnthropicClient(model="claude-opus-5-5", client=sdk)
    res = llm.complete("system prompt", [UserText("How many tickets?")], TOOLS)

    assert rec.kwargs["model"] == "claude-opus-5-5"
    assert rec.kwargs["fallbacks"] == "default" and rec.kwargs["betas"] == [FALLBACK_BETA]
    assert rec.kwargs["output_config"] == {"effort": "medium"}
    assert rec.kwargs["cache_control"] == {"type": "ephemeral"}
    assert rec.kwargs["tools"][0]["input_schema"] == TOOLS[0].parameters and rec.kwargs["tools"][0]["strict"]
    assert res.stop_reason == "tool_use" and res.usage.cache_read_tokens == 1000
    assert res.turn.tool_calls == [ToolCall("toolu_1", "run_sql", {"sql": "SELECT 1"})]


def test_anthropic_replays_native_content_and_groups_tool_results():
    native = [NS(type="thinking", thinking=""), NS(type="tool_use", id="t1", name="run_sql", input={})]
    rec, sdk = anthropic_stub([NS(type="text", text="Done")], "end_turn")
    llm = AnthropicClient(client=sdk)
    llm.complete(
        "s",
        [
            UserText("q"),
            AssistantTurn("", [ToolCall("t1", "run_sql", {})], "anthropic", native),
            ToolResults([ToolResult("t1", "rows", False)]),
            UserText("answer now"),
        ],
        TOOLS,
    )
    msgs = rec.kwargs["messages"]
    assert msgs[1]["content"] is native  # thinking blocks echoed back unchanged
    assert msgs[2]["content"][0] == {"type": "tool_result", "tool_use_id": "t1", "content": "rows", "is_error": False}
    assert msgs[2]["content"][1] == {"type": "text", "text": "answer now"}
    assert len(msgs) == 3


def test_anthropic_refusal_and_models_without_fallbacks():
    rec, sdk = anthropic_stub([], "refusal")
    res = AnthropicClient(model="claude-haiku-5-5", client=sdk).complete("s", [UserText("q")], TOOLS)
    assert res.stop_reason == "refusal"
    assert "fallbacks" not in rec.kwargs  # plain messages.create for models without server-side fallback


# ── OpenAI-compatible ───────────────────────────────────────────────────────
def openai_stub(message, finish_reason="tool_calls"):
    rec = Recorder(
        NS(
            choices=[NS(message=message, finish_reason=finish_reason)],
            model="gpt-4.1-mini",
            usage=NS(prompt_tokens=900, completion_tokens=40),
        )
    )
    return rec, NS(chat=NS(completions=rec))


def test_openai_request_and_tool_call_parsing():
    rec, sdk = openai_stub(
        NS(
            content=None,
            tool_calls=[
                NS(id="call_1", function=NS(name="run_sql", arguments='{"sql": "SELECT 1"}')),
                NS(id="call_2", function=NS(name="run_sql", arguments='{"sql": ')),  # truncated JSON
            ],
        )
    )
    llm = OpenAICompatClient("openai", "gpt-4.1-mini", client=sdk)
    res = llm.complete("system prompt", [UserText("q")], TOOLS)

    assert rec.kwargs["messages"][0] == {"role": "system", "content": "system prompt"}
    assert "max_completion_tokens" in rec.kwargs and "max_tokens" not in rec.kwargs  # GPT-5.x rejects max_tokens
    assert rec.kwargs["tools"][0]["function"]["parameters"] == TOOLS[0].parameters
    assert res.turn.tool_calls[0] == ToolCall("call_1", "run_sql", {"sql": "SELECT 1"})
    assert res.turn.tool_calls[1].arguments is None and "invalid JSON" in res.turn.tool_calls[1].parse_error
    assert res.usage.input_tokens == 900


def test_openai_history_translation():
    rec, sdk = openai_stub(NS(content="Answer", tool_calls=None), "stop")
    llm = OpenAICompatClient("ollama", "qwen2.5:14b", client=sdk)
    res = llm.complete(
        "s",
        [
            UserText("q"),
            AssistantTurn("", [ToolCall("c1", "run_sql", {"sql": "SELECT 1"})], "anthropic", native=object()),
            ToolResults([ToolResult("c1", "column x does not exist", True)]),
        ],
        TOOLS,
    )
    msgs = rec.kwargs["messages"]
    assert json.loads(msgs[2]["tool_calls"][0]["function"]["arguments"]) == {"sql": "SELECT 1"}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "ERROR: column x does not exist"}
    assert res.stop_reason == "end" and res.turn.text == "Answer"


def test_factory_presets(monkeypatch):
    for var in ("LLM_MODEL", "LLM_BASE_URL", "LLM_PROVIDER"):  # ignore a developer .env
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    gemini = create_llm("gemini")
    assert gemini.provider == "gemini" and "generativelanguage" in str(gemini._client.base_url)
    local = create_llm("lmstudio", "my-local-model")
    assert local.model == "my-local-model" and "1234" in str(local._client.base_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert create_llm("anthropic").model == "claude-opus-5-5"


# ── OpenAI Responses API ────────────────────────────────────────────────────
class Item(NS):
    def model_dump(self, exclude_none=True):
        return {k: v for k, v in vars(self).items() if v is not None}


def responses_stub(output, incomplete=None):
    rec = Recorder(
        NS(
            output=output,
            incomplete_details=incomplete,
            model="gpt-5.6-luna",
            usage=NS(input_tokens=3000, output_tokens=200, input_tokens_details=NS(cached_tokens=2048)),
        )
    )
    return rec, NS(responses=rec)


def test_openai_responses_request_and_parsing():
    from data_agent.llm.openai_responses import OpenAIResponsesClient

    rec, sdk = responses_stub(
        [
            Item(type="reasoning", id="rs_1", summary=[]),
            Item(type="function_call", call_id="fc_1", name="run_sql", arguments='{"sql": "SELECT 1"}'),
        ]
    )
    llm = OpenAIResponsesClient(model="gpt-5.6-luna", effort="low", client=sdk)
    res = llm.complete("system prompt", [UserText("q")], TOOLS)

    assert rec.kwargs["instructions"] == "system prompt"
    assert rec.kwargs["reasoning"] == {"effort": "low"}
    assert rec.kwargs["tools"][0] == {
        "type": "function",
        **{k: v for k, v in rec.kwargs["tools"][0].items() if k != "type"},
    }
    assert rec.kwargs["tools"][0]["strict"] is True and "max_tokens" not in rec.kwargs
    assert res.stop_reason == "tool_use" and res.usage.cache_read_tokens == 2048
    assert res.turn.tool_calls == [ToolCall("fc_1", "run_sql", {"sql": "SELECT 1"})]
    assert res.turn.native[0]["type"] == "reasoning"  # reasoning items kept for replay


def test_openai_responses_history_and_non_reasoning_models():
    from data_agent.llm.openai_responses import OpenAIResponsesClient

    rec, sdk = responses_stub([Item(type="message", content=[NS(type="output_text", text="Answer")])])
    native = [{"type": "reasoning", "id": "rs_1"}, {"type": "function_call", "call_id": "fc_1", "name": "run_sql"}]
    llm = OpenAIResponsesClient(model="gpt-4.1-mini", client=sdk)
    res = llm.complete(
        "s",
        [
            UserText("q"),
            AssistantTurn("", [ToolCall("fc_1", "run_sql", {})], "openai", native),
            ToolResults([ToolResult("fc_1", "boom", True)]),
        ],
        TOOLS,
    )
    items = rec.kwargs["input"]
    assert items[1:3] == native
    assert items[3] == {"type": "function_call_output", "call_id": "fc_1", "output": "ERROR: boom"}
    assert "reasoning" not in rec.kwargs  # gpt-4.1 is not a reasoning model
    assert res.stop_reason == "end" and res.turn.text == "Answer"
