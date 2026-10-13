-- =============================================================================
-- Read-only role used by the AI agent.
--
-- First layer of SQL safety, enforced by the database itself, not by the
-- prompt or by Python checks:
--   * SELECT only on the ops schema (no INSERT/UPDATE/DELETE/DDL grants)
--   * every transaction is read-only by default
--   * hard statement timeout, so a bad query cannot hog the database
--   * cannot create objects anywhere
-- The application adds more layers on top (see src/agent/sql_guard.py).
-- The password is set at deploy time (docker/init-agent-password.sh).
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_readonly') THEN
        CREATE ROLE agent_readonly LOGIN NOINHERIT;
    END IF;
END
$$;

REVOKE ALL ON SCHEMA public FROM agent_readonly;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

GRANT USAGE  ON SCHEMA ops TO agent_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA ops TO agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT SELECT ON TABLES TO agent_readonly;

ALTER ROLE agent_readonly SET default_transaction_read_only = on;
ALTER ROLE agent_readonly SET statement_timeout = '5s';
ALTER ROLE agent_readonly SET search_path = ops;
