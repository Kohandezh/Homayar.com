# Base images pinned by digest (HOM-228): the multi-platform indexes of
# node:22-alpine (Node.js 22.23.4, resolved on 2026-10-23) and nginx:1.30-alpine
# (nginx 1.30.6, resolved on 2026-10-30, HOM-345) with `docker buildx imagetools inspect
# <tag>`. Re-pin on purpose, never by overriding NODE_IMAGE or NGINX_IMAGE for a release
# (docs/operations/release.md).
ARG NODE_IMAGE=node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914
ARG NGINX_IMAGE=nginx:1.30-alpine@sha256:0e52bd054126cdc040b1276c0af347aeb02a41b9cfe305d6094767ac30e97949

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
RUN rm -f /etc/nginx/conf.d/default.conf
COPY infra/nginx/console.conf /etc/nginx/nginx.conf
COPY --from=build --chown=101:101 /workspace/dist /usr/share/nginx/html/app
USER 101:101
EXPOSE 8080
ENTRYPOINT []
CMD ["nginx", "-g", "daemon off;"]
