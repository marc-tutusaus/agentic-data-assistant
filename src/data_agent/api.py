"""HTTP API, used by n8n workflows and any other client."""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import Settings, build_agent
from .llm import PROVIDERS, LLMError

app = FastAPI(
    title="Agentic Data Assistant",
    version="0.1.0",
    description="Ask business questions in natural language; an LLM agent answers with SQL-backed data.",
)


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()


def require_token(
    settings: Annotated[Settings, Depends(get_settings)], authorization: Annotated[str | None, Header()] = None
) -> None:
    if settings.api_token is None:
        return
    if not authorization or not secrets.compare_digest(authorization, f"Bearer {settings.api_token}"):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


class AskRequest(BaseModel):
    question: str = Field(
        min_length=3, max_length=2000, examples=["Which active customers are at risk of churning, and why?"]
    )
    provider: str | None = Field(default=None, description=f"One of: {', '.join(PROVIDERS)}")
    model: str | None = None


@app.get("/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, Any]:
    from .agent import Database

    try:
        Database(settings.database_url).query("SELECT 1")
        return {"status": "ok", "database": "ok"}
    except Exception as e:  # noqa: BLE001 - health check reports any failure
        raise HTTPException(status_code=503, detail=f"database unavailable: {type(e).__name__}") from e


@app.post("/ask", dependencies=[Depends(require_token)])
def ask(body: AskRequest, settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, Any]:
    if body.provider and body.provider not in PROVIDERS:
        raise HTTPException(status_code=422, detail=f"provider must be one of {PROVIDERS}")
    try:
        result = build_agent(settings, body.provider, body.model).ask(body.question)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return result.to_dict()
