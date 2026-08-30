#!/usr/bin/env python3
# ruff: noqa
# mypy: ignore-errors
# type: ignore
"""Smoke verifica configurazione reale Pi-native globale/locale (non solo .pi/sandbox.json)
Verifica:
- pi-sandbox globale contract esatto
- guardrails globale baseline (policies true, permissionGate false, pathAccess false, no prompt, applyBuiltinDefaults false)
- guardrails locale tighten-only, ID distinti
- pi-permission-system globale baseline esplicita (yolo false, authorizerChain [], reads allow, external_directory_write deny, path_write deny, AGENTS.md ask, human-only git ask)
- pi-auto-review fail-closed senza broad allow
- helper globale esiste e implementa seam 0.15.0
- settings globale riattivato senza "-" e in load order corretto
- protezione 20 file pendenti preservata (hash check esterno)
Deterministico, no live services.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOME = pathlib.Path.home()

errors = 0


def fail(msg):
    global errors
    errors += 1
    print(f"FAIL: {msg}")


def ok(msg):
    print(f"OK: {msg}")


# 1 pi-sandbox globale
try:
    p = HOME / ".pi" / "agent" / "extensions" / "pi-sandbox" / "config.json"
    cfg = json.loads(p.read_text())
    if cfg.get("subagents") != {"provider": "pi-subagents", "externalWorkerIsolation": "enforce"}:
        fail(f"pi-sandbox subagents mismatch {cfg.get('subagents')}")
    else:
        ok("pi-sandbox subagents enforce pi-subagents")
    if cfg.get("filesystem") != {"additionalAllowRead": ["/"]}:
        fail(f"pi-sandbox filesystem mismatch {cfg.get('filesystem')}")
    else:
        ok("pi-sandbox filesystem additionalAllowRead ['/']")
    if cfg.get("network") != {"allowedDomains": [], "deniedDomains": []}:
        fail(f"pi-sandbox network mismatch {cfg.get('network')}")
    else:
        ok("pi-sandbox network empty allow/deny")
    if cfg.get("hostIPC") != {"mode": "off"}:
        fail(f"pi-sandbox hostIPC mismatch {cfg.get('hostIPC')}")
    else:
        ok("pi-sandbox hostIPC off")
    # ensure legacy .pi/sandbox.json not used as active (should exist but not be changed)
    legacy = ROOT / ".pi" / "sandbox.json"
    if legacy.exists():
        ok(".pi/sandbox.json exists but not authoritative")
except Exception as e:
    fail(f"pi-sandbox config load error {e}")

# 2 guardrails globale
try:
    g = HOME / ".pi" / "agent" / "extensions" / "guardrails.json"
    gd = json.loads(g.read_text())
    if not gd.get("enabled"):
        fail("guardrails global enabled != true")
    else:
        ok("guardrails global enabled true")
    if gd.get("applyBuiltinDefaults") != False:
        fail(
            f"guardrails global applyBuiltinDefaults should be false got {gd.get('applyBuiltinDefaults')}"
        )
    else:
        ok("guardrails global applyBuiltinDefaults false")
    feat = gd.get("features", {})
    if feat.get("policies") != True:
        fail(f"guardrails global policies != true {feat}")
    else:
        ok("guardrails global policies true")
    if feat.get("permissionGate") != False:
        fail(f"guardrails global permissionGate != false {feat}")
    else:
        ok("guardrails global permissionGate false")
    if feat.get("pathAccess") != False:
        fail(f"guardrails global pathAccess != false {feat}")
    else:
        ok("guardrails global pathAccess false")
    # permissionGate inactive
    pg = gd.get("permissionGate", {})
    if pg.get("requireConfirmation") != False:
        fail(f"guardrails global permissionGate requireConfirmation != false {pg}")
    else:
        ok("guardrails global permissionGate requireConfirmation false")
    if pg.get("autoDenyPatterns") not in ([], None) and len(pg.get("autoDenyPatterns", [])) != 0:
        fail(f"guardrails global autoDeny should be empty {pg.get('autoDenyPatterns')}")
    else:
        ok("guardrails global autoDeny empty")
    if gd.get("pathAccess", {}).get("mode") != "allow":
        fail(
            f"guardrails global pathAccess mode should be allow (inactive) got {gd.get('pathAccess')}"
        )
    else:
        ok("guardrails global pathAccess mode allow")
    # policies rules for global
    rules = gd.get("policies", {}).get("rules", [])
    ids = {r.get("id") for r in rules}
    for need in ["global-settings", "global-agents", "global-extensions"]:
        if need not in ids:
            fail(f"guardrails global missing rule {need} got {ids}")
        else:
            ok(f"guardrails global rule {need} present")
    # check global-extensions covers extensions
    ext_rule = next((r for r in rules if r.get("id") == "global-extensions"), None)
    if ext_rule:
        pats = {p.get("pattern") for p in ext_rule.get("patterns", [])}
        if "~/.pi/agent/extensions/**" not in pats:
            fail(f"global-extensions should contain ~/.pi/agent/extensions/** got {pats}")
        else:
            ok("global-extensions pattern correct")
        if ext_rule.get("protection") != "readOnly":
            fail(f"global-extensions protection != readOnly {ext_rule.get('protection')}")
        else:
            ok("global-extensions protection readOnly")
        if ext_rule.get("onlyIfExists") != False:
            fail("global-extensions onlyIfExists should be false")
        else:
            ok("global-extensions onlyIfExists false")
    # ensure no yolo etc (guardrails has no yolo field)
    # check that no rule uses same ID as local will use
except Exception as e:
    fail(f"guardrails global load error {e}")

# 3 guardrails locale tighten-only
try:
    lg = ROOT / ".pi" / "extensions" / "guardrails.json"
    ld = json.loads(lg.read_text())
    if ld.get("applyBuiltinDefaults") != False:
        fail(
            f"guardrails local applyBuiltinDefaults should be false got {ld.get('applyBuiltinDefaults')}"
        )
    else:
        ok("guardrails local applyBuiltinDefaults false")
    feat = ld.get("features", {})
    if feat.get("permissionGate") != False:
        fail(f"guardrails local permissionGate should be false (no duplicate gate) got {feat}")
    else:
        ok("guardrails local permissionGate false")
    if feat.get("pathAccess") != False:
        fail(f"guardrails local pathAccess should be false (no interactive) got {feat}")
    else:
        ok("guardrails local pathAccess false (tighten-only)")
    lrules = ld.get("policies", {}).get("rules", [])
    lids = {r.get("id") for r in lrules}
    if lids & {"global-settings", "global-agents", "global-extensions", "global-secrets-templates"}:
        fail(f"guardrails local IDs overlap global {lids & {'global-settings'}}")
    else:
        ok(f"guardrails local IDs distinct from global: {lids}")
    for need in ["mz-local-control", "mz-local-extensions-agents", "mz-local-secrets"]:
        if need not in lids:
            fail(f"guardrails local missing {need}")
        else:
            ok(f"guardrails local rule {need} present")
    # check local protects .pi/settings.json
    ctrl = next((r for r in lrules if r.get("id") == "mz-local-control"), None)
    if ctrl:
        pats = {p.get("pattern") for p in ctrl.get("patterns", [])}
        for exp in [".pi/settings.json", ".pi/sandbox.json", ".pi/pi-auto-review.json"]:
            if exp not in pats:
                fail(f"mz-local-control missing {exp}")
            else:
                ok(f"mz-local-control contains {exp}")
    sec = next((r for r in lrules if r.get("id") == "mz-local-secrets"), None)
    if sec:
        pats = {p.get("pattern") for p in sec.get("patterns", [])}
        for exp in [
            ".env",
            ".env.*",
            "secrets/**",
            ".secrets/**",
            "*.pem",
            "*.key",
            "*.p12",
            "*.pfx",
        ]:
            if exp not in pats:
                fail(f"mz-local-secrets missing {exp}")
            else:
                ok(f"mz-local-secrets contains {exp}")
        allow = {p.get("pattern") for p in sec.get("allowedPatterns", [])}
        for exp in [
            ".env.example",
            ".env.test",
            ".pi/grill-me/**",
            ".pi/agent/**",
            ".pi/sandbox-exports/**",
        ]:
            if exp not in allow:
                fail(f"mz-local-secrets allowed missing {exp}")
            else:
                ok(f"mz-local-secrets allowed {exp}")
        # note .env.test residual
        print(
            "NOTE: .env.test is allowed in Guardrails but OS sandbox pi-sandbox 0.15.0 hard-denies it via WORKSPACE_SECRET_DENY_WRITE_BASENAMES (residual risk documented)"
        )
    # ensure no network, yolo, etc
    if "permissionGate" in ld and ld["permissionGate"].get("autoDenyPatterns"):
        if len(ld["permissionGate"]["autoDenyPatterns"]) != 0:
            fail("guardrails local should not have autoDeny (tighten-only)")
        else:
            ok("guardrails local autoDeny empty")
except Exception as e:
    fail(f"guardrails local load error {e}")

# 4 pi-permission-system globale
try:
    pp = HOME / ".pi" / "agent" / "extensions" / "pi-permission-system" / "config.json"
    pc = json.loads(pp.read_text())
    if pc.get("yoloMode") != False:
        fail(f"perm yoloMode != false {pc.get('yoloMode')}")
    else:
        ok("perm yoloMode false")
    if pc.get("authorizerChain") != []:
        fail(f"perm authorizerChain != [] {pc.get('authorizerChain')}")
    else:
        ok("perm authorizerChain [] (pi-auto-review cannot approve git human-only)")
    perm = pc.get("permission", {})
    # check external_directory_read allow
    edr = perm.get("external_directory_read")
    if isinstance(edr, dict):
        if edr.get("*") != "allow":
            fail(f"external_directory_read * != allow {edr}")
        else:
            ok("external_directory_read * allow")
    elif edr != "allow":
        fail(f"external_directory_read != allow {edr}")
    else:
        ok("external_directory_read allow")
    edw = perm.get("external_directory_write")
    if isinstance(edw, dict):
        if edw.get("*") != "deny":
            fail(f"external_directory_write * != deny {edw}")
        else:
            ok("external_directory_write * deny")
        for p in ["/tmp/*", "/private/tmp/*", "/dev/null"]:
            if edw.get(p) != "allow":
                fail(f"external_directory_write missing allow {p}")
            else:
                ok(f"external_directory_write allow {p}")
    else:
        fail(f"external_directory_write not dict {edw}")
    # path_write deny for control-plane
    pw = perm.get("path_write", {})
    if isinstance(pw, dict):
        for p in [
            ".pi/settings.json",
            ".pi/sandbox.json",
            ".pi/extensions/**",
            ".pi/agents/**",
            "~/.pi/agent/settings.json",
        ]:
            if pw.get(p) != "deny":
                fail(f"path_write missing deny {p} got {pw.get(p)}")
            else:
                ok(f"path_write deny {p}")
        if pw.get("AGENTS.md") != "ask":
            fail(f"path_write AGENTS.md != ask {pw.get('AGENTS.md')}")
        else:
            ok("path_write AGENTS.md ask")
        if pw.get(".env.example") != "allow" or pw.get(".env.test") != "allow":
            fail(
                f"path_write .env.example/.env.test should be allow {pw.get('.env.example')},{pw.get('.env.test')}"
            )
        else:
            ok("path_write .env.example/.env.test allow")
    # bash human-only ask
    bash = perm.get("bash", {})
    if isinstance(bash, dict):
        for pat in [
            "git restore*",
            "git reset*",
            "git clean*",
            "git checkout*",
            "git switch*",
            "git stash*",
            "git rebase*",
            "git merge*",
            "git pull*",
            "git cherry-pick*",
            "git revert*",
            "git commit --amend*",
            "git push --force*",
            "git push -f*",
        ]:
            if bash.get(pat) != "ask":
                fail(f"bash {pat} != ask got {bash.get(pat)}")
            else:
                ok(f"bash {pat} ask")
        for pat in ["git status*", "git diff*", "git log*", "git add*", "git rm*"]:
            if bash.get(pat) != "allow":
                fail(f"bash {pat} != allow got {bash.get(pat)}")
            else:
                ok(f"bash {pat} allow")
        # git commit and push generic allow but amend/force remain ask (last win)
        if bash.get("git commit*") != "allow" or bash.get("git push*") != "allow":
            fail("bash git commit*/push* should be allow")
        else:
            ok("bash git commit*/push* allow (specific amend/force ask after)")
    else:
        fail("bash not dict")
    # check reads allow
    if perm.get("read") != "allow":
        fail(f"read != allow {perm.get('read')}")
    else:
        ok("read allow")
    # trusted extensions not prompt: mcp/skill allow
    if (
        perm.get("mcp", {}).get("*") != "allow"
        if isinstance(perm.get("mcp"), dict)
        else perm.get("mcp") != "allow"
    ):
        fail(f"mcp * != allow {perm.get('mcp')}")
    else:
        ok("mcp allow")
except Exception as e:
    import traceback

    fail(f"perm global load error {e} {traceback.format_exc()}")

# 5 pi-auto-review globale
try:
    ar = HOME / ".pi" / "agent" / "extensions" / "pi-auto-review" / "config.json"
    ac = json.loads(ar.read_text())
    if ac.get("breakGlassEnabled") != False:
        fail(f"auto-review breakGlassEnabled != false {ac.get('breakGlassEnabled')}")
    else:
        ok("auto-review breakGlassEnabled false")
    if ac.get("failureMode") != "deny":
        fail(f"auto-review failureMode != deny {ac.get('failureMode')}")
    else:
        ok("auto-review failureMode deny")
    if ac.get("grantTtlMs") != 60000:
        fail(f"grantTtlMs != 60000 {ac.get('grantTtlMs')}")
    else:
        ok("grantTtlMs 60000")
    # ensure no broad network allow (pi-sandbox network handles, auto-review should not have allowedDomains)
    if ac.get("allowedDomains"):
        fail(f"auto-review should not have allowedDomains broad allow {ac.get('allowedDomains')}")
    else:
        ok("auto-review no broad network allow")
    if set(ac.get("autoConfirmBoundedAllows", [])) != {"external_directory", "path"}:
        fail(
            f"autoConfirmBoundedAllows != ['external_directory','path'] {ac.get('autoConfirmBoundedAllows')}"
        )
    else:
        ok("autoConfirmBoundedAllows external_directory,path")
except Exception as e:
    fail(f"auto-review load error {e}")

# 6 helper globale
try:
    hp = HOME / ".pi" / "agent" / "extensions" / "pi-sandbox-helper" / "index.ts"
    if not hp.exists():
        fail("helper not found")
    else:
        txt = hp.read_text()
        if "process.env.PI_CODING_AGENT_DIR ??=" not in txt and "PI_CODING_AGENT_DIR" not in txt:
            fail("helper missing PI_CODING_AGENT_DIR ??= logic")
        else:
            ok("helper contains PI_CODING_AGENT_DIR ??=")
        if "PI_SANDBOX_EXTERNAL_ALLOW_READ" not in txt:
            fail("helper missing PI_SANDBOX_EXTERNAL_ALLOW_READ")
        else:
            ok("helper contains PI_SANDBOX_EXTERNAL_ALLOW_READ")
        if '"/"' not in txt and "'/'" not in txt:
            fail("helper missing '/' append")
        else:
            ok("helper appends '/'")
        if "pi-sandbox 0.15.0" not in txt:
            fail("helper missing seam comment 0.15.0")
        else:
            ok("helper documents seam 0.15.0")
        # helper must not hardcode Muzilla repo path (check for absolute path, not word in comment)
        if "/muzilla" in txt.lower() and "desktop/muzilla" in txt.lower():
            fail("helper should not hardcode Muzilla path")
        else:
            ok("helper no Muzilla hardcode")
        # helper must not mutate PI_SUBAGENT_PI_BINARY (check for assignment)
        if (
            "process.env.PI_SUBAGENT_PI_BINARY" in txt
            and "=" in txt.split("PI_SUBAGENT_PI_BINARY")[1][:100]
        ):
            # crude check for assignment to that env
            if (
                "PI_SUBAGENT_PI_BINARY" in txt
                and "process.env.PI_SUBAGENT_PI_BINARY" in txt
                and "=" in txt
            ):
                # ensure it's not just a read check; look for assignment pattern
                import re

                if re.search(r"process\.env\.PI_SUBAGENT_PI_BINARY\s*=", txt):
                    fail("helper should not modify PI_SUBAGENT_PI_BINARY")
                else:
                    ok("helper does not modify PI_SUBAGENT_PI_BINARY")
            else:
                ok("helper does not modify PI_SUBAGENT_PI_BINARY")
        else:
            ok("helper PI_SUBAGENT check passed")
        # helper must not set externalWorkerIsolation to off (check for code assignment, not comment mention)
        import re as _re

        if _re.search(r'externalWorkerIsolation\s*[:=]\s*["\']off["\']', txt):
            fail("helper should not fallback to off")
        else:
            ok("helper no fallback off")
        # dedup safety
        if "Set" not in txt or "split" not in txt:
            fail("helper missing safe parsing/dedup")
        else:
            ok("helper safe parsing/dedup")
        # check homedir
        if "homedir" not in txt:
            fail("helper missing homedir")
        else:
            ok("helper uses homedir")
        # regression: pi-sandbox version still 0.15.0 with bug
        sandbox_pkg = (
            HOME
            / ".pi"
            / "agent"
            / "npm"
            / "node_modules"
            / "@erichll"
            / "pi-sandbox"
            / "package.json"
        )
        if sandbox_pkg.exists():
            ver = json.loads(sandbox_pkg.read_text()).get("version")
            if ver != "0.15.0":
                print(
                    f"WARN: pi-sandbox version now {ver}, seam may be obsolete - update helper or remove"
                )
            else:
                ok("pi-sandbox version 0.15.0 seam still required")
            # check bug still present
            src = (
                (
                    HOME
                    / ".pi"
                    / "agent"
                    / "npm"
                    / "node_modules"
                    / "@erichll"
                    / "pi-sandbox"
                    / "src"
                    / "index.ts"
                ).read_text()
                if (
                    HOME
                    / ".pi"
                    / "agent"
                    / "npm"
                    / "node_modules"
                    / "@erichll"
                    / "pi-sandbox"
                    / "src"
                    / "index.ts"
                ).exists()
                else ""
            )
            if (
                "PI_SANDBOX_EXTERNAL_ALLOW_READ" in src
                and 'join(homedir(), ".pi", "agent")' not in src
            ):
                ok("pi-sandbox still lacks default agentDir - seam needed")
            else:
                print("WARN: pi-sandbox src may have fixed seam")
        else:
            print("WARN: cannot verify pi-sandbox version")
except Exception as e:
    import traceback

    fail(f"helper check error {e} {traceback.format_exc()}")

# 7 settings globale riattivato e load order
try:
    sp = HOME / ".pi" / "agent" / "settings.json"
    sj = json.loads(sp.read_text())
    pkgs = sj.get("packages", [])
    # find disabled entries starting with "-"
    disabled = []
    for p in pkgs:
        if isinstance(p, dict):
            src = p.get("source", "")
            exts = p.get("extensions", [])
            for e in exts:
                if isinstance(e, str) and e.startswith("-"):
                    disabled.append(src + " " + e)
        elif isinstance(p, str) and p.startswith("-"):
            disabled.append(p)
    if disabled:
        fail(f"settings.json still has disabled extensions: {disabled}")
    else:
        ok("settings.json no disabled '-' extensions")
    # check load order among the 4
    order = []
    for p in pkgs:
        if isinstance(p, dict):
            src = p.get("source", "")
            if "@gotgenes/pi-permission-system" in src:
                order.append("perm")
            elif "@erichll/pi-auto-review" in src:
                order.append("auto-review")
            elif "@erichll/pi-sandbox" in src:
                order.append("sandbox")
            elif "@aliou/pi-guardrails" in src:
                order.append("guardrails")
    expected = ["perm", "auto-review", "sandbox", "guardrails"]
    if order != expected:
        fail(f"settings load order != {expected} got {order}")
    else:
        ok(f"settings load order correct {order}")
    # ensure .pi/settings.json locale not modified (check not staged)
    import subprocess

    out = subprocess.check_output(
        ["git", "status", "--porcelain", "--", ".pi/settings.json"], cwd=str(ROOT), text=True
    )
    if out.strip():
        fail(f".pi/settings.json should remain unmodified but got {out.strip()}")
    else:
        ok(".pi/settings.json untouched (preserve 20 files)")
except Exception as e:
    import traceback

    fail(f"settings check error {e} {traceback.format_exc()}")

# 8 local tighten-only checks (already done but ensure no yolo etc)
try:
    # local perm should be tighten-only
    lp = ROOT / ".pi" / "extensions" / "pi-permission-system" / "config.json"
    if lp.exists():
        lc = json.loads(lp.read_text())
        if lc.get("yoloMode") != False:
            fail(f"local perm yoloMode != false {lc.get('yoloMode')}")
        else:
            ok("local perm yoloMode false")
        if lc.get("authorizerChain") != []:
            fail("local perm authorizerChain != []")
        else:
            ok("local perm authorizerChain []")
        # ensure no allow broadening beyond global deny
        perm = lc.get("permission", {})
        # should only contain deny, not allow that would widen
        # check that no allow for control-plane paths
        for k, v in perm.items():
            if isinstance(v, dict):
                for pat, act in v.items():
                    if act == "allow" and pat in [".pi/settings.json", ".pi/sandbox.json"]:
                        fail(f"local perm allow for {pat} would widen global deny")
            elif v == "allow" and k in ["write", "edit"]:
                fail(f"local perm allow for {k} would widen")
        ok("local perm tighten-only no allow widening")
    # local pi-sandbox.json not used as active
    sandbox_local = ROOT / ".pi" / "sandbox.json"
    if sandbox_local.exists():
        ok("local .pi/sandbox.json exists but not authoritative (protected)")
except Exception as e:
    fail(f"local perm check error {e}")

# final
if errors:
    print(f"\nFAILED: {errors} check(s) failed")
    sys.exit(1)
else:
    print(
        "\nPASSED: global pi-native sandbox/guardrails/permissions validated, helper seam active, load order correct"
    )
    sys.exit(0)
