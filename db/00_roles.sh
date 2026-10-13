#!/bin/bash
# Legt beim allerersten Start die eingeschraenkte Anwendungsrolle an.
# Das Passwort kommt aus der Umgebung (APP_DB_PASSWORD), nie aus dem Code.
set -euo pipefail
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD fehlt}"
psql -v ON_ERROR_STOP=1 -v pw="$APP_DB_PASSWORD" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'EOSQL'
CREATE ROLE trading_app LOGIN PASSWORD :'pw';
EOSQL
