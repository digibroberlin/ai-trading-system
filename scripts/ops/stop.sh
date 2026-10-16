#!/usr/bin/env bash
# Haelt den Paper-Dienst sauber an (60 s Zeit zum Beenden). Mit --all auch die Datenbank.
# Aufruf: scripts/ops/stop.sh /pfad/ausserhalb/trading-secrets.env [--all]
set -euo pipefail
source "$(dirname "$0")/common.sh"
load_env_file "${1:-}"

dc stop --timeout 60 trader
if [ "${2:-}" = "--all" ]; then
  dc stop --timeout 60 db
fi
echo "Angehalten."
