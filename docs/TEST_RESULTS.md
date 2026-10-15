# Testergebnisse – fortlaufendes Protokoll

Jeder Eintrag stammt aus einem tatsächlich ausgeführten Lauf. `scripts/preflight.py --record` hängt Einträge automatisch an.
Ausführliche Berichte: [TESTBERICHT.md](TESTBERICHT.md) (Stufe 1), [TESTBERICHT_PHASE1-3.md](TESTBERICHT_PHASE1-3.md).

STABIL heißt: Geheimnis-Scan (inkl. Geschichte), ruff, mypy --strict und alle Tests ohne übersprungene bestanden.

## Stabile Stände

| Tag | Datum | Ergebnis |
|---|---|---|
| `stable/001-phase1-3-foundation` | geprüft 2026-10-15 | 354/354 Tests (Python 3.12.14, PostgreSQL 16.15), in separater Arbeitskopie erneut ausgeführt |
| `stable/002-stage1-core-backtesting` | geprüft 2026-10-14 | 606/606 Tests (Mac 3.12.14 und Container 3.12.15, PostgreSQL 16.15), Gegenprobe 193/193, mypy/ruff ohne Befund |
| `stable/003-autonomy-baseline` | geprüft 2026-10-15 | Preflight STABIL: 633/633 Tests (Mac 3.12.14; Container 3.12.15 ebenfalls 633/633), Gegenprobe 193/193, Geheimnis-Scan inkl. Geschichte sauber |

## Läufe

### 2026-10-15 18:46 UTC - STABIL
Git: `62b511c` auf `chore/autonomy-baseline` · Python 3.12.14 · Darwin

- ✅ Geheimnis-Scan (inkl. Geschichte): GEHEIMNIS-SCAN: keine Funde (Arbeitsstand und gesamte Geschichte).
- ✅ ruff: All checks passed!
- ✅ mypy --strict: Success: no issues found in 55 source files
- ✅ Tests: 633 bestanden, 0 uebersprungen, 0 fehlgeschlagen (106 s)
- ✅ Gegenprobe: ERGEBNIS: 193 entdeckt, 0 nicht entdeckt, 0 nicht geprueft (637 s)
