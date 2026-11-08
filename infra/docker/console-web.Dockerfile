# Base images pinned by digest (HOM-228): the multi-platform indexes of
# node:22-alpine (Node.js 22.23.4, resolved on 2026-10-23) and nginx:1.30-alpine-slim
# (nginx 1.30.6, Alpine 3.24.3, resolved on 2026-10-30, HOM-350) with
# `docker buildx imagetools inspect <tag>`. The slim variant ships no curl (HOM-350).
# Re-pin on purpose, never by overriding NODE_IMAGE or NGINX_IMAGE for a release
# (docs/operations/release.md).
ARG NODE_IMAGE=node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914
ARG NGINX_IMAGE=nginx:1.30-alpine-slim@sha256:dede8895648b461178c3bc604f7eed4423efe5220b2c4d839f5eb3d34ac3f8f7

# hadolint ignore=DL3006
FROM ${NODE_IMAGE} AS build
ARG VITE_API_URL=/api/v1
ARG VITE_BASE_PATH=/app/
WORKDIR /workspace
ENV VITE_API_URL=${VITE_API_URL}
COPY Homayar/frontend/package.json Homayar/frontend/package-lock.json ./
RUN npm ci
COPY Homayar/frontend/ ./
RUN npm run build -- --base="${VITE_BASE_PATH}"

# hadolint ignore=DL3006
FROM ${NGINX_IMAGE} AS runtime
# The console and the reverse proxy need no curl (health checks use BusyBox wget),
# and curl 8.22.0-r0 brought 7 CVEs (HOM-350). This check stops a re-pin to a base
# that ships curl or libcurl again.
# HOM-379, CVE-2026-4738: the base ships zlib 1.3.2-r1 and the newest base of
# 2026-11-09 still did, so upgrade it from the Alpine repository and fail the
# build when zlib is missing or below 1.4-r0, the fixed version.
# hadolint ignore=DL3018
RUN rm -f /etc/nginx/conf.d/default.conf \
    && apk add --no-cache --upgrade zlib \
    && if ! apk info -e nginx >/dev/null 2>&1; then \
         echo "apk cannot answer package queries (HOM-350)" >&2; exit 1; \
       fi \
    && for package in curl libcurl; do \
         if apk info -e "${package}" >/dev/null 2>&1; then \
           echo "${package} must not be in the console-web image (HOM-350)" >&2; exit 1; \
         fi; \
       done \
    && zlib_version="$(apk list --installed zlib 2>/dev/null | sed -n 's/^zlib-\([0-9][^ ]*\) .*/\1/p')" \
    && if [ -z "${zlib_version}" ]; then \
         echo "zlib is not installed; its version floor is 1.4-r0" >&2; exit 1; \
       fi \
    && case "$(apk version -t "${zlib_version}" 1.4-r0)" in \
         '<') echo "zlib ${zlib_version} is not at or above its floor 1.4-r0" >&2; exit 1 ;; \
         '='|'>') echo "zlib ${zlib_version} meets its floor 1.4-r0" ;; \
         *) echo "apk cannot compare zlib ${zlib_version} with 1.4-r0" >&2; exit 1 ;; \
       esac
COPY infra/nginx/console.conf /etc/nginx/nginx.conf
COPY --from=build --chown=101:101 /workspace/dist /usr/share/nginx/html/app
USER 101:101
EXPOSE 8080
ENTRYPOINT []
CMD ["nginx", "-g", "daemon off;"]
