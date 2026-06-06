# Nanopore plasmid sequencing reporter

Nanopore plasmid sequencing reporter is a local desktop and command-line tool for turning Oxford Nanopore plasmid sequencing runs into per-barcode plasmid reports. It is designed for rapid-barcoded ONT plasmid runs such as `SQK-RBK114.24` / `SQK-RBK114-24`, starting from raw POD5 signal files or from already basecalled/demultiplexed intermediate files.

The software performs basecalling, demultiplexing, barcode filtering, read QC, plasmid representative sequence construction, circular duplicate collapse, pLannotate annotation, plasmid map rendering, and final result packaging. It runs without `sudo` and keeps installed tools inside the project directory.

## Main Features

- Native Ubuntu desktop GUI with run-folder selection, live progress, barcode toggling, plasmid image preview, FASTA preview, and `Save As...` result packaging.
- Command-line workflow for batch processing and remote/headless servers.
- Dorado update check once per day, with local installation under `tools/dorado/current`.
- Recursive POD5 discovery, so a parent run directory can be selected directly.
- Resume-friendly options for reusing existing Dorado BAM or demultiplexed FASTQs.
- Barcode abundance filtering to reject unused barcodes that appear from demultiplexing noise.
- Per-barcode read quality and length filtering to reduce unrelated or garbage reads.
- Flye assembly with retry logic, plus a sparse-read fallback using representative read seeding and Racon polishing.
- Circularization support, including minimap2 self-alignment collapse of tandem duplicated plasmid assemblies.
- pLannotate CSV, GenBank, interactive HTML plasmid maps, and PNG plasmid graphics.
- Run-specific `README.md`, `summary.json`, `progress.jsonl`, tool table, logs, and exact rerun command.

## Platform

Supported target:

- Ubuntu 20.04 or newer
- Ubuntu-compatible Linux desktops with Tkinter available
- CentOS/RHEL-like Linux for command-line use

The GUI uses Python Tkinter. Most Ubuntu desktop installations already provide the needed system libraries. This repository does not vendor Dorado, conda environments, pLannotate databases, or other third-party binaries; those are installed project-locally by `scripts/install_tools.sh` and by the pipeline's Dorado update step.

## Directory Layout

Important files and directories:

- `scripts/run_gui.sh`: launch the graphical interface.
- `bin/plasmid_pipeline_gui.py`: desktop GUI application.
- `bin/plasmid_pipeline.py`: command-line pipeline.
- `scripts/install_tools.sh`: project-local installer/updater for dependencies.
- `scripts/patch_plannotate.py`: compatibility patch for pLannotate with current Python package versions.
- `envs/plasmid-pipeline/`: project-local conda environment created by `scripts/install_tools.sh`; ignored by git.
- `tools/`: local tool installations, including Dorado and Dorado models when downloaded; ignored by git.
- `resources/`: project-local resource/database location.

This distribution intentionally excludes previous `logs/`, `results/`, and raw sequencing run folders.

## Installation

Install dependencies into this project directory before the first GUI or command-line run:

```bash
bash scripts/install_tools.sh
```

The installer does not use `sudo`. It creates `envs/plasmid-pipeline/`, prepares required command-line tools, patches pLannotate for current dependency compatibility, and attempts pLannotate database setup. Dorado itself is checked by the pipeline once per day and downloaded under `tools/dorado/current` when needed.

If this repository is copied or moved after a previous installation, run `bash scripts/install_tools.sh` again. Conda command wrappers are not safely relocatable; the installer detects stale wrappers that point to another checkout and rebuilds `envs/plasmid-pipeline/` when needed.

Generated dependency directories are intentionally excluded from git:

- `envs/`
- `tools/`
- `resources/functional_annotation_db/`
- `logs/`
- `results/`

## Quick Start With The GUI

From this directory:

```bash
scripts/run_gui.sh
```

Typical GUI workflow:

1. Click `Browse...` beside `Run folder`.
2. Select the ONT run directory. You can select the folder that directly contains `pod5/`, or a parent folder; the software searches recursively for `.pod5` files.
3. Leave `Kit` as `SQK-RBK114-24` for rapid barcode 114.24 plasmid runs.
4. Choose the Dorado model:
   - `auto`: default; uses `hac` for Dorado >2.0.0 and `sup` for older Dorado.
   - `sup`: best accuracy, slowest.
   - `hac`: faster, lower accuracy.
   - `fast`: fastest, lowest accuracy.
5. Set threads according to the machine.
6. Click `Run Pipeline`.
7. Watch the progress bar and task table. During Dorado basecalling/demultiplexing, Dorado's own terminal progress/ETA output is mirrored into the status/task area when Dorado emits it; the run log is kept readable.
8. When the run finishes, select barcodes in the left panel to view the plasmid PNG map and representative FASTA.
9. Click `Save As...` to write a zip package for sharing or archiving.

The GUI result package includes:

- `representative_sequences.fasta`
- each `03_barcodes/<barcode>/<barcode>.representative.fasta`
- demultiplexed/barcoded FASTQs from `02_demux/`
- filtered FASTQs from `03_barcodes/<barcode>/`
- pLannotate `.csv`, `.gbk`, `.html`, and `.png`
- `README.md`, `summary.json`, `progress.jsonl`, `barcode_filtering.tsv`, and `pipeline.log`

## Command-Line Examples

Run from raw POD5 files:

```bash
envs/plasmid-pipeline/bin/python bin/plasmid_pipeline.py \
  --input-run /path/to/ont_run_folder \
  --kit-name SQK-RBK114-24 \
  --threads 16
```

Run from the current directory while searching all subfolders for POD5 files:

```bash
envs/plasmid-pipeline/bin/python bin/plasmid_pipeline.py \
  --input-run . \
  --kit-name SQK-RBK114-24 \
  --threads 16
```

Reuse previous Dorado basecalling and demultiplexing, avoiding another Dorado run:

```bash
envs/plasmid-pipeline/bin/python bin/plasmid_pipeline.py \
  --input-run . \
  --kit-name SQK-RBK114-24 \
  --threads 16 \
  --basecalled-bam results/previous_run/01_basecalling/calls.bam \
  --demux-dir results/previous_run/02_demux \
  --output results/reused_dorado_run
```

Reuse an existing demultiplexed FASTQ directory without a BAM file:

```bash
envs/plasmid-pipeline/bin/python bin/plasmid_pipeline.py \
  --input-run /path/to/run \
  --demux-dir /path/to/run/basecalling/pass \
  --kit-name SQK-RBK114-24 \
  --model sup \
  --threads 16 \
  --output results/reused_fastq_run
```

Use CPU basecalling:

```bash
envs/plasmid-pipeline/bin/python bin/plasmid_pipeline.py \
  --input-run /path/to/run \
  --kit-name SQK-RBK114-24 \
  --model hac \
  --device cpu \
  --threads 12
```

Use all CUDA GPUs:

```bash
envs/plasmid-pipeline/bin/python bin/plasmid_pipeline.py \
  --input-run /path/to/run \
  --kit-name SQK-RBK114-24 \
  --model sup \
  --device cuda:all \
  --threads 32
```

Relax barcode/read thresholds for sparse plasmid runs:

```bash
envs/plasmid-pipeline/bin/python bin/plasmid_pipeline.py \
  --input-run /path/to/run \
  --kit-name SQK-RBK114-24 \
  --model sup \
  --threads 16 \
  --min-barcode-reads 20 \
  --min-assembly-reads 2 \
  --min-qscore 7
```

## Important Options

- `--input-run`: ONT run directory, parent folder, POD5 directory, or a single `.pod5` file.
- `--output`: result directory. If omitted, a timestamped directory is created under `results/`.
- `--kit-name`: Dorado kit name. For SQK-RBK114.24 use `SQK-RBK114-24`.
- `--model`: Dorado model selector: `auto`, `sup`, `hac`, `fast`, or a full model path. The default `auto` uses `hac` for Dorado >2.0.0 and `sup` for older Dorado.
- `--device`: Dorado device, for example `auto`, `cpu`, `cuda:0`, or `cuda:all`.
- `--threads`: thread count for assembly/polishing/demux steps.
- `--basecalled-bam`: reuse an existing Dorado BAM and skip basecalling.
- `--demux-dir`: reuse an existing Dorado demultiplexed FASTQ directory.
- `--skip-dorado-update`: skip the daily Dorado update check.
- `--min-barcode-reads`: reject low-count barcode bins.
- `--min-barcode-fraction`: reject barcode bins below a fraction of the strongest barcode.
- `--min-read-length`: remove short reads before assembly.
- `--min-qscore`: remove low average-Q reads before assembly.
- `--min-assembly-reads`: minimum cleaned reads required before representative sequence construction.

## Output Files

Each run writes a result directory containing:

- `README.md`: human-readable run report with tool table, rerun command, completed outputs, and failed/pending resources.
- `summary.json`: machine-readable per-barcode status.
- `progress.jsonl`: live progress events used by the GUI.
- `pipeline.log`: exact commands and stderr from tools.
- `barcode_filtering.tsv`: barcode read counts and accept/reject reasons.
- `representative_sequences.fasta`: combined final representative sequences.
- `02_demux/`: Dorado demultiplexed FASTQs when demux was run inside this output directory.
- `03_barcodes/<barcode>/<barcode>.filtered.fastq`: per-barcode reads after length/Q filtering.
- `03_barcodes/<barcode>/<barcode>.representative.fasta`: final representative plasmid sequence.
- `03_barcodes/<barcode>/plannotate/*.csv`: pLannotate annotation table.
- `03_barcodes/<barcode>/plannotate/*.gbk`: annotated GenBank file.
- `03_barcodes/<barcode>/plannotate/*.html`: interactive plasmid map.
- `03_barcodes/<barcode>/plannotate/*.png`: static plasmid graphic for reports.

## How Barcode Filtering Works

Nanopore demultiplexing can create FASTQs for barcodes that were not actually used. The reporter rejects barcode bins when they are:

- `unclassified`
- below `--min-barcode-reads`
- below `--min-barcode-fraction` of the strongest classified barcode
- absent from a discovered sample sheet, when a sample sheet is present

For accepted barcode bins, reads are filtered by length and mean Q score. This reduces adapter-rich, very short, low-quality, or unrelated reads before assembly.

## How Representative Plasmids Are Built

For each accepted barcode, the reporter tries to produce one representative plasmid sequence:

1. Assemble cleaned reads with Flye.
2. Retry Flye with shorter overlap when needed.
3. Reject suspicious non-circular Flye representatives when they are much larger than the read length distribution.
4. Fall back to a read-seed plus Racon-polished consensus for sparse bins.
5. Collapse tandem duplicated plasmid assemblies using minimap2 self-alignment.
6. Trim simple circular overlaps when detected.
7. Rotate the sequence to a stable canonical start.
8. Annotate with pLannotate and render plasmid maps.

`summary.json` labels Flye-supported representatives as `confidence: standard` and sparse fallback representatives as `confidence: low`.

## Installing Or Refreshing Tools

To install or refresh tools on another machine, run:

```bash
bash scripts/install_tools.sh
```

The installer does not use `sudo`. It prefers conda, mamba, or micromamba, and installs into:

- `envs/plasmid-pipeline/`
- `tools/`
- `resources/functional_annotation_db/` where applicable

Dorado is checked by the pipeline once per day and updated under `tools/dorado/current` when a newer release is available. These installed/downloaded files are local runtime artifacts, not source files for GitHub.

If a copied checkout still contains an old `envs/` directory, rerun `bash scripts/install_tools.sh` or start the GUI with `scripts/run_gui.sh`. The launcher checks for incomplete or non-relocatable local environments before opening the GUI.

## Troubleshooting

If the GUI does not open:

```bash
envs/plasmid-pipeline/bin/python - <<'PY'
import tkinter
print(tkinter.TkVersion)
PY
```

If Tkinter is missing on a fresh Ubuntu system, install the system Tkinter package through the site administrator or run the command-line pipeline instead.

If pLannotate annotation works but PNG images are missing, make sure a headless browser such as Firefox or Chromium is available. HTML maps may still be produced even when PNG export is unavailable.

If no plasmid sequences are produced, inspect:

- `barcode_filtering.tsv`
- `summary.json`
- `pipeline.log`
- the `Failed Or Pending Resources` section of the run `README.md`

Common causes are too few reads in the barcode, overly strict QC thresholds, missing pLannotate databases, or very noisy sequence bins.

## Citation And Reporting

When reporting results, include the run-specific `README.md`, `summary.json`, final FASTA, and pLannotate outputs. pLannotate itself requests citation of its associated publication; see the pLannotate documentation/output for current citation details.
