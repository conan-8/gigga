# GIGGA

GIGGA is a spec-locked build pipeline for [opencode](https://opencode.ai).
Switch to the `gigga` agent with **Tab**, give it a request, and it drives a plain-code
state machine (`scheduler.py`) through the pipeline:

1. **Spec pack** — one AI drafts the spec AND self-attacks it, producing questions with
   default assumptions. Only truly blocking questions (max 2) are asked; everything else
   proceeds on documented defaults. With no blocking questions, the same call also
   reconciles the rules and decomposes the work in one pass.
2. **Fork** — each isolated part is built by its own builder, in parallel. Each builder
   reports a syntax-check exit code as an objective floor.
3. **Integrate** — if parts are structurally disjoint, they are copied together with no
   AI involved; otherwise a fresh AI fixes the seams. Single-part runs skip this.
4. **Reject-only review** — an independent judge (different model) compares the result
   to the original request and can only reject, tagging each defect with the responsible
   part so rebuilds stay targeted.

The orchestrator never edits code itself; it only runs commands and records events. Pass/fail
comes from builder exit codes plus the reject-only judge — no AI ever grades its own work.
Default assumptions are disclosed at delivery; if one is wrong, a targeted amendment loop
re-reconciles and rebuilds only what changed.

## What's in the box

| File | Role |
| --- | --- |
| `gigga.md` | Master orchestrator (`mode: primary`). Switch to it with Tab. |
| `gigga-spec.md` | Planner — drafts the spec pack, reconciles + decomposes, rewrites failing parts. |
| `gigga-builder.md` | Implements one isolated part against the rules; reports a syntax-check exit code. |
| `gigga-merge.md` | Joins the finished parts and fixes the seams (skipped when parts are disjoint). |
| `gigga-judge-fidelity.md` | Independent reject-only reviewer (the gate). |
| `gigga-checker.md` | Quick-fix sanity checker (used only in post-HALT recovery). |
| `gigga-config.md` | Interactive model configurator. Switch to it with Tab to change agent models. |
| `scheduler.py` | The plain-code state machine the orchestrator drives. |

The seven `.md` files are opencode agents; `scheduler.py` is the state machine they run against.

## Requirements

- [opencode](https://opencode.ai)
- `curl`
- `python3` (for `scheduler.py`)

## Install

> **Note:** agent files go into your opencode `agent/` directory, while `scheduler.py`
> always installs to `~/.config/opencode/gigga/` because the orchestrator references it at that
> exact path. `install.sh` handles both.

### One-line install

```bash
# project-local (run from your project root)
curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/install.sh" | bash

# global (available in every project)
curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/install.sh" | bash -s -- --global
```

Re-run the same command any time to update.

### Model customization

The install script supports per-agent model overrides via environment variables:

```bash
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
| `GIGGA_MODEL_BUILDER` | `gigga-builder` |
| `GIGGA_MODEL_MERGE` | `gigga-merge` (integrator) |
| `GIGGA_MODEL_JUDGE` | `gigga-judge-fidelity` |
| `GIGGA_MODEL_CHECKER` | `gigga-checker` |
| `GIGGA_MODEL_CONFIG` | `gigga-config` (configurator) |

Per-agent vars override `GIGGA_MODEL`.

### Manual install

Copy the files yourself:

1. Copy the seven `gigga*.md` files from [`gigga/`](gigga/) into your project's `.opencode/agent/`
   (or `~/.config/opencode/agent/` for global use).
2. Copy [`gigga/scheduler.py`](gigga/scheduler.py) into `~/.config/opencode/gigga/`.

```bash
# project-local example
mkdir -p .opencode/agent ~/.config/opencode/gigga
cp gigga/gigga*.md .opencode/agent/
cp gigga/scheduler.py ~/.config/opencode/gigga/
```

> **Note:** new agent files require an opencode restart to take effect.

## Usage

Restart opencode, press **Tab** until you reach the `gigga` agent, and give it a request.

## Changing models after install

Switch to the `gigga-config` agent with **Tab**. It shows every GIGGA agent's current
model and lets you pick new ones interactively. Restart opencode after changing models.
