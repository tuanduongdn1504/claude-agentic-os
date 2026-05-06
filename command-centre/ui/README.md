# command-centre/ui

Vite + React 18 + TypeScript + Tailwind + TanStack Router + React Query.

## Run dev

```
cd command-centre
# Terminal 1 — backend
/usr/bin/python3 scripts/server.py          # http://127.0.0.1:8765

# Terminal 2 — frontend dev server
cd ui
npm install
npm run dev                                 # http://127.0.0.1:5173  (proxies /api + /v1 to :8765)
```

## Build for production

```
cd ui
npm install
npm run build                               # produces ui/dist/
```

Then FastAPI at :8765 serves the built UI directly — visit
`http://127.0.0.1:8765/` and SPA routes resolve.

## E2E tests

```
cd ui
npm install
npx playwright install chromium
npm run test:e2e                            # assumes server on 8765 + dist built
```

## Architecture notes

- Routes are hand-built in `src/router.tsx` (3 routes). No `routeTree.gen.ts`
  generator needed — intentional simplification.
- All React Query hooks live in `src/hooks/useQueries.ts`. System health +
  attention poll at 10 s; summary + observability poll at 30 s.
- API client + types in `src/lib/`. Dark-theme token map is in
  `tailwind.config.ts`.
- `CollapsibleSection` persists open/closed in `localStorage` under
  `cc:section:<id>` per prompt spec.
- `Sheet` has focus trap + ESC-close + aria-modal.
- Panels live in `src/components/panels/`. Six are wired in this cut —
  the remaining ~27 follow the same shape (React Query hook → card →
  the API endpoint you already have).
