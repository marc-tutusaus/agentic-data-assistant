"""Shared fixtures: a fresh PostgreSQL database with schema + seed loaded.

Uses TEST_DATABASE_URL when set (CI uses a postgres service container).
Otherwise starts a throw-away local PostgreSQL with `pgserver` (dev only).
"""

import os
import pathlib
import uuid

import psycopg
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SQL = ROOT / "sql"
LOAD_ORDER = [
    SQL / "schema" / "01_schema.sql",
    SQL / "seed" / "01_seed.sql",
    SQL / "schema" / "02_agent_readonly_role.sql",
]
AGENT_PASSWORD = "test-agent-password"


def _server_url(tmp_path_factory) -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return url
    pgserver = pytest.importorskip("pgserver", reason="set TEST_DATABASE_URL or `pip install pgserver`")
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="stop")
    return server.get_uri()


def _with_db(url: str, dbname: str, user: str | None = None, password: str | None = None) -> str:
    info = psycopg.conninfo.conninfo_to_dict(url)
    info["dbname"] = dbname
    if user:
        info["user"], info["password"] = user, password
    return psycopg.conninfo.make_conninfo(**info)


@pytest.fixture(scope="session")
def db_urls(tmp_path_factory):
    """Create a fresh database, load everything, return admin + agent URLs."""
    server_url = _server_url(tmp_path_factory)
    dbname = f"test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(server_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')

    admin_url = _with_db(server_url, dbname)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        for f in LOAD_ORDER:
            conn.execute(f.read_text(encoding="utf-8"))
        conn.execute(f"ALTER ROLE agent_readonly PASSWORD '{AGENT_PASSWORD}'")

    yield {"admin": admin_url, "agent": _with_db(server_url, dbname, "agent_readonly", AGENT_PASSWORD)}

    with psycopg.connect(server_url, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


@pytest.fixture
def admin_conn(db_urls):
    with psycopg.connect(db_urls["admin"], autocommit=True) as conn:
        yield conn


@pytest.fixture
def agent_conn(db_urls):
    with psycopg.connect(db_urls["agent"], autocommit=True) as conn:
        yield conn
