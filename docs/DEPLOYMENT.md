# Betrieb auf einem eigenen Server (AP-28, D-030)

Diese Anleitung ist **vorbereitet, nicht ausgeführt**. Sie wird erst gebraucht, wenn Sie den Paper-Betrieb (kein echtes
Geld) auf einem Server laufen lassen möchten (Blocker B-005). Alle Skripte zeigen ohne `--apply` nur an, was sie tun
würden (Trockenlauf). Der Handel startet auch auf dem Server **nur** mit Ihrer ausdrücklichen Freigabe (Schritt 6).

## Was Sie brauchen

* Ein Konto bei einem Server-Anbieter, z. B. **Hetzner Cloud** (hetzner.com). Für den Paper-Betrieb reicht ein kleiner
  Server (z. B. 2 vCPU, 4 GB RAM, Ubuntu 24.04) – etwa 4–6 € im Monat (Stand der Preisliste vor dem Kauf prüfen).
* Einen SSH-Schlüssel auf Ihrem Mac (`ls ~/.ssh/id_ed25519.pub`; sonst `ssh-keygen -t ed25519`).
* Optional einen Alarm-Kanal (z. B. die kostenlose App **ntfy**, Slack oder eine Telegram-Brücke) – das entscheiden Sie.

## Schritte

1. **Server anlegen** (Hetzner-Konsole → „Server hinzufügen“): Ubuntu 24.04, Ihren SSH-Schlüssel auswählen, **keine**
   Passwort-Anmeldung. Die IP-Adresse notieren.
2. **Code holen** (auf dem Server, als root): `git clone https://github.com/digibroberlin/ai-trading-system.git /root/ats`
   (das Repository ist privat: dafür einen schreibgeschützten Deploy Key in GitHub anlegen – Repository → Settings →
   Deploy keys).
3. **Härten**: `cd /root/ats && deploy/server/harden.sh` (Trockenlauf lesen), dann `deploy/server/harden.sh --check` und
   `deploy/server/harden.sh --apply`. Danach meldet man sich nur noch als `trading` mit SSH-Schlüssel an
   (`ssh trading@<IP>`); root und Passwörter sind gesperrt. Firewall: nur SSH herein.
4. **Passwort-Datei** anlegen: `/etc/ai-trading/secrets.env` mit (eigene, lange Zufallswerte):
   ```
   POSTGRES_PASSWORD=...
   APP_DB_PASSWORD=...
   COST_SERVER_MONTHLY_USD=6.00        # Serverkosten in USD – fließen ins Netto nach allen Kosten
   ALERT_WEBHOOK_URL=https://...       # optional: Ihr Alarm-Kanal
   ```
   Rechte: `chown trading:trading /etc/ai-trading/secrets.env && chmod 600 /etc/ai-trading/secrets.env`.
   **Nie** Börsen-Schlüssel eintragen – der Dienst startet dann gar nicht (PAPER-Schutz).
5. **Installieren**: `REF=main deploy/server/install.sh` (Trockenlauf), dann `--check`, dann
   `sudo REF=main deploy/server/install.sh --apply`. Das richtet ein: Start beim Hochfahren, tägliche Sicherung mit
   Rückspiel-Test (03:17 UTC), Alarm-Prüfung alle 5 Minuten. Ohne Freigabe läuft danach nur die Datenbank – es wird
   **nicht** gehandelt.
6. **Paper-Handel freigeben** (nur, wenn Sie es wollen): in der Passwort-Datei
   `TRADING_ENGINE_ENABLED=1` und `TRADING_ENGINE_APPROVAL=PAPER-HANDEL FREIGEGEBEN` ergänzen, dann
   `sudo systemctl restart ai-trading`. Zurücknehmen: die beiden Zeilen löschen und neu starten.
7. **Dashboard ansehen** (vom Mac): `ssh -L 8000:127.0.0.1:8000 trading@<IP>`, dann im Browser http://127.0.0.1:8000.

## Was danach automatisch passiert

| Was | Wann | Wo nachsehen |
|---|---|---|
| Sicherung + Rückspiel-Test, alte Sicherungen aufräumen (14 behalten) | täglich 03:17 UTC | `journalctl -u ai-trading-backup` |
| Gesundheitsprüfung, Alarm bei Zustandswechsel (inkl. KI-Budget-Sperre, Not-Aus, Datenbank, Marktdaten) | alle 5 min | `journalctl -u ai-trading-alert` |
| Neustart eines hängenden Dienstes (Wächter) | nach 15 min Stillstand | `docker compose -f docker-compose.prod.yml logs trader` |
| Sicherheitsupdates des Systems | täglich | `/var/log/unattended-upgrades/` |

Ein **Not-Aus** führt bewusst nicht zum Neustart; er bleibt, bis Sie ihn nach Prüfung freigeben (README „Globaler
Not-Aus“). Sicherungen liegen auf dem Server unter `/opt/ai-trading-system/backups` – eine Kopie außerhalb des Servers
(z. B. Hetzner Storage Box) ist empfehlenswert und kostet extra; das entscheiden Sie.

## Grenzen (ehrlich)

* Die Skripte sind nur im Trockenlauf geprüft, nicht auf einem echten Server.
* Live-Marktdaten wurden aus der Entwicklungsumgebung noch nie abgerufen (B-008) – der erste echte Lauf ist zugleich
  die erste Probe der Börsen-Anbindung. Deshalb zuerst einige Tage ohne Freigabe nur beobachten
  (`python -m trading.marketdata.watch`, siehe README).
