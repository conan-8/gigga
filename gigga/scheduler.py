#!/usr/bin/env python3
"""GIGGA scheduler — plain-code pipeline state machine.

Serial pipeline. One task at a time. All state in a directory on disk.
Append-only journal (JSONL) so it resumes after a crash.

Usage:
    scheduler.py start      --repo <path> --request-file <f> [--fastrack] [--dir <d>] [--allow-dirty] [--checks <file>]
    scheduler.py init       <state_dir> <request_file> --repo <path> [--fastrack] [--allow-dirty] [--checks <file>]
    scheduler.py next       <state_dir> [--brief]
    scheduler.py record     <state_dir> <event_json_or_file> [--brief]
    scheduler.py status     <state_dir>
    scheduler.py amend      <state_dir> <amendment_json_file>
    scheduler.py revive     <state_dir> <to_phase>
    scheduler.py mergecheck <state_dir> [--apply]
    scheduler.py worktree   <state_dir> create <part_id>
    scheduler.py worktree   <state_dir> list
    scheduler.py worktree   <state_dir> remove <part_id>
    scheduler.py worktree   <state_dir> remove-all
    scheduler.py apply      <state_dir>
    scheduler.py diff       <state_dir>
    scheduler.py rollback   <state_dir>
    scheduler.py check      <state_dir> --part <id> [--scope changed|full]
    scheduler.py check      <state_dir> --merged [--scope full]

start creates the run dir, writes request, inits, probes, and prints first next-state.
record accepts inline JSON or a file path holding one event object OR an array of events.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ATTEMPT_CEILING = 4
AMENDMENT_CAP = 3
NO_PROGRESS_WINDOW = 6
CHECK_TIMEOUT_DEFAULT = 600
CHECK_OUTPUT_CAP = 4096

PHASES = [
    "FASTTRACK",
    "SPEC_DRAFT",
    "SPEC_ATTACK",
    "SPEC_RECONCILE",
    "TASK_PLAN",
    "TASK_BUILD",
    "TASK_MERGE",
    "JUDGE_FIDELITY",
    "APPLY",
    "DONE",
    "HALT",
    "QUARANTINE",
]

ESCALATION_LEVELS = ["initial", "retry", "rewrite", "hard", "quarantine"]

AGENTS = {
    "FASTTRACK": "gigga-builder",
    "SPEC_DRAFT": "gigga-spec",
    "SPEC_ATTACK": None,
    "SPEC_RECONCILE": "gigga-spec",
    "TASK_PLAN": None,
    "TASK_BUILD": "gigga-builder",
    "TASK_MERGE": None,
    "JUDGE_FIDELITY": "gigga-judge-fidelity",
    "APPLY": None,
}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def journal_path(state_dir):
    return Path(state_dir) / "journal.jsonl"


def state_file(state_dir):
    return Path(state_dir) / "state.json"


def append_journal(state_dir, event):
    event["ts"] = now_iso()
    event["seq"] = next_seq(state_dir)
    jp = journal_path(state_dir)
    jp.parent.mkdir(parents=True, exist_ok=True)
    with open(jp, "a") as f:
        f.write(json.dumps(event, separators=(",", ":")) + "\n")


def next_seq(state_dir):
    jp = journal_path(state_dir)
    if not jp.exists():
        return 0
    count = 0
    with open(jp) as f:
        for _ in f:
            count += 1
    return count


def load_state(state_dir):
    sf = state_file(state_dir)
    if not sf.exists():
        return None
    with open(sf) as f:
        return json.load(f)


def save_state(state_dir, state):
    sf = state_file(state_dir)
    sf.parent.mkdir(parents=True, exist_ok=True)
    tmp = sf.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, sf)


def replay_journal(state_dir):
    """Rebuild state from journal after crash."""
    jp = journal_path(state_dir)
    if not jp.exists():
        return None
    state = None
    with open(jp) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            state = apply_event(state, event)
    if state:
        save_state(state_dir, state)
    return state


def apply_event(state, event):
    etype = event.get("type")

    if etype == "init":
        return {
            "run_id": event["run_id"],
            "request": event["request"],
            "phase": "FASTTRACK" if event.get("fastrack") else "SPEC_DRAFT",
            "fastrack": bool(event.get("fastrack")),
            "task_index": 0,
            "tasks": [],
            "amendments": [],
            "escalation": "initial",
            "attempts": 0,
            "no_progress_count": 0,
            "last_progress_seq": 0,
            "created": event["ts"],
            "repo_path": event.get("repo_path"),
            "baseline_sha": event.get("baseline_sha"),
            "baseline_branch": event.get("baseline_branch"),
            "dirty": event.get("dirty"),
            "worktrees": {},
            "checks": event.get("checks", {}),
        }

    if state is None:
        return state

    if etype == "phase_advance":
        state["phase"] = event["to_phase"]
        state["no_progress_count"] = 0
        state["last_progress_seq"] = event["seq"]
        if event.get("reset_escalation"):
            state["escalation"] = "initial"
            state["attempts"] = 0
        return state

    if etype == "task_plan":
        state["tasks"] = event["tasks"]
        state["task_index"] = 0
        return state

    if etype == "amendment":
        state["amendments"].append({
            "id": event.get("id", str(uuid.uuid4())[:8]),
            "clause": event.get("clause"),
            "text": event.get("text"),
            "seq": event["seq"],
        })
        return state

    if etype == "escalation":
        state["escalation"] = event["level"]
        state["attempts"] = event.get("attempts", state["attempts"] + 1)
        return state

    if etype == "failure":
        state["attempts"] += 1
        state["no_progress_count"] += 1
        return state

    if etype == "progress":
        state["no_progress_count"] = 0
        state["last_progress_seq"] = event["seq"]
        return state

    if etype == "task_advance":
        state["task_index"] = event["task_index"]
        state["escalation"] = "initial"
        state["attempts"] = 0
        return state

    if etype == "revive":
        state["phase"] = event["to_phase"]
        state["escalation"] = "initial"
        state["attempts"] = 0
        state["no_progress_count"] = 0
        state.pop("halt_reason", None)
        return state

    if etype == "halt":
        state["phase"] = "HALT"
        state["halt_reason"] = event.get("reason", "unknown")
        return state

    if etype == "quarantine":
        state["phase"] = "QUARANTINE"
        return state

    if etype == "done":
        state["phase"] = "DONE"
        return state

    if etype == "worktree":
        state.setdefault("worktrees", {})
        state["worktrees"][event["part_id"]] = {
            "path": event["path"],
            "branch": event["branch"],
        }
        return state

    if etype == "worktree_remove":
        state.get("worktrees", {}).pop(event["part_id"], None)
        return state

    if etype == "worktree_remove_all":
        state["worktrees"] = {}
        return state

    if etype == "check_result":
        state.setdefault("check_results", {})
        key = event.get("part_id") or "__merged__"
        state["check_results"][key] = {
            "passed": event["passed"],
            "results": event["results"],
            "seq": event["seq"],
        }
        return state

    return state


def check_halt_conditions(state):
    if state["attempts"] >= ATTEMPT_CEILING:
        return f"attempt_ceiling: {state['attempts']} attempts reached (max {ATTEMPT_CEILING})"
    if len(state["amendments"]) > AMENDMENT_CAP:
        return f"amendment_cap: {len(state['amendments'])} amendments filed (max {AMENDMENT_CAP})"
    if state["no_progress_count"] >= NO_PROGRESS_WINDOW:
        return f"no_progress_window: {state['no_progress_count']} events without progress (max {NO_PROGRESS_WINDOW})"
    return None


def escalate(state):
    idx = ESCALATION_LEVELS.index(state["escalation"])
    if idx + 1 < len(ESCALATION_LEVELS):
        return ESCALATION_LEVELS[idx + 1]
    return "quarantine"


def _git(repo_path, *args, check=True):
    result = subprocess.run(
        ["git", "-C", str(repo_path)] + list(args),
        capture_output=True, text=True, cwd=str(repo_path),
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def _detect_checks(repo_path):
    rp = Path(repo_path)
    checks = {"typecheck": [], "lint": [], "unit": [], "e2e": [], "extra": []}

    pkg_json = rp / "package.json"
    scripts = {}
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            scripts = pkg.get("scripts", {})
        except (json.JSONDecodeError, OSError):
            pass

    if (rp / "tsconfig.json").exists():
        checks["typecheck"] = ["npx", "tsc", "--noEmit"]

    if "lint" in scripts:
        checks["lint"] = ["npm", "run", "lint"]
    elif (rp / ".eslintrc.json").exists() or (rp / ".eslintrc.js").exists() or (rp / "eslint.config.js").exists() or (rp / "eslint.config.mjs").exists():
        checks["lint"] = ["npx", "eslint", "--max-warnings=0"]

    if (rp / "vitest.config.ts").exists() or (rp / "vitest.config.js").exists() or (rp / "vitest.config.mts").exists():
        checks["unit"] = ["npx", "vitest", "run"]
    elif (rp / "jest.config.js").exists() or (rp / "jest.config.ts").exists() or "jest" in scripts:
        checks["unit"] = ["npx", "jest"]
    elif "test" in scripts:
        checks["unit"] = ["npm", "test"]

    if (rp / "playwright.config.ts").exists() or (rp / "playwright.config.js").exists():
        checks["e2e"] = ["npx", "playwright", "test"]

    if not any(checks[k] for k in ("typecheck", "lint", "unit", "e2e")):
        if (rp / "pyproject.toml").exists() or (rp / "pytest.ini").exists() or (rp / "setup.cfg").exists():
            checks["unit"] = ["python", "-m", "pytest"]
            if (rp / "pyproject.toml").exists():
                try:
                    content = (rp / "pyproject.toml").read_text()
                    if "mypy" in content or (rp / "mypy.ini").exists():
                        checks["typecheck"] = ["python", "-m", "mypy"]
                    if "ruff" in content or (rp / "ruff.toml").exists() or (rp / ".ruff.toml").exists():
                        checks["lint"] = ["python", "-m", "ruff", "check"]
                except OSError:
                    pass
        elif (rp / "go.mod").exists():
            checks["typecheck"] = ["go", "vet", "./..."]
            checks["unit"] = ["go", "test", "./..."]
        elif (rp / "Cargo.toml").exists():
            checks["typecheck"] = ["cargo", "check"]
            checks["unit"] = ["cargo", "test"]

    checks["timeout"] = CHECK_TIMEOUT_DEFAULT
    return checks


def cmd_init(state_dir, request_file, repo, fastrack=False, allow_dirty=False, checks_file=None):
    sd = Path(state_dir)
    if (sd / "journal.jsonl").exists():
        print(json.dumps({"error": "state_dir already initialized", "state_dir": str(sd)}))
        sys.exit(1)

    repo_path = Path(repo).resolve()
    if not (repo_path / ".git").exists():
        print(json.dumps({"error": f"not a git repository: {repo_path}", "hint": "path must contain a .git directory or file"}))
        sys.exit(1)

    try:
        baseline_sha = _git(repo_path, "rev-parse", "HEAD").stdout.strip()
        baseline_branch = _git(repo_path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        status_out = _git(repo_path, "status", "--porcelain").stdout.strip()
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    dirty = len(status_out) > 0
    if dirty and not allow_dirty:
        print(json.dumps({
            "error": "repository has uncommitted changes",
            "hint": "commit or stash changes before init, or pass --allow-dirty",
            "dirty_files": status_out.splitlines()[:10],
        }))
        sys.exit(1)

    if checks_file:
        checks = json.loads(Path(checks_file).read_text())
        checks.setdefault("timeout", CHECK_TIMEOUT_DEFAULT)
    else:
        checks = _detect_checks(repo_path)

    request = Path(request_file).read_text().strip()
    run_id = str(uuid.uuid4())[:12]

    sd.mkdir(parents=True, exist_ok=True)
    (sd / "spec").mkdir(exist_ok=True)
    (sd / "tasks").mkdir(exist_ok=True)
    (sd / "artifacts").mkdir(exist_ok=True)

    (sd / "checks.json").write_text(json.dumps(checks, indent=2))

    append_journal(state_dir, {
        "type": "init",
        "run_id": run_id,
        "request": request,
        "fastrack": fastrack,
        "repo_path": str(repo_path),
        "baseline_sha": baseline_sha,
        "baseline_branch": baseline_branch,
        "dirty": dirty,
        "checks": checks,
    })

    state = replay_journal(state_dir)

    result = {"ok": True, "run_id": run_id, "phase": state["phase"], "fastrack": fastrack, "state_dir": str(sd),
              "repo_path": str(repo_path), "baseline_sha": baseline_sha, "baseline_branch": baseline_branch, "dirty": dirty}

    if not any(checks.get(k) for k in ("typecheck", "lint", "unit", "e2e", "extra")):
        result["warning"] = "no checks detected — run has no objective gate. provide --checks <file>"

    print(json.dumps(result, indent=2))


def build_next_result(state, brief=False):
    phase = state["phase"]
    agent = AGENTS.get(phase)
    escalation = state["escalation"]

    task_id = None
    task_info = None
    if phase.startswith("TASK_") and state["tasks"]:
        idx = min(state["task_index"], len(state["tasks"]) - 1)
        task_id = state["tasks"][idx].get("id", f"task-{idx}")
        task_info = state["tasks"][idx]

    if brief:
        result = {
            "phase": phase,
            "agent": agent,
            "task_id": task_id,
            "escalation": escalation,
            "attempts": state["attempts"],
            "repo_path": state.get("repo_path"),
            "baseline_sha": state.get("baseline_sha"),
        }
        if phase == "HALT":
            result["halt_reason"] = state.get("halt_reason", "unknown")
        return result

    result = {
        "phase": phase,
        "agent": agent,
        "escalation": escalation,
        "task_id": task_id,
        "task_info": task_info,
        "task_index": state["task_index"],
        "total_tasks": len(state["tasks"]),
        "attempts": state["attempts"],
        "amendments_filed": len(state["amendments"]),
        "run_id": state["run_id"],
        "request": state["request"],
        "fastrack": state.get("fastrack", False),
        "repo_path": state.get("repo_path"),
        "baseline_sha": state.get("baseline_sha"),
        "baseline_branch": state.get("baseline_branch"),
        "dirty": state.get("dirty"),
        "worktrees": state.get("worktrees", {}),
    }

    if phase == "HALT":
        result["halt_reason"] = state.get("halt_reason", "unknown")
        result["autopsy"] = build_autopsy(state)

    return result


def cmd_next(state_dir, brief=False):
    state = load_state(state_dir)
    if state is None:
        state = replay_journal(state_dir)
    if state is None:
        print(json.dumps({"error": "no state found; run init first"}))
        sys.exit(1)

    halt = check_halt_conditions(state)
    if halt and state["phase"] not in ("DONE", "HALT", "QUARANTINE"):
        append_journal(state_dir, {"type": "halt", "reason": halt})
        state = replay_journal(state_dir)

    print(json.dumps(build_next_result(state, brief=brief), indent=2))


def build_autopsy(state):
    return {
        "run_id": state["run_id"],
        "request": state["request"],
        "phase_at_halt": state["phase"],
        "halt_reason": state.get("halt_reason"),
        "attempts": state["attempts"],
        "escalation": state["escalation"],
        "amendments": state["amendments"],
        "tasks": state["tasks"],
        "task_index": state["task_index"],
        "no_progress_count": state["no_progress_count"],
        "fastrack": state.get("fastrack", False),
    }


def cmd_start(repo, request_file, fastrack=False, run_dir=None, allow_dirty=False, checks_file=None):
    if run_dir:
        sd = Path(run_dir)
    else:
        ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        sd = Path.home() / ".gigga" / f"run-{ts}"

    sd.mkdir(parents=True, exist_ok=True)
    req_dest = sd / "request.txt"
    req_content = Path(request_file).read_text().strip()
    req_dest.write_text(req_content + "\n")

    repo_path = Path(repo).resolve()
    if not (repo_path / ".git").exists():
        print(json.dumps({"error": f"not a git repository: {repo_path}", "hint": "path must contain a .git directory or file"}))
        sys.exit(1)

    try:
        baseline_sha = _git(repo_path, "rev-parse", "HEAD").stdout.strip()
        baseline_branch = _git(repo_path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        status_out = _git(repo_path, "status", "--porcelain").stdout.strip()
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    dirty = len(status_out) > 0
    if dirty and not allow_dirty:
        print(json.dumps({
            "error": "repository has uncommitted changes",
            "hint": "commit or stash changes before init, or pass --allow-dirty",
            "dirty_files": status_out.splitlines()[:10],
        }))
        sys.exit(1)

    if checks_file:
        checks = json.loads(Path(checks_file).read_text())
        checks.setdefault("timeout", CHECK_TIMEOUT_DEFAULT)
    else:
        checks = _detect_checks(repo_path)

    run_id = str(uuid.uuid4())[:12]
    (sd / "spec").mkdir(exist_ok=True)
    (sd / "tasks").mkdir(exist_ok=True)
    (sd / "artifacts").mkdir(exist_ok=True)
    (sd / "checks.json").write_text(json.dumps(checks, indent=2))

    state_dir = str(sd)
    append_journal(state_dir, {
        "type": "init",
        "run_id": run_id,
        "request": req_content,
        "fastrack": fastrack,
        "repo_path": str(repo_path),
        "baseline_sha": baseline_sha,
        "baseline_branch": baseline_branch,
        "dirty": dirty,
        "checks": checks,
    })

    state = replay_journal(state_dir)

    _run_probe(state_dir, str(repo_path), checks)

    result = {
        "ok": True,
        "state_dir": state_dir,
        "run_id": run_id,
        "phase": state["phase"],
        "agent": AGENTS.get(state["phase"]),
        "fastrack": fastrack,
        "repo_path": str(repo_path),
        "baseline_sha": baseline_sha,
        "baseline_branch": baseline_branch,
        "dirty": dirty,
        "probe": str(sd / "spec" / "probe.md"),
    }

    if not any(checks.get(k) for k in ("typecheck", "lint", "unit", "e2e", "extra")):
        result["warning"] = "no checks detected — run has no objective gate. provide --checks <file>"

    print(json.dumps(result, indent=2))


def _run_probe(state_dir, repo_path, checks):
    rp = Path(repo_path)
    out = []
    cap = 8192

    out.append("# Probe\n")

    log = _git(repo_path, "log", "--oneline", "-20", check=False).stdout.strip()
    out.append(f"## Recent commits\n```\n{log}\n```\n")

    ls_files = _git(repo_path, "ls-files", check=False).stdout.strip().splitlines()
    tree = {}
    for f in ls_files:
        parts = f.split("/")
        key = "/".join(parts[:3]) if len(parts) > 3 else f
        tree[key] = tree.get(key, 0) + 1
    tree_lines = sorted(tree.items())[:60]
    tree_str = "\n".join(f"  {k} ({v} files)" if v > 1 else f"  {k}" for k, v in tree_lines)
    out.append(f"## Tree (depth 3, {len(ls_files)} files total)\n```\n{tree_str}\n```\n")

    for manifest in ("package.json", "pyproject.toml", "go.mod", "Cargo.toml"):
        mp = rp / manifest
        if mp.exists():
            try:
                content = mp.read_text()
                if manifest == "package.json":
                    pkg = json.loads(content)
                    slim = {k: pkg[k] for k in ("name", "scripts", "dependencies", "devDependencies") if k in pkg}
                    content = json.dumps(slim, indent=2)
                out.append(f"## {manifest}\n```\n{content[:2048]}\n```\n")
            except (OSError, json.JSONDecodeError):
                pass

    out.append("## Checks ladder\n```json\n" + json.dumps(checks, indent=2) + "\n```\n")

    baseline_checks = Path(state_dir) / "baseline-checks.json"
    if baseline_checks.exists():
        out.append(f"## Baseline checks\n```json\n{baseline_checks.read_text()[:1024]}\n```\n")

    ext_map = {".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
               ".jsx": "JavaScript", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".java": "Java"}
    loc = {}
    largest = []
    for f in ls_files:
        fp = rp / f
        ext = fp.suffix
        lang = ext_map.get(ext)
        if not lang:
            continue
        try:
            size = fp.stat().st_size
            loc[lang] = loc.get(lang, 0) + size
            largest.append((size, f))
        except OSError:
            pass
    largest.sort(reverse=True)
    loc_str = "\n".join(f"  {k}: ~{v//1024}KB" for k, v in sorted(loc.items(), key=lambda x: -x[1]))
    top_files = "\n".join(f"  {s//1024}KB {f}" for s, f in largest[:20])
    out.append(f"## LOC by language\n{loc_str}\n\n## Top 20 largest files\n{top_files}\n")

    probe_text = "\n".join(out)
    if len(probe_text) > cap:
        probe_text = probe_text[:cap] + "\n\n[truncated]\n"

    (Path(state_dir) / "spec" / "probe.md").write_text(probe_text)


def cmd_record(state_dir, event_arg, brief=False):
    try:
        raw = json.loads(event_arg)
    except (json.JSONDecodeError, ValueError):
        raw = json.loads(Path(event_arg).read_text())
    events = raw if isinstance(raw, list) else [raw]

    state = load_state(state_dir)
    if state is None:
        state = replay_journal(state_dir)
    if state is None:
        print(json.dumps({"error": "no state; run init first"}))
        sys.exit(1)

    for ev in events:
        ev.setdefault("type", "progress")
        append_journal(state_dir, ev)

    state = replay_journal(state_dir)

    halt = check_halt_conditions(state)
    if halt and state["phase"] not in ("DONE", "HALT", "QUARANTINE"):
        append_journal(state_dir, {"type": "halt", "reason": halt})
        state = replay_journal(state_dir)

    result = build_next_result(state, brief=brief)
    result["ok"] = True
    print(json.dumps(result, indent=2))


def cmd_status(state_dir):
    state = load_state(state_dir)
    if state is None:
        state = replay_journal(state_dir)
    if state is None:
        print(json.dumps({"error": "no state found"}))
        sys.exit(1)
    print(json.dumps(state, indent=2))


def cmd_revive(state_dir, to_phase):
    state = load_state(state_dir)
    if state is None:
        state = replay_journal(state_dir)
    if state is None:
        print(json.dumps({"error": "no state; run init first"}))
        sys.exit(1)

    if state["phase"] not in ("HALT", "QUARANTINE"):
        print(json.dumps({"error": "not halted", "phase": state["phase"]}))
        sys.exit(1)

    if to_phase not in PHASES:
        print(json.dumps({"error": f"unknown phase: {to_phase}"}))
        sys.exit(1)

    append_journal(state_dir, {"type": "revive", "to_phase": to_phase})
    state = replay_journal(state_dir)
    print(json.dumps({"ok": True, "phase": state["phase"]}))


def cmd_amend(state_dir, amendment_file):
    state = load_state(state_dir)
    if state is None:
        state = replay_journal(state_dir)
    if state is None:
        print(json.dumps({"error": "no state; run init first"}))
        sys.exit(1)

    if len(state["amendments"]) >= AMENDMENT_CAP:
        print(json.dumps({
            "error": "amendment_cap_reached",
            "filed": len(state["amendments"]),
            "cap": AMENDMENT_CAP,
        }))
        sys.exit(1)

    amendment = json.loads(Path(amendment_file).read_text())
    amendment["type"] = "amendment"
    amendment["id"] = str(uuid.uuid4())[:8]
    append_journal(state_dir, amendment)
    state = replay_journal(state_dir)

    print(json.dumps({
        "ok": True,
        "amendment_id": amendment["id"],
        "total_amendments": len(state["amendments"]),
        "remaining": AMENDMENT_CAP - len(state["amendments"]),
    }))


def cmd_mergecheck(state_dir, apply=False):
    parts_dir = Path(state_dir) / "parts"
    if not parts_dir.exists():
        print(json.dumps({"error": "no parts/ directory"}))
        sys.exit(1)

    part_ids = sorted(
        d.name for d in parts_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )
    if not part_ids:
        print(json.dumps({"error": "no parts found"}))
        sys.exit(1)

    file_map = {}
    conflicts = []
    for pid in part_ids:
        pdir = parts_dir / pid
        for f in pdir.rglob("*"):
            if f.is_file() and not f.name.startswith("."):
                rel = str(f.relative_to(pdir))
                if rel in file_map:
                    conflicts.append({"file": rel, "parts": [file_map[rel], pid]})
                else:
                    file_map[rel] = pid

    for pid in part_ids:
        pdir = parts_dir / pid
        for f in pdir.rglob("*"):
            if f.is_file() and not f.name.startswith("."):
                try:
                    content = f.read_text(errors="ignore")
                except OSError:
                    continue
                for other in part_ids:
                    if other != pid and (f"parts/{other}" in content or f"/{other}/" in content):
                        conflicts.append({
                            "file": str(f.relative_to(pdir)),
                            "part": pid,
                            "references": other,
                        })

    mergeable = len(conflicts) == 0
    applied = False

    if apply and mergeable:
        merged = Path(state_dir) / "merged"
        merged.mkdir(exist_ok=True)
        for pid in part_ids:
            pdir = parts_dir / pid
            for f in pdir.rglob("*"):
                if f.is_file():
                    dest = merged / f.relative_to(pdir)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
        applied = True

    print(json.dumps({
        "mergeable": mergeable,
        "conflicts": conflicts,
        "parts": part_ids,
        "applied": applied,
    }, indent=2))


def cmd_worktree(state_dir, subcmd, part_id=None):
    state = load_state(state_dir)
    if state is None:
        state = replay_journal(state_dir)
    if state is None:
        print(json.dumps({"error": "no state; run init first"}))
        sys.exit(1)

    repo_path = state.get("repo_path")
    if not repo_path:
        print(json.dumps({"error": "no repo_path in state (old journal without --repo)"}))
        sys.exit(1)

    baseline_sha = state.get("baseline_sha")
    run_id = state["run_id"]
    wt_base = Path(state_dir) / "worktrees"

    if subcmd == "list":
        print(json.dumps(state.get("worktrees", {}), indent=2))
        return

    if subcmd == "create":
        if not part_id:
            print(json.dumps({"error": "usage: worktree <state_dir> create <part_id>"}))
            sys.exit(1)

        existing = state.get("worktrees", {}).get(part_id)
        if existing and Path(existing["path"]).exists():
            print(json.dumps({"ok": True, "path": existing["path"], "branch": existing["branch"], "reused": True}))
            return

        branch = f"gigga/{run_id}/{part_id}"
        wt_path = wt_base / part_id
        wt_base.mkdir(parents=True, exist_ok=True)

        _git(repo_path, "worktree", "prune", check=False)
        branch_exists = _git(repo_path, "rev-parse", "--verify", branch, check=False)
        if branch_exists.returncode == 0:
            _git(repo_path, "branch", "-D", branch)

        try:
            _git(repo_path, "worktree", "add", str(wt_path), "-b", branch, baseline_sha)
        except RuntimeError as e:
            print(json.dumps({"error": f"worktree add failed: {e}"}))
            sys.exit(1)

        append_journal(state_dir, {
            "type": "worktree",
            "part_id": part_id,
            "path": str(wt_path),
            "branch": branch,
        })
        replay_journal(state_dir)

        print(json.dumps({"ok": True, "path": str(wt_path), "branch": branch, "reused": False}))
        return

    if subcmd == "remove":
        if not part_id:
            print(json.dumps({"error": "usage: worktree <state_dir> remove <part_id>"}))
            sys.exit(1)

        existing = state.get("worktrees", {}).get(part_id)
        if existing:
            wt_path = existing["path"]
            branch = existing["branch"]
            _git(repo_path, "worktree", "remove", "--force", wt_path, check=False)
            _git(repo_path, "branch", "-D", branch, check=False)
            append_journal(state_dir, {"type": "worktree_remove", "part_id": part_id})
            replay_journal(state_dir)

        print(json.dumps({"ok": True, "removed": part_id}))
        return

    if subcmd == "remove-all":
        worktrees = state.get("worktrees", {})
        for pid, info in worktrees.items():
            _git(repo_path, "worktree", "remove", "--force", info["path"], check=False)
            _git(repo_path, "branch", "-D", info["branch"], check=False)

        result_branch = f"gigga/{run_id}/result"
        _git(repo_path, "branch", "-D", result_branch, check=False)

        append_journal(state_dir, {"type": "worktree_remove_all"})
        replay_journal(state_dir)
        print(json.dumps({"ok": True, "removed": list(worktrees.keys())}))
        return

    print(json.dumps({"error": f"unknown worktree subcommand: {subcmd}"}))
    sys.exit(1)


def cmd_apply(state_dir):
    state = load_state(state_dir)
    if state is None:
        state = replay_journal(state_dir)
    if state is None:
        print(json.dumps({"error": "no state; run init first"}))
        sys.exit(1)

    repo_path = state.get("repo_path")
    if not repo_path:
        print(json.dumps({"error": "no repo_path in state"}))
        sys.exit(1)

    baseline_sha = state.get("baseline_sha")
    run_id = state["run_id"]
    result_branch = f"gigga/{run_id}/result"
    worktrees = state.get("worktrees", {})

    if not worktrees:
        print(json.dumps({"error": "no worktrees to merge"}))
        sys.exit(1)

    _git(repo_path, "branch", "-D", result_branch, check=False)

    result_wt = Path(state_dir) / "worktrees" / "__result__"
    if result_wt.exists():
        _git(repo_path, "worktree", "remove", "--force", str(result_wt), check=False)

    try:
        _git(repo_path, "worktree", "add", str(result_wt), "-b", result_branch, baseline_sha)
    except RuntimeError as e:
        print(json.dumps({"error": f"cannot create result worktree: {e}"}))
        sys.exit(1)

    tasks = state.get("tasks", [])
    part_order = [t.get("id", f"task-{i}") for i, t in enumerate(tasks)]
    for pid in worktrees:
        if pid not in part_order:
            part_order.append(pid)

    merged_parts = []
    failed_parts = []
    for pid in part_order:
        if pid not in worktrees:
            continue
        branch = worktrees[pid]["branch"]
        wt_path = worktrees[pid]["path"]

        _git(wt_path, "add", "-A", check=False)
        status = _git(wt_path, "status", "--porcelain", check=False).stdout.strip()
        if status:
            _git(wt_path, "commit", "-m", f"gigga: {pid}", check=False)

        merge_r = _git(result_wt, "merge", "--no-edit", branch, check=False)
        if merge_r.returncode != 0:
            failed_parts.append(pid)
            _git(result_wt, "merge", "--abort", check=False)
        else:
            merged_parts.append(pid)

    diffstat = _git(result_wt, "diff", "--stat", f"{baseline_sha}..HEAD", check=False).stdout.strip()

    request_summary = state["request"][:80]
    if merged_parts:
        msg = f"gigga({run_id}): {request_summary}\n\nParts: {', '.join(merged_parts)}"
        _git(result_wt, "commit", "--allow-empty", "-m", msg, check=False)

    _git(repo_path, "worktree", "remove", "--force", str(result_wt), check=False)

    print(json.dumps({
        "ok": len(failed_parts) == 0,
        "result_branch": result_branch,
        "merged_parts": merged_parts,
        "failed_parts": failed_parts,
        "diffstat": diffstat,
        "request_summary": request_summary,
    }, indent=2))


def cmd_diff(state_dir):
    state = load_state(state_dir)
    if state is None:
        state = replay_journal(state_dir)
    if state is None:
        print(json.dumps({"error": "no state; run init first"}))
        sys.exit(1)

    repo_path = state.get("repo_path")
    if not repo_path:
        print(json.dumps({"error": "no repo_path in state"}))
        sys.exit(1)

    baseline_sha = state.get("baseline_sha")
    run_id = state["run_id"]
    result_branch = f"gigga/{run_id}/result"

    branch_exists = _git(repo_path, "rev-parse", "--verify", result_branch, check=False)
    if branch_exists.returncode != 0:
        worktrees = state.get("worktrees", {})
        if worktrees:
            first_wt = next(iter(worktrees.values()))
            diff_out = _git(repo_path, "diff", f"{baseline_sha}..{first_wt['branch']}", check=False).stdout
        else:
            print(json.dumps({"error": f"branch {result_branch} not found and no worktrees exist"}))
            sys.exit(1)
    else:
        diff_out = _git(repo_path, "diff", f"{baseline_sha}..{result_branch}", check=False).stdout

    print(diff_out)


def cmd_rollback(state_dir):
    state = load_state(state_dir)
    if state is None:
        state = replay_journal(state_dir)
    if state is None:
        print(json.dumps({"error": "no state; run init first"}))
        sys.exit(1)

    repo_path = state.get("repo_path")
    if not repo_path:
        print(json.dumps({"error": "no repo_path in state"}))
        sys.exit(1)

    run_id = state["run_id"]
    worktrees = state.get("worktrees", {})
    removed_wt = []
    removed_branches = []

    for pid, info in worktrees.items():
        _git(repo_path, "worktree", "remove", "--force", info["path"], check=False)
        _git(repo_path, "branch", "-D", info["branch"], check=False)
        removed_wt.append(pid)
        removed_branches.append(info["branch"])

    result_branch = f"gigga/{run_id}/result"
    r = _git(repo_path, "branch", "-D", result_branch, check=False)
    if r.returncode == 0:
        removed_branches.append(result_branch)

    append_journal(state_dir, {"type": "worktree_remove_all"})

    wt_list = _git(repo_path, "worktree", "list", "--porcelain", check=False).stdout
    print(json.dumps({
        "ok": True,
        "removed_worktrees": removed_wt,
        "removed_branches": removed_branches,
        "remaining_worktrees": wt_list.strip(),
    }, indent=2))


def cmd_check(state_dir, part_id=None, merged=False, scope="changed"):
    state = load_state(state_dir)
    if state is None:
        state = replay_journal(state_dir)
    if state is None:
        print(json.dumps({"error": "no state; run init first"}))
        sys.exit(1)

    repo_path = state.get("repo_path")
    baseline_sha = state.get("baseline_sha")
    if not repo_path:
        print(json.dumps({"error": "no repo_path in state"}))
        sys.exit(1)

    checks_file = Path(state_dir) / "checks.json"
    if checks_file.exists():
        checks = json.loads(checks_file.read_text())
    else:
        checks = state.get("checks", {})

    timeout = checks.get("timeout", CHECK_TIMEOUT_DEFAULT)

    if merged:
        work_dir = Path(state_dir) / "merged"
        branch = None
        key = "__merged__"
    elif part_id:
        wt_info = state.get("worktrees", {}).get(part_id)
        if not wt_info:
            print(json.dumps({"error": f"no worktree for part: {part_id}"}))
            sys.exit(1)
        work_dir = Path(wt_info["path"])
        branch = wt_info["branch"]
        key = part_id
    else:
        print(json.dumps({"error": "specify --part <id> or --merged"}))
        sys.exit(1)

    if not work_dir.exists():
        print(json.dumps({"error": f"work directory does not exist: {work_dir}"}))
        sys.exit(1)

    changed_files = []
    if scope == "changed" and branch:
        diff_out = _git(work_dir, "diff", "--name-only", f"{baseline_sha}..HEAD", check=False).stdout.strip()
        changed_files = diff_out.splitlines() if diff_out else []

    ladder = []
    for stage in ("typecheck", "lint", "unit", "e2e", "extra"):
        cmd = checks.get(stage, [])
        if not cmd:
            continue
        if scope == "changed" and stage in ("lint", "unit") and changed_files:
            if stage == "lint" and cmd[0] == "npx" and "eslint" in cmd:
                cmd = cmd + changed_files
        ladder.append((stage, cmd))

    results = []
    all_passed = True
    for stage, cmd in ladder:
        start = time.time()
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                cwd=str(work_dir), timeout=timeout,
            )
            duration = round(time.time() - start, 2)
            entry = {
                "stage": stage,
                "cmd": cmd,
                "exit_code": proc.returncode,
                "duration_s": duration,
                "passed": proc.returncode == 0,
            }
            if proc.returncode != 0:
                entry["stderr_tail"] = proc.stderr[-CHECK_OUTPUT_CAP:]
                all_passed = False
        except subprocess.TimeoutExpired:
            duration = round(time.time() - start, 2)
            entry = {
                "stage": stage,
                "cmd": cmd,
                "exit_code": -1,
                "duration_s": duration,
                "passed": False,
                "stderr_tail": f"TIMEOUT after {timeout}s",
            }
            all_passed = False

        results.append(entry)
        if not entry["passed"]:
            break

    append_journal(state_dir, {
        "type": "check_result",
        "part_id": part_id,
        "merged": merged,
        "scope": scope,
        "passed": all_passed,
        "results": results,
    })

    print(json.dumps({
        "ok": all_passed,
        "part_id": part_id,
        "merged": merged,
        "scope": scope,
        "results": results,
    }, indent=2))

    if not all_passed:
        sys.exit(1)


def _reject_unknown_flags(args, allowed):
    for a in args:
        if a.startswith("--") and a not in allowed:
            print(json.dumps({"error": f"unknown flag: {a}", "allowed": sorted(allowed)}))
            sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "start":
        args = sys.argv[2:]
        _reject_unknown_flags(args, {"--repo", "--request-file", "--fastrack", "--dir", "--allow-dirty", "--checks"})
        repo = None
        request_file = None
        run_dir = None
        checks_file = None
        fastrack = "--fastrack" in args
        allow_dirty = "--allow-dirty" in args
        i = 0
        while i < len(args):
            if args[i] == "--repo" and i + 1 < len(args):
                repo = args[i + 1]
                i += 2
            elif args[i] == "--request-file" and i + 1 < len(args):
                request_file = args[i + 1]
                i += 2
            elif args[i] == "--dir" and i + 1 < len(args):
                run_dir = args[i + 1]
                i += 2
            elif args[i] == "--checks" and i + 1 < len(args):
                checks_file = args[i + 1]
                i += 2
            else:
                i += 1
        if not repo:
            print(json.dumps({"error": "--repo <path> is required"}))
            sys.exit(1)
        if not request_file:
            print(json.dumps({"error": "--request-file <path> is required"}))
            sys.exit(1)
        cmd_start(repo, request_file, fastrack=fastrack, run_dir=run_dir, allow_dirty=allow_dirty, checks_file=checks_file)
        return

    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    state_dir = sys.argv[2]

    if cmd == "init":
        if len(sys.argv) < 4:
            print("usage: scheduler.py init <state_dir> <request_file> --repo <path> [--fastrack] [--allow-dirty] [--checks <file>]")
            sys.exit(1)
        args = sys.argv[4:]
        _reject_unknown_flags(args, {"--repo", "--fastrack", "--allow-dirty", "--checks"})
        fastrack = "--fastrack" in args
        allow_dirty = "--allow-dirty" in args
        repo = None
        checks_file = None
        i = 0
        while i < len(args):
            if args[i] == "--repo" and i + 1 < len(args):
                repo = args[i + 1]
                i += 2
            elif args[i] == "--checks" and i + 1 < len(args):
                checks_file = args[i + 1]
                i += 2
            else:
                i += 1
        if not repo:
            print(json.dumps({"error": "--repo <path> is required"}))
            sys.exit(1)
        cmd_init(state_dir, sys.argv[3], repo, fastrack=fastrack, allow_dirty=allow_dirty, checks_file=checks_file)
    elif cmd == "next":
        _reject_unknown_flags(sys.argv[3:], {"--brief"})
        brief = "--brief" in sys.argv[3:]
        cmd_next(state_dir, brief=brief)
    elif cmd == "record":
        if len(sys.argv) < 4:
            print("usage: scheduler.py record <state_dir> <event_json_or_file> [--brief]")
            sys.exit(1)
        _reject_unknown_flags(sys.argv[4:], {"--brief"})
        brief = "--brief" in sys.argv[4:]
        cmd_record(state_dir, sys.argv[3], brief=brief)
    elif cmd == "status":
        _reject_unknown_flags(sys.argv[3:], set())
        cmd_status(state_dir)
    elif cmd == "amend":
        if len(sys.argv) < 4:
            print("usage: scheduler.py amend <state_dir> <amendment_json_file>")
            sys.exit(1)
        _reject_unknown_flags(sys.argv[4:], set())
        cmd_amend(state_dir, sys.argv[3])
    elif cmd == "revive":
        if len(sys.argv) < 4:
            print("usage: scheduler.py revive <state_dir> <to_phase>")
            sys.exit(1)
        _reject_unknown_flags(sys.argv[4:], set())
        cmd_revive(state_dir, sys.argv[3])
    elif cmd == "mergecheck":
        _reject_unknown_flags(sys.argv[3:], {"--apply"})
        apply_flag = "--apply" in sys.argv[3:]
        cmd_mergecheck(state_dir, apply=apply_flag)
    elif cmd == "worktree":
        if len(sys.argv) < 4:
            print("usage: scheduler.py worktree <state_dir> create|list|remove|remove-all [part_id]")
            sys.exit(1)
        subcmd = sys.argv[3]
        part_id = sys.argv[4] if len(sys.argv) > 4 else None
        cmd_worktree(state_dir, subcmd, part_id)
    elif cmd == "apply":
        _reject_unknown_flags(sys.argv[3:], set())
        cmd_apply(state_dir)
    elif cmd == "diff":
        _reject_unknown_flags(sys.argv[3:], set())
        cmd_diff(state_dir)
    elif cmd == "rollback":
        _reject_unknown_flags(sys.argv[3:], set())
        cmd_rollback(state_dir)
    elif cmd == "check":
        args = sys.argv[3:]
        _reject_unknown_flags(args, {"--part", "--merged", "--scope"})
        part_id = None
        merged = False
        scope = "changed"
        i = 0
        while i < len(args):
            if args[i] == "--part" and i + 1 < len(args):
                part_id = args[i + 1]
                i += 2
            elif args[i] == "--merged":
                merged = True
                i += 1
            elif args[i] == "--scope" and i + 1 < len(args):
                scope = args[i + 1]
                i += 2
            else:
                i += 1
        cmd_check(state_dir, part_id=part_id, merged=merged, scope=scope)
    else:
        print(f"unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
