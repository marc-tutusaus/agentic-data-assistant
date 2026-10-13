"""API tests: FastAPI TestClient with a scripted LLM."""

import pytest
from fastapi.testclient import TestClient

from data_agent import api
from data_agent.agent import DataAgent, Database, Toolbox
from data_agent.config import Settings
from data_agent.llm.fake import ScriptedLLM, say, tool


@pytest.fixture
def client(db_urls, monkeypatch):
    settings = Settings(
        database_url=db_urls["agent"],
        max_steps=5,
        max_rows=50,
        sql_timeout_ms=5000,
        n8n_webhook_base_url=None,
        api_token="secret-token",
    )
    api.app.dependency_overrides[api.get_settings] = lambda: settings

    def fake_build_agent(s, provider=None, model=None, on_step=None):
        llm = ScriptedLLM(
            tool("run_sql", sql="SELECT count(*) AS customers FROM customers", purpose="count"),
            say("There are 120 customers."),
        )
        return DataAgent(llm, Toolbox(Database(s.database_url)), max_steps=s.max_steps)

    monkeypatch.setattr(api, "build_agent", fake_build_agent)
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}


def test_ask_requires_token(client):
    assert client.post("/ask", json={"question": "How many customers?"}).status_code == 401
    bad = client.post("/ask", json={"question": "How many customers?"}, headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401


def test_ask_returns_answer_trace_and_sql(client):
    r = client.post("/ask", json={"question": "How many customers?"}, headers={"Authorization": "Bearer secret-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "There are 120 customers."
    assert body["stop"] == "answered"
    assert body["sql"] == ["SELECT count(*) AS customers FROM customers"]
    assert body["steps"][0]["tool"] == "run_sql" and body["steps"][0]["ok"] is True
    assert body["usage"]["input_tokens"] > 0


def test_ask_validates_input(client):
    headers = {"Authorization": "Bearer secret-token"}
    assert client.post("/ask", json={"question": ""}, headers=headers).status_code == 422
    assert client.post("/ask", json={"question": "hello?", "provider": "skynet"}, headers=headers).status_code == 422
