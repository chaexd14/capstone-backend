
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Required for common Python dependencies and PostgreSQL client libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gcc \
       libpq-dev \
       clamav \
       clamav-freshclam \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock .
RUN python -m pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .

RUN useradd --system --create-home talentmatch \
    && mkdir -p /app/staticfiles /var/lib/clamav \
    && chown talentmatch:talentmatch /app/staticfiles /var/lib/clamav

USER talentmatch

EXPOSE 8000

CMD ["bash", "start-production.sh", "web"]
