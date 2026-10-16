#!/usr/bin/env bash
# Sicherung mit Rueckspiel-Test und Aufraeumen (fuer den taeglichen Zeitplan, AP-28). Rueckgabe != 0 = Alarm.
# Aufruf: scripts/ops/backup.sh /pfad/ausserhalb/trading-secrets.env
set -euo pipefail
source "$(dirname "$0")/common.sh"
load_env_file "${1:-}"

dc run --rm backup
