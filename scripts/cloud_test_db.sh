#!/usr/bin/env bash
# Richtet in einer Cloud-Sitzung (Linux, Debian/Ubuntu) eine WEGWERF-PostgreSQL fuer die Tests ein.
#   bash scripts/cloud_test_db.sh [/pfad/zur/env-datei]      (Standard: /tmp/ai-trading-test-db.env)
#   set -a; source /tmp/ai-trading-test-db.env; set +a
#
# * Nur fuer Testumgebungen ohne echte Daten - NIE auf einem Server mit Daten ausfuehren.
# * Die Passwoerter werden bei jedem Aufruf zufaellig erzeugt und nur in die env-Datei AUSSERHALB des Projekts geschrieben.
# * Legt wie db/00_roles.sh die eingeschraenkte Rolle trading_app an; die Tests erzeugen ihre Datenbanken selbst.
set -euo pipefail
ENV_FILE="${1:-/tmp/ai-trading-test-db.env}"

if [ "$(id -u)" = "0" ]; then
  SUDO=""
  AS_PG="runuser -u postgres --"
elif sudo -n true 2>/dev/null; then
  SUDO="sudo"
  AS_PG="sudo -u postgres"
else
  echo "Weder root noch sudo verfuegbar - PostgreSQL kann hier nicht eingerichtet werden." >&2
  exit 2
fi

if ! ls /usr/lib/postgresql/*/bin/pg_ctl >/dev/null 2>&1; then
  echo "Installiere PostgreSQL aus den Paketquellen ..."
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -qq
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql >/dev/null
fi
VERSION="$(ls /usr/lib/postgresql | sort -V | tail -1)"
if ! $SUDO pg_lsclusters -h 2>/dev/null | grep -q .; then
  $SUDO pg_createcluster "$VERSION" main >/dev/null
fi
$SUDO pg_ctlcluster "$VERSION" main start 2>/dev/null || true
PORT="$($SUDO pg_lsclusters -h | awk -v v="$VERSION" '$1 == v && $2 == "main" {print $3}')"

admin_pw="test-$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
app_pw="test-$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
$AS_PG psql -p "$PORT" -qAt -v ON_ERROR_STOP=1 <<SQL
DO \$\$BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'trading') THEN CREATE ROLE trading SUPERUSER LOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'trading_app') THEN CREATE ROLE trading_app LOGIN; END IF;
END\$\$;
ALTER ROLE trading PASSWORD '$admin_pw';
ALTER ROLE trading_app PASSWORD '$app_pw';
SQL

umask 077
cat > "$ENV_FILE" <<ENV
TEST_ADMIN_URL=postgresql://trading:$admin_pw@127.0.0.1:$PORT/postgres
TEST_APP_PASSWORD=$app_pw
ENV
echo "PostgreSQL $VERSION laeuft auf Port $PORT. Zugangsdaten fuer die Tests: $ENV_FILE"
