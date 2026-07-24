# GIGGA

GIGGA is a spec-locked, test-first build pipeline for [opencode](https://opencode.ai).
Switch to the `gigga` agent with **Tab**, give it a request, and it drives a plain-code
state machine (`scheduler.py`) through six stages:

1. **Ask** — draft the spec, attack it with questions, reconcile the answers, freeze it.
2. **Lock tests** — a separate AI writes the tests from the frozen rules, then they are hashed and locked.
3. **Fork** — the frozen spec is split into isolated parts, each built by its own builder in parallel.
4. **Objective gate** — the computer (not an AI) runs the locked tests; failures rebuild only the failing part.
5. **Integrate** — a fresh AI joins the parts, fixing only the seams.
6. **Reject-only review** — an independent judge compares the result to the original request and can only reject.

The orchestrator never edits code itself; it only runs commands and records events. Pass/fail is
objective (test exit codes) plus the reject-only judge — no AI ever grades its own work.

## What's in the box

| File | Role |
| --- | --- |
| `gigga.md` | Master orchestrator (`mode: primary`). Switch to it with Tab. |
| `gigga-spec.md` | Planner — drafts the spec, decomposes it into isolated parts. |
| `gigga-attacker.md` | Finds everything left unsaid in the draft and turns it into questions. |
| `gigga-reconciler.md` | Writes your answers down as numbered, frozen rules. |
| `gigga-test-author.md` | Writes and locks the tests from the rules, before any code exists. |
| `gigga-builder.md` | Implements one isolated part against the rules and locked tests. |
| `gigga-merge.md` | Joins the finished parts and fixes the seams. |
| `gigga-judge-fidelity.md` | Independent reject-only reviewer. |
| `scheduler.py` | The plain-code state machine the orchestrator drives. |

The eight `.md` files are opencode agents; `scheduler.py` is the state machine they run against.

## Requirements

- [opencode](https://opencode.ai)
- `curl`
- `python3` (for `scheduler.py`)

## Install

> **Note:** the eight agent files go into your opencode `agents/` directory, but `scheduler.py`
> always installs to `~/.config/opencode/gigga/` because the orchestrator references it at that
> exact path. Both steps below are required.

### One-line install (project-local)

Run this from the root of your project:

```bash
curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/gigga/{gigga,gigga-spec,gigga-attacker,gigga-reconciler,gigga-test-author,gigga-builder,gigga-merge,gigga-judge-fidelity}.md" -o ".opencode/agents/#1.md" --create-dirs && curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/gigga/scheduler.py" -o "$HOME/.config/opencode/gigga/scheduler.py" --create-dirs
```

This drops the eight agents into `.opencode/agents/` and the scheduler into `~/.config/opencode/gigga/`.

### One-line install (global)

To make GIGGA available in every project instead:

```bash
curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/gigga/{gigga,gigga-spec,gigga-attacker,gigga-reconciler,gigga-test-author,gigga-builder,gigga-merge,gigga-judge-fidelity}.md" -o "$HOME/.config/opencode/agents/#1.md" --create-dirs && curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/gigga/scheduler.py" -o "$HOME/.config/opencode/gigga/scheduler.py" --create-dirs
```

### Using install.sh

Or let the script do it:

```bash
# project-local
curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/install.sh" | bash

# global
curl -fsSL "https://raw.githubusercontent.com/conan-8/gigga/main/install.sh" | bash -s -- --global
```

### Manual install

Copy the files yourself:

1. Copy the eight `gigga*.md` files from [`gigga/`](gigga/) into your project's `.opencode/agents/`
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
