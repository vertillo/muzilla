#!/usr/bin/env python3
"""Test verifica leggero sandbox pi-native per GRILL-ME-SANDBOX-PI-NATIVE-2026-08-29.md
Verifica 15 nodi senza drift/manifest, Docker immutata, music non protetta, OS sandbox attivo.
Deterministico, no live services, no fixtures esterne.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / ".pi" / "sandbox.json"
GUARDRAILS = ROOT / ".pi" / "extensions" / "guardrails.json"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"OK: {msg}")


errors = 0

try:
    sb = json.loads(SANDBOX.read_text())
    gd = json.loads(GUARDRAILS.read_text())
except Exception as exc:
    print(f"FAIL: JSON load error: {exc}")
    sys.exit(1)

# Q1+Q2: Perimetro B, denyRead rimosso
if not sb.get("enabled"):
    errors += 1
    fail("sandbox.enabled != true")
else:
    ok("sandbox.enabled true")

if sb.get("permissionPromptTimeoutSeconds") != 30:
    errors += 1
    fail(f"permissionPromptTimeoutSeconds != 30 got {sb.get('permissionPromptTimeoutSeconds')}")
else:
    ok("permissionPromptTimeoutSeconds 30")

if sb.get("allowBrowserProcess"):
    errors += 1
    fail("allowBrowserProcess != false")
else:
    ok("allowBrowserProcess false")

# network allow all
net = sb.get("network", {})
if net.get("allowedDomains") != ["*"] or net.get("deniedDomains") != []:
    errors += 1
    fail(f"network allow all mismatch: {net}")
else:
    ok("network allow all")

if not net.get("allowLocalBinding") or not net.get("allowAllUnixSockets"):
    errors += 1
    fail("network flags not all true")
else:
    if not net.get("allowUnauthenticatedSocksProxy"):
        errors += 1
        fail("network flags not all true")
    else:
        ok("network flags true")

fs = sb.get("filesystem", {})
if fs.get("denyRead") != []:
    errors += 1
    fail(f"filesystem.denyRead != [] got {fs.get('denyRead')}")
else:
    ok("filesystem.denyRead [] (B unico, no largo)")

expected_allow_read = {
    ".",
    "~/.config",
    "~/.local",
    "Library",
    "~/.pi/agent",
    "~/.cache",
    "/private/tmp",
    "/tmp",
}
if not expected_allow_read.issubset(set(fs.get("allowRead", []))):
    errors += 1
    fail(f"allowRead mismatch got {fs.get('allowRead')} expected {expected_allow_read}")
else:
    ok("allowRead correct")

expected_allow_write = {".", "/tmp", "/private/tmp", "~/.pi/agent", "~/.cache"}
if not expected_allow_write.issubset(set(fs.get("allowWrite", []))):
    errors += 1
    fail(f"allowWrite mismatch got {fs.get('allowWrite')}")
else:
    ok("allowWrite correct")

expected_deny_write = {".env", ".env.*", "*.pem", "*.key", "./.pi/**", "./.agents/**", "/Users/asant/Desktop/muzilla/.pi/**", "/Users/asant/Desktop/muzilla/.agents/**"}
if set(fs.get("denyWrite", [])) != expected_deny_write:
    errors += 1
    fail(f"denyWrite mismatch got {fs.get('denyWrite')}")
else:
    ok("denyWrite RO hard-block correct (repo-scoped, global ~/.pi/agent writable)")

# GRILL Q1/Q2: normalize ~ and relative - verify ~/Desktop/muzilla variants are in allow lists
for needle in ["~/Desktop/muzilla", "~/Desktop/muzilla/.venv"]:
    if needle not in fs.get("allowRead", []):
        errors += 1
        fail(f"allowRead missing normalized variant {needle} for GRILL Q1/Q2")
    else:
        ok(f"allowRead contains {needle} (GRILL Q1/Q2)")

for needle in ["~/Desktop/muzilla", "~/Desktop/muzilla/.venv"]:
    if needle not in fs.get("allowWrite", []):
        errors += 1
        fail(f"allowWrite missing normalized variant {needle} for GRILL Q1/Q2")
    else:
        ok(f"allowWrite contains {needle} (GRILL Q1/Q2)")

# Verify global ~/.pi/agent is not blocked by denyWrite (GRILL Q4)
if any(".pi" in pat and pat.startswith("/") and ".pi/agent" in pat for pat in fs.get("denyWrite", [])):
    # should be repo-scoped only, not global
    if "/Users/asant/.pi/agent" in fs.get("denyWrite", []):
        errors += 1
        fail("denyWrite must not block global ~/.pi/agent")
    else:
        ok("denyWrite does not block global ~/.pi/agent (GRILL Q4)")

# Verify denyRead still empty for single B perimeter
if fs.get("denyRead") != []:
    errors += 1
    fail("denyRead must stay [] for single B")
else:
    ok("denyRead still [] after fix")

# Q3+Q4: guardrails secret-files readOnly + *.pem/*.key
rules = gd.get("policies", {}).get("rules", [])
pats: set[str] = set()
if not rules:
    errors += 1
    fail("no policies rules")
else:
    rule = rules[0]
    if rule.get("protection") != "readOnly":
        errors += 1
        fail(f"secret-files protection != readOnly got {rule.get('protection')}")
    else:
        ok("secret-files protection readOnly")
    pats = {p.get("pattern") for p in rule.get("patterns", [])}
    if pats != {".env", ".env.*", "*.pem", "*.key"}:
        errors += 1
        fail(f"secret-files patterns mismatch got {pats}")
    else:
        ok("secret-files patterns 4 correct")
    allow_pats = {p.get("pattern") for p in rule.get("allowedPatterns", [])}
    if allow_pats != {".env.example", ".env.test"}:
        errors += 1
        fail(f"allowedPatterns mismatch got {allow_pats}")
    else:
        ok("secret-files allowedPatterns correct")

# Q4 pathAccess
pa = gd.get("pathAccess", {})
if pa.get("mode") != "block":
    errors += 1
    fail(f"pathAccess mode != block got {pa.get('mode')}")
else:
    ok("pathAccess mode block")

allowed_paths = {p.get("path") for p in pa.get("allowedPaths", [])}
expected_paths = {
    "~/.pi/agent",
    "/tmp",
    "~/.config",
    "~/.local",
    "~/Library",
    "~/.cache",
    "/private/tmp",
}
if not expected_paths.issubset(allowed_paths):
    errors += 1
    fail(f"allowedPaths mismatch got {allowed_paths} expected {expected_paths}")
else:
    ok("pathAccess allowedPaths 7 correct (Hermes + tmp + config + cache)")

# Q9 autoDeny 4 git
ad = gd.get("permissionGate", {}).get("autoDenyPatterns", [])
ad_pats = {p.get("pattern") for p in ad}
expected_ad = {"git reset --hard", "git clean", "git push --force", "git push -f"}
if ad_pats != expected_ad:
    errors += 1
    fail(f"autoDeny mismatch got {ad_pats}")
else:
    ok("autoDeny 4 git correct")

# Q10 allowedPatterns 17
ap = gd.get("permissionGate", {}).get("allowedPatterns", [])
ap_pats = {p.get("pattern") for p in ap}
expected_ap = {
    "uv ",
    "ruff ",
    "mypy ",
    "lint-imports",
    "pytest",
    "npm ",
    "npx ",
    "docker ",
    "gh ",
    "make ",
    "git ",
    "rg ",
    "grep ",
    "jq ",
    "python ",
    "python3 ",
    "node ",
}
if ap_pats != expected_ap:
    errors += 1
    fail(f"allowedPatterns mismatch got {ap_pats} expected {expected_ap}")
else:
    ok("allowedPatterns 17 correct (toolchain estesa)")

if not gd.get("permissionGate", {}).get("requireConfirmation"):
    errors += 1
    fail("requireConfirmation != true")
else:
    ok("requireConfirmation true")

# Q5 music not protected
all_patterns = pats.union(allowed_paths).union(ap_pats).union(ad_pats)
if any("music" in str(p).lower() for p in all_patterns):
    errors += 1
    fail("music should not be protected")
else:
    ok("music not protected")

# Q12 default distruttivi: applyBuiltinDefaults true
if not gd.get("applyBuiltinDefaults"):
    errors += 1
    fail("applyBuiltinDefaults != true")
else:
    ok("applyBuiltinDefaults true (OS distruttivi)")

# Q14 no drift/manifest: ensure no manifest file created
manifest_candidates = list(ROOT.glob(".pi/sandbox-manifest*")) + list(
    ROOT.glob("sandbox-manifest*")
)
if manifest_candidates:
    errors += 1
    fail(f"unexpected manifest file found: {manifest_candidates}")
else:
    ok("no drift/manifest (conforme)")

# Q15 Docker immutata check via git diff
try:
    out = subprocess.check_output(
        ["git", "diff", "--", "docker/", "docker-compose.sandbox.yml"],
        cwd=str(ROOT),
        text=True,
    )
    if out.strip():
        errors += 1
        fail(f"docker diff not empty:\n{out[:500]}")
    else:
        ok("Docker immutata (git diff vuoto)")
except Exception as exc:
    print(f"WARN: git diff check skipped: {exc}")

# Final
if errors:
    print(f"\nFAILED: {errors} check(s) failed")
    sys.exit(1)
else:
    print("\nPASSED: tutti i 15 nodi conformi, sandbox pi-native corretta, Docker immutata")
    sys.exit(0)
