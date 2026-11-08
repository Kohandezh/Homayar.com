# Base image pinned by digest (HOM-28, HOM-349): the multi-platform index of
# python:3.12-slim-trixie (Python 3.12.16, Debian 13 trixie) resolved on 2026-10-30.
# Its linux/amd64 and linux/arm64 configs both have PYTHON_VERSION=3.12.16. Re-pin with
# `docker buildx imagetools inspect python:3.12-slim-trixie` (on 2026-10-30 Docker was
# down, so the digest was read from the Docker registry API). Re-pin on purpose, never
# by overriding PYTHON_IMAGE for a release (docs/operations/release.md).
ARG PYTHON_IMAGE=python:3.12-slim-trixie@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
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
# HOM-364: the release build runs on a server whose link to PyPI is slow (about
# 300 KB/s). uv's default HTTP read timeout (30 s) made `uv sync` fail there.
ENV UV_HTTP_TIMEOUT=300
ENV UV_HTTP_RETRIES=10
# HOM-377, CVE-2026-109518: the pinned base ships apt and libapt-pkg7.0 3.0.3,
# and the newest base of 2026-11-05 still had 3.0.3, so the fix (3.0.4) comes
# from trixie-security, not from a new base digest. The update and the upgrade
# below are the one step that still runs apt 3.0.3 to fetch 3.0.4, so they set
# Acquire::Retries=0: the advisory needs retries above 0 (the Debian default).
# Every later apt-get in this stage runs with 3.0.4. The 3.0.4 floor check runs
# right after the upgrade and before the installs, so they never run with apt 3.0.3.
# hadolint ignore=DL3008
RUN apt-get -o Acquire::Retries=0 update \
    && apt-get -o Acquire::Retries=0 install --yes --no-install-recommends --only-upgrade apt libapt-pkg7.0 \
    && arch="$(dpkg --print-architecture)" \
    && for floor in \
         "apt 3.0.4" \
         "libapt-pkg7.0 3.0.4"; do \
         package="${floor%% *}"; minimum="${floor#* }"; \
         state="$(dpkg-query --show --showformat="\${db:Status-Status} \${Version}" "${package}:${arch}" 2>/dev/null || true)"; \
         case "${state}" in \
           "installed "?*) version="${state#installed }" ;; \
           *) echo "${package} is not installed; its version floor is ${minimum}" >&2; exit 1 ;; \
         esac; \
         if ! dpkg --compare-versions "${version}" ge "${minimum}"; then \
           echo "${package} ${version} is not at or above its floor ${minimum}" >&2; exit 1; \
         fi; \
         echo "${package} ${version} meets its floor ${minimum}"; \
       done \
    && apt-get install --yes --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
WORKDIR /workspace
COPY Homayar/pyproject.toml Homayar/uv.lock ./
# --locked refuses a uv.lock that is stale against pyproject.toml.
# HOM-364: a slow link to PyPI can still break one `uv sync`, so try at most
# 3 times inside this one RUN. uv keeps finished downloads in its cache
# (/root/.cache/uv), and within one RUN the cache survives a failed try, so a
# retry does not download everything again. A separate RUN or a new build
# would start with an empty cache. After 3 failed tries the build fails.
RUN sync_deps() { \
      if [ "${INSTALL_AI_LOCAL}" = "true" ]; then \
        uv sync --locked --no-dev --no-install-project --extra ai-local; \
      else \
        uv sync --locked --no-dev --no-install-project; \
      fi; \
    }; \
    attempt=1; \
    until sync_deps; do \
      if [ "${attempt}" -ge 3 ]; then \
        echo "uv sync failed after 3 attempts" >&2; \
        exit 1; \
      fi; \
      echo "uv sync attempt ${attempt} of 3 failed; retrying in 15 s" >&2; \
      sleep 15; \
      attempt=$((attempt + 1)); \
    done

# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS runtime
ENV VIRTUAL_ENV=/opt/venv
ENV PATH=${VIRTUAL_ENV}/bin:${PATH}
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
# Debian package floors (HOM-349). trixie-security keeps only the newest version of
# a package, so an exact pkg=version pin would break the build on Debian's next fix.
# The build fails instead when a package (of the build architecture) is missing,
# older than its floor, or has a version dpkg cannot compare. Read on 2026-10-30
# from packages.debian.org/trixie and security-tracker.debian.org:
#   libmbedcrypto16 (ffmpeg via librist4): fixes CVE-2025-47917, CVE-2026-34872,
#     CVE-2026-34873 and CVE-2026-34875.
#   libaom3: fixes CVE-2023-6879.
#   apt, libapt-pkg7.0 (HOM-377): fix CVE-2026-109518.
#   ffmpeg, libtiff6: the trixie versions of today. CVE-2026-75143 (ffmpeg) and
#     CVE-2026-52490 (tiffcrop) have no fix in Debian stable yet
#     (docs/security/vulnerability-triage.md, "HOM-349 re-scan").
# HOM-377, CVE-2026-109518: the pinned base ships apt and libapt-pkg7.0 3.0.3,
# and the newest base of 2026-11-05 still had 3.0.3, so the fix (3.0.4) comes
# from trixie-security, not from a new base digest. The update and the upgrade
# below are the one step that still runs apt 3.0.3 to fetch 3.0.4, so they set
# Acquire::Retries=0: the advisory needs retries above 0 (the Debian default).
# Every later apt-get in this stage runs with 3.0.4.
# hadolint ignore=DL3008
RUN apt-get -o Acquire::Retries=0 update \
    && apt-get -o Acquire::Retries=0 install --yes --no-install-recommends --only-upgrade apt libapt-pkg7.0 \
    && apt-get install --yes --no-install-recommends ca-certificates ffmpeg libgomp1 libmagic1 libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && arch="$(dpkg --print-architecture)" \
    && for floor in \
         "apt 3.0.4" \
         "ffmpeg 7:7.1.5-0+deb13u1" \
         "libaom3 3.12.1-1+deb13u1" \
         "libapt-pkg7.0 3.0.4" \
         "libmbedcrypto16 3.6.6-0.1~deb13u1" \
         "libtiff6 4.7.0-3+deb13u3"; do \
         package="${floor%% *}"; minimum="${floor#* }"; \
         state="$(dpkg-query --show --showformat="\${db:Status-Status} \${Version}" "${package}:${arch}" 2>/dev/null || true)"; \
         case "${state}" in \
           "installed "?*) version="${state#installed }" ;; \
           *) echo "${package} is not installed; its version floor is ${minimum}" >&2; exit 1 ;; \
         esac; \
         if ! dpkg --compare-versions "${version}" ge "${minimum}"; then \
           echo "${package} ${version} is not at or above its floor ${minimum}" >&2; exit 1; \
         fi; \
         echo "${package} ${version} meets its floor ${minimum}"; \
       done \
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
