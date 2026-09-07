ARG NODE_IMAGE=node:22-alpine

# hadolint ignore=DL3006
FROM ${NODE_IMAGE} AS dependencies
ARG PNPM_VERSION=11.1.2
WORKDIR /workspace
ENV PNPM_HOME=/pnpm
ENV PATH=${PNPM_HOME}:${PATH}
RUN corepack enable && corepack prepare "pnpm@${PNPM_VERSION}" --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm config set --location=project --json allowBuilds \
      '{"esbuild":true,"sharp":true,"unrs-resolver":true}' \
    && pnpm install --frozen-lockfile

FROM dependencies AS build
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
COPY next-env.d.ts next.config.ts postcss.config.mjs tsconfig.json ./
COPY content ./content
COPY drizzle ./drizzle
COPY public ./public
COPY src ./src
RUN pnpm build && pnpm prune --prod

# hadolint ignore=DL3006
FROM ${NODE_IMAGE} AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
RUN addgroup -S -g 10001 app && adduser -S -D -H -u 10001 -G app app
COPY --from=build --chown=10001:10001 /workspace/package.json ./package.json
COPY --from=build --chown=10001:10001 /workspace/next.config.ts ./next.config.ts
COPY --from=build --chown=10001:10001 /workspace/node_modules ./node_modules
COPY --from=build --chown=10001:10001 /workspace/.next ./.next
COPY --from=build --chown=10001:10001 /workspace/content ./content
COPY --from=build --chown=10001:10001 /workspace/public ./public
USER 10001:10001
EXPOSE 3000
CMD ["node_modules/.bin/next", "start"]
