import pytest

from data_agent.agent.sql_guard import UnsafeSQLError, guard_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM tickets",
        "select team, avg(csat) from ops.tickets group by team;",
        "WITH recent AS (SELECT * FROM tickets WHERE created_at > now() - interval '30 days') SELECT count(*) FROM recent",
        "SELECT 1 UNION ALL SELECT 2",
        "SELECT percentile_cont(0.9) WITHIN GROUP (ORDER BY csat) FROM tickets",
        "SELECT t.*, row_number() OVER (PARTITION BY team ORDER BY created_at) FROM tickets t",
        "SELECT pg_size_pretty_like FROM tickets_view_named_pg_free",  # names merely containing pg_ are fine
    ],
)
def test_accepts_read_queries(sql):
    guarded = guard_sql(sql, max_rows=50)
    assert guarded.executable.endswith("LIMIT 51")
    assert guarded.original in guarded.executable


@pytest.mark.parametrize(
    "sql, reason",
    [
        ("DELETE FROM tickets", "read queries"),
        ("UPDATE customers SET name = 'x'", "read queries"),
        ("DROP TABLE invoices", "read queries"),
        ("INSERT INTO agents VALUES (1)", "read queries"),
        ("SELECT 1; DELETE FROM tickets", "exactly one"),
        ("WITH gone AS (DELETE FROM tickets RETURNING *) SELECT * FROM gone", "Data-modifying"),
        ("SELECT * INTO backup FROM tickets", "INTO"),
        ("SELECT * FROM tickets FOR UPDATE", "locking"),
        ("SELECT pg_sleep(30)", "pg_sleep"),
        ("SELECT query_to_xml('DELETE FROM tickets', true, true, '')", "query_to_xml"),
        ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
        ("SELECT set_config('statement_timeout', '0', false)", "set_config"),
        ("SELECT * FROM pg_catalog.pg_authid", "schema"),
        ("SELECT * FROM information_schema.tables", "schema"),
        ("SELECT * FROM pg_settings", "System catalog"),
        ("COPY tickets TO '/tmp/out.csv'", "read queries"),
        ("", "Empty"),
        ("SELEC broken", "parse|read queries"),
    ],
)
def test_rejects_unsafe_queries(sql, reason):
    with pytest.raises(UnsafeSQLError, match=reason):
        guard_sql(sql)


def test_cte_named_like_a_system_table_is_allowed():
    guard_sql("WITH pg_stats_month AS (SELECT 1 AS x) SELECT * FROM pg_stats_month")
