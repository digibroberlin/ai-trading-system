#!/usr/bin/env bash
# Zustand der Dienste und Gesundheit des Paper-Dienstes (/api/health). Rueckgabe 0 = gesund, 1 = nicht gesund.
# Aufruf: scripts/ops/status.sh /pfad/ausserhalb/trading-secrets.env
set -euo pipefail
source "$(dirname "$0")/common.sh"
load_env_file "${1:-}"

dc ps
if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "+ GET http://127.0.0.1:8000/api/health"
  exit 0
fi
if curl -fsS --max-time 5 http://127.0.0.1:8000/api/health; then
  echo
  exit 0
fi
echo
echo "Paper-Dienst nicht gesund oder nicht gestartet (Logs: docker compose -f docker-compose.prod.yml logs trader)."
exit 1
