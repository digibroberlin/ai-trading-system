#!/usr/bin/env bash
# Gemeinsame Bausteine der Betriebs-Skripte (AP-27, D-029). Nicht direkt aufrufen.
#   ENV-Datei: erstes Argument oder TRADING_ENV_FILE - muss AUSSERHALB des Projekts liegen und Rechte 600 haben.
#   DRY_RUN=1: nur anzeigen, was ausgefuehrt wuerde (fuer Tests und zum Nachlesen).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.prod.yml"

fail() { echo "FEHLER: $*" >&2; exit 1; }

load_env_file() {
  ENV_FILE="${1:-${TRADING_ENV_FILE:-}}"
  [ -n "$ENV_FILE" ] || fail "Passwort-Datei angeben: $0 /pfad/ausserhalb/trading-secrets.env"
  [ -f "$ENV_FILE" ] || fail "Passwort-Datei $ENV_FILE nicht gefunden."
  ENV_FILE="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
  case "$ENV_FILE" in
    "$ROOT"/*) fail "Die Passwort-Datei darf nicht im Projektordner liegen (sie koennte sonst eingecheckt werden)." ;;
  esac
  local mode
  mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE")"
  [ "$mode" = "600" ] || [ "$mode" = "400" ] || fail "Passwort-Datei hat Rechte $mode - bitte: chmod 600 $ENV_FILE"
}

# Sicherungen gehoeren dem Benutzer, der die Skripte ausfuehrt (docker-compose.prod.yml: backup.user)
OPS_UID="$(id -u)"
OPS_GID="$(id -g)"
export OPS_UID OPS_GID

prepare_backup_dir() {
  if [ "${DRY_RUN:-0}" = "1" ]; then echo "+ mkdir -p -m 700 $ROOT/backups"; else mkdir -p -m 700 "$ROOT/backups"; fi
}

# docker compose (neu) oder docker-compose (alt, z. B. Colima auf dem Mac)
dc() {
  local cmd
  if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "+ compose $*"
    return 0
  fi
  if docker compose version >/dev/null 2>&1; then cmd=(docker compose); else cmd=(docker-compose); fi
  "${cmd[@]}" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

env_value() {  # letzter Wert einer Variablen in der Passwort-Datei, ohne Anfuehrungszeichen (nie ausgeben!)
  sed -n "s/^[[:space:]]*\(export[[:space:]]\+\)\?$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" | tail -n 1 | tr -d "\"'"
}

trading_enabled() {
  # Nur zur Anzeige: Steht die Freigabe in der Passwort-Datei? Massgeblich prueft der Dienst selbst.
  [ "$(env_value TRADING_ENGINE_ENABLED)" = "1" ] && [ -n "$(env_value TRADING_ENGINE_APPROVAL)" ]
}
