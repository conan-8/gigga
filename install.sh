#!/usr/bin/env bash
set -euo pipefail

OWNER="conan-8"
REPO="gigga"
BRANCH="main"
BASE="${GIGGA_BASE:-https://raw.githubusercontent.com/${OWNER}/${REPO}/${BRANCH}/gigga}"

AGENTS=(
  gigga
  gigga-spec
  gigga-builder
  gigga-merge
  gigga-judge-fidelity
  gigga-checker
  gigga-config
)

usage() {
  cat >&2 <<'EOF'
usage: install.sh [--global]

Environment variables for model customization:
  GIGGA_MODEL              Default model for ALL agents
  GIGGA_MODEL_ORCHESTRATOR Model for the orchestrator (gigga)
  GIGGA_MODEL_SPEC         Model for the planner (gigga-spec)
  GIGGA_MODEL_BUILDER      Model for the builder (gigga-builder)
  GIGGA_MODEL_MERGE        Model for the integrator (gigga-merge)
  GIGGA_MODEL_JUDGE        Model for the judge (gigga-judge-fidelity)
  GIGGA_MODEL_CHECKER      Model for the quick-fix checker (gigga-checker)
  GIGGA_MODEL_CONFIG       Model for the configurator (gigga-config)

Per-agent vars override GIGGA_MODEL. Example:
  GIGGA_MODEL=openai/gpt-5.2 GIGGA_MODEL_JUDGE=anthropic/claude-sonnet-4-20250514 install.sh
EOF
  exit 2
}

GLOBAL=0
for arg in "$@"; do
  case "$arg" in
    --global) GLOBAL=1 ;;
    -h|--help) usage ;;
    *) echo "error: unknown argument: $arg" >&2; usage ;;
  esac
done

if [ "$GLOBAL" -eq 1 ]; then
  AGENT_DIR="$HOME/.config/opencode/agent"
else
  AGENT_DIR=".opencode/agent"
fi
SCHEDULER_DIR="$HOME/.config/opencode/gigga"

mkdir -p "$AGENT_DIR" "$SCHEDULER_DIR"

fetch() {
  local url="$1" out="$2"
  echo "  downloading $(basename "$out")"
  if ! curl -fsSL "$url" -o "$out"; then
    echo "error: failed to download $url" >&2
    exit 1
  fi
}

model_for() {
  local name="$1"
  case "$name" in
    gigga)              echo "${GIGGA_MODEL_ORCHESTRATOR:-${GIGGA_MODEL:-}}" ;;
    gigga-spec)         echo "${GIGGA_MODEL_SPEC:-${GIGGA_MODEL:-}}" ;;
    gigga-builder)      echo "${GIGGA_MODEL_BUILDER:-${GIGGA_MODEL:-}}" ;;
    gigga-merge)        echo "${GIGGA_MODEL_MERGE:-${GIGGA_MODEL:-}}" ;;
    gigga-judge-fidelity) echo "${GIGGA_MODEL_JUDGE:-${GIGGA_MODEL:-}}" ;;
    gigga-checker)      echo "${GIGGA_MODEL_CHECKER:-${GIGGA_MODEL:-}}" ;;
    gigga-config)       echo "${GIGGA_MODEL_CONFIG:-${GIGGA_MODEL:-}}" ;;
    *)                  echo "${GIGGA_MODEL:-}" ;;
  esac
}

echo "Installing GIGGA agents into $AGENT_DIR"
for name in "${AGENTS[@]}"; do
  fetch "$BASE/$name.md" "$AGENT_DIR/$name.md"
  model="$(model_for "$name")"
  if [ -n "$model" ]; then
    sed -i "s|^model:.*|model: $model|" "$AGENT_DIR/$name.md"
    echo "  $name -> $model"
  fi
done

echo "Installing scheduler into $SCHEDULER_DIR"
fetch "$BASE/scheduler.py" "$SCHEDULER_DIR/scheduler.py"

echo "done: ${#AGENTS[@]} agents ready"
