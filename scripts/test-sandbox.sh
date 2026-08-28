#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.sandbox.yml"
PROD_GATE_IMAGE="muzilla-sandbox-prod-gate:local"

if [[ -z "${DOCKER_SOCKET:-}" ]]; then
    if [[ -S /var/run/docker.sock ]]; then
        export DOCKER_SOCKET=/var/run/docker.sock
    elif [[ -S "$HOME/.docker/run/docker.sock" ]]; then
        export DOCKER_SOCKET="$HOME/.docker/run/docker.sock"
    else
        printf 'test-sandbox: Docker socket not found.\n' >&2
        exit 2
    fi
fi
[[ -S "$DOCKER_SOCKET" ]] || {
    printf 'test-sandbox: DOCKER_SOCKET is not a Unix socket: %s\n' "$DOCKER_SOCKET" >&2
    exit 2
}

if [[ -z "${DOCKER_GID:-}" ]]; then
    # Docker Desktop remaps its host socket to root:root in the Linux VM.
    if [[ "$(uname -s)" == Darwin ]]; then
        export DOCKER_GID=0
    elif DOCKER_GID="$(stat -c '%g' "$DOCKER_SOCKET" 2>/dev/null)"; then
        export DOCKER_GID
    elif DOCKER_GID="$(stat -L -f '%g' "$DOCKER_SOCKET" 2>/dev/null)"; then
        export DOCKER_GID
    else
        printf 'test-sandbox: cannot determine the Docker socket group: %s\n' "$DOCKER_SOCKET" >&2
        exit 2
    fi
fi
export PI_AGENT_DIR="${PI_AGENT_DIR:-$HOME/.pi/agent}"
export GIT_CONFIG_PATH="${GIT_CONFIG_PATH:-$HOME/.gitconfig}"
[[ -d "$PI_AGENT_DIR" ]] || {
    printf 'test-sandbox: Pi agent directory not found: %s\n' "$PI_AGENT_DIR" >&2
    exit 2
}
[[ -e "$GIT_CONFIG_PATH" ]] || export GIT_CONFIG_PATH=/dev/null

cd "$ROOT_DIR"
compose=(docker compose -f "$COMPOSE_FILE")
"${compose[@]}" config --quiet
"${compose[@]}" build sandbox

"${compose[@]}" run --rm sandbox sh -ec '
    set -eu

    test ! -w /workspace/.pi
    test ! -w /
    test ! -w /workspace/docker-compose.sandbox.yml
    test ! -w /workspace/docker/Dockerfile.sandbox
    test ! -w /workspace/docker/pi-extensions
    test ! -w /workspace/.dockerignore
    test ! -w /workspace/.env
    test ! -w /workspace/Makefile
    test ! -w /workspace/scripts/pi-sandbox
    test ! -w /workspace/scripts/sandbox-entrypoint.sh
    test ! -w /workspace/scripts/test-sandbox.sh

    extension_list="$(pi list)"
    for extension in \
        "pi-mcp-adapter" \
        "pi-web-access" \
        "pi-subagents" \
        "@dietrichgebert/ponytail" \
        "@ff-labs/pi-fff" \
        "@narumitw/pi-usage" \
        "@firstpick/pi-extension-grill-me" \
        "@narumitw/pi-goal" \
        "pi-hermes-memory" \
        "pi-lens" \
        "@narumitw/pi-btw"; do
        printf "%s\n" "$extension_list" | grep -Fq "npm:$extension"
    done

    uv sync --frozen --all-extras
    command -v ffmpeg >/dev/null
    command -v fpcalc >/dev/null
    (cd frontend && npm ci --legacy-peer-deps --no-audit --no-fund)
    (cd e2e && npm ci --no-audit --no-fund)

    docker build --label com.muzilla.sandbox=true \
        --target runtime -f docker/Dockerfile -t "'"$PROD_GATE_IMAGE"'" .
    if docker history --no-trunc "'"$PROD_GATE_IMAGE"'" | grep -E "GITHUB_TOKEN|GH_TOKEN"; then
        printf "GITHUB_TOKEN appeared in the candidate image history.\n" >&2
        exit 1
    fi
    docker image rm "'"$PROD_GATE_IMAGE"'" >/dev/null
' 
