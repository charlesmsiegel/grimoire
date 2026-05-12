# Grimoire frontend

React + Vite + TypeScript SPA. See [`../specs/14-frontend.md`](../specs/14-frontend.md).

## Run

```sh
pnpm install
pnpm dev
```

Visits `http://127.0.0.1:5173`. Proxies `/api/*` and `/ws/*` to `http://127.0.0.1:8000` (the backend).

## Test

```sh
pnpm lint
pnpm typecheck
pnpm build
```
