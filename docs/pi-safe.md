# Pi safe mode with Gondolin

This setup is for autonomous or unattended Pi work such as `/goal`. It keeps the normal Pi
installation on the host, but routes Pi's built-in filesystem and shell tools through a local
Gondolin Linux micro-VM.

The design deliberately separates two modes:

- `pi` — the normal interactive installation, with the user's full extension set;
- `scripts/pi-safe` — a minimal host-side Pi profile plus a Gondolin VM for autonomous work.

The normal `~/.pi/agent` is not replaced or migrated.

## Why this is different from the previous Docker design

Plain Docker isolates the whole Pi process, including every third-party extension. Gondolin keeps
Pi itself on the host and isolates tool execution. That avoids duplicating the normal Pi login and
extension environment, but it introduces an important rule: **Pi extensions still execute on the
host**.

For that reason `pi-safe` does not load the normal global extension set. It uses a separate
`PI_CODING_AGENT_DIR` (default `~/.pi/agent-gondolin-safe`) containing only:

- `pi-subagents`;
- `@narumitw/pi-goal`;
- the reviewed `gondolin-safe` extension from this repository.

Provider authentication and model definitions are symlinked from the normal Pi agent directory so
the login is not duplicated. Sessions, trust, package state, and other mutable safe-profile data
remain separate.

The user's normal packages such as `pi-lens`, `pi-web-access`, `pi-mcp-adapter`, Hermes, ponytail,
FFF, usage, BTW, grill-me, and similar extensions remain untouched and available in normal `pi`.
They are intentionally absent from strict unattended safe mode unless the operator explicitly
chooses to install/audit one in the safe profile.

## Security boundary

When `PI_GONDOLIN_SAFE=1`, `.pi/extensions/gondolin-safe/index.ts` replaces Pi's built-in:

- `read`
- `write`
- `edit`
- `bash`
- `grep`
- `find`
- `ls`

and routes user `!` shell commands through the same Gondolin VM.

Only the current Git worktree is host-backed, at `/workspace`. Absolute tool paths outside the
worktree are rejected instead of being mapped to host paths.

The following project control-plane paths are readable but VFS mutations are rejected:

- `.pi/**`
- `.agents/**`
- `AGENTS.md`
- `CLAUDE.md`
- `.mcp.json`

The policy is enforced for filesystem mutations performed through guest shell commands as well as
Pi's write/edit tools, because the check is at Gondolin's VFS boundary.

Host `.venv` and `node_modules` directories are hidden from the Linux guest and replaced by
disposable in-VM overlays. This prevents Linux package artifacts from corrupting macOS virtualenvs
or node_modules.

Gondolin's `RealFSProvider` also blocks symlink traversal that escapes the exposed host directory.

### Host-side executable resources

Because extensions execute where Pi executes, the launcher fails closed when a project contains
additional `.pi/extensions` entries or project `packages`/`extensions` settings. Set
`PI_SAFE_ALLOW_PROJECT_EXTENSIONS=1` only after auditing those resources and accepting that their
code executes directly on the host.

The Gondolin extension also blocks unexpected LLM-invoked custom tools. Its allowlist contains the
seven routed built-ins, goal lifecycle tools, subagent orchestration/supervisor tools, and
`structured_output`.

This is defense in depth, not a claim that arbitrary malicious host extension code can be sandboxed
by an event hook. The safe profile avoids loading such code in the first place.

## Guest network policy

Gondolin HTTP/TLS mediation is configured explicitly. The default guest allowlist is:

```text
pypi.org
files.pythonhosted.org
registry.npmjs.org
```

and requests are limited to `GET`/`HEAD` by default. Internal/private IP ranges are blocked and
WebSocket egress is disabled.

This is enough for many deterministic Python/npm dependency downloads. Extend the hostname list
only when a gate genuinely needs another host:

```bash
PI_GONDOLIN_ALLOWED_HOSTS='pypi.org,files.pythonhosted.org,registry.npmjs.org,example.org' \
  scripts/pi-safe
```

A workflow that genuinely needs guest POST/PUT/etc. must explicitly opt in:

```bash
PI_GONDOLIN_ALLOW_HTTP_WRITES=1 scripts/pi-safe
```

That weakens the egress boundary and should not be the default for unattended goals. Even an
allowlisted download host can still be an exfiltration surface through URLs, so host allowlists are
risk reduction rather than a complete information-flow proof.

The model-provider connection itself is made by host Pi, not by the guest VM. Provider credentials
are not mounted into `/workspace` or the guest root filesystem.

## Git commit and push

The repository, including `.git`, is host-backed under `/workspace`, so guest Git can perform:

```text
git status
git diff
git add
git commit
```

Commit author name/email are copied into process environment variables by the launcher; the host
Git configuration is not mounted as a filesystem.

For GitHub push, use an SSH origin:

```bash
git remote set-url origin git@github.com:OWNER/REPO.git
```

Gondolin can proxy guest SSH through the host SSH agent without exposing the private key to the
guest. The extension derives the current `origin` repository and applies an SSH `execPolicy` that:

- allows only `github.com`;
- denies interactive/non-Git SSH;
- allows only the current `OWNER/REPO.git`;
- allows only `git-upload-pack` and `git-receive-pack`.

Therefore a host SSH agent that has access to several repositories is not automatically usable by
the guest for those other repositories.

Before using autonomous push, verify on the host:

```bash
printf '%s\n' "$SSH_AUTH_SOCK"
ssh-add -l
ssh -T git@github.com
ssh-keygen -F github.com
```

The host must already trust GitHub in `known_hosts`. The guest SSH client talks to Gondolin's
ephemeral proxy and therefore uses relaxed checking for that inner hop; the real upstream GitHub
host-key verification still happens on the host side.

Non-GitHub remotes are not enabled by the default policy.

## Reproducible guest image

The project includes `.pi/extensions/gondolin-safe/image.template.json`.

`pi-safe-setup` builds an architecture-specific Gondolin guest from a Debian Trixie OCI rootfs with
at least:

- Python 3 and venv/pip support;
- `uv`;
- Node.js/npm;
- Git/OpenSSH;
- bash, curl, findutils, ripgrep;
- compiler/build tooling;
- Chromium;
- chromaprint tooling.

Using an OCI rootfs means Docker or Podman is required **during the one-time image build only**.
The Docker daemon/socket is not passed to Pi or the running Gondolin VM.

The generated assets live under:

```text
~/.cache/pi-safe-gondolin/assets-aarch64
~/.cache/pi-safe-gondolin/assets-x86_64
```

as appropriate. Rebuild them after material guest-image changes:

```bash
scripts/pi-safe-setup --rebuild-image
```

## First-time setup on macOS

Requirements:

1. Pi already installed/authenticated normally.
2. Node.js >= 23.6.0 on the host.
3. QEMU.
4. `lz4` and `e2fsprogs` for Gondolin image building; macOS already supplies `cpio`.
5. Docker Desktop/compatible Docker engine or Podman for the one-time Debian OCI rootfs export.

Typical Homebrew prerequisites are:

```bash
brew install node qemu lz4 e2fsprogs
```

Do not let an autonomous agent perform this host setup. Install/verify these prerequisites manually.

After checking out this PR/branch:

```bash
chmod +x scripts/pi-safe scripts/pi-safe-setup
scripts/pi-safe-setup
```

The setup script:

1. verifies Node and QEMU;
2. installs the pinned `@earendil-works/gondolin` dependency next to the reviewed extension;
3. creates `~/.pi/agent-gondolin-safe`;
4. symlinks only `auth.json`/`models.json` from normal Pi when present;
5. copies settings while dropping global `packages`/`extensions`;
6. installs only `pi-subagents` and `@narumitw/pi-goal` into the safe profile;
7. globally auto-discovers the reviewed Gondolin policy within that safe profile;
8. builds/caches the architecture-specific guest image.

The source `~/.pi/agent` is not modified.

If normal model/provider preferences change later, refresh the sanitized safe settings with:

```bash
scripts/pi-safe-setup --refresh-profile
```

## Start safe Pi

From the repository:

```bash
scripts/pi-safe
```

The first run has its own Pi project-trust state. Approve the repository only after reviewing its
project extensions/configuration.

Inside Pi:

```text
/gondolin-safe
```

shows the VM id, workspace, shell, network allowlist, and GitHub repository SSH policy.

Normal interactive work continues to use:

```bash
pi
```

with the full normal extension environment.

## Reuse in another repository

The safe agent directory and guest image are global per machine. You do not need to rebuild them for
every repository.

A convenient global launcher can point to this reviewed script, for example:

```bash
mkdir -p ~/.local/bin
ln -sf /absolute/path/to/muzilla/scripts/pi-safe ~/.local/bin/pi-safe
```

Then:

```bash
cd /path/to/another/repository
pi-safe
```

The Gondolin extension in the safe agent profile uses that repository's current working directory as
the isolated `/workspace` and derives its GitHub SSH policy from that repository's `origin`.

For another repository, safe mode will refuse additional project-local executable Pi packages or
extensions by default. This is intentional: project prompt/skill files are data, but project
extensions are host-executed code.

## Gate compatibility

Strict Gondolin mode improves host safety but does **not** make every existing Muzilla gate fully
autonomous.

| Gate/capability | Strict `pi-safe` | Notes |
| --- | --- | --- |
| `ruff`, `mypy`, `lint-imports`, `pytest` | Supported | Runs in guest. `.venv` is disposable; dependency install may repeat, with package network restricted to allowlisted hosts. |
| database/Alembic tests | Supported | Local files/processes stay inside guest except intended repo writes. |
| frontend lint/typecheck/test/build | Supported | Runs in guest; `node_modules` is disposable and host node_modules is hidden. |
| ordinary Git commit | Supported | `.git` is in the host-backed worktree. |
| GitHub SSH fetch/push | Supported with host setup | Requires SSH origin, working `SSH_AUTH_SOCK`, trusted GitHub host key; repo-level exec policy is enforced. |
| live provider tests | Denied by default | Consistent with Muzilla's deterministic-test contract. Add hosts only deliberately. |
| `pi-lens` tools/background pipeline | Not available in strict mode | `pi-lens` runs host-side, so it is deliberately omitted. Core readiness commands should run via guest `bash`. |
| `researcher` web tools | Not available in strict mode | `pi-web-access` is host-side and omitted. A goal requiring new external research should block or receive research separately. |
| Chrome DevTools MCP browser-tester | Not available in strict mode | `pi-mcp-adapter`/MCP executes on the host. Do not silently treat this as browser PASS. |
| Playwright E2E CLI | Partial | Guest has Chromium, but project Playwright browser/dependency setup may require additional allowed download hosts or configuration. Validate before relying on unattended E2E. |
| Docker/exact-image/Compose gates | Not available | No host Docker socket is exposed and no Docker daemon is provided inside the VM. These gates must be manual/blocked or moved later to a separate isolated daemon. |
| host service access | Denied by default | No general host-network bridge. Add narrow mapped TCP/ingress policy only when explicitly designed. |

Under the current Muzilla `AGENTS.md`, a completion item that requires an unavailable mandatory gate
must **not** call `goal_complete`; it should report a genuine environment blocker. Safe mode does not
weaken acceptance requirements.

### Browser acceptance is the largest current gap

Backend/frontend unit-style gates fit Gondolin well. Independent browser acceptance currently does
not, because Muzilla's `browser-tester` expects the host-side Chrome DevTools MCP extension.

Possible future solutions are:

- run a browser/MCP stack inside the VM and expose a deliberately narrow bridge;
- use a separate isolated browser worker/container;
- keep browser acceptance as a manual/final gate outside unattended `/goal`.

Do not enable host MCP globally just to make a goal turn green; that would reintroduce a host-side
tool path around the VM.

### Docker acceptance is another deliberate gap

Never solve Docker gates by passing `/var/run/docker.sock` into the VM. Possession of the host Docker
socket can effectively restore broad host filesystem/control access. If fully autonomous exact-image
gates become necessary, use a separate isolated Docker daemon/VM with an explicit narrow interface.

## Moving to another machine

The repository configuration is portable, but the host prerequisites and generated VM assets are
machine-specific.

On a new machine:

1. install/authenticate normal Pi;
2. install Node >= 23.6, QEMU, image-build dependencies, and Docker/Podman for the one-time OCI build;
3. run `scripts/pi-safe-setup`;
4. approve project trust in the new safe profile;
5. configure the machine's SSH agent/known_hosts if push is required.

Gondolin supports macOS and Linux. ARM64 is its most-tested runtime path. Linux x86_64 is supported
but is less battle-tested than the ARM64 path. Windows is not a supported Gondolin host path for
this setup.

The generated QEMU/guest assets should be rebuilt for the target architecture rather than copied
between ARM64 and x86_64 systems.

## Residual limitations and threat model

Gondolin is a stronger boundary for generated shell/filesystem activity than prompt instructions,
but it is not identical to putting the entire Pi process in a container/VM.

Remaining host-trusted components include:

- Pi itself;
- `pi-goal`;
- `pi-subagents`;
- the reviewed Gondolin-safe extension;
- QEMU/Gondolin host code.

A vulnerability or malicious behavior in those host components is outside the guest-tool boundary.
This is why the safe profile intentionally minimizes its extension set.

The VM also does not provide a proof against all resource-exhaustion/DoS behavior, and allowed
network destinations remain possible data-exfiltration surfaces. Treat Gondolin as an enforcement
layer with a defined attack surface, not as permission to load arbitrary untrusted host extensions.

For the strongest "all extension code is isolated too" model, running the entire Pi process inside
a container/VM remains stronger. Gondolin is chosen here because it gives a substantially smaller
host blast radius for autonomous tool execution while preserving the normal host Pi installation
for interactive use.
