# Pi Docker sandbox

This setup runs the entire Pi Coding Agent process, including third-party extensions and custom
tools, inside Docker. It is intended for autonomous `/goal` work without exposing the macOS
Python/Homebrew environment or personal SSH credentials.

The Docker image and Pi agent-home volume are global and reusable across repositories. Project
configuration and project skills remain in each repository.

## Security model

The launcher mounts the current Git root at `/workspace` read-write so Pi can edit code, run Git,
commit, and (when configured) push. It then over-mounts these project control-plane paths
read-only when they exist:

- `.pi/`
- `.agents/`
- `AGENTS.md`
- `CLAUDE.md`
- `.mcp.json`

The container deliberately does **not** mount:

- `/var/run/docker.sock`
- the macOS home directory
- the host `~/.ssh`
- the host SSH agent
- Homebrew/Python/system paths

Docker-related acceptance gates therefore cannot use the host Docker daemon. Treat that as an
environment blocker until a separate isolated daemon is added.

The runtime uses a non-root `pi` user, `no-new-privileges`, drops Linux capabilities, and uses an
ephemeral `/tmp`. The root filesystem is still writable but disposable; package-manager or
runtime damage inside the container disappears when the container exits.

## Persistent state

Named volumes:

| Volume | Purpose |
| --- | --- |
| `pi-agent-home` | `~/.pi/agent`: auth, settings, packages, sessions, prompts, themes, Hermes state, trust |
| `pi-global-agents` | optional global `~/.agents`, including global skills |
| `pi-npm-cache` | npm cache |
| `pi-uv-cache` | uv cache |
| `pi-git-OWNER-REPO` | per-repository deploy key and GitHub `known_hosts` |

Project-local `.agents/skills/` and `.pi/skills/` are not copied into a Docker volume. They remain
in the repository and are discovered from `/workspace` after the project is trusted.

## First-time setup

Make the helpers executable after checking out the commit containing this setup:

```bash
chmod +x \
  docker/pi-safe/entrypoint.sh \
  scripts/pi-safe \
  scripts/pi-safe-setup \
  scripts/pi-safe-install-extensions \
  scripts/pi-safe-git-key
```

Build the image, create persistent volumes, and migrate platform-independent Pi state from the
existing macOS `~/.pi/agent`:

```bash
scripts/pi-safe-setup --migrate-agent-home
```

The migration mounts the host agent home read-only and deliberately excludes:

- `npm/`
- `git/`
- `trust.json`

This avoids copying macOS `node_modules` or native artifacts into Linux. The original host
`~/.pi/agent` is not modified.

Then reconcile/install the Linux copies of the extension packages:

```bash
scripts/pi-safe-install-extensions
```

The required package set is currently:

```text
npm:pi-mcp-adapter
npm:pi-web-access
npm:pi-subagents
npm:@dietrichgebert/ponytail
npm:@ff-labs/pi-fff
npm:@narumitw/pi-usage
npm:@firstpick/pi-extension-grill-me
npm:@narumitw/pi-goal
npm:pi-hermes-memory
npm:pi-lens
npm:@narumitw/pi-btw
```

The package installation lives under `pi-agent-home`, so it is done once rather than on every
container run.

## Start Pi

From any Git repository:

```bash
/path/to/muzilla/scripts/pi-safe
```

The launcher discovers that repository's Git root automatically and mounts it at `/workspace`.
The first time a repository is opened, approve Pi's normal project-trust prompt. The decision is
persisted in `pi-agent-home`; the stable in-container path is always `/workspace`.

To expose a single global command, either copy or symlink the generic launcher into a directory
on `PATH`, for example:

```bash
mkdir -p ~/.local/bin
ln -sf /absolute/path/to/muzilla/scripts/pi-safe ~/.local/bin/pi-safe
```

Then use the same image, global extensions, and persistent Pi state from any repository:

```bash
cd ~/projects/another-project
pi-safe
```

The global Pi/Hermes state is shared; project-local `.pi` and `.agents` content comes from the
current repository.

## Commit and push

`git commit` needs no extra credential because `.git/` is part of the read-write repository bind
mount. The launcher also forwards the effective Git author name/email as environment variables
without mounting the host Git configuration.

Push uses a separate GitHub deploy key for each repository. From the repository to authorize:

```bash
/path/to/muzilla/scripts/pi-safe-git-key create
```

The helper creates a named volume such as:

```text
pi-git-anphetamina-muzilla
```

and prints the public key. It does **not** modify GitHub.

Manually add that key at:

```text
GitHub repository -> Settings -> Deploy keys -> Add deploy key
```

Enable **Allow write access**.

The helper writes GitHub's published Ed25519 host key to `known_hosts` and keeps strict host-key
checking enabled. The expected GitHub Ed25519 fingerprint is:

```text
SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU
```

If the clone currently uses an HTTPS origin, switch this clone to SSH manually after adding the
deploy key:

```bash
git remote set-url origin git@github.com:OWNER/REPO.git
```

Then verify authentication, fetch, and push authorization without changing the remote:

```bash
/path/to/muzilla/scripts/pi-safe-git-key test
```

The push check uses `git push --dry-run origin HEAD`.

The normal `pi-safe` launcher detects the current GitHub repository and mounts its
`pi-git-OWNER-REPO` volume read-only when that volume exists. It never mounts personal SSH keys.

## Recommended GitHub protection

A per-repository deploy key intentionally permits writes to that repository. Configure a GitHub
ruleset/branch protection for important branches so GitHub itself rejects force pushes and branch
deletion. This is independent of `AGENTS.md` and remains effective even if an agent ignores a
prompt-level Git policy.

## Browser MCP

The image includes Chromium and Xvfb. The existing project `.mcp.json` can remain read-only and
cross-platform; the image exposes a stable-Chrome-compatible wrapper for Chromium and runs a
virtual display for `chrome-devtools-mcp`.

Chromium is launched with its inner sandbox disabled because Docker `no-new-privileges` prevents
Chromium's setuid sandbox. The Docker container is therefore the outer isolation boundary for
browser work.

## Updating Pi and extensions

The Pi CLI itself is part of the Docker image. Rebuild the image to refresh it:

```bash
scripts/pi-safe-setup
```

Extension packages are stored in `pi-agent-home` and can be updated independently:

```bash
pi-safe update --extensions
```

Do not copy the host `~/.pi/agent/npm` or `~/.pi/agent/git` trees into the Linux volume.

## Isolation checks

After setup, verify these properties before using unattended goals:

```bash
pi-safe
```

Inside Pi's shell/tool environment or a temporary diagnostic session, confirm:

```text
/workspace/.pi              readable, not writable
/workspace/.agents          readable, not writable
/workspace/AGENTS.md        readable, not writable
/var/run/docker.sock        absent
/home/pi/.ssh               absent unless this repository has a deploy-key volume
```

A normal repository source/test file remains writable. Destroying the Pi container must not
remove settings, packages, sessions, Hermes data, or project trust because those live in named
volumes.
