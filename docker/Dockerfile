# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Stage 1 — builder
#
# Uses the official ``uv`` image to materialise a frozen environment. We
# install the project + runtime deps (no dev extras) into ``/app/.venv``;
# the runtime stage copies the venv and the source tree, nothing else.
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:0.5-python3.12-bookworm AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies first (cached layer) — copy lock + project files only.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# ---------------------------------------------------------------------------
# Stage 2 — runtime
#
# Distroless: no shell, no apt. Run as nonroot. The ``domain-watcher``
# entry point is the venv's console script copied across.
# ---------------------------------------------------------------------------
FROM gcr.io/distroless/python3-debian12:nonroot AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=builder --chown=nonroot:nonroot /app/.venv /app/.venv
COPY --from=builder --chown=nonroot:nonroot /app/src /app/src

# State directory (volume in compose). nonroot owns it so the container
# can write its sqlite file without an explicit chown.
VOLUME ["/var/lib/domain-watcher"]

ENV DOMAIN_WATCHER_CONFIG=/etc/domain-watcher/config.yaml

USER nonroot:nonroot

# distroless's ``python3`` binary is the entrypoint; we hand it the
# console-script wrapper that uv installed into the venv.
ENTRYPOINT ["/app/.venv/bin/domain-watcher"]
CMD ["run"]
