ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS dependencies
ARG UV_VERSION=0.11.24
ARG INSTALL_AI_LOCAL=false
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
RUN if [ "${INSTALL_AI_LOCAL}" = "true" ]; then \
      uv sync --frozen --no-dev --no-install-project --extra ai-local; \
    else \
      uv sync --frozen --no-dev --no-install-project; \
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
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
