# Zwei Ziele aus einer Datei:
#   runtime  schlankes Laufzeit-Image (ohne Tests und Entwicklerwerkzeuge) - fuer `app` und `migrate`
#   test     zusaetzlich pytest, ruff, mypy und die Tests - nur fuer den Dienst `tests`
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MIGRATIONS_DIR=/app/db/migrations

WORKDIR /app

# Nicht als root laufen
RUN useradd --create-home --uid 10001 trader

COPY pyproject.toml README.md ./
COPY src ./src
COPY db/migrations ./db/migrations

FROM base AS runtime
RUN pip install --no-cache-dir ".[db]"
USER trader
# Standard: Demo mit synthetischen Daten (PAPER, ohne Datenbank).
CMD ["python", "-m", "trading.demo"]

FROM base AS test
COPY tests ./tests
COPY scripts ./scripts
RUN pip install --no-cache-dir ".[dev,db]"
USER trader
CMD ["pytest", "-q", "-rs"]
