"""Data layer tests: seed invariants, analytics queries and read-only role."""

import pathlib

import psycopg
import pytest

ANALYTICS = sorted((pathlib.Path(__file__).resolve().parents[1] / "sql" / "analytics").glob("*.sql"))


def _statements(sql: str) -> list[str]:
    """Split a file into statements, ignoring comment-only chunks."""
    out = []
    for chunk in sql.split(";"):
        code = "\n".join(ln for ln in chunk.splitlines() if not ln.strip().startswith("--")).strip()
        if code:
            out.append(chunk)
    return out


# ── seed invariants ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "table, minimum",
    [
        ("customers", 100),
        ("contracts", 100),
        ("agents", 10),
        ("tickets", 5000),
        ("ai_triage", 3000),
        ("time_entries", 5000),
        ("invoices", 1000),
    ],
)
def test_tables_are_populated(admin_conn, table, minimum):
    (count,) = admin_conn.execute(f"SELECT count(*) FROM ops.{table}").fetchone()
    assert count >= minimum


def test_every_ticket_belongs_to_an_active_contract(admin_conn):
    (orphans,) = admin_conn.execute("""
        SELECT count(*) FROM ops.tickets t
        WHERE NOT EXISTS (
            SELECT 1 FROM ops.contracts co
            WHERE co.customer_id = t.customer_id
              AND t.created_at::date >= co.start_date
              AND (co.end_date IS NULL OR t.created_at::date <= co.end_date))
    """).fetchone()
    assert orphans == 0


def test_seed_contains_the_stories_the_analytics_look_for(admin_conn):
    churned, leaked, voice = admin_conn.execute("""
        SELECT
          (SELECT count(*) FROM ops.customers cu WHERE NOT EXISTS
             (SELECT 1 FROM ops.contracts co WHERE co.customer_id = cu.customer_id AND co.end_date IS NULL)),
          (SELECT count(*) FROM ops.time_entries
             WHERE billable AND invoice_id IS NULL AND logged_at < date_trunc('month', current_date)),
          (SELECT count(*) FROM ops.tickets WHERE channel = 'voice_agent')
    """).fetchone()
    assert churned > 0 and leaked > 0 and voice > 0


def test_ai_accuracy_increases_with_confidence(admin_conn):
    low, high = admin_conn.execute("""
        SELECT avg((a.predicted_team = t.team)::int) FILTER (WHERE a.confidence < 70),
               avg((a.predicted_team = t.team)::int) FILTER (WHERE a.confidence >= 90)
        FROM ops.ai_triage a JOIN ops.tickets t USING (ticket_id)
    """).fetchone()
    assert high > low


# ── analytics pack ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ANALYTICS, ids=lambda p: p.stem)
def test_analytics_query_runs_and_returns_rows(agent_conn, path):
    """Every analytics query must run under the read-only agent role."""
    for stmt in _statements(path.read_text(encoding="utf-8")):
        cur = agent_conn.execute(stmt)
        if cur.description:
            assert cur.fetchall(), f"{path.name} returned no rows"


# ── read-only role ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "stmt",
    [
        "DELETE FROM ops.tickets",
        "UPDATE ops.customers SET name = 'x'",
        "INSERT INTO ops.agents (full_name, team, hourly_cost, hired_at) VALUES ('x', 'Sales', 1, current_date)",
        "DROP TABLE ops.invoices",
        "CREATE TABLE ops.evil (id int)",
        "CREATE TABLE public.evil (id int)",
    ],
)
def test_agent_role_cannot_write(agent_conn, stmt):
    with pytest.raises(psycopg.Error):
        agent_conn.execute(stmt)


def test_agent_role_has_statement_timeout(agent_conn):
    (timeout,) = agent_conn.execute("SHOW statement_timeout").fetchone()
    assert timeout == "5s"
    with pytest.raises(psycopg.errors.QueryCanceled):
        agent_conn.execute("SELECT pg_sleep(10)")
