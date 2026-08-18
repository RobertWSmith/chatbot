FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_APP=run.py

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install .

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "flask --app run.py db upgrade && gunicorn --bind 0.0.0.0:8000 --workers ${WEB_CONCURRENCY:-2} --threads ${WEB_THREADS:-4} --timeout ${WEB_TIMEOUT:-120} run:app"]
