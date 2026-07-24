#!/usr/bin/env bash
set -euo pipefail

OWNER="conan-8"
REPO="gigga"
BRANCH="main"
BASE="${GIGGA_BASE:-https://raw.githubusercontent.com/${OWNER}/${REPO}/${BRANCH}/gigga}"

AGENTS=(
  gigga
  gigga-spec
  gigga-attacker
  gigga-reconciler
  gigga-test-author
  gigga-builder
  gigga-merge
  gigga-judge-fidelity
)

usage() {
  echo "usage: install.sh [--global]" >&2
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
  AGENT_DIR="$HOME/.config/opencode/agents"
else
  AGENT_DIR=".opencode/agents"
fi
# gigga.md hardcodes the scheduler at ~/.config/opencode/gigga/scheduler.py
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

echo "Installing GIGGA agents into $AGENT_DIR"
for name in "${AGENTS[@]}"; do
  fetch "$BASE/$name.md" "$AGENT_DIR/$name.md"
done

echo "Installing scheduler into $SCHEDULER_DIR"
fetch "$BASE/scheduler.py" "$SCHEDULER_DIR/scheduler.py"

echo "✓ ${#AGENTS[@]} agents ready"
