# TCG Necropolis container image.
#
# The image holds only the code. Card images, decks, profiles and caches live
# in the /data volume (TCGNECRO_DATA), so the container can be replaced at
# any time without losing anything. See docs/DOCKER.md.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TCGNECRO_DATA=/data

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml README.md LICENSE ./
COPY tcgnecro/__init__.py tcgnecro/__init__.py
RUN pip install . && pip uninstall -y tcgnecro

COPY tcgnecro ./tcgnecro
RUN pip install --no-deps . && rm -rf /app/tcgnecro /app/build

COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Run as an unprivileged user. Compose can override the uid/gid to match the
# owner of the data folder on the host (see compose.yaml).
RUN useradd --uid 1000 --user-group --home-dir /app --no-create-home tcgnecro \
    && mkdir -p /data && chown tcgnecro:tcgnecro /data
USER tcgnecro

VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["tcgnecro", "serve", "--host", "0.0.0.0", "--port", "8000"]
