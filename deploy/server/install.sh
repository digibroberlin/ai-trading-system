#!/usr/bin/env bash
# Anwendung auf dem Server installieren oder aktualisieren (AP-28, D-030).
#
#   deploy/server/install.sh                      # TROCKENLAUF (Standard)
#   sudo deploy/server/install.sh --check         # nur die Vorpruefungen (veraendert nie etwas)
#   sudo REF=stable/005-... deploy/server/install.sh --apply
#
# Schritte: Code in /opt/ai-trading-system auf einen festen Stand bringen (Standard: Zweig main = nur stabile Staende),
# Passwort-Datei pruefen (/etc/ai-trading/secrets.env, Rechte 600, gehoert `trading`), systemd-Einheiten einrichten:
#   ai-trading.service        Start/Stopp des Betriebs (scripts/ops/start.sh / stop.sh) - auch nach einem Neustart
#   ai-trading-backup.timer   taeglich 03:17 UTC Sicherung mit Rueckspiel-Test (bei Fehler: Alarm)
#   ai-trading-alert.timer    alle 5 Minuten Gesundheit pruefen und Zustandswechsel melden
# Der Handel startet trotzdem NUR mit der Freigabe in der Passwort-Datei (TRADING_ENGINE_ENABLED=1 + Freigabetext).
set -euo pipefail

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1
OPS_USER="${OPS_USER:-trading}"
REF="${REF:-main}"
REPO="${REPO:-git@github.com:digibroberlin/ai-trading-system.git}"
APP_DIR="/opt/ai-trading-system"
ENV_FILE="${INSTALL_ENV_FILE:-/etc/ai-trading/secrets.env}"
UNITS="ai-trading.service ai-trading-backup.service ai-trading-backup.timer ai-trading-alert.service ai-trading-alert.timer ai-trading-alert-failure.service"

run() {
  if [ "$APPLY" = "1" ]; then "$@"; else printf '+ %s\n' "$*"; fi
}
as_user() { run sudo -u "$OPS_USER" "$@"; }

preflight() {
  [ -f "$ENV_FILE" ] || { echo "FEHLER: $ENV_FILE fehlt (siehe docs/DEPLOYMENT.md, Schritt 4)." >&2; exit 1; }
  [ "$(stat -c '%a %U' "$ENV_FILE")" = "600 $OPS_USER" ] || {
    echo "FEHLER: $ENV_FILE muss Rechte 600 haben und $OPS_USER gehoeren." >&2; exit 1; }
  [ "$(id -u)" = "0" ] || { echo "FEHLER: --apply nur als root (sudo)." >&2; exit 1; }
}

if [ "$CHECK" = "1" ]; then preflight; echo "Vorpruefung bestanden."; exit 0; fi
if [ "$APPLY" = "1" ]; then preflight; else echo "TROCKENLAUF - es wird nichts veraendert (wirklich: --apply als root)."; fi

echo "== 1. Code auf Stand $REF"
if [ "$APPLY" = "1" ] && [ -d "$APP_DIR/.git" ]; then
  as_user git -C "$APP_DIR" fetch --tags origin
else
  as_user git clone "$REPO" "$APP_DIR"
fi
as_user git -C "$APP_DIR" checkout --detach "$REF"

echo "== 2. systemd-Einheiten"
for unit in $UNITS; do
  run install -m 0644 "$APP_DIR/deploy/systemd/$unit" "/etc/systemd/system/$unit"
done
run systemctl daemon-reload
run systemctl enable --now ai-trading-backup.timer ai-trading-alert.timer
run systemctl enable ai-trading.service
run systemctl restart ai-trading.service

echo "Fertig. Zustand: sudo -u $OPS_USER $APP_DIR/scripts/ops/status.sh $ENV_FILE"
