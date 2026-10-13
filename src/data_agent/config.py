"""Runtime configuration from environment variables (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .agent import DataAgent, Database, Toolbox
from .llm import create_llm

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str
    max_steps: int
    max_rows: int
    sql_timeout_ms: int
    n8n_webhook_base_url: str | None
    api_token: str | None

    @classmethod
    def from_env(cls) -> Settings:
        url = os.getenv("AGENT_DATABASE_URL")
        if not url:
            raise RuntimeError(
                "AGENT_DATABASE_URL is not set. Copy .env.example to .env "
                "(or run `data-agent dev-db` to start a local database)."
            )
        return cls(
            database_url=url,
            max_steps=int(os.getenv("AGENT_MAX_STEPS", "12")),
            max_rows=int(os.getenv("SQL_MAX_ROWS", "200")),
            sql_timeout_ms=int(os.getenv("SQL_TIMEOUT_MS", "5000")),
            n8n_webhook_base_url=os.getenv("N8N_WEBHOOK_BASE_URL") or None,
            api_token=os.getenv("API_TOKEN") or None,
        )


def build_agent(settings: Settings, provider: str | None = None, model: str | None = None, on_step=None) -> DataAgent:
    toolbox = Toolbox(
        Database(settings.database_url, settings.sql_timeout_ms),
        max_rows=settings.max_rows,
        n8n_base_url=settings.n8n_webhook_base_url,
    )
    return DataAgent(create_llm(provider, model), toolbox, max_steps=settings.max_steps, on_step=on_step)
