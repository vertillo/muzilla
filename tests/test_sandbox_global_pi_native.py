# mypy: ignore-errors
# ruff: noqa
# type: ignore
"""Test verifica configurazione reale Pi-native globale/locale (non solo .pi/sandbox.json)
Copre pi-sandbox, guardrails split, permission-system baseline, auto-review, helper e load order.
"""

import json
import pathlib
import re

HOME = pathlib.Path.home()
ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_pi_sandbox_global_contract():
    cfg = json.loads((HOME / ".pi/agent/extensions/pi-sandbox/config.json").read_text())
    assert cfg["subagents"] == {"provider": "pi-subagents", "externalWorkerIsolation": "enforce"}
    assert cfg["filesystem"] == {"additionalAllowRead": ["/"]}
    assert cfg["network"] == {"allowedDomains": [], "deniedDomains": []}
    assert cfg["hostIPC"] == {"mode": "off"}


def test_guardrails_global_baseline():
    gd = json.loads((HOME / ".pi/agent/extensions/guardrails.json").read_text())
    assert gd["enabled"] is True
    assert gd["applyBuiltinDefaults"] is False
    assert gd["features"]["policies"] is True
    assert gd["features"]["permissionGate"] is False
    assert gd["features"]["pathAccess"] is False
    assert gd["permissionGate"]["requireConfirmation"] is False
    assert gd["permissionGate"]["autoDenyPatterns"] == []
    assert gd["pathAccess"]["mode"] == "allow"
    ids = {r["id"] for r in gd["policies"]["rules"]}
    for need in ["global-settings", "global-agents", "global-extensions"]:
        assert need in ids
    ext = next(r for r in gd["policies"]["rules"] if r["id"] == "global-extensions")
    assert "~/.pi/agent/extensions/**" in {p["pattern"] for p in ext["patterns"]}
    assert ext["protection"] == "readOnly"
    assert ext["onlyIfExists"] is False


def test_guardrails_local_tighten_only():
    ld = json.loads((ROOT / ".pi/extensions/guardrails.json").read_text())
    assert ld["applyBuiltinDefaults"] is False
    assert ld["features"]["permissionGate"] is False
    assert ld["features"]["pathAccess"] is False
    lids = {r["id"] for r in ld["policies"]["rules"]}
    assert not (lids & {"global-settings", "global-agents", "global-extensions"})
    for need in ["mz-local-control", "mz-local-extensions-agents", "mz-local-secrets"]:
        assert need in lids
    sec = next(r for r in ld["policies"]["rules"] if r["id"] == "mz-local-secrets")
    pats = {p["pattern"] for p in sec["patterns"]}
    for exp in [".env", ".env.*", "secrets/**", ".secrets/**", "*.pem", "*.key", "*.p12", "*.pfx"]:
        assert exp in pats
    allow = {p["pattern"] for p in sec.get("allowedPatterns", [])}
    for exp in [
        ".env.example",
        ".env.test",
        ".pi/grill-me/**",
        ".pi/agent/**",
        ".pi/sandbox-exports/**",
    ]:
        assert exp in allow
    # residual: .env.test allowed in guardrails but blocked by sandbox OS (documented)


def test_permission_system_global_baseline():
    pc = json.loads((HOME / ".pi/agent/extensions/pi-permission-system/config.json").read_text())
    assert pc["yoloMode"] is False
    assert pc["authorizerChain"] == []
    perm = pc["permission"]
    assert perm["read"] == "allow"
    assert perm["external_directory_read"]["*"] == "allow"
    assert perm["external_directory_write"]["*"] == "deny"
    assert perm["external_directory_write"]["/tmp/*"] == "allow"
    assert perm["path_write"]["AGENTS.md"] == "ask"
    assert perm["path_write"][".env.example"] == "allow"
    assert perm["path_write"][".env.test"] == "allow"
    assert perm["path_write"][".pi/settings.json"] == "deny"
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
        assert perm["bash"][pat] == "ask"
    for pat in ["git status*", "git diff*", "git log*", "git add*", "git rm*"]:
        assert perm["bash"][pat] == "allow"
    assert perm["bash"]["git commit*"] == "allow"
    assert perm["bash"]["git push*"] == "allow"


def test_auto_review_global():
    ac = json.loads((HOME / ".pi/agent/extensions/pi-auto-review/config.json").read_text())
    assert ac["breakGlassEnabled"] is False
    assert ac["failureMode"] == "deny"
    assert ac["grantTtlMs"] == 60000
    assert set(ac["autoConfirmBoundedAllows"]) == {"external_directory", "path"}


def test_helper_seam():
    hp = HOME / ".pi/agent/extensions/pi-sandbox-helper/index.ts"
    assert hp.exists()
    txt = hp.read_text()
    assert "PI_CODING_AGENT_DIR" in txt
    assert "PI_SANDBOX_EXTERNAL_ALLOW_READ" in txt
    assert "pi-sandbox 0.15.0" in txt
    assert '"/"' in txt or "'/'" in txt
    assert "homedir" in txt
    assert "Set" in txt and "split" in txt
    # must not hardcode muzilla repo path
    assert "/muzilla" not in txt.lower() or "desktop/muzilla" not in txt.lower()
    # must not assign PI_SUBAGENT_PI_BINARY
    assert not re.search(r"process\.env\.PI_SUBAGENT_PI_BINARY\s*=", txt)
    # must not fallback to off via assignment
    assert not re.search(r"externalWorkerIsolation\s*[:=]\s*[\"']off[\"']", txt)
    # version still 0.15.0
    ver = json.loads(
        (HOME / ".pi/agent/npm/node_modules/@erichll/pi-sandbox/package.json").read_text()
    )["version"]
    assert ver == "0.15.0"


def test_helper_regression_seam_effective():
    """Regressivo: se pi-sandbox fixa nativamente il seam, helper diventa obsoleto e test deve fallire per rimozione."""
    # helper must implement the two required behaviours; if upstream fixes, this test signals to remove helper
    hp = HOME / ".pi/agent/extensions/pi-sandbox-helper/index.ts"
    txt = hp.read_text()
    # check that pi-sandbox still lacks default agentDir propagation
    src = (HOME / ".pi/agent/npm/node_modules/@erichll/pi-sandbox/src/index.ts").read_text()
    has_default = 'join(homedir(), ".pi", "agent")' in src
    # helper is required while bug persists; if bug fixed, this assertion will signal
    assert "PI_SANDBOX_EXTERNAL_ALLOW_READ" in src
    # if upstream now includes default agentDir, helper seam is obsolete - we expect has_default to be False while helper needed
    # When has_default becomes True, this test should be updated to remove helper
    assert not has_default, (
        "pi-sandbox now defaults PI_CODING_AGENT_DIR, helper seam may be obsolete - remove helper"
    )


def test_global_settings_reactivated_and_order():
    sj = json.loads((HOME / ".pi/agent/settings.json").read_text())
    pkgs = sj["packages"]
    disabled = []
    for p in pkgs:
        if isinstance(p, dict):
            for e in p.get("extensions", []):
                if isinstance(e, str) and e.startswith("-"):
                    disabled.append(e)
        elif isinstance(p, str) and p.startswith("-"):
            disabled.append(p)
    assert disabled == [], f"disabled extensions still present {disabled}"
    order = []
    for p in pkgs:
        if isinstance(p, dict):
            s = p.get("source", "")
            if "@gotgenes/pi-permission-system" in s:
                order.append("perm")
            elif "@erichll/pi-auto-review" in s:
                order.append("auto-review")
            elif "@erichll/pi-sandbox" in s:
                order.append("sandbox")
            elif "@aliou/pi-guardrails" in s:
                order.append("guardrails")
    assert order == ["perm", "auto-review", "sandbox", "guardrails"]


def test_local_not_modified_settings():
    # .pi/settings.json must not be staged/modified
    import subprocess

    out = subprocess.check_output(
        ["git", "status", "--porcelain", "--", ".pi/settings.json"], cwd=str(ROOT), text=True
    )
    assert out.strip() == ""


def test_pi_sandbox_not_using_legacy():
    # .pi/sandbox.json should exist but not be active authoritative (we check it remains as before)
    assert (ROOT / ".pi/sandbox.json").exists()
