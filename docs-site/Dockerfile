# Docusaurus dev + build image. Linux/Windows devs run this and need nothing
# else installed locally — no Node, no npm, no Playwright browsers.
#
# Build:  docker build -t waldo-docs ./docs-site
# Dev:    docker compose -f docker-compose.docs.yml up docs (port 3000)
# Build:  docker compose -f docker-compose.docs.yml run --rm docs-build

FROM node:20-bookworm-slim AS base
ENV NODE_ENV=development
WORKDIR /app

# System deps for Playwright + sharp image deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        libnss3 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
    && rm -rf /var/lib/apt/lists/*

COPY package.json ./
RUN npm install --no-audit --no-fund \
    && npx playwright install --with-deps chromium

COPY . .

EXPOSE 3000

CMD ["npm", "start"]
