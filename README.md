# GIGGA

GIGGA is a `mode: primary` orchestrator agent. Switch to it with **Tab**, then give it a request.

It creates a fresh run directory at `~/.gigga/run-<timestamp>/` and drives `scheduler.py`
through a spec-locked pipeline anchored to a git repository:

1. **Spec** — the planner inspects the repo (recon), drafts clauses, attacks ambiguities with questions, reconciles answers into frozen rules, and decomposes into isolated parts anchored to real files.
2. **Build** — each part gets its own git worktree branched off the baseline SHA. Builders have full repo visibility but edit only their worktree. The scheduler runs an objective check ladder (typecheck → lint → unit → e2e) per part; builder self-reports are advisory only.
3. **Merge** — a fresh AI joins the parts, fixing only the seams.
4. **Judge** — an independent judge compares the result to the original request and can only reject.
5. **Apply** — part branches are merged into `gigga/<run_id>/result`, a diffstat is reported, and worktrees are cleaned up. The user's working tree and branch are never touched.

The orchestrator never edits code itself; it only runs commands and records events. Pass/fail is
objective (scheduler-run check exit codes) plus the reject-only judge — no AI ever grades its own work.

## Init

```
scheduler.py init <state_dir> <request_file> --repo <path> [--fastrack] [--allow-dirty] [--checks <file>]
```

`--repo` is required. The repo must be clean (or pass `--allow-dirty`). At init the scheduler
captures `baseline_sha`, `baseline_branch`, and auto-detects a check ladder from the repo
(package.json, tsconfig, vitest/jest/playwright config, pyproject, go.mod, Cargo.toml).
Override detection with `--checks <file>`.

## Key commands

| Command | Purpose |
|---|---|
| `worktree <dir> create <id>` | Create a git worktree for a part, branched off baseline |
| `check <dir> --part <id>` | Run the check ladder in a part's worktree |
| `apply <dir>` | Merge part branches into a delivery branch |
| `diff <dir>` | Print diff against baseline |
| `rollback <dir>` | Delete all run branches and worktrees |

> **Note:** new agent files require an opencode restart to take effect.
