/**
 * Hardened Gondolin routing for autonomous Muzilla Pi sessions.
 *
 * This extension is intentionally inert during normal `pi` use. `scripts/pi-safe`
 * enables it with PI_GONDOLIN_SAFE=1. In safe mode it:
 *
 * - runs Pi's seven built-in filesystem/shell tools in a Gondolin micro-VM;
 * - routes user `!` commands to the same VM;
 * - mounts only the current Git worktree at /workspace;
 * - rejects writes to project control-plane paths at the VFS boundary;
 * - hides host .venv/node_modules behind disposable in-VM overlays;
 * - applies explicit HTTP/TLS egress policy instead of Gondolin's allow-all default;
 * - proxies GitHub SSH through the host with an exec policy restricted to the
 *   current repository; and
 * - blocks unexpected host-side custom tools in strict safe mode.
 *
 * The implementation follows Pi's official Gondolin routing example, with
 * repository-specific safety policy layered on top.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

import {
  MemoryProvider,
  RealFSProvider,
  ShadowProvider,
  VM,
  createHttpHooks,
  createShadowPathPredicate,
  getInfoFromSshExecRequest,
} from "@earendil-works/gondolin";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import {
  type BashOperations,
  createBashTool,
  createEditTool,
  createFindTool,
  createGrepTool,
  createLsTool,
  createReadTool,
  createWriteTool,
  type EditOperations,
  type FindOperations,
  type GrepToolDetails,
  type GrepToolInput,
  type LsOperations,
  type ReadOperations,
  type WriteOperations,
} from "@earendil-works/pi-coding-agent";

const GUEST_WORKSPACE = "/workspace";
const DEFAULT_GREP_LIMIT = 100;
const SAFE_ENV = "PI_GONDOLIN_SAFE";

const CONTROL_PATHS = [
  "/.pi",
  "/.agents",
  "/AGENTS.md",
  "/CLAUDE.md",
  "/.mcp.json",
] as const;

const HOST_TOOL_ALLOWLIST = new Set([
  "read",
  "write",
  "edit",
  "bash",
  "grep",
  "find",
  "ls",
  "structured_output",
  "subagent",
  "subagent_wait",
  "subagent_supervisor",
  "contact_supervisor",
  "goal_complete",
  "goal_blocked",
  "goal_wait",
]);

const MUTATING_VFS_OPS = new Set([
  "write",
  "mkdir",
  "unlink",
  "rmdir",
  "rename",
  "chmod",
  "chown",
  "lchown",
  "utimes",
  "lutimes",
  "truncate",
  "link",
  "symlink",
  "copyFile",
]);

function enabled(): boolean {
  return process.env[SAFE_ENV] === "1";
}

function stripAtPrefix(value: string): string {
  return value.startsWith("@") ? value.slice(1) : value;
}

function toPosix(value: string): string {
  return value.split(path.sep).join(path.posix.sep);
}

function isInsideHostPath(root: string, value: string): boolean {
  const relativePath = path.relative(root, value);
  return relativePath === "" || (!relativePath.startsWith("..") && !path.isAbsolute(relativePath));
}

function hostPathToGuest(localCwd: string, hostPath: string): string {
  const relativePath = path.relative(localCwd, hostPath);
  if (!isInsideHostPath(localCwd, hostPath)) {
    throw new Error(`path escapes host workspace: ${hostPath}`);
  }
  return relativePath ? path.posix.join(GUEST_WORKSPACE, toPosix(relativePath)) : GUEST_WORKSPACE;
}

function toGuestPath(localCwd: string, inputPath: string): string {
  const trimmed = stripAtPrefix(inputPath.trim());
  if (!trimmed) return GUEST_WORKSPACE;

  // Agents are told that the guest cwd is /workspace, so accept explicit guest
  // paths without trying to reinterpret them as host absolute paths.
  if (trimmed === GUEST_WORKSPACE || trimmed.startsWith(`${GUEST_WORKSPACE}/`)) {
    const resolved = path.posix.resolve(trimmed);
    if (resolved !== GUEST_WORKSPACE && !resolved.startsWith(`${GUEST_WORKSPACE}/`)) {
      throw new Error(`path escapes guest workspace: ${inputPath}`);
    }
    return resolved;
  }

  if (path.isAbsolute(trimmed)) {
    if (!isInsideHostPath(localCwd, trimmed)) {
      throw new Error(`path escapes host workspace: ${inputPath}`);
    }
    return hostPathToGuest(localCwd, trimmed);
  }

  const resolved = path.posix.resolve(GUEST_WORKSPACE, toPosix(trimmed));
  if (resolved !== GUEST_WORKSPACE && !resolved.startsWith(`${GUEST_WORKSPACE}/`)) {
    throw new Error(`path escapes guest workspace: ${inputPath}`);
  }
  return resolved;
}

function isControlPath(value: unknown): boolean {
  if (typeof value !== "string") return false;
  const normalized = path.posix.resolve("/", value);
  return CONTROL_PATHS.some((protectedPath) =>
    normalized === protectedPath || normalized.startsWith(`${protectedPath}/`),
  );
}

function openFlagsMutate(flags: unknown): boolean {
  if (typeof flags === "string") return /[wa+]/.test(flags);
  if (typeof flags !== "number") return false;
  const c = fs.constants;
  const mutatingBits = c.O_WRONLY | c.O_RDWR | c.O_CREAT | c.O_TRUNC | c.O_APPEND;
  return (flags & mutatingBits) !== 0;
}

function assertControlPlaneReadonly(ctx: {
  op: string;
  path?: string;
  oldPath?: string;
  newPath?: string;
  flags?: string | number;
}): void {
  const touchesControlPlane = [ctx.path, ctx.oldPath, ctx.newPath].some(isControlPath);
  if (!touchesControlPlane) return;

  const mutates = MUTATING_VFS_OPS.has(ctx.op) || (ctx.op === "open" && openFlagsMutate(ctx.flags));
  if (!mutates) return;

  const error = new Error(`read-only project control plane: ${ctx.path ?? ctx.oldPath ?? ctx.newPath ?? "unknown"}`) as NodeJS.ErrnoException;
  error.code = "EROFS";
  throw error;
}

function parseAllowedHosts(): string[] {
  const raw = process.env.PI_GONDOLIN_ALLOWED_HOSTS ?? "pypi.org,files.pythonhosted.org,registry.npmjs.org";
  return raw
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
}

function getGithubRepo(localCwd: string): string | undefined {
  let origin: string;
  try {
    origin = execFileSync("git", ["-C", localCwd, "remote", "get-url", "origin"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return undefined;
  }

  let repo = "";
  if (origin.startsWith("git@github.com:")) repo = origin.slice("git@github.com:".length);
  else if (origin.startsWith("ssh://git@github.com/")) repo = origin.slice("ssh://git@github.com/".length);
  else if (origin.startsWith("https://github.com/")) repo = origin.slice("https://github.com/".length);
  else return undefined;

  repo = repo.replace(/^\/+/, "").replace(/\.git$/, "");
  return repo.includes("/") ? `${repo}.git` : undefined;
}

function createGondolinReadOps(vm: VM, localCwd: string): ReadOperations {
  return {
    readFile: async (filePath) => vm.fs.readFile(toGuestPath(localCwd, filePath)),
    access: async (filePath) => {
      await vm.fs.access(toGuestPath(localCwd, filePath));
    },
    detectImageMimeType: async (filePath) => {
      const ext = path.posix.extname(toGuestPath(localCwd, filePath)).toLowerCase();
      if (ext === ".png") return "image/png";
      if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
      if (ext === ".gif") return "image/gif";
      if (ext === ".webp") return "image/webp";
      return null;
    },
  };
}

function createGondolinWriteOps(vm: VM, localCwd: string): WriteOperations {
  return {
    writeFile: async (filePath, content) => {
      await vm.fs.writeFile(toGuestPath(localCwd, filePath), content, { encoding: "utf8" });
    },
    mkdir: async (dirPath) => {
      await vm.fs.mkdir(toGuestPath(localCwd, dirPath), { recursive: true });
    },
  };
}

function createGondolinEditOps(vm: VM, localCwd: string): EditOperations {
  const readOps = createGondolinReadOps(vm, localCwd);
  const writeOps = createGondolinWriteOps(vm, localCwd);
  return {
    readFile: readOps.readFile,
    writeFile: writeOps.writeFile,
    access: readOps.access,
  };
}

function createGondolinLsOps(vm: VM, localCwd: string): LsOperations {
  return {
    exists: async (filePath) => {
      try {
        await vm.fs.access(toGuestPath(localCwd, filePath));
        return true;
      } catch {
        return false;
      }
    },
    stat: async (filePath) => vm.fs.stat(toGuestPath(localCwd, filePath)),
    readdir: async (dirPath) => vm.fs.listDir(toGuestPath(localCwd, dirPath)),
  };
}

async function walkGuestFiles(
  vm: VM,
  root: string,
  visit: (guestPath: string, relativePath: string) => Promise<boolean>,
  signal?: AbortSignal,
): Promise<boolean> {
  if (signal?.aborted) throw new Error("Operation aborted");
  const stat = await vm.fs.stat(root, { signal });
  if (!stat.isDirectory()) return visit(root, path.posix.basename(root));

  const walkDirectory = async (dir: string, relativeDir: string): Promise<boolean> => {
    if (signal?.aborted) throw new Error("Operation aborted");
    const entries = await vm.fs.listDir(dir, { signal });
    for (const entry of entries) {
      if (entry === ".git" || entry === "node_modules" || entry === ".venv") continue;
      const guestPath = path.posix.join(dir, entry);
      const relativePath = relativeDir ? path.posix.join(relativeDir, entry) : entry;
      let entryStat: Awaited<ReturnType<VM["fs"]["stat"]>>;
      try {
        entryStat = await vm.fs.stat(guestPath, { signal });
      } catch {
        continue;
      }
      if (entryStat.isDirectory()) {
        if (!(await walkDirectory(guestPath, relativePath))) return false;
      } else if (!(await visit(guestPath, relativePath))) {
        return false;
      }
    }
    return true;
  };

  return walkDirectory(root, "");
}

function matchesToolGlob(relativePath: string, pattern: string): boolean {
  const normalizedPattern = toPosix(pattern);
  if (normalizedPattern.includes("/")) {
    return (
      path.posix.matchesGlob(relativePath, normalizedPattern) ||
      path.posix.matchesGlob(relativePath, `**/${normalizedPattern}`)
    );
  }
  return path.posix.matchesGlob(path.posix.basename(relativePath), normalizedPattern);
}

function createGondolinFindOps(vm: VM, localCwd: string): FindOperations {
  return {
    exists: async (filePath) => {
      try {
        await vm.fs.access(toGuestPath(localCwd, filePath));
        return true;
      } catch {
        return false;
      }
    },
    glob: async (pattern, cwd, options) => {
      const root = toGuestPath(localCwd, cwd);
      const results: string[] = [];
      await walkGuestFiles(vm, root, async (_guestPath, relativePath) => {
        if (results.length >= options.limit) return false;
        if (matchesToolGlob(relativePath, pattern)) results.push(relativePath);
        return results.length < options.limit;
      });
      return results;
    },
  };
}

function createLineMatcher(pattern: string, literal: boolean | undefined, ignoreCase: boolean | undefined) {
  if (literal) {
    const needle = ignoreCase ? pattern.toLowerCase() : pattern;
    return (line: string) => (ignoreCase ? line.toLowerCase() : line).includes(needle);
  }
  const regex = new RegExp(pattern, ignoreCase ? "i" : undefined);
  return (line: string) => regex.test(line);
}

async function executeGondolinGrep(
  vm: VM,
  localCwd: string,
  params: GrepToolInput,
  signal?: AbortSignal,
): Promise<{ content: Array<{ type: "text"; text: string }>; details: GrepToolDetails | undefined }> {
  const root = toGuestPath(localCwd, params.path ?? ".");
  const rootStat = await vm.fs.stat(root, { signal });
  const rootIsDirectory = rootStat.isDirectory();
  const matcher = createLineMatcher(params.pattern, params.literal, params.ignoreCase);
  const contextLines = params.context && params.context > 0 ? params.context : 0;
  const limit = Math.max(1, params.limit ?? DEFAULT_GREP_LIMIT);
  const output: string[] = [];
  let matches = 0;

  await walkGuestFiles(
    vm,
    root,
    async (guestPath, relativePath) => {
      if (matches >= limit) return false;
      if (params.glob && !matchesToolGlob(relativePath, params.glob)) return true;

      let content: string;
      try {
        content = await vm.fs.readFile(guestPath, { encoding: "utf8", signal });
      } catch {
        return true;
      }

      const lines = content.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
      const displayPath = rootIsDirectory ? relativePath : path.posix.basename(guestPath);
      for (let index = 0; index < lines.length; index++) {
        if (!matcher(lines[index] ?? "")) continue;
        matches += 1;
        const start = Math.max(0, index - contextLines);
        const end = Math.min(lines.length - 1, index + contextLines);
        for (let contextIndex = start; contextIndex <= end; contextIndex++) {
          const separator = contextIndex === index ? ":" : "-";
          output.push(`${displayPath}${separator}${contextIndex + 1}${separator} ${lines[contextIndex] ?? ""}`);
        }
        if (matches >= limit) return false;
      }
      return true;
    },
    signal,
  );

  if (matches === 0) {
    return { content: [{ type: "text", text: "No matches found" }], details: undefined };
  }
  if (matches >= limit) output.push(`\n[${limit} matches limit reached]`);
  return { content: [{ type: "text", text: output.join("\n") }], details: undefined };
}

function sanitizeEnv(env: NodeJS.ProcessEnv | undefined): Record<string, string> | undefined {
  if (!env) return undefined;
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(env)) {
    if (typeof value === "string") result[key] = value;
  }
  return result;
}

function createGondolinBashOps(vm: VM, localCwd: string, shellPath: string): BashOperations {
  return {
    exec: async (command, cwd, { onData, signal, timeout, env }) => {
      if (signal?.aborted) throw new Error("aborted");
      const guestCwd = toGuestPath(localCwd, cwd);
      const controller = new AbortController();
      const onAbort = () => controller.abort();
      signal?.addEventListener("abort", onAbort, { once: true });

      let timedOut = false;
      const timer = timeout && timeout > 0
        ? setTimeout(() => {
            timedOut = true;
            controller.abort();
          }, timeout * 1000)
        : undefined;

      try {
        const proc = vm.exec([shellPath, "-lc", command], {
          cwd: guestCwd,
          env: sanitizeEnv(env),
          signal: controller.signal,
          stdout: "pipe",
          stderr: "pipe",
        });
        for await (const chunk of proc.output()) onData(chunk.data);
        const result = await proc;
        return { exitCode: result.exitCode };
      } catch (error) {
        if (signal?.aborted) throw new Error("aborted");
        if (timedOut) throw new Error(`timeout:${timeout}`);
        throw error;
      } finally {
        if (timer) clearTimeout(timer);
        signal?.removeEventListener("abort", onAbort);
      }
    },
  };
}

export default function gondolinSafe(pi: ExtensionAPI) {
  if (!enabled()) return;

  const localCwd = process.cwd();
  const localRead = createReadTool(localCwd);
  const localWrite = createWriteTool(localCwd);
  const localEdit = createEditTool(localCwd);
  const localBash = createBashTool(localCwd);
  const localGrep = createGrepTool(localCwd);
  const localFind = createFindTool(localCwd);
  const localLs = createLsTool(localCwd);

  let vm: VM | undefined;
  let vmStarting: Promise<VM> | undefined;
  let shellPath = "/bin/sh";

  async function startVm(ctx?: ExtensionContext): Promise<VM> {
    ctx?.ui.setStatus("gondolin-safe", ctx.ui.theme.fg("accent", "Gondolin safe: starting"));

    // Do not expose host virtualenvs or node_modules to Linux. Writes to these
    // paths land in a disposable MemoryProvider instead of the host worktree.
    const hostWorkspace = new RealFSProvider(localCwd);
    const workspace = new ShadowProvider(hostWorkspace, {
      shouldShadow: createShadowPathPredicate([
        "/.venv",
        "/node_modules",
        "/frontend/node_modules",
        "/e2e/node_modules",
      ]),
      writeMode: "tmpfs",
      tmpfs: new MemoryProvider(),
      denySymlinkBypass: true,
    });

    const { httpHooks, env: httpEnv } = createHttpHooks({
      allowedHosts: parseAllowedHosts(),
      blockInternalRanges: true,
      // Package installation for deterministic gates should be download-only.
      // Set PI_GONDOLIN_ALLOW_HTTP_WRITES=1 only for a deliberately approved
      // workflow that needs non-GET/HEAD guest HTTP requests.
      isRequestAllowed: (request) =>
        process.env.PI_GONDOLIN_ALLOW_HTTP_WRITES === "1" ||
        request.method === "GET" ||
        request.method === "HEAD",
    });

    const allowedRepo = getGithubRepo(localCwd);
    const useSsh = Boolean(allowedRepo && process.env.SSH_AUTH_SOCK);
    const knownHostsFile = process.env.PI_GONDOLIN_SSH_KNOWN_HOSTS ?? path.join(os.homedir(), ".ssh", "known_hosts");

    const created = await VM.create({
      sessionLabel: `pi-safe ${path.basename(localCwd)}`,
      httpHooks,
      allowWebSockets: false,
      env: {
        ...httpEnv,
        NPM_CONFIG_AUDIT: "false",
        NPM_CONFIG_FUND: "false",
        PIP_DISABLE_PIP_VERSION_CHECK: "1",
        GIT_TERMINAL_PROMPT: "0",
        // This controls guest -> Gondolin proxy verification only. Gondolin
        // still verifies the real upstream GitHub host key on the host.
        GIT_SSH_COMMAND:
          "ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o GlobalKnownHostsFile=/dev/null -o LogLevel=ERROR",
      },
      dns: {
        mode: "synthetic",
        syntheticHostMapping: "per-host",
      },
      ssh: useSsh && allowedRepo
        ? {
            allowedHosts: ["github.com"],
            agent: process.env.SSH_AUTH_SOCK,
            knownHostsFile,
            execPolicy: (request) => {
              const git = getInfoFromSshExecRequest(request);
              if (!git) return { allow: false, message: "non-git SSH denied by pi-safe" };
              if (git.repo !== allowedRepo) {
                return { allow: false, message: `repository denied by pi-safe: ${git.repo}` };
              }
              if (git.service !== "git-upload-pack" && git.service !== "git-receive-pack") {
                return { allow: false, message: `git SSH service denied: ${git.service}` };
              }
              return { allow: true };
            },
          }
        : undefined,
      vfs: {
        mounts: {
          [GUEST_WORKSPACE]: workspace,
        },
        hooks: {
          before: (vfsContext) => assertControlPlaneReadonly(vfsContext),
        },
      },
    });

    const bashProbe = await created.exec(["/bin/sh", "-lc", "command -v bash || true"]);
    shellPath = bashProbe.stdout.trim() || "/bin/sh";
    vm = created;

    ctx?.ui.setStatus(
      "gondolin-safe",
      ctx.ui.theme.fg("accent", `Gondolin safe: ${created.id.slice(0, 8)}`),
    );
    ctx?.ui.notify(
      `Gondolin VM ready. Host ${localCwd} is exposed only as ${GUEST_WORKSPACE}.`,
      "info",
    );
    return created;
  }

  async function ensureVm(ctx?: ExtensionContext): Promise<VM> {
    if (vm) return vm;
    if (!vmStarting) {
      vmStarting = startVm(ctx).finally(() => {
        vmStarting = undefined;
      });
    }
    return vmStarting;
  }

  // Belt-and-suspenders: the safe Pi profile intentionally loads only
  // pi-goal, pi-subagents, and this project extension. If another extension is
  // nevertheless discovered, an agent cannot invoke its custom tool unless it
  // is explicitly part of the orchestration allowlist above.
  pi.on("tool_call", async (event) => {
    if (HOST_TOOL_ALLOWLIST.has(event.toolName)) return;
    return {
      block: true,
      reason: `pi-safe blocks host-side custom tool '${event.toolName}'`,
    };
  });

  pi.on("session_start", async (_event, ctx) => {
    await ensureVm(ctx);
  });

  pi.on("session_shutdown", async (_event, ctx) => {
    const activeVm = vm;
    vm = undefined;
    vmStarting = undefined;
    if (!activeVm) return;
    ctx.ui.setStatus("gondolin-safe", ctx.ui.theme.fg("muted", "Gondolin safe: stopping"));
    try {
      await activeVm.close();
    } finally {
      ctx.ui.setStatus("gondolin-safe", undefined);
    }
  });

  pi.registerCommand("gondolin-safe", {
    description: "Show hardened Gondolin VM status",
    handler: async (_args, ctx) => {
      const activeVm = await ensureVm(ctx);
      ctx.ui.notify(
        [
          `Gondolin VM: ${activeVm.id}`,
          `Host workspace: ${localCwd}`,
          `Guest workspace: ${GUEST_WORKSPACE}`,
          `Guest shell: ${shellPath}`,
          `HTTP allowlist: ${parseAllowedHosts().join(", ") || "(none)"}`,
          `GitHub repo SSH policy: ${getGithubRepo(localCwd) ?? "unavailable"}`,
        ].join("\n"),
        "info",
      );
    },
  });

  pi.registerTool({
    ...localRead,
    async execute(id, params, signal, onUpdate, ctx) {
      const activeVm = await ensureVm(ctx);
      const tool = createReadTool(GUEST_WORKSPACE, {
        operations: createGondolinReadOps(activeVm, localCwd),
      });
      return tool.execute(id, params, signal, onUpdate);
    },
  });

  pi.registerTool({
    ...localWrite,
    async execute(id, params, signal, onUpdate, ctx) {
      const activeVm = await ensureVm(ctx);
      const tool = createWriteTool(GUEST_WORKSPACE, {
        operations: createGondolinWriteOps(activeVm, localCwd),
      });
      return tool.execute(id, params, signal, onUpdate);
    },
  });

  pi.registerTool({
    ...localEdit,
    async execute(id, params, signal, onUpdate, ctx) {
      const activeVm = await ensureVm(ctx);
      const tool = createEditTool(GUEST_WORKSPACE, {
        operations: createGondolinEditOps(activeVm, localCwd),
      });
      return tool.execute(id, params, signal, onUpdate);
    },
  });

  pi.registerTool({
    ...localBash,
    async execute(id, params, signal, onUpdate, ctx) {
      const activeVm = await ensureVm(ctx);
      const tool = createBashTool(GUEST_WORKSPACE, {
        operations: createGondolinBashOps(activeVm, localCwd, shellPath),
      });
      return tool.execute(id, params, signal, onUpdate);
    },
  });

  pi.registerTool({
    ...localLs,
    async execute(id, params, signal, onUpdate, ctx) {
      const activeVm = await ensureVm(ctx);
      const tool = createLsTool(GUEST_WORKSPACE, {
        operations: createGondolinLsOps(activeVm, localCwd),
      });
      return tool.execute(id, params, signal, onUpdate);
    },
  });

  pi.registerTool({
    ...localFind,
    async execute(id, params, signal, onUpdate, ctx) {
      const activeVm = await ensureVm(ctx);
      const tool = createFindTool(GUEST_WORKSPACE, {
        operations: createGondolinFindOps(activeVm, localCwd),
      });
      return tool.execute(id, params, signal, onUpdate);
    },
  });

  pi.registerTool({
    ...localGrep,
    async execute(_id, params, signal, _onUpdate, ctx) {
      const activeVm = await ensureVm(ctx);
      return executeGondolinGrep(activeVm, localCwd, params, signal);
    },
  });

  pi.on("user_bash", async (_event, ctx) => {
    const activeVm = await ensureVm(ctx);
    return { operations: createGondolinBashOps(activeVm, localCwd, shellPath) };
  });

  pi.on("before_agent_start", async (event, ctx) => {
    await ensureVm(ctx);
    const localLine = `Current working directory: ${localCwd}`;
    const guestLine =
      `Current working directory: ${GUEST_WORKSPACE} (Gondolin micro-VM; only the Git worktree is host-backed). ` +
      "Project control-plane paths .pi, .agents, AGENTS.md, CLAUDE.md, and .mcp.json are read-only.";
    const systemPrompt = event.systemPrompt.includes(localLine)
      ? event.systemPrompt.replace(localLine, guestLine)
      : `${event.systemPrompt}\n\n${guestLine}`;
    return { systemPrompt };
  });
}
