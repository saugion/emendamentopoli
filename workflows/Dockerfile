# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /uvx /bin/
WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_PYTHON_PREFERENCE=only-system \
    UV_NO_PYTHON_DOWNLOADS=1 \
    UV_CACHE_DIR=/tmp/uv-cache \
    XDG_CACHE_HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}" \
    HEALTH_SERVER_HOST=127.0.0.1 \
    HEALTH_SERVER_PORT=3001
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev
RUN groupadd -g 1000 app && useradd -u 1000 -g 1000 -m -s /usr/sbin/nologin app
USER 1000:1000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=20s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3001/health', timeout=5).status==200 else 1)"
CMD ["python", "-m", "emendamenti_worker.entrypoint"]
