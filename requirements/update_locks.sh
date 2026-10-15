#!/usr/bin/env bash
# Erzeugt die Sperrdateien (*.lock) mit festen Versionen UND Pruefsummen aus den *.txt-Dateien neu.
#   bash requirements/update_locks.sh        (braucht `uv`, z. B. pip install uv in einer Umgebung AUSSERHALB des Projekts)
#
# Danach: python scripts/dependency_audit.py, alle Pruefungen laufen lassen, Aenderung der Versionen dokumentieren.
# Installiert wird immer mit `pip install --require-hashes -r requirements/<name>.lock`: pip bricht ab, wenn eine
# heruntergeladene Datei nicht exakt die eingetragene Pruefsumme hat (Schutz gegen ausgetauschte Pakete, Audit M12).
set -euo pipefail
cd "$(dirname "$0")"
compile() {  # $1 = Name, $2 = Python-Version
  uv pip compile --quiet --generate-hashes --universal --no-header --python-version "$2" \
    "$1.txt" -o "$1.lock"
}
compile dev 3.12
compile runtime 3.12
compile audit 3.12
compile py39 3.9
echo "Sperrdateien aktualisiert."
