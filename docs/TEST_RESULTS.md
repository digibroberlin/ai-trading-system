# Testergebnisse – fortlaufendes Protokoll

Jeder Eintrag stammt aus einem tatsächlich ausgeführten Lauf. `scripts/preflight.py --record` hängt Einträge automatisch an.
Ausführliche Berichte: [TESTBERICHT.md](TESTBERICHT.md) (Stufe 1), [TESTBERICHT_PHASE1-3.md](TESTBERICHT_PHASE1-3.md).

STABIL heißt: Geheimnis-Scan (inkl. Geschichte), ruff, mypy --strict und alle Tests ohne übersprungene bestanden.

## Stabile Stände

| Tag | Datum | Ergebnis |
|---|---|---|
| `stable/001-phase1-3-foundation` | geprüft 2026-10-15 | 354/354 Tests (Python 3.12.14, PostgreSQL 16.15), in separater Arbeitskopie erneut ausgeführt |
| `stable/002-stage1-core-backtesting` | geprüft 2026-10-14 | 606/606 Tests (Mac 3.12.14 und Container 3.12.15, PostgreSQL 16.15), Gegenprobe 193/193, mypy/ruff ohne Befund |

## Läufe
