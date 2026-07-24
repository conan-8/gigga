#!/usr/bin/env python3
"""GIGGA scheduler — plain-code pipeline state machine.

Serial pipeline. One task at a time. All state in a directory on disk.
Append-only journal (JSONL) so it resumes after a crash.

Usage:
    scheduler.py init   <state_dir> <request_file> [--fastrack]
    scheduler.py next   <state_dir>
    scheduler.py record <state_dir> <event_json_file>
    scheduler.py status <state_dir>
    scheduler.py amend  <state_dir> <amendment_json_file>
"""

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

ATTEMPT_CEILING = 4
AMENDMENT_CAP = 3
NO_PROGRESS_WINDOW = 6

PHASES = [
    "FASTTRACK",
    "SPEC_DRAFT",
    "SPEC_ATTACK",
    "SPEC_RECONCILE",
    "SPEC_FREEZE",
    "TASK_PLAN",
    "TASK_TEST_AUTHOR",
    "TASK_REF_IMPL",
    "TASK_BUILD",
    "TASK_GATES",
    "TASK_FUZZ",
    "TASK_MUTATE",
    "TASK_AUDIT",
    "TASK_MERGE",
    "JUDGE_PROMO",
    "JUDGE_FIDELITY",
    "DONE",
    "HALT",
    "QUARANTINE",
]

ESCALATION_LEVELS = ["initial", "retry", "rewrite", "hard", "quarantine"]

AGENTS = {
    "FASTTRACK": "gigga-builder",
    "SPEC_DRAFT": "gigga-spec",
    "SPEC_ATTACK": "gigga-attacker",
    "SPEC_RECONCILE": "gigga-reconciler",
    "SPEC_FREEZE": None,
    "TASK_PLAN": "gigga-spec",
    "TASK_TEST_AUTHOR": "gigga-test-author",
    "TASK_REF_IMPL": "gigga-ref-impl",
    "TASK_BUILD": "gigga-builder",
    "TASK_GATES": None,
    "TASK_FUZZ": "gigga-ref-impl",
    "TASK_MUTATE": "gigga-mutator",
    "TASK_AUDIT": "gigga-auditor",
    "TASK_MERGE": None,
    "JUDGE_PROMO": "gigga-judge-promo",
    "JUDGE_FIDELITY": "gigga-judge-fidelity",
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
            "spec_hash": None,
            "spec_frozen": False,
            "amendments": [],
            "escalation": "initial",
            "attempts": 0,
            "no_progress_count": 0,
            "last_progress_seq": 0,
            "created": event["ts"],
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

    if etype == "spec_frozen":
        state["spec_frozen"] = True
        state["spec_hash"] = event["hash"]
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


def cmd_init(state_dir, request_file, fastrack=False):
    sd = Path(state_dir)
    if (sd / "journal.jsonl").exists():
        print(json.dumps({"error": "state_dir already initialized", "state_dir": str(sd)}))
        sys.exit(1)

    request = Path(request_file).read_text().strip()
    run_id = str(uuid.uuid4())[:12]

    sd.mkdir(parents=True, exist_ok=True)
    (sd / "spec").mkdir(exist_ok=True)
    (sd / "tasks").mkdir(exist_ok=True)
    (sd / "artifacts").mkdir(exist_ok=True)

    append_journal(state_dir, {
        "type": "init",
        "run_id": run_id,
        "request": request,
        "fastrack": fastrack,
    })

    state = replay_journal(state_dir)
    print(json.dumps({"ok": True, "run_id": run_id, "phase": state["phase"], "fastrack": fastrack, "state_dir": str(sd)}))


def cmd_next(state_dir):
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

    phase = state["phase"]
    agent = AGENTS.get(phase)
    escalation = state["escalation"]

    task_id = None
    task_info = None
    if phase.startswith("TASK_") and state["tasks"]:
        idx = min(state["task_index"], len(state["tasks"]) - 1)
        task_id = state["tasks"][idx].get("id", f"task-{idx}")
        task_info = state["tasks"][idx]

    if phase == "TASK_GATES":
        agent = None

    if phase == "TASK_MERGE":
        agent = None

    if phase == "SPEC_FREEZE":
        agent = None

    if escalation == "hard" and phase == "TASK_BUILD":
        agent = "gigga-builder"

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
        "spec_frozen": state["spec_frozen"],
        "spec_hash": state["spec_hash"],
        "run_id": state["run_id"],
        "request": state["request"],
        "fastrack": state.get("fastrack", False),
    }

    if phase == "HALT":
        result["halt_reason"] = state.get("halt_reason", "unknown")
        result["autopsy"] = build_autopsy(state)

    print(json.dumps(result, indent=2))


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
        "spec_hash": state["spec_hash"],
        "fastrack": state.get("fastrack", False),
    }


def cmd_record(state_dir, event_file):
    event = json.loads(Path(event_file).read_text())
    etype = event.get("type", "progress")
    event["type"] = etype

    state = load_state(state_dir)
    if state is None:
        state = replay_journal(state_dir)
    if state is None:
        print(json.dumps({"error": "no state; run init first"}))
        sys.exit(1)

    append_journal(state_dir, event)
    state = replay_journal(state_dir)

    halt = check_halt_conditions(state)
    if halt and state["phase"] not in ("DONE", "HALT", "QUARANTINE"):
        append_journal(state_dir, {"type": "halt", "reason": halt})
        state = replay_journal(state_dir)

    print(json.dumps({"ok": True, "phase": state["phase"], "seq": event.get("seq")}))


def cmd_status(state_dir):
    state = load_state(state_dir)
    if state is None:
        state = replay_journal(state_dir)
    if state is None:
        print(json.dumps({"error": "no state found"}))
        sys.exit(1)
    print(json.dumps(state, indent=2))


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


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    state_dir = sys.argv[2]

    if cmd == "init":
        if len(sys.argv) < 4:
            print("usage: scheduler.py init <state_dir> <request_file> [--fastrack]")
            sys.exit(1)
        fastrack = "--fastrack" in sys.argv[4:]
        cmd_init(state_dir, sys.argv[3], fastrack=fastrack)
    elif cmd == "next":
        cmd_next(state_dir)
    elif cmd == "record":
        if len(sys.argv) < 4:
            print("usage: scheduler.py record <state_dir> <event_json_file>")
            sys.exit(1)
        cmd_record(state_dir, sys.argv[3])
    elif cmd == "status":
        cmd_status(state_dir)
    elif cmd == "amend":
        if len(sys.argv) < 4:
            print("usage: scheduler.py amend <state_dir> <amendment_json_file>")
            sys.exit(1)
        cmd_amend(state_dir, sys.argv[3])
    else:
        print(f"unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
