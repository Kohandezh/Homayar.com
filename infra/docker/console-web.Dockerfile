ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginx:1.28-alpine

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
