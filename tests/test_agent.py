"""End-to-end agent tests: scripted LLM + real PostgreSQL (read-only agent role)."""

import json

import httpx
import pytest

from data_agent.agent import DataAgent, Database, Toolbox
from data_agent.llm import AssistantTurn, LLMResponse, ToolCall, ToolResults, Usage
from data_agent.llm.fake import ScriptedLLM, say, tool


@pytest.fixture
def toolbox(db_urls):
    return Toolbox(Database(db_urls["agent"]), max_rows=50)


def last_tool_results(llm: ScriptedLLM) -> list:
    """Tool results the agent sent back to the model on its latest call."""
    return [m for m in llm.calls[-1]["messages"] if isinstance(m, ToolResults)][-1].results


def test_agent_explores_queries_self_corrects_and_answers(toolbox):
    llm = ScriptedLLM(
        tool("list_tables"),
        tool("describe_table", table="tickets"),
        tool("run_sql", sql="SELECT tema, count(*) FROM tickets GROUP BY 1", purpose="tickets per team"),  # typo
        tool(
            "run_sql",
            sql="SELECT team, count(*) AS n FROM tickets GROUP BY 1 ORDER BY 2 DESC",
            purpose="tickets per team",
        ),
        say("Service Desk handles the most tickets."),
    )
    result = DataAgent(llm, toolbox).ask("Which team handles the most tickets?")

    assert result.stop == "answered"
    assert result.answer == "Service Desk handles the most tickets."
    assert [s.tool for s in result.steps] == ["list_tables", "describe_table", "run_sql", "run_sql"]
    assert [s.ok for s in result.steps] == [True, True, False, True]
    assert "tema" in result.steps[2].error  # the DB error went back to the model...
    assert result.sql[-1].startswith("SELECT team")  # ...and it fixed the query
    assert result.usage == Usage(500, 130)

    tables = json.loads(llm.calls[1]["messages"][-1].results[0].content)
    assert {"tickets", "customers", "ai_triage"} <= {t["table"] for t in tables}
    assert all(t["description"] for t in tables)

    rows = json.loads(last_tool_results(llm)[0].content)["rows"]
    assert len(rows) == 6 and rows[0][0] == "Service Desk"


def test_describe_table_exposes_business_comments(toolbox):
    out = toolbox.execute("describe_table", {"table": "contracts"})
    payload = json.loads(out.content)
    end_date = next(c for c in payload["columns"] if c["column"] == "end_date")
    assert "churned" in end_date["description"]
    assert any("customers" in fk for fk in payload["foreign_keys"])


def test_destructive_sql_from_the_model_never_reaches_the_db(toolbox, admin_conn):
    before = admin_conn.execute("SELECT count(*) FROM ops.tickets").fetchone()[0]
    llm = ScriptedLLM(
        tool("run_sql", sql="DELETE FROM tickets", purpose="cleanup"),
        tool("run_sql", sql="WITH x AS (DELETE FROM tickets RETURNING 1) SELECT count(*) FROM x", purpose="sneaky"),
        say("I can only read data."),
    )
    result = DataAgent(llm, toolbox).ask("Delete all tickets")
    assert [s.ok for s in result.steps] == [False, False]
    assert all("rejected" in s.error for s in result.steps)
    assert admin_conn.execute("SELECT count(*) FROM ops.tickets").fetchone()[0] == before


def test_large_results_are_capped(toolbox):
    out = toolbox.execute("run_sql", {"sql": "SELECT ticket_id FROM tickets", "purpose": "all ids"})
    payload = json.loads(out.content)
    assert payload["row_count"] == 50 and "aggregate" in payload["note"]
    assert out.meta["truncated"] is True


def test_saved_queries_are_listed_and_run(toolbox):
    listed = json.loads(toolbox.execute("list_saved_queries", {}).content)
    names = {q["name"] for q in listed}
    assert {"churn_risk", "revenue_leakage", "ai_triage_quality"} <= names
    assert all(q["question"] for q in listed)

    out = toolbox.execute("run_saved_query", {"name": "ai_triage_quality"})
    result_sets = json.loads(out.content)
    assert len(result_sets) == 2  # bands + model comparison
    assert result_sets[1]["columns"][0] == "model"


def test_step_limit_forces_a_final_answer(toolbox):
    llm = ScriptedLLM(*[tool("list_tables") for _ in range(3)], say("Partial answer: not enough steps."))
    result = DataAgent(llm, toolbox, max_steps=3).ask("Complex question")
    assert result.stop == "max_steps"
    assert result.answer.startswith("Partial answer")
    assert "step limit" in llm.calls[-1]["messages"][-1].text


def test_refusal_is_reported(toolbox):
    def refuse(_messages):
        return LLMResponse(AssistantTurn("", [], "fake"), "refusal", Usage(10, 0), "fake-model")

    result = DataAgent(ScriptedLLM(refuse), toolbox).ask("...")
    assert result.stop == "refused"


def test_invalid_tool_arguments_are_returned_as_errors(toolbox):
    def bad_json(_messages):
        call = ToolCall("c1", "run_sql", None, "invalid JSON arguments: Expecting value")
        return LLMResponse(AssistantTurn("", [call], "fake"), "tool_use", Usage(10, 5), "fake-model")

    llm = ScriptedLLM(bad_json, say("ok"))
    result = DataAgent(llm, toolbox).ask("...")
    assert result.steps[0].ok is False
    assert "invalid JSON" in last_tool_results(llm)[0].content


def test_trigger_workflow_posts_to_allowlisted_n8n_webhook(db_urls):
    sent = []
    transport = httpx.MockTransport(lambda req: sent.append(req) or httpx.Response(200, json={"ok": True}))
    box = Toolbox(
        Database(db_urls["agent"]), n8n_base_url="http://n8n:5678/webhook/", http=httpx.Client(transport=transport)
    )

    assert "trigger_workflow" in [s.name for s in box.specs()]
    out = box.execute(
        "trigger_workflow", {"workflow": "send_report", "title": "Weekly SLA", "body": "All good", "severity": "info"}
    )
    assert not out.is_error
    assert str(sent[0].url) == "http://n8n:5678/webhook/agent-send-report"
    assert json.loads(sent[0].content)["title"] == "Weekly SLA"

    denied = box.execute(
        "trigger_workflow", {"workflow": "delete_everything", "title": "", "body": "", "severity": "info"}
    )
    assert denied.is_error and len(sent) == 1


def test_trigger_workflow_is_not_offered_without_n8n(toolbox):
    assert "trigger_workflow" not in [s.name for s in toolbox.specs()]
