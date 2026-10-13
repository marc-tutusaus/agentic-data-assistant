"""The agent's tools: explore the model, query it safely, reuse vetted queries, act via n8n."""

from __future__ import annotations

import json
import os
import pathlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import psycopg

from ..llm import ToolSpec
from .db import Database
from .sql_guard import UnsafeSQLError, guard_sql


def default_saved_queries_dir() -> pathlib.Path:
    """SAVED_QUERIES_DIR, else the repo's sql/analytics (source checkout), else ./sql/analytics."""
    if os.getenv("SAVED_QUERIES_DIR"):
        return pathlib.Path(os.environ["SAVED_QUERIES_DIR"])
    repo_dir = pathlib.Path(__file__).resolve().parents[3] / "sql" / "analytics"
    return repo_dir if repo_dir.is_dir() else pathlib.Path.cwd() / "sql" / "analytics"


# Workflows the agent may trigger: name -> (n8n webhook path, description for the model)
WORKFLOWS = {
    "send_report": ("agent-send-report", "Send a written report (e.g. an analysis summary) to the team by email."),
    "raise_alert": ("agent-raise-alert", "Raise an alert to the on-call channel about a problem found in the data."),
}


class ToolError(Exception):
    """Expected tool failure, returned to the model as an error result it can act on."""


@dataclass
class ToolOutput:
    content: str
    is_error: bool = False
    meta: dict[str, Any] = field(default_factory=dict)  # for traces (e.g. executed SQL), not sent to the model


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or list(properties),
        "additionalProperties": False,
    }


class Toolbox:
    def __init__(
        self,
        db: Database,
        max_rows: int = 200,
        saved_queries_dir: pathlib.Path | None = None,
        n8n_base_url: str | None = None,
        http: httpx.Client | None = None,
    ):
        self.db = db
        self.max_rows = max_rows
        self.saved_queries = self._load_saved_queries(saved_queries_dir or default_saved_queries_dir())
        self.n8n_base_url = n8n_base_url.rstrip("/") if n8n_base_url else None
        self.http = http or httpx.Client(timeout=15)
        self._handlers: dict[str, Callable[[dict[str, Any]], ToolOutput]] = {
            "list_tables": self.list_tables,
            "describe_table": self.describe_table,
            "run_sql": self.run_sql,
            "list_saved_queries": self.list_saved_queries,
            "run_saved_query": self.run_saved_query,
        }
        if self.n8n_base_url:
            self._handlers["trigger_workflow"] = self.trigger_workflow

    # ── tool definitions sent to the LLM ────────────────────────────────────
    def specs(self) -> list[ToolSpec]:
        specs = [
            ToolSpec(
                "list_tables",
                "List the tables of the company database with their description and approximate row count. "
                "Start here when you don't know the data model yet.",
                _schema({}),
            ),
            ToolSpec(
                "describe_table",
                "Show the columns of one table: type, nullability, documentation comment and foreign keys. "
                "Call it before writing SQL against a table, so you never guess column names or meanings.",
                _schema({"table": {"type": "string", "description": "Table name, e.g. tickets"}}),
            ),
            ToolSpec(
                "run_sql",
                "Run ONE read-only PostgreSQL query (SELECT or WITH ... SELECT) on schema ops and get the rows "
                f"as JSON. At most {self.max_rows} rows are returned, so aggregate in SQL instead of fetching "
                "raw rows. Intervals are returned in hours. On error you get the database message: fix the "
                "query and try again.",
                _schema(
                    {
                        "sql": {"type": "string", "description": "A single PostgreSQL SELECT statement"},
                        "purpose": {"type": "string", "description": "One sentence: what this query answers"},
                    }
                ),
            ),
            ToolSpec(
                "list_saved_queries",
                "List reviewed analytics queries (SLA, churn risk, revenue leakage, AI triage quality...). "
                "Prefer one of these when it answers the question: they are tested and correct.",
                _schema({}),
            ),
            ToolSpec(
                "run_saved_query",
                "Run one of the reviewed analytics queries by name and get all its result sets.",
                _schema(
                    {
                        "name": {
                            "type": "string",
                            "enum": sorted(self.saved_queries),
                            "description": "Saved query name from list_saved_queries",
                        }
                    }
                ),
            ),
        ]
        if self.n8n_base_url:
            specs.append(
                ToolSpec(
                    "trigger_workflow",
                    "Trigger an n8n automation. Only use it when the user explicitly asks to send, notify or alert. "
                    + " ".join(f"'{k}': {v[1]}" for k, v in WORKFLOWS.items()),
                    _schema(
                        {
                            "workflow": {"type": "string", "enum": sorted(WORKFLOWS)},
                            "title": {"type": "string", "description": "Short subject line"},
                            "body": {"type": "string", "description": "Content in Markdown, with the key numbers"},
                            "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                        }
                    ),
                )
            )
        return specs

    # ── dispatch ────────────────────────────────────────────────────────────
    def execute(self, name: str, arguments: dict[str, Any] | None) -> ToolOutput:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolOutput(f"Unknown tool '{name}'. Available: {', '.join(self._handlers)}", is_error=True)
        if arguments is None:
            return ToolOutput("Tool arguments were not valid JSON. Send a JSON object.", is_error=True)
        try:
            return handler(arguments)
        except ToolError as e:
            return ToolOutput(str(e), is_error=True)
        except UnsafeSQLError as e:
            return ToolOutput(f"Query rejected: {e}", is_error=True)
        except psycopg.errors.QueryCanceled:
            return ToolOutput(
                "The query took too long and was cancelled. Simplify it, filter by date or aggregate earlier.",
                is_error=True,
            )
        except psycopg.Error as e:
            message = (e.diag.message_primary if getattr(e, "diag", None) else None) or str(e)
            hint = f" Hint: {e.diag.message_hint}" if getattr(e, "diag", None) and e.diag.message_hint else ""
            return ToolOutput(f"Database error: {message}.{hint}", is_error=True)

    # ── handlers ────────────────────────────────────────────────────────────
    def list_tables(self, _args: dict[str, Any]) -> ToolOutput:
        result = self.db.query("""
            SELECT c.relname AS table, obj_description(c.oid, 'pg_class') AS description,
                   greatest(c.reltuples, 0)::bigint AS approx_rows
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'ops' AND c.relkind = 'r'
            ORDER BY c.relname""")
        return ToolOutput(
            json.dumps([dict(zip(result.columns, r, strict=True)) for r in result.rows], ensure_ascii=False)
        )

    def describe_table(self, args: dict[str, Any]) -> ToolOutput:
        table = str(args.get("table", "")).split(".")[-1].strip().lower()
        columns = self.db.query(
            """
            SELECT a.attname AS column, format_type(a.atttypid, a.atttypmod) AS type,
                   NOT a.attnotnull AS nullable, col_description(a.attrelid, a.attnum) AS description
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'ops' AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum""",
            (table,),
        )
        if not columns.rows:
            raise ToolError(f"Table '{table}' does not exist in schema ops. Call list_tables to see the tables.")
        fks = self.db.query(
            """
            SELECT pg_get_constraintdef(con.oid) AS foreign_key
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'ops' AND c.relname = %s AND con.contype = 'f'""",
            (table,),
        )
        payload = {
            "table": table,
            "columns": [{k: v for k, v in zip(columns.columns, r, strict=True) if v is not None} for r in columns.rows],
            "foreign_keys": [r[0] for r in fks.rows],
        }
        return ToolOutput(json.dumps(payload, ensure_ascii=False))

    def run_sql(self, args: dict[str, Any]) -> ToolOutput:
        guarded = guard_sql(str(args.get("sql", "")), self.max_rows)
        result = self.db.query(guarded.executable, max_rows=self.max_rows)
        payload = {"columns": result.columns, "rows": result.rows, "row_count": len(result.rows)}
        if result.truncated:
            payload["note"] = f"Only the first {self.max_rows} rows are shown: aggregate or filter in SQL."
        return ToolOutput(
            json.dumps(payload, ensure_ascii=False, default=str),
            meta={"sql": guarded.original, "rows": len(result.rows), "truncated": result.truncated},
        )

    def list_saved_queries(self, _args: dict[str, Any]) -> ToolOutput:
        return ToolOutput(
            json.dumps(
                [{"name": k, "question": v["question"]} for k, v in self.saved_queries.items()], ensure_ascii=False
            )
        )

    def run_saved_query(self, args: dict[str, Any]) -> ToolOutput:
        name = str(args.get("name", ""))
        if name not in self.saved_queries:
            raise ToolError(f"Unknown saved query '{name}'. Call list_saved_queries.")
        results = self.db.script(self.saved_queries[name]["sql"])
        payload = [{"columns": r.columns, "rows": r.rows[: self.max_rows]} for r in results]
        return ToolOutput(json.dumps(payload, ensure_ascii=False, default=str), meta={"saved_query": name})

    def trigger_workflow(self, args: dict[str, Any]) -> ToolOutput:
        workflow = str(args.get("workflow", ""))
        if workflow not in WORKFLOWS:
            raise ToolError(f"Unknown workflow '{workflow}'. Allowed: {', '.join(WORKFLOWS)}")
        url = f"{self.n8n_base_url}/{WORKFLOWS[workflow][0]}"
        body = {k: args.get(k) for k in ("title", "body", "severity")} | {"source": "agentic-data-assistant"}
        try:
            response = self.http.post(url, json=body)
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise ToolError(f"Workflow '{workflow}' could not be triggered: {e}") from e
        return ToolOutput(
            json.dumps({"status": "triggered", "workflow": workflow}),
            meta={"workflow": workflow, "http_status": response.status_code},
        )

    # ── helpers ─────────────────────────────────────────────────────────────
    @staticmethod
    def _load_saved_queries(directory: pathlib.Path) -> dict[str, dict[str, str]]:
        """Each .sql file starts with '-- Q: <business question>' (may continue on the next comment line)."""
        queries = {}
        for path in sorted(directory.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            match = re.search(r"--\s*Q:\s*(.+?)\n(?:--\s{2,}(.+?)\n)?", sql)
            question = " ".join(g.strip() for g in match.groups() if g) if match else path.stem
            queries[re.sub(r"^\d+_", "", path.stem)] = {"question": question, "sql": sql}
        return queries
