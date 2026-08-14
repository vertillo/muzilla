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
npm run generate-types  # regenerate src/lib/api-types.ts from the backend OpenAPI schema
```

## Conventions

- The backend's OpenAPI schema is the single source of server types: `npm run
  generate-types` regenerates `src/lib/api-types.ts` (via `scripts/export_openapi_schema.py`
  and `openapi-typescript`), and the API client (`openapi-fetch`) uses those generated types.
  View adapters must not recreate the API schema.
- The web UI is Muzilla's primary interface and the only surface that applies
  metadata/file changes. The CLI is support and troubleshooting tooling only.

See the repository root `AGENTS.md`, `docs/product-spec.md`, and `docs/completion-matrix.md`
for the product contract and open work.
