# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# dependencies first, so code changes reuse the cached layer
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

FROM python:3.12-slim-bookworm
# uid 1000 matches the host user, so a bind-mounted data directory stays writable on both sides
RUN useradd --create-home --uid 1000 bdf
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH BDF_DATA_DIR=/data PYTHONUNBUFFERED=1
USER bdf
WORKDIR /data
VOLUME /data
ENTRYPOINT ["bdf"]
CMD ["--help"]
