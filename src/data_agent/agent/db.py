"""Database access for the agent: always read-only, always time-limited."""

from __future__ import annotations

import datetime as dt
import decimal
from dataclasses import dataclass
from typing import Any

import psycopg


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False


def to_json_value(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return round(v.total_seconds() / 3600, 2)  # hours: easier for the model to reason about
    return v


class Database:
    def __init__(self, url: str, timeout_ms: int = 5000):
        self.url = url
        self.timeout_ms = timeout_ms

    def query(self, sql: str, params: tuple | dict | None = None, max_rows: int | None = None) -> QueryResult:
        """Run one statement in a READ ONLY transaction with a local timeout.

        Defense in depth: the agent's DB role is already read-only with a timeout,
        and this repeats both at transaction level in case the app is pointed at
        a more privileged role by mistake.
        """
        with psycopg.connect(self.url) as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute(f"SET LOCAL statement_timeout = {int(self.timeout_ms)}")
                cur = conn.execute(sql, params)
                if cur.description is None:
                    return QueryResult([], [])
                columns = [d.name for d in cur.description]
                rows = cur.fetchall() if max_rows is None else cur.fetchmany(max_rows + 1)
        truncated = max_rows is not None and len(rows) > max_rows
        rows = rows[:max_rows] if max_rows is not None else rows
        return QueryResult(columns, [[to_json_value(v) for v in r] for r in rows], truncated)

    def script(self, sql: str) -> list[QueryResult]:
        """Run a trusted multi-statement file (saved queries), returning every result set."""
        results = []
        with psycopg.connect(self.url) as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute(f"SET LOCAL statement_timeout = {int(self.timeout_ms)}")
                cur = conn.cursor()
                cur.execute(sql)
                while True:
                    if cur.description:
                        results.append(
                            QueryResult(
                                [d.name for d in cur.description],
                                [[to_json_value(v) for v in r] for r in cur.fetchall()],
                            )
                        )
                    if not cur.nextset():
                        break
        return results
