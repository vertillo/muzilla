# Muzilla frontend

The Muzilla web UI, built with React 19, TypeScript, and Vite.

## Commands

From this directory:

```bash
npm install
npm run dev          # dev server
npm run lint         # oxlint
npm run typecheck    # tsc --noEmit
npm run test         # vitest
npm run build        # tsc -b && vite build
npm run generate-types  # regenerate src/lib/api-types.ts via `uv run` (no activated venv needed)
```

## Conventions

- The backend's OpenAPI schema is the single source of server types: `npm run
  generate-types` regenerates `src/lib/api-types.ts` (via `uv run python
  scripts/export_openapi_schema.py` and `openapi-typescript`; no activated venv
  required). `src/lib/types.ts` derives stable frontend/server contract
  aliases from those generated types, and the request layer in `src/lib/api.ts` is a custom
  wrapper around the browser `fetch` API, typed through them. View adapters must not recreate
  the API schema.
- The web UI is Muzilla's primary interface. The target product contract makes it the only
  surface for metadata/file Apply; the current legacy ChangeSet CLI apply/undo surface
  remains transitional work tracked by COMPAT-CHANGESET-001.

See the repository root `AGENTS.md`, `docs/product-spec.md`, and `docs/completion-matrix.md`
for the product contract and open work.
