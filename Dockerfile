# svccat-ui — uv-managed image (Django + htmx + Alpine.js + DaisyUI; no node toolchain, no build step). Runs gunicorn on :8080 (service_manager.wsgi) behind an ingress that terminates TLS.
FROM docker.io/library/python:3.12-slim
# uv: static binary from the distroless image; keep >= the uv that generated uv.lock (revision 3).
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /usr/local/bin/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
# COPY flattens directory sources (contents land in dest, the dir name is not
# kept) — each package needs its own dest path or imports break at runtime
COPY manage.py ./
COPY service_manager ./service_manager
COPY core ./core
# Vendored assets (htmx.min.js, alpine.min.js, daisyui.css[.gz]) are not in the repo; bake them in at build time via a BuildKit bind (docker build --build-context assets=./assets .):
# COPY --from=assets htmx.min.js alpine.min.js daisyui.css daisyui.css.gz core/public/
EXPOSE 8080
CMD ["sh", "-c", "gunicorn service_manager.wsgi:application --bind 0.0.0.0:${PORT} --workers 2 --access-logfile -"]
