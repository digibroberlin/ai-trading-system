# Zwei Ziele aus einer Datei:
#   runtime  schlankes Laufzeit-Image (ohne Tests und Entwicklerwerkzeuge) - fuer `app`, `trader` und `migrate`
#   ops      Laufzeit plus PostgreSQL-Client - fuer Sicherungen mit Rueckspiel-Test (docker-compose.prod.yml)
#   test     zusaetzlich pytest, ruff, mypy und die Tests - nur fuer den Dienst `tests`
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MIGRATIONS_DIR=/app/db/migrations \
    PYTHONPATH=/app/src

WORKDIR /app

# Nicht als root laufen
RUN useradd --create-home --uid 10001 trader

# Pakete nur aus Sperrdateien mit Pruefsummen (Audit M12): pip bricht ab, wenn eine Datei nicht exakt passt.
# Das Projekt selbst wird nicht per pip installiert (sonst wuerde pip ungeprueft setuptools nachladen);
# der Code liegt unter /app/src und ist ueber PYTHONPATH erreichbar.
COPY requirements ./requirements
COPY pyproject.toml README.md ./
COPY src ./src
COPY db/migrations ./db/migrations

FROM base AS runtime
RUN pip install --no-cache-dir --require-hashes -r requirements/runtime.lock
USER trader
# Standard: Demo mit synthetischen Daten (PAPER, ohne Datenbank).
CMD ["python", "-m", "trading.demo"]

FROM base AS ops
# Sicherungen im Betrieb (AP-26/27): Laufzeit-Pakete plus PostgreSQL-Client-Werkzeuge (Debian 13: Version 17).
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --require-hashes -r requirements/runtime.lock
COPY scripts/backup.py ./scripts/backup.py
USER trader
CMD ["python", "scripts/backup.py", "cycle"]

FROM base AS test
# PostgreSQL-Client-Werkzeuge fuer den Rueckspiel-Test der Sicherungen (AP-26). Debian 13 liefert Version 17 - neuere
# Werkzeuge koennen den Server 16 sichern; scripts/backup.py prueft das vor jeder Sicherung.
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
COPY tests ./tests
COPY scripts ./scripts
# Auch diese Dateien pruefen Tests (nur Installationswege, keine Geheimnisse - CI nutzt Wegwerf-Testpasswoerter).
COPY Dockerfile docker-compose.prod.yml ./
COPY .github ./.github
RUN pip install --no-cache-dir --require-hashes -r requirements/dev.lock
USER trader
CMD ["pytest", "-q", "-rs"]
