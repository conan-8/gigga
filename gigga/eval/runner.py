#!/usr/bin/env python3
"""GIGGA eval harness. Runs task arms from baseline commits, collects metrics.

Usage:
    runner.py run --task <name|all> --arm <gigga|plan-build|plan-build-strong> [--repeats N]
    runner.py report [--run-dir <dir>]
    runner.py list

Task definitions live in tasks/<name>.json. Results in results/<run_id>/.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

EVAL_DIR = Path(__file__).parent
TASKS_DIR = EVAL_DIR / "tasks"
RESULTS_DIR = EVAL_DIR / "results"
SCHEDULER = EVAL_DIR.parent / "scheduler.py"

ARMS = {
    "gigga": {
        "description": "GIGGA full pipeline, current config",
        "mode": "gigga",
    },
    "plan-build": {
        "description": "Plain plan+build, same model as GIGGA builders",
        "mode": "plan-build",
        "model": "alibaba-token-plan/qwen3.8-max-preview",
    },
    "plan-build-strong": {
        "description": "Plain plan+build, strongest available model",
        "mode": "plan-build",
        "model": "zai-coding-plan/glm-5.2",
    },
}


def load_task(name):
    tp = TASKS_DIR / f"{name}.json"
    if not tp.exists():
        print(json.dumps({"error": f"task not found: {name}", "path": str(tp)}))
        sys.exit(1)
    return json.loads(tp.read_text())


def list_tasks():
    tasks = sorted(TASKS_DIR.glob("*.json"))
    for t in tasks:
        data = json.loads(t.read_text())
        print(f"  {t.stem:30s} {data.get('category', '?'):12s} {data.get('title', '')}")
    if not tasks:
        print("  (no tasks defined — add JSON files to tasks/)")


def run_arm(task, arm_name, run_dir, repeat_idx):
    arm = ARMS[arm_name]
    task_dir = run_dir / task["name"] / arm_name / f"repeat-{repeat_idx}"
    task_dir.mkdir(parents=True, exist_ok=True)

    clone_dir = task_dir / "repo"
    work_dir = task_dir / "work"
    work_dir.mkdir(exist_ok=True)

    result = {
        "task": task["name"],
        "arm": arm_name,
        "repeat": repeat_idx,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "wall_clock_seconds": None,
        "halt": False,
        "halt_reason": None,
        "checks_pass": None,
        "baseline_delta": None,
        "diff_size": None,
        "spec_drift": None,
        "error": None,
    }

    try:
        subprocess.run(
            ["git", "clone", "--no-checkout", task["repo"], str(clone_dir)],
            capture_output=True, text=True, check=True,
        )
        subprocess.run(
            ["git", "-C", str(clone_dir), "checkout", task["baseline_sha"]],
            capture_output=True, text=True, check=True,
        )

        req_file = work_dir / "request.txt"
        req_file.write_text(task["prompt"] + "\n")

        state_dir = work_dir / "state"
        start = time.time()

        if arm["mode"] == "gigga":
            proc = subprocess.run(
                [sys.executable, str(SCHEDULER), "start",
                 "--repo", str(clone_dir),
                 "--request-file", str(req_file),
                 "--dir", str(state_dir)],
                capture_output=True, text=True, timeout=7200,
            )
            result["scheduler_output"] = proc.stdout[-4096:]
            if proc.returncode != 0:
                result["error"] = proc.stderr[-2048:]
        else:
            result["error"] = "plan-build arm requires opencode session — not yet automated"

        elapsed = time.time() - start
        result["wall_clock_seconds"] = round(elapsed, 1)

        if state_dir.exists():
            sf = state_dir / "state.json"
            if sf.exists():
                state = json.loads(sf.read_text())
                result["halt"] = state.get("phase") == "HALT"
                result["halt_reason"] = state.get("halt_reason")

            jp = state_dir / "journal.jsonl"
            if jp.exists():
                shutil.copy2(jp, task_dir / "journal.jsonl")

    except subprocess.TimeoutExpired:
        result["error"] = "timeout (7200s)"
        result["wall_clock_seconds"] = 7200
    except Exception as e:
        result["error"] = str(e)

    result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (task_dir / "result.json").write_text(json.dumps(result, indent=2))
    return result


def cmd_run(task_name, arm_name, repeats):
    run_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + str(uuid.uuid4())[:6]
    run_dir = RESULTS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if task_name == "all":
        task_files = sorted(TASKS_DIR.glob("*.json"))
        tasks = [json.loads(f.read_text()) for f in task_files]
    else:
        tasks = [load_task(task_name)]

    arms = [arm_name] if arm_name else list(ARMS.keys())

    manifest = {
        "run_id": run_id,
        "tasks": [t["name"] for t in tasks],
        "arms": arms,
        "repeats": repeats,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    results = []
    for task in tasks:
        for arm in arms:
            for r in range(1, repeats + 1):
                print(f"  {task['name']} / {arm} / repeat {r}...", flush=True)
                res = run_arm(task, arm, run_dir, r)
                results.append(res)
                status = "ok" if not res["error"] else f"ERR: {res['error'][:60]}"
                print(f"    → {res['wall_clock_seconds']}s {status}")

    (run_dir / "all-results.json").write_text(json.dumps(results, indent=2))
    print(f"\nResults: {run_dir}")
    print_report(results)


def print_report(results):
    print(f"\n{'task':<25} {'arm':<20} {'rep':>3} {'wall_s':>7} {'halt':>5} {'error'}")
    print("-" * 90)
    for r in results:
        halt = "Y" if r["halt"] else ""
        err = (r["error"] or "")[:40]
        print(f"{r['task']:<25} {r['arm']:<20} {r['repeat']:>3} {r['wall_clock_seconds'] or '?':>7} {halt:>5} {err}")

    by_arm = {}
    for r in results:
        by_arm.setdefault(r["arm"], []).append(r)
    print(f"\n{'arm':<20} {'n':>3} {'mean_wall':>10} {'halt_rate':>10} {'variance':>10}")
    print("-" * 60)
    for arm, rs in sorted(by_arm.items()):
        walls = [r["wall_clock_seconds"] for r in rs if r["wall_clock_seconds"]]
        halts = sum(1 for r in rs if r["halt"])
        mean_w = sum(walls) / len(walls) if walls else 0
        var_w = sum((w - mean_w) ** 2 for w in walls) / len(walls) if walls else 0
        print(f"{arm:<20} {len(rs):>3} {mean_w:>9.1f}s {halts/len(rs)*100:>9.0f}% {var_w:>9.1f}")


def cmd_report(run_dir):
    if run_dir:
        rd = Path(run_dir)
    else:
        runs = sorted(RESULTS_DIR.iterdir()) if RESULTS_DIR.exists() else []
        if not runs:
            print("no results found")
            return
        rd = runs[-1]

    ar = rd / "all-results.json"
    if not ar.exists():
        print(f"no all-results.json in {rd}")
        return
    results = json.loads(ar.read_text())
    print_report(results)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        list_tasks()
    elif cmd == "run":
        task_name = "all"
        arm_name = None
        repeats = 3
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--task" and i + 1 < len(args):
                task_name = args[i + 1]
                i += 2
            elif args[i] == "--arm" and i + 1 < len(args):
                arm_name = args[i + 1]
                i += 2
            elif args[i] == "--repeats" and i + 1 < len(args):
                repeats = int(args[i + 1])
                i += 2
            else:
                i += 1
        cmd_run(task_name, arm_name, repeats)
    elif cmd == "report":
        run_dir = None
        args = sys.argv[2:]
        for i, a in enumerate(args):
            if a == "--run-dir" and i + 1 < len(args):
                run_dir = args[i + 1]
        cmd_report(run_dir)
    else:
        print(f"unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
