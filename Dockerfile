# syntax=docker/dockerfile:1
# Build: docker build --build-arg UV_VERSION=$(uv --version | cut -d' ' -f2) -t incident-investigator .
ARG UV_VERSION=latest
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:3.14-slim
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev && useradd --create-home app && chown -R app /app
ENV PATH="/app/.venv/bin:$PATH"
USER app
ENTRYPOINT ["investigator"]
CMD ["--help"]
