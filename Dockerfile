FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Nicht als root laufen
RUN useradd --create-home --uid 10001 trader

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY db ./db

RUN pip install --no-cache-dir ".[dev,db]"

USER trader

# Standard: Demo mit synthetischen Daten (PAPER). Tests: docker-compose --env-file <Passwortdatei> run --rm app pytest
CMD ["python", "-m", "trading.demo"]
