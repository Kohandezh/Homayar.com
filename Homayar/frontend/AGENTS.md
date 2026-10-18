# Product console

React 19 + Vite single-page app. It is served next to the backend under `/app/`.

## Commands (run in `Homayar/frontend/`, use npm)

- `npm run lint`
- `npm test` (Vitest)
- `npm run build -- --base=/app/` (the same build CI runs)
- `npm run test:e2e` (Playwright)

## Boundaries

- Work only in `Homayar/frontend/`. Do not read `website/`. It is a different app.
- Do not read backend Python to learn an API shape. Start from the existing types in `src/types/` and the callers in `src/lib/*-api.ts`. Open a backend router only when the task adds or changes an endpoint and no type exists yet.

## HeroUI v3

What you remember about HeroUI is likely v2 and wrong here. Before using a component, check the docs with the `heroui-react` MCP server or the `heroui-react` skill.

Do not run `heroui agents-md`. It writes a 28 KB docs index into this file, and that index loads on every session.
