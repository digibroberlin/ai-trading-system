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

### 2026-10-15 19:51 UTC - STABIL (Cloud-Routine, AP-03)
Git: `ae639e5` (develop vor AP-05) + Arbeitskopie · Python 3.12.3 · Linux (Cloud) · PostgreSQL 16 über `scripts/cloud_test_db.sh`

- ✅ Geheimnis-Scan (inkl. Geschichte), ruff, mypy --strict (55 Dateien): ohne Befund
- ✅ Tests: 633 bestanden, 0 übersprungen, 0 fehlgeschlagen (147 s)
- ✅ Gegenprobe: 193 entdeckt, 0 nicht entdeckt, 0 nicht geprüft (ca. 41 min)
- ✅ Kern unter Python 3.9.25 ohne psycopg: 539 bestanden, 5 Datenbank-Module sichtbar übersprungen (vorher 5 Importfehler)

### 2026-10-16 03:33 UTC - STABIL
Git: `58edf21` auf `ap/08-order-wiederholung` + Arbeitskopie (Test für O02) · Python 3.12.3 · Linux (Cloud-Routine, AP-08) · PostgreSQL 16

- ✅ Geheimnis-Scan (inkl. Geschichte): GEHEIMNIS-SCAN: keine Funde (Arbeitsstand und gesamte Geschichte).
- ✅ ruff: All checks passed!
- ✅ mypy --strict: Success: no issues found in 57 source files
- ✅ Tests: 806 bestanden, 0 uebersprungen, 0 fehlgeschlagen (163 s)
- ✅ Gegenprobe (nur AP-08-Einträge P18, O01–O07, G06, G28, G48–G52, mit Datenbank): 15 von 15 entdeckt
  (O02 erst nach einem zusätzlichen Test; vorher 14/15)
- ✅ Kern unter Python 3.9.25 ohne psycopg: 683 bestanden, 6 Datenbank-Module sichtbar übersprungen

### 2026-10-16 04:20 UTC - STABIL
Git: `26edcdc` auf `ap/09-resize-under-lock` · Python 3.12.3 · Linux (Cloud-Routine, AP-09) · PostgreSQL 16

- ✅ Geheimnis-Scan (inkl. Geschichte): GEHEIMNIS-SCAN: keine Funde (Arbeitsstand und gesamte Geschichte).
- ✅ ruff: All checks passed!
- ✅ mypy --strict: Success: no issues found in 57 source files
- ✅ Tests: 821 bestanden, 0 uebersprungen, 0 fehlgeschlagen (207 s)
- ✅ Gegenprobe (nur AP-09-Einträge Q01–Q03, I04, I06, G06, G53, mit Datenbank): 7 von 7 entdeckt
- ✅ Kern unter Python 3.9.25 ohne psycopg: 691 bestanden, 6 Datenbank-Module sichtbar übersprungen

### 2026-10-16 12:37 UTC - STABIL
Git: Arbeitszweig `ap/12-pipeline-reihenfolge` · Python 3.12.3 · Linux (Cloud-Routine, AP-12) · PostgreSQL 16

- ✅ Geheimnis-Scan (inkl. Geschichte): keine Funde – gemessen **ohne** den fremden Zweig `ap/31-kostenoptimierung`
  (dessen Token-Zählvariablen der Scanner fälschlich meldet, siehe BLOCKERS B-006); `develop` und dieser Zweig sind sauber.
- ✅ ruff: All checks passed!
- ✅ mypy --strict: Success: no issues found in 58 source files
- ✅ Tests: 886 bestanden, 0 uebersprungen, 0 fehlgeschlagen (218 s)
- ✅ Gegenprobe (nur Pipeline-Einträge I01–I03, I06, I09, O07, Q01, Q03, W01–W06, mit Datenbank): 14 von 14 entdeckt
  (Q01 erst nach einem zusätzlichen, deterministischen Test; vorher 13/14)
- ✅ Kern unter Python 3.9 ohne psycopg: 703 bestanden, 9 Datenbank-Module sichtbar übersprungen
