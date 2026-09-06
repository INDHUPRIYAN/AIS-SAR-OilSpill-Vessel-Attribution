# OceanTrace frontend — Vite/React build served by nginx, /api proxied to the
# backend service.
#
# Build (from the repo root — the context must be the repo root):
#   docker build -f docker/frontend.Dockerfile -t oceantrace-frontend .

# --- stage 1: build ---------------------------------------------------------
FROM node:20-alpine AS build

WORKDIR /build
COPY main_system/frontend/package.json main_system/frontend/package-lock.json ./
RUN npm ci

COPY main_system/frontend/ ./
RUN npm run build

# --- stage 2: serve ---------------------------------------------------------
FROM nginx:1.27-alpine

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:80/ || exit 1
