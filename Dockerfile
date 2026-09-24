FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DECISION_BENCH_ROOT=/app \
    HOST=0.0.0.0 \
    PORT=8000 \
    DATABASE_PATH=/data/decision_bench.sqlite

COPY pyproject.toml README.md ./
COPY src ./src
COPY web ./web
COPY packs ./packs
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["decision-bench"]
