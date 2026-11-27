# syntax=docker/dockerfile:1@sha256:572cebbc2b15e6f84c077742952674889cb63cd5e9b80abdffcbe587b6f19183
# Multi-stage Dockerfile for Clavis Frontend
# Build context: ./src/frontend
#
# The base images (node, nginx) and the syntax image are pinned by digest, so a
# rebuild of the same code runs on the same OS packages. Raise a digest on
# purpose, together with OS_PATCH_DATE below.

# Stage 1: Development (Vite dev server with HMR)
FROM node:24-alpine@sha256:116b800c3754b440ee0aa3ed2f20f5bf3d99a554bfd9adeea09ea8017f6e85f5 AS development

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]

# Stage 2: Builder (produces optimized static bundle)
FROM node:24-alpine@sha256:116b800c3754b440ee0aa3ed2f20f5bf3d99a554bfd9adeea09ea8017f6e85f5 AS builder

WORKDIR /app

# Vite bakes import.meta.env.VITE_API_URL into the bundle at build time.
# Compose/CI may pass this via build.args; ENV makes it visible to `npm run build`.
#
# Default is EMPTY (same-origin / relative URLs). The SPA is always served behind
# a reverse proxy that also routes /api/* to the backend on the same origin
# (Caddy, or the reverse proxy of the host), so relative requests reach the API on any hostname
# and satisfy the `connect-src 'self'` CSP. A hardcoded default like
# `http://localhost` ships a broken bundle whenever the build doesn't override it
# (prod calls localhost → every API call fails + CSP violation). Only set
# VITE_API_URL when the API genuinely lives on a different origin.
ARG VITE_API_URL=
ENV VITE_API_URL=${VITE_API_URL}

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

RUN npm run build

# Stage 3: Runtime (nginx serving static files)
FROM nginx:1.31-alpine@sha256:fb4658584bd9a916ffb6f6e365f97c25cfdb2923801f551ea08356e695f14ff7 AS runtime

# apk upgrade: the base image lags behind Alpine's security fixes, and an image scan
# reports every fixable package of a running image.
# Release build caches layers in GitHub Actions, so this layer never reruns on its own, and a new
# tag keeps the old packages. Raise OS_PATCH_DATE to take the current Alpine fixes, as in the backend.
ARG OS_PATCH_DATE=2026-11-15
RUN apk add --no-cache curl && apk upgrade --no-cache

# The tile server of both maps is a setting. The default serves a stack file
# that sets no MAP_TILE_URL: the FOSSGIS tiles in Germany, like the API's default.
# nginx.conf is a template; at start the entrypoint derives ${MAP_TILE_ORIGIN}
# (the origin of the URL) for the CSP and fills the template with it.
ENV MAP_TILE_URL=https://tile.openstreetmap.de/{z}/{x}/{y}.png

RUN <<'ENTRYPOINT'
cat > /docker-entrypoint.d/10-map-tile-origin.envsh <<'SCRIPT'
# The CSP allows exactly the origin of the tile server. Runs before
# 20-envsubst-on-templates.sh, which fills ${MAP_TILE_ORIGIN} into the nginx
# template — and substitutes only variables that exist, so nginx's own $host
# stays. The nginx entrypoint runs *.sh as a child process but *sources*
# *.envsh — only the .envsh name makes the export reach the envsubst step.
url="${MAP_TILE_URL:-}"
[ -n "$url" ] || url='https://tile.openstreetmap.de/{z}/{x}/{y}.png'
# scheme://host[:port]; a {s}. subdomain placeholder becomes the CSP wildcard *.
origin="$(printf '%s' "$url" | sed -E -e 's#^([a-zA-Z][a-zA-Z0-9+.-]*://[^/?#]+).*$#\1#' -e 's#://\{s\}\.#://*.#')"
case "$origin" in
    *://*[!/]*) ;;
    *)
        # Not a URL: the CSP then allows no tile server, and nginx still starts
        # (an unset variable would stay as ${MAP_TILE_ORIGIN} and stop nginx).
        echo "10-map-tile-origin: MAP_TILE_URL is no URL, the maps get no tiles: $url" >&2
        origin=''
        ;;
esac
export MAP_TILE_ORIGIN="$origin"
SCRIPT
# The nginx entrypoint sources only .envsh files WITH the executable bit
# (and runs *.sh as child processes, where an export would be lost).
chmod +x /docker-entrypoint.d/10-map-tile-origin.envsh
ENTRYPOINT

COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=builder /app/dist /usr/share/nginx/html

# The mount point of the operator's notice folder (deployment/README.md). It
# stays empty in the image: without a mount, /operator/notice.json is a 404
# and the app shows no notice.
RUN mkdir -p /usr/share/nginx/html/operator

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:80/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
