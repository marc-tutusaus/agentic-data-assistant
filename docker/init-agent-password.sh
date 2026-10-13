#!/bin/sh
# Runs once, after the schema, seed and role scripts (docker-entrypoint-initdb.d).
# Sets the agent role password from the environment, so it is never hard-coded in SQL.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v pwd="$AGENT_DB_PASSWORD" <<'SQL'
ALTER ROLE agent_readonly WITH PASSWORD :'pwd';
SQL
