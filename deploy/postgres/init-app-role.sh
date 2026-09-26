#!/bin/bash
# First boot only (docker-entrypoint-initdb.d): create the runtime role the API connects as.
# psql binds the password as a quoted literal (:'var'), so it never passes through the shell.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_password="$TIJORI_APP_DB_PASSWORD" <<'SQL'
CREATE ROLE tijori_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
SQL
