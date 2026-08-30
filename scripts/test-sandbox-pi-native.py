#!/usr/bin/env python3
"""Validator Pi-native 0.6.5 — no Docker, no external services."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
GLOBAL_SB = Path.home()/".pi/agent/sandbox.json"
LOCAL_SB = ROOT/".pi/sandbox.json"
GLOBAL_GD = Path.home()/".pi/agent/extensions/guardrails.json"
LOCAL_GD = ROOT/".pi/extensions/guardrails.json"
SETTINGS = ROOT/".pi/settings.json"

def fail(m): print(f"FAIL: {m}")
def ok(m): print(f"OK: {m}")
err=0
try:
    gsb=json.loads(GLOBAL_SB.read_text())
    lsb=json.loads(LOCAL_SB.read_text())
    ggd=json.loads(GLOBAL_GD.read_text())
    lgd=json.loads(LOCAL_GD.read_text())
    st=json.loads(SETTINGS.read_text())
except Exception as e:
    print(f"FAIL: JSON load {e}"); sys.exit(1)

# global sandbox checks
if not gsb.get("enabled"): fail("global sandbox enabled"); err+=1
else: ok("global sandbox enabled")
if gsb.get("network",{}).get("allowAllUnixSockets") is not False: fail("global allowAllUnixSockets must be false"); err+=1
else: ok("global allowAllUnixSockets false")
if not gsb.get("network",{}).get("allowLocalBinding"): fail("global allowLocalBinding"); err+=1
else: ok("global allowLocalBinding true")
if gsb.get("network",{}).get("allowedDomains")!=["*"]: fail("global allowedDomains"); err+=1
else: ok("global allowedDomains *")
if "~/.ssh" not in str(gsb.get("filesystem",{}).get("denyRead",[])): fail("global denyRead missing ~/.ssh"); err+=1
else: ok("global denyRead host credentials")
if "." not in gsb.get("filesystem",{}).get("allowRead",[]): fail("global allowRead ."); err+=1
else: ok("global allowRead .")
if "~/.cache" not in gsb.get("filesystem",{}).get("allowWrite",[]): fail("global allowWrite cache"); err+=1
else: ok("global allowWrite cache")
if "~/.config" in gsb.get("filesystem",{}).get("allowWrite",[]): fail("global allowWrite must not contain ~/.config broad"); err+=1
else: ok("global allowWrite no broad ~/.config")

# local sandbox checks
if not lsb.get("enabled"): fail("local sandbox enabled"); err+=1
else: ok("local sandbox enabled")
if lsb.get("network",{}).get("allowAllUnixSockets") is not False: fail("local allowAllUnixSockets false"); err+=1
else: ok("local allowAllUnixSockets false")
if ".pi/settings.json" not in str(lsb.get("filesystem",{}).get("denyWrite",[])): fail("local denyWrite .pi/settings.json"); err+=1
else: ok("local denyWrite control-plane")
if lsb.get("filesystem",{}).get("denyRead") != []: fail("local denyRead must be []"); err+=1
else: ok("local denyRead []")

# guardrails global
if not ggd.get("features",{}).get("permissionGate"): fail("global permissionGate"); err+=1
else: ok("global permissionGate true")
if ggd.get("pathAccess",{}).get("mode")!="block": fail("global pathAccess block"); err+=1
else: ok("global pathAccess block")
if not any(r["id"]=="global-host-credentials" for r in ggd.get("policies",{}).get("rules",[])): fail("global host credentials rule"); err+=1
else: ok("global host credentials rule")

# guardrails local
if lgd.get("pathAccess",{}).get("mode")!="block": fail("local pathAccess block"); err+=1
else: ok("local pathAccess block")
if not any(r["id"]=="mz-local-control" for r in lgd.get("policies",{}).get("rules",[])): fail("local mz-local-control"); err+=1
else: ok("local mz-local-control")

# settings extensions
sa=st.get("subagents",{})
if not sa.get("defaultExtensions"): fail("settings defaultExtensions"); err+=1
else: ok("settings defaultExtensions")
for role in ["scout","researcher","worker","reviewer","oracle","delegate","browser-tester"]:
    exts=sa.get("agentOverrides",{}).get(role,{}).get("extensions")
    if not exts: fail(f"settings {role} extensions"); err+=1
    else: ok(f"settings {role} extensions")
    # check pi-sandbox present in every role
    if exts and not any("pi-sandbox" in e for e in exts): fail(f"{role} missing pi-sandbox"); err+=1

# no residues
import pathlib
for p in [Path.home()/".pi/agent/extensions/pi-sandbox/config.json",
          Path.home()/".pi/agent/extensions/pi-sandbox/config.json.bak",
          Path.home()/".pi/agent/extensions/pi-auto-review",
          Path.home()/".pi/agent/extensions/pi-permission-system",
          ROOT/".pi/extensions/pi-permission-system",
          ROOT/"tests/test_sandbox_global_pi_native.py",
          ROOT/"scripts/test-sandbox-global-pi-native.py"]:
    if p.exists(): fail(f"residue still exists: {p}"); err+=1
    else: ok(f"no residue {p.name if p.is_file() else p}")

if err: print(f"\nFAILED {err}"); sys.exit(1)
print("\nPASSED pi-native 0.6.5")
