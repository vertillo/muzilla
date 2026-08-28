"""Writes the app's OpenAPI schema to a static JSON file for
`openapi-typescript` to consume without needing a running server. Not shipped
in the wheel — a dev/CI-only tool.

Run: `uv run python scripts/export_openapi_schema.py frontend/openapi.json`
  or from frontend/: `npm run generate-types` (uses `uv run` internally, no
  activated venv required).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from muzilla.api.app import create_app  # noqa: E402


def main(out_path: Path) -> None:
    app = create_app()
    schema = app.openapi()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"wrote OpenAPI schema to {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python scripts/export_openapi_schema.py <out_path>", file=sys.stderr)
        raise SystemExit(1)
    main(Path(sys.argv[1]))
