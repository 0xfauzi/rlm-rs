# RLM-RS UI

Developer-facing Next.js UI for inspecting and driving RLM-RS sessions, executions, runtime steps, and citations.

## How it works

- The UI calls same-origin endpoints (`/v1/*`, `/health/*`) and relies on Next.js rewrites defined in `ui/next.config.js`.
- In dev, those rewrites proxy to:
  - `API_PROXY_TARGET` (default `http://localhost:8080`)
  - `LOCALSTACK_PROXY_TARGET` (default `http://localhost:4566`)
- This keeps the UI environment-agnostic: the UI does not hardcode backend URLs in fetch calls.

## Quick start

From the repo root:

```bash
docker compose up --build
```

Then open `http://localhost:3000`.

## Local UI development

From `ui/`:

```bash
npm ci
npm run dev
```

The UI expects the backend stack to be reachable via the proxy targets above. The easiest way is to start the full stack with `docker compose up --build` from the repo root.

## Tests and quality checks

From `ui/`:

- Lint: `npm run lint`
- Typecheck: `npm run typecheck`
- Unit tests (Vitest): `npm test`
- E2E tests (Playwright): `npm run test:e2e`
- Production build: `npm run build`

## Common gotchas

- Pages that call `useSearchParams` must wrap the consuming client component in a `Suspense` boundary to avoid prerender/build errors (repo-wide invariant).
- Keep secrets out of the browser: provider keys belong in the orchestrator/API processes, not in the UI.

For more UI-specific guidance, see `ui/AGENTS.md`.
