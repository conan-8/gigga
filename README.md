# GIGGA

GIGGA is a spec-locked, test-first build pipeline for [opencode](https://opencode.ai).
Switch to the `gigga` agent with **Tab**, give it a request, and it drives a plain-code
state machine (`scheduler.py`) through the pipeline:

1. **Spec pack** — one AI drafts the spec AND self-attacks it, producing questions with
   default assumptions. Only truly blocking questions (max 2) are asked; everything else
   proceeds on documented defaults.
2. **Reconcile + decompose** — a second call merges the answers into frozen rules and
   splits them into isolated parts in one pass.
3. **Lock tests** — a separate AI writes the tests from the frozen rules, then they are
   hashed and locked.
4. **Fork** — each isolated part is built by its own builder, in parallel.
5. **Objective gate** — the computer (not an AI) runs the locked tests; failures rebuild
   only the failing part.
6. **Integrate** — if parts are structurally disjoint, they are copied together with no
   AI involved; otherwise a fresh AI fixes the seams.
7. **Reject-only review** — an independent judge (different model) compares the result
   to the original request and can only reject.

The orchestrator never edits code itself; it only runs commands and records events. Pass/fail
is objective (test exit codes) plus the reject-only judge — no AI ever grades its own work.
Default assumptions are disclosed at delivery; if one is wrong, a targeted amendment loop
re-reconciles, re-locks tests, and rebuilds only what changed.

## What's in the box

| File | Role |
| --- | --- |
| `gigga.md` | Master orchestrator (`mode: primary`). Switch to it with Tab. |
| `gigga-spec.md` | Planner — drafts the spec pack, reconciles + decomposes, rewrites failing parts. |
| `gigga-test-author.md` | Writes and locks the tests from the rules, before any code exists. |
| `gigga-builder.md` | Implements one isolated part against the rules and locked tests. |
| `gigga-merge.md` | Joins the finished parts and fixes the seams (skipped when parts are disjoint). |
| `gigga-judge-fidelity.md` | Independent reject-only reviewer. |
| `gigga-checker.md` | Quick-fix sanity checker (used only in post-HALT recovery). |
| `scheduler.py` | The plain-code state machine the orchestrator drives. |

The seven `.md` files are opencode agents; `scheduler.py` is the state machine they run against.

## Requirements

- [opencode](https://opencode.ai)
- `curl`
- `python3` (for `scheduler.py`)

## Install

> **Note:** the agent files go into your opencode `agents/` directory, but `scheduler.py`
> always installs to `~/.config/opencode/gigga/` because the orchestrator references it at that
> exact path. Both steps below are required.

### One-line install (project-local)

Run this from the root of your project:

```bash
curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/gigga/{gigga,gigga-spec,gigga-test-author,gigga-builder,gigga-merge,gigga-judge-fidelity,gigga-checker}.md" -o ".opencode/agents/#1.md" --create-dirs && curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/gigga/scheduler.py" -o "$HOME/.config/opencode/gigga/scheduler.py" --create-dirs
```

### One-line install (global)

To make GIGGA available in every project instead:

```bash
curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/gigga/{gigga,gigga-spec,gigga-test-author,gigga-builder,gigga-merge,gigga-judge-fidelity,gigga-checker}.md" -o "$HOME/.config/opencode/agents/#1.md" --create-dirs && curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/gigga/scheduler.py" -o "$HOME/.config/opencode/gigga/scheduler.py" --create-dirs
```

### Using install.sh (with model customization)

The install script supports per-agent model overrides via environment variables:

```bash
# project-local, all defaults
curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/install.sh" | bash

# global
curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/install.sh" | bash -s -- --global

# custom model for all agents
curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/install.sh" | GIGGA_MODEL=openai/gpt-5.2 bash

# custom model for all, but a different judge
curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/install.sh" | GIGGA_MODEL=openai/gpt-5.2 GIGGA_MODEL_JUDGE=anthropic/claude-sonnet-4-20250514 bash
```

| Variable | Agent |
| --- | --- |
| `GIGGA_MODEL` | Default for ALL agents |
| `GIGGA_MODEL_ORCHESTRATOR` | `gigga` (orchestrator) |
| `GIGGA_MODEL_SPEC` | `gigga-spec` (planner) |
| `GIGGA_MODEL_TEST_AUTHOR` | `gigga-test-author` |
| `GIGGA_MODEL_BUILDER` | `gigga-builder` |
| `GIGGA_MODEL_MERGE` | `gigga-merge` (integrator) |
| `GIGGA_MODEL_JUDGE` | `gigga-judge-fidelity` |
| `GIGGA_MODEL_CHECKER` | `gigga-checker` |

Per-agent vars override `GIGGA_MODEL`.

### Manual install

Copy the files yourself:

1. Copy the seven `gigga*.md` files from [`gigga/`](gigga/) into your project's `.opencode/agents/`
   (or `~/.config/opencode/agents/` for global use).
2. Copy [`gigga/scheduler.py`](gigga/scheduler.py) into `~/.config/opencode/gigga/`.

```bash
# project-local example
mkdir -p .opencode/agents ~/.config/opencode/gigga
cp gigga/gigga*.md .opencode/agents/
cp gigga/scheduler.py ~/.config/opencode/gigga/
```

> **Note:** new agent files require an opencode restart to take effect.

## Usage

Restart opencode, press **Tab** until you reach the `gigga` agent, and give it a request.
