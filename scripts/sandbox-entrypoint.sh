#!/usr/bin/env bash
set -Eeuo pipefail

readonly workspace=/workspace
readonly pi_agent_dir="${PI_AGENT_DIR:-$HOME/.pi/agent}"
readonly baked_pi_agent=/opt/pi-agent

fail() {
    printf 'sandbox-entrypoint: %s\n' "$1" >&2
    exit 1
}

assert_read_only_dir() {
    local directory=$1
    [[ -d "$directory" ]] || fail "required read-only directory is missing: $directory"
    local marker="$directory/.sandbox-write-test.$$"
    if /usr/bin/touch "$marker" 2>/dev/null; then
        /bin/rm -f "$marker"
        fail "protected directory is writable: $directory"
    fi
}

assert_read_only_file() {
    local file=$1
    [[ -e "$file" ]] || fail "required protected file is missing: $file"
    if [[ -w "$file" ]]; then
        fail "protected file is writable: $file"
    fi
}

# These mounts are part of the security contract. A missing or writable project
# settings mount must stop the session instead of silently falling back to a mutable copy.
assert_read_only_dir "$workspace/.pi"
assert_read_only_dir "$workspace/docker/pi-extensions"
assert_read_only_file "$workspace/.dockerignore"
assert_read_only_file "$workspace/.env"
assert_read_only_file "$workspace/docker-compose.sandbox.yml"
assert_read_only_file "$workspace/docker/Dockerfile.sandbox"
assert_read_only_file "$workspace/Makefile"
assert_read_only_file "$workspace/scripts/pi-sandbox"
assert_read_only_file "$workspace/scripts/sandbox-entrypoint.sh"
assert_read_only_file "$workspace/scripts/test-sandbox.sh"
[[ ! -w / ]] || fail "container root is writable"

mkdir -p "$pi_agent_dir"

baked_version="$(cat "$baked_pi_agent/.sandbox-baked-version")"
current_version=""
if [[ -f "$pi_agent_dir/.sandbox-baked-version" ]]; then
    current_version="$(cat "$pi_agent_dir/.sandbox-baked-version")"
fi
if [[ "$current_version" != "$baked_version" ]]; then
    /bin/rm -rf "$pi_agent_dir/npm"
    mkdir -p "$pi_agent_dir"
    cp -a "$baked_pi_agent/npm" "$pi_agent_dir/npm"
    if [[ ! -f "$pi_agent_dir/settings.json" ]]; then
        cp "$baked_pi_agent/settings.json" "$pi_agent_dir/settings.json"
    fi
    printf '%s\n' "$baked_version" >"$pi_agent_dir/.sandbox-baked-version"
fi

sync_host_file() {
    local source=$1
    local destination=$2
    if [[ -f "$source" ]]; then
        mkdir -p "$(dirname "$destination")"
        cp "$source" "$destination"
    fi
}

if [[ -d /host-pi ]]; then
    # Settings/configuration are copied into the private named HOME. The project .pi
    # directory stays a read-only bind mount and is never copied or changed here.
    sync_host_file /host-pi/settings.json "$pi_agent_dir/settings.json"
    sync_host_file /host-pi/pi-goal.json "$pi_agent_dir/pi-goal.json"
    sync_host_file /host-pi/models-store.json "$pi_agent_dir/models-store.json"
    sync_host_file /host-pi/mcp-cache.json "$pi_agent_dir/mcp-cache.json"
    sync_host_file /host-pi/mcp-npx-cache.json "$pi_agent_dir/mcp-npx-cache.json"

    if [[ -d /host-pi/profiles ]]; then
        mkdir -p "$pi_agent_dir/profiles"
        rsync -a --delete /host-pi/profiles/ "$pi_agent_dir/profiles/"
    fi
    if [[ -d /host-pi/skills ]]; then
        mkdir -p "$pi_agent_dir/skills"
        rsync -a --delete /host-pi/skills/ "$pi_agent_dir/skills/"
    fi

    # Keep the host's provider login readable without copying its secret into an image
    # layer. The symlink target is the read-only host mount; writes fail closed.
    if [[ -f /host-pi/auth.json ]]; then
        if [[ -L "$pi_agent_dir/auth.json" && ! -e "$pi_agent_dir/auth.json" ]]; then
            /bin/rm -f "$pi_agent_dir/auth.json"
        fi
        if [[ ! -e "$pi_agent_dir/auth.json" ]]; then
            ln -s /host-pi/auth.json "$pi_agent_dir/auth.json"
        fi
    fi

    if [[ -f /host-pi/npm/package.json && -f /host-pi/npm/package-lock.json ]]; then
        mkdir -p "$pi_agent_dir/npm"
        cp /host-pi/npm/package.json /host-pi/npm/package-lock.json "$pi_agent_dir/npm/"
        /bin/rm -rf "$pi_agent_dir/npm/node_modules"
        (cd "$pi_agent_dir/npm" && npm ci --legacy-peer-deps --no-audit --no-fund)
    fi
fi

# Fallback for already-built images: Pi's proper-lockfile needs to create a
# .lock file next to project settings.json, which fails with EROFS on the
# read-only /workspace/.pi mount and makes Pi silently ignore project settings
# (global gpt-5.5 wins over project muse-spark). Merge project settings into
# PI_AGENT_DIR/settings.json so the sandbox HOME already reflects the project
# model even before the Dockerfile EROFS patch is baked in.
if [[ -f "$workspace/.pi/settings.json" ]]; then
    if [[ -f "$pi_agent_dir/settings.json" ]]; then
        PI_GLOBAL="$pi_agent_dir/settings.json" PI_PROJECT="$workspace/.pi/settings.json" node -e '
            const fs=require("fs");
            const gPath=process.env.PI_GLOBAL;
            const pPath=process.env.PI_PROJECT;
            try {
                const g=JSON.parse(fs.readFileSync(gPath,"utf8"));
                const p=JSON.parse(fs.readFileSync(pPath,"utf8"));
                function deepMerge(a,b){
                    const out={...a};
                    for(const k of Object.keys(b)){
                        if(b[k] && typeof b[k]==="object" && !Array.isArray(b[k]) && a[k] && typeof a[k]==="object" && !Array.isArray(a[k])){
                            out[k]=deepMerge(a[k],b[k]);
                        } else {
                            out[k]=b[k];
                        }
                    }
                    return out;
                }
                const merged=deepMerge(g,p);
                if(JSON.stringify(merged)!==JSON.stringify(g)){
                    fs.writeFileSync(gPath, JSON.stringify(merged,null,2)+"\n");
                    console.log("sandbox-entrypoint: merged project .pi/settings.json into PI_AGENT_DIR/settings.json");
                }
            } catch(e){ console.error("sandbox-entrypoint: project settings merge failed:", e.message); }
        '
    else
        mkdir -p "$(dirname "$pi_agent_dir/settings.json")"
        cp "$workspace/.pi/settings.json" "$pi_agent_dir/settings.json"
    fi
fi

# Seed the host's current project memory once, then let the private HOME own all future
# writes. This keeps another project's history out of the sandbox while preserving useful
# starting context for this repository.
project_name="$(basename "$workspace")"
memory_marker="$pi_agent_dir/.sandbox-host-memory-${project_name}"
if [[ ! -e "$memory_marker" && -d "/host-pi/projects-memory/$project_name" ]]; then
    mkdir -p "$pi_agent_dir/projects-memory"
    rsync -a "/host-pi/projects-memory/$project_name/" "$pi_agent_dir/projects-memory/$project_name/"
    : >"$memory_marker"
fi

if [[ -f "$workspace/pyproject.toml" && -f "$workspace/uv.lock" ]]; then
    (cd "$workspace" && uv sync --frozen --all-extras)
fi
if [[ -f "$workspace/frontend/package-lock.json" ]]; then
    (cd "$workspace/frontend" && npm ci --legacy-peer-deps --no-audit --no-fund)
fi
if [[ -d /opt/playwright-browsers ]]; then
    playwright_cache="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
    mkdir -p "$playwright_cache"
    if [[ -z "$(find "$playwright_cache" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
        cp -a /opt/playwright-browsers/. "$playwright_cache/"
    fi
fi
if [[ -f "$workspace/e2e/package-lock.json" ]]; then
    (cd "$workspace/e2e" && npm ci --no-audit --no-fund)
fi

# GITHUB_TOKEN is intentionally supplied at `docker compose run` time. The global Git
# config is copied to tmpfs because the host config mount is read-only; credentials never
# enter the image or the repository bind mount.
mkdir -p /tmp
if [[ -f "$HOME/.gitconfig" ]]; then
    cp "$HOME/.gitconfig" /tmp/gitconfig
else
    : >/tmp/gitconfig
fi
chmod 0600 /tmp/gitconfig
export GIT_CONFIG_GLOBAL=/tmp/gitconfig
/usr/bin/git config --global --unset-all credential.helper 2>/dev/null || true
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    : >/tmp/git-credentials
    chmod 0600 /tmp/git-credentials
    /usr/bin/git config --global credential.helper 'store --file=/tmp/git-credentials'
    printf 'protocol=https\nhost=github.com\nusername=x-access-token\npassword=%s\n\n' "$GITHUB_TOKEN" |
        /usr/bin/git credential approve
    if [[ -z "${GH_TOKEN:-}" ]]; then
        export GH_TOKEN="$GITHUB_TOKEN"
    fi
    if command -v gh >/dev/null 2>&1; then
        gh auth setup-git >/tmp/gh-auth-setup.log 2>&1 || true
        /usr/bin/git config --global --add credential.helper 'store --file=/tmp/git-credentials'
    fi
fi

# Put normal shell commands behind best-effort path guards. These are convenience guards,
# not the security boundary: Docker socket access and absolute executable paths remain a
# deliberate residual risk documented alongside this sandbox.
guard_file="${BASH_ENV:-/tmp/sandbox-bash-env}"
mkdir -p "$(dirname "$guard_file")"
cat >"$guard_file" <<'GUARD'
if [[ -n "${BASH_VERSION:-}" && "${SANDBOX_GUARDS_DISABLED:-0}" != 1 ]]; then
    rm() {
        local argument resolved
        for argument in "$@"; do
            [[ "$argument" == -* || "$argument" == -- ]] && continue
            resolved="$(realpath -m -- "$argument")"
            case "$resolved" in
                /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/media|/mnt|/opt|/proc|/root|/run|/sbin|/srv|/sys|/usr|/var|/host-pi|/workspace|/workspace/.git|/workspace/.git/*|/workspace/.pi|/workspace/.pi/*|/workspace/.env|/workspace/.dockerignore|/workspace/docker-compose.sandbox.yml|/workspace/docker/Dockerfile.sandbox|/workspace/docker/pi-extensions|/workspace/docker/pi-extensions/*|/workspace/Makefile|/workspace/scripts/pi-sandbox|/workspace/scripts/sandbox-entrypoint.sh|/workspace/scripts/test-sandbox.sh)
                    printf 'sandbox-rm: refusing protected path: %s\n' "$argument" >&2
                    return 126
                    ;;
                /workspace/*|/tmp/*|/home/agent/*)
                    ;;
                *)
                    printf 'sandbox-rm: refusing path outside sandbox: %s\n' "$argument" >&2
                    return 126
                    ;;
            esac
        done
        command /bin/rm "$@"
    }

    git() {
        local argument
        if [[ "${SANDBOX_ALLOW_DESTRUCTIVE_GIT:-0}" != 1 ]]; then
            for argument in "$@"; do
                case "$argument" in
                    clean|reset|restore)
                        printf 'sandbox-git: destructive git command blocked: git %s\n' "$argument" >&2
                        printf 'Set SANDBOX_ALLOW_DESTRUCTIVE_GIT=1 only for a deliberate disposable-worktree operation.\n' >&2
                        return 126
                        ;;
                esac
            done
        fi
        command /usr/bin/git "$@"
    }

    docker() {
        local command_name="${1:-}" argument source option
        for argument in "$@"; do
            case "$argument" in
                --privileged|--pid=host|--network=host|--ipc=host|--uts=host)
                    printf 'sandbox-docker: refusing host/privileged isolation override: %s\n' "$argument" >&2
                    return 126
                    ;;
            esac
        done
        if [[ "$command_name" == system ]]; then
            for argument in "${@:2}"; do
                case "$argument" in
                    prune|prune:*)
                        printf 'sandbox-docker: refusing Docker system prune from the sandbox.\n' >&2
                        return 126
                        ;;
                esac
            done
        fi
        if [[ "$command_name" == run || "$command_name" == create ]]; then
            local -a arguments=("$@")
            local index
            for ((index = 1; index < ${#arguments[@]}; index++)); do
                argument="${arguments[index]}"
                source=""
                if [[ "$argument" == -v || "$argument" == --volume || "$argument" == --mount ]]; then
                    ((index++))
                    argument="${arguments[index]:-}"
                fi
                if [[ "$argument" == --mount=* ]]; then
                    argument="${argument#*=}"
                elif [[ "$argument" == -v=* || "$argument" == --volume=* ]]; then
                    argument="${argument#*=}"
                fi
                if [[ "$argument" == *src=* || "$argument" == *source=* ]]; then
                    IFS=',' read -ra options <<< "$argument"
                    for option in "${options[@]}"; do
                        case "$option" in
                            src=*|source=*)
                                source="${option#*=}"
                                ;;
                        esac
                    done
                elif [[ "$argument" == */*:* ]]; then
                    source="${argument%%:*}"
                fi
                [[ -n "$source" ]] || continue
                case "$source" in
                    /workspace|/workspace/*|/tmp|/tmp/*|/home/agent|/home/agent/*)
                        ;;
                    /*)
                        printf 'sandbox-docker: refusing host path mount: %s\n' "$source" >&2
                        return 126
                        ;;
                esac
            done
        fi
        command /usr/bin/docker "$@"
    }
fi
GUARD
chmod 0644 "$guard_file"

# Do not echo environment values or credential-helper output. Extension/tool logs remain
# available through the private Pi session volume.
exec "$@"
