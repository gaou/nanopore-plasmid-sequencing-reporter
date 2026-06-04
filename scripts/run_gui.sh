#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT_DIR/envs/plasmid-pipeline/bin/python"

needs_install() {
  local env_dir="$ROOT_DIR/envs/plasmid-pipeline"
  local required=(python flye minimap2 samtools racon seqkit)
  local name script
  [[ -d "$env_dir" ]] || return 0
  for name in "${required[@]}"; do
    [[ -x "$env_dir/bin/$name" ]] || return 0
  done
  for script in "$env_dir/bin/"*; do
    [[ -f "$script" ]] || continue
    if grep -qa "/envs/plasmid-pipeline/bin/python" "$script" && \
       ! grep -qaF "$env_dir/bin/python" "$script"; then
      return 0
    fi
  done
  "$PYTHON" -c 'import sys; print(sys.executable)' >/dev/null 2>&1 || return 0
  return 1
}

if needs_install; then
  "$ROOT_DIR/scripts/install_tools.sh"
fi

if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

exec "$PYTHON" "$ROOT_DIR/bin/plasmid_pipeline_gui.py"
