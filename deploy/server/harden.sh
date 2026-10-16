#!/usr/bin/env bash
# Server-Haertung fuer den Paper-Betrieb (AP-28, D-030) - Ubuntu 24.04 (z. B. Hetzner Cloud).
#
#   deploy/server/harden.sh            # TROCKENLAUF (Standard): zeigt nur, was geschehen wuerde
#   sudo deploy/server/harden.sh --check   # nur die Vorpruefungen (veraendert nie etwas)
#   sudo deploy/server/harden.sh --apply
#
# Was es tut: Sicherheitsupdates automatisch, Firewall (nur SSH herein, mit Begrenzung), SSH nur mit Schluessel und ohne
# root, fail2ban, Zeit in UTC, begrenzte Protokolle, Docker aus den Ubuntu-Paketen, Betriebsbenutzer `trading`.
# Es oeffnet KEINEN Port fuer Datenbank oder Dashboard (Dashboard nur per SSH-Tunnel). Hinweis: Docker umgeht die
# Firewall fuer veroeffentlichte Ports - deshalb veroeffentlicht docker-compose.prod.yml nur 127.0.0.1.
# Schutz vor Aussperren: Ohne hinterlegten SSH-Schluessel fuer `trading` oder root bricht --apply ab, BEVOR SSH
# veraendert wird.
set -euo pipefail

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1
OPS_USER="${OPS_USER:-trading}"
KEY_FILES="${HARDEN_KEY_FILES:-/root/.ssh/authorized_keys /home/$OPS_USER/.ssh/authorized_keys}"

run() {
  if [ "$APPLY" = "1" ]; then "$@"; else printf '+ %s\n' "$*"; fi
}

write_file() {  # write_file PFAD MODUS  (Inhalt von stdin)
  local path="$1" mode="$2"
  if [ "$APPLY" = "1" ]; then
    install -D -m "$mode" /dev/stdin "$path"
  else
    printf '+ schreibe %s (%s):\n' "$path" "$mode"
    sed 's/^/    /'
  fi
}

preflight() {
  local keys=0 f
  for f in $KEY_FILES; do [ -s "$f" ] && keys=1; done
  [ "$keys" = "1" ] || { echo "FEHLER: kein SSH-Schluessel hinterlegt - Abbruch, sonst sperrt man sich aus." >&2; exit 1; }
  [ "$(id -u)" = "0" ] || { echo "FEHLER: --apply nur als root (sudo)." >&2; exit 1; }
  # shellcheck disable=SC1091
  . /etc/os-release
  [ "${ID:-}" = "ubuntu" ] && [ "${VERSION_ID:-}" = "24.04" ] || { echo "FEHLER: nur fuer Ubuntu 24.04." >&2; exit 1; }
}

if [ "$CHECK" = "1" ]; then preflight; echo "Vorpruefung bestanden."; exit 0; fi
if [ "$APPLY" = "1" ]; then preflight; else echo "TROCKENLAUF - es wird nichts veraendert (wirklich: --apply als root)."; fi

echo "== 1. Pakete und automatische Sicherheitsupdates"
run apt-get update
run apt-get -y upgrade
run apt-get -y install ufw fail2ban unattended-upgrades docker.io docker-compose-v2 git curl python3
write_file /etc/apt/apt.conf.d/20auto-upgrades 0644 <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF

echo "== 2. Betriebsbenutzer $OPS_USER (ohne Passwort, nur SSH-Schluessel)"
if [ "$APPLY" = "1" ] && id "$OPS_USER" >/dev/null 2>&1; then
  echo "   $OPS_USER besteht schon"
else
  run adduser --disabled-password --gecos "" "$OPS_USER"
fi
run usermod -aG docker "$OPS_USER"
if [ "$APPLY" = "1" ] && [ ! -s "/home/$OPS_USER/.ssh/authorized_keys" ]; then
  install -d -m 0700 -o "$OPS_USER" -g "$OPS_USER" "/home/$OPS_USER/.ssh"
  install -m 0600 -o "$OPS_USER" -g "$OPS_USER" /root/.ssh/authorized_keys "/home/$OPS_USER/.ssh/authorized_keys"
else
  printf '+ SSH-Schluessel von root fuer %s uebernehmen, falls noch keiner hinterlegt ist\n' "$OPS_USER"
fi
run install -d -m 0700 -o "$OPS_USER" -g "$OPS_USER" /etc/ai-trading /var/lib/ai-trading
run install -d -m 0755 -o "$OPS_USER" -g "$OPS_USER" /opt/ai-trading-system

echo "== 3. SSH: nur Schluessel, kein root, nur $OPS_USER"
write_file /etc/ssh/sshd_config.d/90-ai-trading.conf 0644 <<CONF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
X11Forwarding no
MaxAuthTries 3
AllowUsers $OPS_USER
CONF
run sshd -t
run systemctl reload ssh

echo "== 4. Firewall: nur SSH herein (begrenzt), alles andere zu"
run ufw default deny incoming
run ufw default allow outgoing
run ufw limit OpenSSH
run ufw --force enable

echo "== 5. fail2ban fuer SSH"
write_file /etc/fail2ban/jail.d/sshd.local 0644 <<'CONF'
[sshd]
enabled = true
maxretry = 5
bantime = 1h
CONF
run systemctl enable --now fail2ban

echo "== 6. Zeit (UTC, synchronisiert) und begrenzte Protokolle"
run timedatectl set-timezone UTC
run systemctl enable --now systemd-timesyncd
write_file /etc/systemd/journald.conf.d/90-ai-trading.conf 0644 <<'CONF'
[Journal]
SystemMaxUse=500M
CONF
write_file /etc/docker/daemon.json 0644 <<'CONF'
{"log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "5"}, "live-restore": true, "no-new-privileges": true}
CONF
run systemctl restart docker

echo "Fertig. Naechster Schritt: deploy/server/install.sh (siehe docs/DEPLOYMENT.md)."
