# Stufe 1 – Zwischenstand (Arbeit unterbrochen, NICHT fertig)

Stand: 2026-10-13. Die Umsetzung wurde mitten im Umbau unterbrochen (Nutzungslimit).
**Der Code ist in diesem Zustand nicht lauffähig; die Tests wurden seit Beginn des Umbaus nicht ausgeführt.**

## Rückweg zum letzten funktionierenden Stand (354 Tests grün)

- Projekt: `backups/projekt_2026-10-13_vor-stufe1.tar.gz` (kompletter Stand vor Stufe 1)
- Datenbank: `backups/datenbank_trading_2026-10-13_vor-stufe1.sql` (Dump ohne Passwörter)
- Die laufende Datenbank `trading` wurde **nicht** verändert (kein Schema-Umbau, keine Migration ausgeführt).

## Bereits neu geschrieben (ungetestet)

- `docs/AUDIT.md` – vollständiger Audit-Bericht
- `src/trading/domain/instrument.py` – Instrument-Schlüssel (Börse, Markttyp, Symbol) [H1]
- `src/trading/domain/models.py`, `domain/universe.py`, `domain/reasons.py` – auf Instrument-Schlüssel umgestellt; `FundingEvent`, `Mark`, `CloseResult`, neue `PortfolioState`-Felder
- `src/trading/safety/limits.py` – 100 % Brutto-Exposure, 10 %-Schock / 5 % Stress-Verlust, Bewertungskurs-Alter [H5]
- `src/trading/execution/costs.py` – Rundung zu Ungunsten des Händlers, `FillQuote`, Ausstieg per Market-Order
- `src/trading/safety/order_risk.py` – die EINE Risikorechnung (Zustand nach der Order, alle Grenzen gleichzeitig) [M4, H5]
- `src/trading/safety/risk_manager.py` – dünne Hülle um `order_risk`
- `src/trading/marketdata/candles.py` – Prüfung einzelner Kerzen und ganzer Reihen [H3]
- `src/trading/safety/data_validator.py` – je Instrument, Referenzkurs verfällt
- `src/trading/execution/paper_executor.py` – Kerzenprüfung/Reihenfolge, Funding als Ereignis, `close_position`, volle Margin bei Liquidation, atomare Operationen [H3, M10, M11]
- `src/trading/audit/events.py` – neue Ereignistypen

## Noch offen (alte Schnittstelle, passt nicht mehr zum neuen Kern)

- `agents/sora_rules.py`, `agents/rin_rules.py`, `agents/momo.py`, `agents/contracts.py`, `agents/aki.py`, `pipeline.py`
- `marketdata/synthetic.py`, `demo.py`, `execution/protocol.py`
- `persistence/pg_executor.py`, Schema-Migration (`db/migrations/…`), Migrations-Läufer, `docker-compose.yml`/`Dockerfile`
- Performance-Modul mit Kapitalkurve [M13], Backtesting-Engine (noch nicht begonnen)
- Sämtliche Tests (`tests/…`) und `scripts/restart_check.py` – noch auf dem alten Stand
- Verifikation (Unit, PostgreSQL, mypy, ruff, Mutationstests, Docker), Dokumentation, Abschlussbericht
