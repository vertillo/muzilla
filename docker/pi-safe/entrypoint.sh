#!/usr/bin/env bash
set -euo pipefail

# chrome-devtools-mcp currently defaults to a headed browser. Run an isolated
# virtual display so project MCP configuration remains cross-platform and does
# not need Linux-only executable/headless flags.
if command -v Xvfb >/dev/null 2>&1; then
  Xvfb "${DISPLAY:-:99}" -screen 0 1920x1080x24 -nolisten tcp \
    >/tmp/pi-safe-xvfb.log 2>&1 &
fi

exec pi "$@"
