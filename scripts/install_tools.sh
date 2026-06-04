#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
ENV_DIR="$ROOT_DIR/envs/plasmid-pipeline"
TOOLS_DIR="$ROOT_DIR/tools"
mkdir -p "$LOG_DIR" "$TOOLS_DIR" "$ROOT_DIR/resources/functional_annotation_db"
LOG="$LOG_DIR/install_tools_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

echo "# Tool installation log"
echo "Started: $(date -Iseconds)"
echo "Root: $ROOT_DIR"

find_conda() {
  if command -v mamba >/dev/null 2>&1; then echo mamba; return; fi
  if command -v micromamba >/dev/null 2>&1; then echo micromamba; return; fi
  if command -v conda >/dev/null 2>&1; then echo conda; return; fi
}

CONDA_BIN="$(find_conda || true)"
if [[ -z "${CONDA_BIN:-}" ]]; then
  echo "No conda/mamba/micromamba found. Attempting project-local micromamba install."
  case "$(uname -m)" in
    x86_64|amd64) MM_ARCH=linux-64 ;;
    aarch64|arm64) MM_ARCH=linux-aarch64 ;;
    *) echo "Unsupported architecture for automatic micromamba: $(uname -m)"; exit 2 ;;
  esac
  if command -v curl >/dev/null 2>&1; then
    curl -L "https://micro.mamba.pm/api/micromamba/$MM_ARCH/latest" -o "$TOOLS_DIR/micromamba.tar.bz2"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$TOOLS_DIR/micromamba.tar.bz2" "https://micro.mamba.pm/api/micromamba/$MM_ARCH/latest"
  else
    echo "Neither curl nor wget is available. Cannot install micromamba."
    exit 2
  fi
  tar -xjf "$TOOLS_DIR/micromamba.tar.bz2" -C "$TOOLS_DIR" bin/micromamba
  CONDA_BIN="$TOOLS_DIR/bin/micromamba"
fi

echo "Conda frontend: $CONDA_BIN"

if [[ ! -x "$ENV_DIR/bin/python" ]]; then
  echo "Creating $ENV_DIR"
  if [[ "$CONDA_BIN" == *micromamba ]]; then
    "$CONDA_BIN" create -y -p "$ENV_DIR" -c conda-forge -c bioconda \
      python=3.11 flye minimap2 samtools seqkit racon filtlong chopper porechop
  else
    "$CONDA_BIN" create -y -p "$ENV_DIR" -c conda-forge -c bioconda \
      python=3.11 flye minimap2 samtools seqkit racon filtlong chopper porechop
  fi
else
  echo "Existing environment found: $ENV_DIR"
fi

echo "Attempting pLannotate install/update inside environment."
if [[ ! -x "$ENV_DIR/bin/plannotate" ]]; then
  if "$CONDA_BIN" install -y -p "$ENV_DIR" -c conda-forge -c bioconda plannotate; then
    echo "pLannotate conda install/update completed."
    "$CONDA_BIN" install -y -p "$ENV_DIR" -c conda-forge 'streamlit<1.30' || \
      echo "Could not pin streamlit<1.30; pLannotate may fail if streamlit.cli is unavailable."
    "$CONDA_BIN" install -y -p "$ENV_DIR" -c conda-forge 'protobuf<3.21' || \
      echo "Could not pin protobuf<3.21; pLannotate may fail with older Streamlit protobuf modules."
    "$CONDA_BIN" install -y -p "$ENV_DIR" -c conda-forge 'altair<5' || \
      echo "Could not pin altair<5; pLannotate may fail with older Streamlit Altair imports."
    "$CONDA_BIN" install -y -p "$ENV_DIR" -c conda-forge 'setuptools<81' || \
      echo "Could not pin setuptools<81; pLannotate may fail if pkg_resources is missing."
  else
    echo "pLannotate conda install failed; trying source/PyPI fallback."
    "$ENV_DIR/bin/python" -m pip install --upgrade pip || true
    "$ENV_DIR/bin/python" -m pip install --upgrade git+https://github.com/barricklab/pLannotate.git || \
      "$ENV_DIR/bin/python" -m pip install --upgrade git+https://github.com/mmcguffi/pLannotate.git || \
      echo "pLannotate source install failed; pipeline will continue without annotation and will report pending resource."
  fi
else
  echo "pLannotate already present: $ENV_DIR/bin/plannotate"
fi

echo "Attempting pLannotate database setup if command exists."
if [[ -x "$ENV_DIR/bin/plannotate" ]]; then
  "$ENV_DIR/bin/python" "$ROOT_DIR/scripts/patch_plannotate.py" "$ENV_DIR" || \
    echo "pLannotate compatibility patch failed; annotation may fail with current dependency versions."
  export ARDEA_FUNC_DB="${ARDEA_FUNC_DB:-$ROOT_DIR/resources/functional_annotation_db}"
  mkdir -p "$ARDEA_FUNC_DB"
  "$ENV_DIR/bin/plannotate" setupdb || \
    echo "pLannotate database setup command failed; see pLannotate version/help for the current setup syntax."
fi

echo "Completed: $(date -Iseconds)"
echo "Log: $LOG"
