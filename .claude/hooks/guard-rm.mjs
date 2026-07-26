#!/usr/bin/env node
/**
 * PreToolUse hook: confine `rm` to the project directory.
 *
 * Reads the Claude Code hook payload from stdin. If the Bash command contains
 * an `rm` invocation that (a) uses a recursive/force flag, or (b) targets any
 * path resolving OUTSIDE the project root, the command is denied.
 *
 * Path resolution handles absolute paths, ~ expansion, and ../ traversal by
 * resolving against the project root — the thing string-glob deny rules cannot do.
 *
 * Output protocol: print a JSON object on stdout with
 *   hookSpecificOutput.permissionDecision = "deny" | "allow"
 * Exit 0 either way (the JSON carries the decision).
 */

import path from "node:path";
import os from "node:os";

// Resolved from the environment, never hardcoded — this file is tracked and
// shared across machines, so a literal path here would be wrong everywhere but
// one checkout. (It previously read "/Users/asant/Desktop/budai", a different
// project entirely, which silently made every relative-target `rm` unresolvable.)
const PROJECT_ROOT = path.resolve(process.env.CLAUDE_PROJECT_DIR || process.cwd());

function readStdin() {
  return new Promise((resolve) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (c) => (data += c));
    process.stdin.on("end", () => resolve(data));
    // If nothing is piped, don't hang.
    setTimeout(() => resolve(data), 2000);
  });
}

function allow() {
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "allow",
      },
    })
  );
  process.exit(0);
}

function deny(reason) {
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: reason,
      },
    })
  );
  process.exit(0);
}

/** Expand ~ and resolve a token to an absolute path against `base`. */
function resolveTarget(token, base) {
  let t = token;
  if (t === "~" || t.startsWith("~/")) {
    t = path.join(os.homedir(), t.slice(1));
  }
  if (path.isAbsolute(t)) return path.resolve(t);
  return path.resolve(base, t);
}

function isInsideProject(absPath) {
  const rel = path.relative(PROJECT_ROOT, absPath);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

/**
 * Split a shell command on separators (; | && || newline) and inspect each
 * segment. For any segment whose first word is `rm`, evaluate its flags/targets.
 * Returns a deny-reason string, or null if the command is fine.
 */
function inspect(command) {
  const segments = command.split(/(?:\|\||&&|[;\n|])/);

  // Track the working directory implied by any `cd` in an earlier segment.
  // We only trust it if every cd so far stayed inside the project; the moment
  // a cd leaves (or can't be resolved), rm targets become unresolvable → deny.
  let cwd = PROJECT_ROOT;
  let cwdTrusted = true;

  for (const rawSeg of segments) {
    const seg = rawSeg.trim();
    if (!seg) continue;

    // Tokenize on whitespace (best-effort; good enough for rm arg scanning).
    const tokens = seg.split(/\s+/);

    // Update implied cwd on `cd`.
    if (tokens[0] === "cd") {
      const dest = tokens[1];
      if (!dest || /[$`*?\[\]{}]/.test(dest)) {
        cwdTrusted = false;
      } else {
        let d = dest;
        if (d === "~" || d.startsWith("~/")) d = path.join(os.homedir(), d.slice(1));
        const abs = path.isAbsolute(d) ? path.resolve(d) : path.resolve(cwd, d);
        cwd = abs;
        cwdTrusted = isInsideProject(abs);
      }
      continue;
    }

    // Find an `rm` that is the command word of this segment.
    if (tokens[0] !== "rm") continue;

    // If we've cd'd somewhere untrusted, relative rm targets can't be judged.
    if (!cwdTrusted) {
      return `Blocked: 'rm' runs after a 'cd' outside the project directory; refusing so a delete can't escape ${PROJECT_ROOT}.`;
    }

    const args = tokens.slice(1);
    const targets = [];
    let sawEndOfOpts = false;

    for (const arg of args) {
      if (!sawEndOfOpts && arg === "--") {
        sawEndOfOpts = true;
        continue;
      }
      if (!sawEndOfOpts && arg.startsWith("-")) {
        // Flag bundle. Block recursive/force outright.
        if (/^--(recursive|force)$/.test(arg)) {
          return `Blocked: 'rm ${arg}' (recursive/force delete is not permitted).`;
        }
        if (/^-[a-zA-Z]*[rRf]/.test(arg)) {
          return `Blocked: 'rm ${arg}' (recursive/force flag is not permitted).`;
        }
        continue; // other harmless flags like -i, -v
      }
      targets.push(arg);
    }

    for (const target of targets) {
      // Refuse anything with unresolved shell expansion we can't evaluate safely.
      if (/[$`*?\[\]{}]/.test(target)) {
        return `Blocked: 'rm' target "${target}" contains a glob/variable that can't be safely resolved. Delete a specific file instead.`;
      }
      const abs = resolveTarget(target, cwd);
      if (!isInsideProject(abs)) {
        return `Blocked: 'rm' target "${target}" resolves to ${abs}, which is outside the project directory (${PROJECT_ROOT}).`;
      }
    }
  }
  return null;
}

const raw = await readStdin();
let payload;
try {
  payload = JSON.parse(raw || "{}");
} catch {
  // Can't parse — don't block anything on our account.
  allow();
}

const toolName = payload.tool_name || payload.toolName;
const command =
  (payload.tool_input && payload.tool_input.command) ||
  (payload.toolInput && payload.toolInput.command) ||
  "";

if (toolName !== "Bash" || !command) allow();

const reason = inspect(command);
if (reason) deny(reason);
allow();
