#!/bin/bash
# Q7/Q7b — non-bypassable docker wrapper (ponytail: allowlist /workspace|/tmp|/home/agent, bypass SANDBOX_ALLOW_DOCKER_BYPASS=1 disables wrapper checks only)
set -euo pipefail
if [[ "${SANDBOX_ALLOW_DOCKER_BYPASS:-0}" == 1 ]]; then
    exec /usr/bin/docker.real "$@"
fi
for arg in "$@"; do
    case "$arg" in
    --privileged | --pid=host | --network=host | --ipc=host | --uts=host)
        printf 'sandbox-docker: refusing host/privileged isolation override: %s\n' "$arg" >&2
        exit 126
        ;;
    esac
done
if [[ "${1:-}" == system ]]; then
    for a in "${@:2}"; do
        case "$a" in
        prune | prune:*)
            printf 'sandbox-docker: refusing Docker system prune from the sandbox.\n' >&2
            exit 126
            ;;
        esac
    done
fi
if [[ "${1:-}" == run || "${1:-}" == create ]]; then
    args=("$@")
    for ((i = 1; i < ${#args[@]}; i++)); do
        arg="${args[i]}"
        src=""
        if [[ "$arg" == -v || "$arg" == --volume || "$arg" == --mount ]]; then
            ((i++))
            arg="${args[i]:-}"
        fi
        if [[ "$arg" == --mount=* ]]; then
            arg="${arg#*=}"
        elif [[ "$arg" == -v=* || "$arg" == --volume=* ]]; then
            arg="${arg#*=}"
        fi
        if [[ "$arg" == *src=* || "$arg" == *source=* ]]; then
            IFS=',' read -ra opts <<<"$arg"
            for opt in "${opts[@]}"; do
                case "$opt" in src=* | source=*) src="${opt#*=}" ;; esac
            done
        elif [[ "$arg" == */*:* ]]; then
            src="${arg%%:*}"
        fi
        [[ -n "$src" ]] || continue
        case "$src" in
        /workspace | /workspace/* | /tmp | /tmp/* | /home/agent | /home/agent/*) ;;
        /*)
            printf 'sandbox-docker: refusing host path mount: %s\n' "$src" >&2
            printf 'Set SANDBOX_ALLOW_DOCKER_BYPASS=1 for deliberate host mount.\n' >&2
            exit 126
            ;;
        esac
    done
fi
exec /usr/bin/docker.real "$@"
