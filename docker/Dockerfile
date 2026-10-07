FROM python:3.12-slim

LABEL maintainer="snodo" \
      description="Snodo protocol engine"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /workspace

ENV UV_TOOL_BIN_DIR=/usr/local/bin

RUN uv tool install snodo

ENTRYPOINT ["snodo"]
