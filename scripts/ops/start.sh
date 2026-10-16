#!/usr/bin/env bash
# Startet den Betrieb: Datenbank -> Sicherung mit Rueckspiel-Test -> Migration -> Paper-Dienst (AP-27).
# Aufruf: scripts/ops/start.sh /pfad/ausserhalb/trading-secrets.env
set -euo pipefail
source "$(dirname "$0")/common.sh"
load_env_file "${1:-}"

echo "1/4 Datenbank starten"
dc up -d --build --wait db
echo "2/4 Sicherung vor jeder Aenderung (mit Rueckspiel-Test)"
prepare_backup_dir
dc run --rm --build backup
echo "3/4 Schema aktualisieren"
dc run --rm --build migrate
echo "4/4 Paper-Dienst starten"
dc up -d --build trader
if trading_enabled; then
  echo "Freigabe gesetzt: Der Paper-Handel laeuft (kein echtes Geld). Zustand: scripts/ops/status.sh $ENV_FILE"
else
  echo "KEINE Freigabe: Der Dienst beendet sich sofort wieder - es wird nicht gehandelt."
fi
