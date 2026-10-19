# Base image pinned by digest (HOM-28): the multi-platform index of
# python:3.12-slim-bookworm (Python 3.12.15) resolved on 2026-10-18 with
# `docker buildx imagetools inspect python:3.12-slim-bookworm`. Re-pin on purpose,
# never by overriding PYTHON_IMAGE for a release (docs/operations/release.md).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG UV_VERSION=0.11.24
ARG INSTALL_AI_LOCAL=false

# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS dependencies
ARG UV_VERSION
ARG INSTALL_AI_LOCAL
ENV VIRTUAL_ENV=/opt/venv
ENV PATH=${VIRTUAL_ENV}/bin:${PATH}
ENV UV_PROJECT_ENVIRONMENT=${VIRTUAL_ENV}
ENV UV_LINK_MODE=copy
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
WORKDIR /workspace
COPY Homayar/pyproject.toml Homayar/uv.lock ./
# --locked refuses a uv.lock that is stale against pyproject.toml.
RUN if [ "${INSTALL_AI_LOCAL}" = "true" ]; then \
      uv sync --locked --no-dev --no-install-project --extra ai-local; \
    else \
      uv sync --locked --no-dev --no-install-project; \
    fi

# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS runtime
ENV VIRTUAL_ENV=/opt/venv
ENV PATH=${VIRTUAL_ENV}/bin:${PATH}
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates ffmpeg libgomp1 libmagic1 libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=dependencies /opt/venv /opt/venv
COPY --chown=10001:10001 Homayar/alembic.ini /app/alembic.ini
COPY --chown=10001:10001 Homayar/backend /app/backend
RUN mkdir -p /app/data/legacy-uploads /app/data/objects /app/data/quarantine /app/logs \
    && chown -R 10001:10001 /app/data /app/logs

# Release identity (HOM-28, HOM-29). For a release, take every value from
# `python3 infra/release/release_manifest.py build-args --rev <commit>`. Without them
# the image is marked unreleased: all-zero SOURCE_SHA, homayar_build_info
# source_sha="unreleased", OCI version "unreleased".
ARG PYTHON_IMAGE
ARG UV_VERSION
ARG INSTALL_AI_LOCAL
ARG SOURCE_SHA=0000000000000000000000000000000000000000
ARG SOURCE_DATE_EPOCH=0
ARG SOURCE_DATE=1970-01-01T00:00:00Z
ARG RELEASE_VERSION=unreleased
ARG RELEASE_PROFILE=
LABEL org.opencontainers.image.title="homayar-api" \
      org.opencontainers.image.source="https://github.com/Kohandezh/Homayar.com" \
      org.opencontainers.image.revision="${SOURCE_SHA}" \
      org.opencontainers.image.version="${RELEASE_VERSION}" \
      org.opencontainers.image.created="${SOURCE_DATE}" \
      org.opencontainers.image.base.name="${PYTHON_IMAGE}"
COPY Homayar/pyproject.toml Homayar/uv.lock /app/release-inputs/
# The profile follows INSTALL_AI_LOCAL; an explicit RELEASE_PROFILE must agree with it.
RUN derived_profile=standard; \
    if [ "${INSTALL_AI_LOCAL}" = "true" ]; then derived_profile=ai-local; fi; \
    profile="${RELEASE_PROFILE:-${derived_profile}}"; \
    if [ "${profile}" != "${derived_profile}" ]; then \
      echo "RELEASE_PROFILE=${profile} does not match INSTALL_AI_LOCAL=${INSTALL_AI_LOCAL}" >&2; \
      exit 2; \
    fi; \
    python -m backend.observability.release write-build-info \
      --source-sha "${SOURCE_SHA}" \
      --source-date-epoch "${SOURCE_DATE_EPOCH}" \
      --profile "${profile}" \
      --base-image "${PYTHON_IMAGE}" \
      --uv-version "${UV_VERSION}" \
      --lock /app/release-inputs/uv.lock \
      --pyproject /app/release-inputs/pyproject.toml \
      --output /app/build-info.json
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
