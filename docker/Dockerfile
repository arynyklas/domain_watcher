# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Stage 1 — builder
#
# Uses the official ``uv`` image to materialise a frozen environment. We
# install the project + runtime deps (no dev extras) into ``/app/.venv``;
# the runtime stage copies the venv and the source tree, nothing else.
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:0.11.13-python3.13-trixie AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies first (cached layer) — copy lock + project files only.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/

# ``uv sync`` builds the venv against the Python that lives in *this*
# image — uv's image installs Python from source under ``/usr/local``.
# Distroless ships its Debian-packaged Python under ``/usr`` instead.
# Without rewriting, two things break when the venv is copied into the
# runtime:
#   1. ``bin/python3{,.13}`` symlinks point at ``/usr/local/bin``
#      (missing in distroless) — the entrypoint exec returns ENOENT.
#   2. ``pyvenv.cfg``'s ``home = /usr/local/bin`` makes the runtime
#      Python look for the standard library under ``/usr/local/lib``
#      (missing in distroless) — first ``import`` raises ImportError.
# Both are paths uv records during sync; we rewrite them post-sync to
# the distroless-native locations. The builder doesn't need those paths
# to exist — the rewritten references resolve at runtime.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev \
 && sed -i 's|^home = .*|home = /usr/bin|' /app/.venv/pyvenv.cfg \
 && ln -sfn /usr/bin/python3.13 /app/.venv/bin/python3.13 \
 && ln -sfn python3.13           /app/.venv/bin/python3 \
 && ln -sfn python3.13           /app/.venv/bin/python


# ---------------------------------------------------------------------------
# Stage 2 — runtime
#
# Distroless: no shell, no apt. Run as nonroot. The ``domain-watcher``
# entry point is the venv's console script copied across.
#
# We pin to ``python3-debian13`` (Debian 13 / trixie, Python 3.13) so the
# venv's ``bin/python3`` symlink resolves to the system interpreter that
# distroless ships at ``/usr/bin/python3.13``. ``python3-debian12`` would
# ship Python 3.11 — incompatible with the PEP 695 generic class syntax
# we use (e.g. ``class ConfigHolder[C]:`` in ``application/use_cases``).
# The builder image above matches the runtime on glibc (trixie) and on
# Python minor (3.13); the ``RUN`` step in the builder also re-targets
# the venv's interpreter symlinks to ``/usr/bin/python3.13`` so the
# entrypoint script execs cleanly under distroless's Python. The package
# itself still supports Python >=3.12 via pip; only the docker image is
# pinned to 3.13 to track its distroless base.
# ---------------------------------------------------------------------------
FROM gcr.io/distroless/python3-debian13:nonroot AS runtime

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
