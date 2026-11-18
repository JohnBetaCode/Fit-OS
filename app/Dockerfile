# Fit-OS — una imagen, un proceso (FastAPI + scheduler). arm64 y amd64.
FROM python:3.15-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg: GIF→MP4 del catálogo (solo corre al importar). git: fetch del dataset. sqlite3: backups/inspección.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini ffmpeg git sqlite3 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 fitos

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml alembic.ini ./
COPY migrations ./migrations
COPY fitos ./fitos
RUN pip install -e . --no-deps

RUN mkdir -p /data && chown -R fitos:fitos /data /app
USER fitos

EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "fitos.main:app", "--host", "0.0.0.0", "--port", "8000"]
