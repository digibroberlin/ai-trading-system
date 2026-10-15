# Entscheidungen

Jede Entscheidung mit Datum, Begründung und Folgen. Neue Einträge unten anfügen; alte nicht löschen, sondern als
„ersetzt durch D-xxx“ markieren.

## D-001 · 2026-10-15 · Branch-Modell und stabile Stände
`main` = nur stabile Stände (je ein annotierter Tag `stable/NNN-name`); `develop` = geprüfte Zwischenstände;
`ap/<nr>-<name>` = Arbeitszweige je Arbeitspaket. Stabil heißt: `scripts/preflight.py` meldet STABIL (Geheimnis-Scan inkl.
Geschichte, ruff, mypy --strict, alle Tests **ohne** übersprungene). Für Meilensteine zusätzlich die Gegenprobe.
**Warum:** jederzeit zu einem funktionierenden Stand zurückkehren können; `main` nie durch Unfertiges überschreiben.
**Hinweis:** Auf einem kostenlosen GitHub-Konto lässt sich `main` bei privaten Repositories nicht technisch sperren
(Branch-Schutz ist dort kostenpflichtig). Der Schutz beruht deshalb auf diesem Ablauf und auf GitHub Actions.

## D-002 · 2026-10-15 · Datenbank-Abzüge aus der Git-Geschichte entfernt (vor der ersten Veröffentlichung)
Zwei Abzüge der lokalen Paper-Datenbank (`backups/*.sql`) waren eingecheckt. Sie enthielten keine Passwörter (geprüft), aber
Datenbank-Abzüge gehören grundsätzlich nicht in ein Repository. Da noch nichts hochgeladen war, wurde die lokale Geschichte
bereinigt (`git filter-branch`). Vorher wurde die vollständige alte Geschichte als `backups/git-vor-bereinigung_2026-10-15.bundle`
gesichert. Folge: Die Commit-Kennungen ab Stufe 1 haben sich geändert; die Tags zeigen auf die bereinigten Stände.
Inhaltlich (Code, Tests, Doku) ist nichts verändert.

## D-003 · 2026-10-15 · Feste Werkzeug-Versionen
`requirements-dev.txt` legt pytest, ruff, mypy und psycopg fest; lokal, im Container und in GitHub Actions gleich.
Prüfsummen und Sicherheitsprüfung der Abhängigkeiten folgen in AP-05.

## D-004 · 2026-10-15 · Empfohlene GitHub-Anbindung: Schlüssel nur für dieses eine Repository
Für das Hochladen vom Mac wird ein eigener SSH-Schlüssel erzeugt, der auf GitHub als „Deploy Key“ mit Schreibrecht **nur für
`ai-trading-system`** hinterlegt wird. Er kann keine anderen Repositories und kein Konto verwalten. Alternative mit mehr
Rechten wäre das GitHub-Programm `gh` mit Kontoanmeldung. Der Schlüssel liegt in `~/.ssh` (außerhalb des Projekts), nie im Repository.

## D-005 · 2026-10-15 · Vorläufig: eigener Kern bleibt, fremde Bausteine nur an den Rändern (wird in AP-04 geprüft)
Der eigene Kern (Risikorechnung, Executor, Datenbank-Gurte, Protokoll) ist auf die Sicherheitsregeln dieses Projekts
zugeschnitten und durch 606 Tests und 193 Gegenproben abgesichert. Ein Wechsel auf ein großes Framework würde diese Absicherung
neu verlangen. Vorläufige Linie: Börsenzugriff über eine bewährte Bibliothek (Kandidat CCXT), Ideen aus Freqtrade/Nautilus/
Hummingbot übernehmen, aber keinen fremden Code unkontrolliert kopieren. Endgültige Entscheidung mit Lizenzprüfung in AP-04.
