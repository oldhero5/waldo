# Waldo Documentation

Built with [Docusaurus](https://docusaurus.io).

## Quickstart (Docker — no local Node)

```bash
# from repo root
docker compose -f docker-compose.docs.yml up docs        # dev server on :3000
docker compose -f docker-compose.docs.yml run --rm docs-build       # static build
docker compose -f docker-compose.docs.yml run --rm docs-screenshots # capture UI screenshots
```

## Quickstart (host)

```bash
cd docs-site
npm install
npm start                  # http://localhost:3000
npm run build              # output to ./build
npm run screenshots        # requires WALDO_USER, WALDO_PASSWORD env vars
```

## Adding a page

1. Drop a markdown file under `docs/<section>/your-page.md` with frontmatter:
   ```md
   ---
   title: Your Page
   sidebar_position: N
   ---
   ```
2. Add it to `sidebars.ts` under the appropriate section.
3. `npm start` picks it up via HMR.

## Screenshots

The Playwright capture script at `scripts/screenshots.spec.ts` drives a real Waldo dev server, logs in as `WALDO_USER`, and writes PNGs to `static/img/screenshots/`. Reference them in MDX as `![alt](/img/screenshots/page.png)`.
