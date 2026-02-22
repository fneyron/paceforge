# ---- Dependencies ----
FROM node:22-alpine AS deps
RUN apk add --no-cache python3 make g++
RUN corepack enable && corepack prepare pnpm@latest --activate
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# ---- Builder ----
FROM node:22-alpine AS builder
RUN corepack enable && corepack prepare pnpm@latest --activate
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ENV NEXT_TELEMETRY_DISABLED=1

# Push DB schema (creates sqlite.db) then build
RUN pnpm db:push && pnpm build

# Prepare a minimal node_modules with only better-sqlite3 (serverExternalPackage)
RUN mkdir -p /tmp/native_modules && \
    cp -rL node_modules/better-sqlite3 /tmp/native_modules/better-sqlite3

# ---- Runner ----
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Copy standalone build (includes all server deps except serverExternalPackages)
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
COPY --from=builder /app/sqlite.db ./sqlite.db

# Copy better-sqlite3 native module (resolved from pnpm symlinks)
COPY --from=builder /tmp/native_modules/better-sqlite3 ./node_modules/better-sqlite3

EXPOSE 3000
ENV HOSTNAME="0.0.0.0"
ENV PORT=3000

CMD ["node", "server.js"]
