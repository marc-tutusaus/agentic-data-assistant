"""Second layer of SQL safety: validate LLM-written SQL before it reaches the DB.

Layer 1 is the database role (read-only, statement timeout) and always holds.
This layer rejects anything that is not a single read query and gives the
model a precise error message, so it can fix its own query on the next step.

The SQL is parsed with sqlglot (a real parser, not regexes) only to validate
it. The original text is executed unchanged, wrapped in an outer LIMIT, so a
parser round-trip can never alter the meaning of the query.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

ALLOWED_SCHEMAS = {"ops"}

# Functions that can sleep, read files, call other servers, change settings
# or run arbitrary SQL strings.
BLOCKED_FUNCTION_PREFIXES = (
    "pg_sleep",
    "pg_read",
    "pg_ls_",
    "pg_stat_file",
    "pg_terminate",
    "pg_cancel",
    "pg_reload",
    "pg_rotate",
    "pg_advisory",
    "pg_notify",
    "lo_",
    "dblink",
    "set_config",
    "query_to_xml",
    "query_to_json",
    "table_to_xml",
    "cursor_to_xml",
    "database_to_xml",
    "schema_to_xml",
    "pg_logical",
    "pg_replication",
)


class UnsafeSQLError(ValueError):
    """The query was rejected. The message is written for the LLM to act on."""


@dataclass(frozen=True)
class GuardedQuery:
    original: str
    executable: str  # original wrapped with the row cap


def _function_name(func: exp.Func) -> str:
    return (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()


def guard_sql(sql: str, max_rows: int = 200) -> GuardedQuery:
    original = sql.strip().rstrip(";").strip()
    if not original:
        raise UnsafeSQLError("Empty query.")

    try:
        statements = [s for s in sqlglot.parse(original, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQLError(f"Could not parse the SQL: {str(e).splitlines()[0]}") from e

    if len(statements) != 1:
        raise UnsafeSQLError("Send exactly one SQL statement per call.")
    stmt = statements[0]

    if not isinstance(stmt, exp.Query):
        raise UnsafeSQLError(f"Only read queries (SELECT / WITH ... SELECT) are allowed, got {stmt.key.upper()}.")
    for node in stmt.walk():
        if isinstance(
            node,
            (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Copy),
        ):
            raise UnsafeSQLError("Data-modifying statements are not allowed, even inside a CTE.")
        if isinstance(node, exp.Into):
            raise UnsafeSQLError("SELECT ... INTO is not allowed.")
        if isinstance(node, exp.Lock):
            raise UnsafeSQLError("Row locking (FOR UPDATE / FOR SHARE) is not allowed.")

    for func in stmt.find_all(exp.Func):
        name = _function_name(func)
        if name.startswith(BLOCKED_FUNCTION_PREFIXES):
            raise UnsafeSQLError(f"Function {name}() is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    for table in stmt.find_all(exp.Table):
        schema, name = table.db.lower(), table.name.lower()
        if schema and schema not in ALLOWED_SCHEMAS:
            raise UnsafeSQLError(
                f"Only tables in schema {', '.join(sorted(ALLOWED_SCHEMAS))} can be queried, "
                f"not {schema}.{name}. Use list_tables / describe_table to explore the model."
            )
        if not schema and name not in cte_names and name.startswith(("pg_", "information_schema")):
            raise UnsafeSQLError(f"System catalog {name} is not available. Use list_tables / describe_table.")

    # fetch one extra row to detect truncation
    executable = f"SELECT * FROM (\n{original}\n) AS agent_query LIMIT {max_rows + 1}"
    return GuardedQuery(original=original, executable=executable)
