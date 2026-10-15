# Zwei Ziele aus einer Datei:
#   runtime  schlankes Laufzeit-Image (ohne Tests und Entwicklerwerkzeuge) - fuer `app` und `migrate`
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

FROM base AS test
COPY tests ./tests
COPY scripts ./scripts
RUN pip install --no-cache-dir --require-hashes -r requirements/dev.lock
USER trader
CMD ["pytest", "-q", "-rs"]
