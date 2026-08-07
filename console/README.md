# SodaMem Console

A read-mostly admin console for the SodaMem API: browse memories, run
searches, inspect prompt-ready context blocks, and check async job status.
Deletes are the only write path, and always go through a two-step confirm.

Stack: Vite + React + TypeScript + Tailwind CSS v4 + shadcn/ui (components
copied into `src/components/ui/`, not an npm dependency). No router library —
five flat pages don't need one, see `src/lib/router.tsx`.

## Wire contract

The API types in `src/lib/types.ts` and the client in `src/lib/api.ts` are a
hand-maintained mirror of `server/models.py`. There is no codegen step: if a
field changes on the server, update both files by hand.

## Develop

```bash
npm install
npm run dev
```

Opens on `http://localhost:5173`. The dev server proxies `/api/*` to
`http://localhost:8000` (see the `server.proxy` block in `vite.config.ts`) —
useful if you want to develop against a local `sodamem` server without CORS
configuration. In the common case, though, the console just calls same-origin
paths (`/health`, `/v1/...`) directly, matching how it's served in
production (see "Mounting" below). Point the console at a different host
entirely via the **API base URL** field on the Overview page.

## Build

```bash
npm run build
```

Type-checks with `tsc -b`, then builds a static bundle to `console/dist/`.
Asset paths are relative (`base: './'` in `vite.config.ts`), so the build
works whether it's opened directly, served from `/`, or mounted under a
path prefix like `/console`.

```
dist/
  index.html
  assets/
    index-*.js
    index-*.css
    geist-*.woff2   (self-hosted font, no external font CDN)
  favicon.svg
```

## Mounting

`server/console_mount.py` exports `mount_console(app: FastAPI) -> bool`. If
`console/dist/` exists, it's mounted at `/console` (static files + SPA
fallback to `index.html` for client-side routes on refresh/deep-link). If the
dist directory doesn't exist — e.g. `npm run build` was never run — it logs
an INFO line and returns `False`. The API must never fail to start because
the console wasn't built; production images that don't need the console can
skip the `console/` build step entirely.

This file does not wire itself into `server/app.py` — call `mount_console(app)`
from the app factory to actually enable it.

## Auth

Two fields live on the Overview page, both persisted to this browser's
`localStorage` only (never uploaded anywhere):

- **API key** — sent as `Authorization: Bearer <key>` on every request.
- **API base URL** — leave empty to call the same origin the console is
  served from (the normal case in production). Set it to point the console
  at a different `sodamem` deployment.

Every request failure — non-2xx HTTP status, network error, malformed
response — surfaces in the UI with the server's `ErrorBody.code` and
`message`, never a generic "something went wrong" (see
`src/components/error-state.tsx`).
