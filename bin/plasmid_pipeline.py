#!/usr/bin/env python3
"""Automated ONT rapid-barcode plasmid basecalling, assembly, polishing, and annotation."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
LOGS = ROOT / "logs"
ENV = ROOT / "envs" / "plasmid-pipeline"
DEFAULT_DB = ROOT / "resources" / "functional_annotation_db"
DORADO_REPO = "https://api.github.com/repos/nanoporetech/dorado/releases/latest"


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def progress_event(path: Path, phase: str, status: str, progress: float, message: str,
                   barcode: str | None = None) -> None:
    event = {
        "time": now(),
        "phase": phase,
        "status": status,
        "progress": max(0.0, min(1.0, progress)),
        "message": message,
    }
    if barcode:
        event["barcode"] = barcode
    with path.open("a") as handle:
        handle.write(json.dumps(event) + "\n")
    print(f"PROGRESS {event['progress']:.3f} {phase} {status}: {message}", flush=True)


def run(cmd: list[str], log: Path, cwd: Path | None = None, stdout: Path | None = None) -> None:
    line = " ".join(map(str, cmd))
    with log.open("a") as handle:
        handle.write(f"\n[{now()}] $ {line}\n")
    env = os.environ.copy()
    env["PATH"] = f"{ENV / 'bin'}:{TOOLS / 'dorado' / 'current' / 'bin'}:{env.get('PATH', '')}"
    if stdout:
        with stdout.open("wb") as out, log.open("ab") as err:
            proc = subprocess.run(cmd, cwd=cwd, stdout=out, stderr=err, env=env)
    else:
        with log.open("ab") as err:
            proc = subprocess.run(cmd, cwd=cwd, stdout=err, stderr=err, env=env)
    if proc.returncode:
        raise RuntimeError(f"command failed ({proc.returncode}): {line}")


def cmd_output(cmd: list[str]) -> str:
    try:
        env = os.environ.copy()
        env.setdefault("PYTHONWARNINGS", "ignore::UserWarning")
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, env=env).strip()
    except Exception as exc:
        return f"unavailable: {exc}"


def tool_version(path: str) -> str:
    for args in ([path, "--version"], [path, "version"], [path, "-V"]):
        text = cmd_output(args)
        lowered = text.lower()
        if text and "unknown flag" not in lowered and "invalid option" not in lowered and not lowered.startswith("unavailable:"):
            return text.splitlines()[0]
    return "available"


def find_tool(name: str, prefer_env: bool = True) -> str | None:
    paths = []
    if prefer_env:
        paths.append(ENV / "bin" / name)
    paths.extend([TOOLS / "dorado" / "current" / "bin" / name, Path("/bin") / name])
    for path in paths:
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
    return shutil.which(name)


def version_tuple(text: str) -> tuple[int, ...]:
    m = re.search(r"(\d+(?:\.\d+)+)", text)
    return tuple(int(x) for x in m.group(1).split(".")) if m else tuple()


def latest_dorado_release(log: Path) -> dict | None:
    try:
        with urllib.request.urlopen(DORADO_REPO, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        with log.open("a") as handle:
            handle.write(f"[{now()}] Dorado update check failed: {exc}\n")
        return None


def dorado_asset(release: dict) -> tuple[str, str] | None:
    machine = platform.machine().lower()
    arch = "linux-arm64" if machine in {"aarch64", "arm64"} else "linux-x64"
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if arch in name and name.endswith((".tar.gz", ".tgz")):
            return name, asset["browser_download_url"]
    tag = str(release.get("tag_name", "")).lstrip("v")
    if tag:
        name = f"dorado-{tag}-{arch}.tar.gz"
        return name, f"https://cdn.oxfordnanoportal.com/software/analysis/{name}"
    return None


def ensure_dorado(args: argparse.Namespace, log: Path) -> str:
    TOOLS.mkdir(exist_ok=True)
    dorado_dir = TOOLS / "dorado"
    dorado_dir.mkdir(parents=True, exist_ok=True)
    stamp = dorado_dir / ".last_update_check"
    today = dt.date.today().isoformat()
    current = find_tool("dorado")
    if args.skip_dorado_update:
        return current or "dorado"
    if stamp.exists() and stamp.read_text().strip() == today and current:
        return current

    release = latest_dorado_release(log)
    if not release:
        if current:
            return current
        raise RuntimeError("Dorado is not installed and latest release metadata could not be fetched.")
    latest_tag = release.get("tag_name", "")
    current_version = cmd_output([current, "--version"]) if current else ""
    asset = dorado_asset(release)
    need_install = not current or version_tuple(current_version) < version_tuple(latest_tag)
    with log.open("a") as handle:
        handle.write(f"[{now()}] Dorado current={current_version!r} latest={latest_tag!r} need_install={need_install}\n")
    if need_install and asset:
        name, url = asset
        archive = dorado_dir / name
        with log.open("a") as handle:
            handle.write(f"[{now()}] Downloading Dorado: {url}\n")
        urllib.request.urlretrieve(url, archive)
        extract_dir = dorado_dir / latest_tag
        tmp_dir = dorado_dir / f".extract-{latest_tag}"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(tmp_dir)
        members = [p for p in tmp_dir.iterdir() if p.is_dir()]
        if not members:
            raise RuntimeError(f"could not locate Dorado directory in {archive}")
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        shutil.move(str(members[0]), extract_dir)
        current_link = dorado_dir / "current"
        if current_link.exists() or current_link.is_symlink():
            current_link.unlink()
        current_link.symlink_to(extract_dir, target_is_directory=True)
        current = str(current_link / "bin" / "dorado")
    stamp.write_text(today + "\n")
    return current or "dorado"


def normalize_kit(kit: str) -> str:
    return kit.replace(".", "-").upper()


def fastq_records(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", errors="replace") as handle:
        while True:
            name = handle.readline().rstrip()
            if not name:
                break
            seq = handle.readline().rstrip()
            handle.readline()
            qual = handle.readline().rstrip()
            yield name, seq, qual


def qscore(qual: str) -> float:
    if not qual:
        return 0.0
    return sum(ord(c) - 33 for c in qual) / len(qual)


def write_filtered_fastq(src: Path, dst: Path, min_len: int, min_q: float) -> dict:
    n = kept = bases = 0
    lengths = []
    with dst.open("w") as out:
        for name, seq, qual in fastq_records(src):
            n += 1
            if len(seq) >= min_len and qscore(qual) >= min_q:
                kept += 1
                bases += len(seq)
                lengths.append(len(seq))
                out.write(f"{name}\n{seq}\n+\n{qual}\n")
    return {"raw_reads": n, "kept_reads": kept, "kept_bases": bases, "n50": n50(lengths)}


def n50(lengths: list[int]) -> int:
    if not lengths:
        return 0
    half = sum(lengths) / 2
    acc = 0
    for length in sorted(lengths, reverse=True):
        acc += length
        if acc >= half:
            return length
    return 0


def read_fasta(path: Path) -> list[tuple[str, str]]:
    records, name, parts = [], None, []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if name:
                    records.append((name, "".join(parts)))
                name, parts = line[1:].split()[0], []
            else:
                parts.append(line.upper())
    if name:
        records.append((name, "".join(parts)))
    return records


def write_fasta(path: Path, records: list[tuple[str, str]]) -> None:
    with path.open("w") as out:
        for name, seq in records:
            out.write(f">{name}\n")
            for i in range(0, len(seq), 80):
                out.write(seq[i:i + 80] + "\n")


def read_seed_polish(filtered_fastq: Path, out_dir: Path, minimap2: str | None, racon: str | None,
                     threads: int, log: Path) -> tuple[Path, dict]:
    records = list(fastq_records(filtered_fastq))
    if not records:
        raise RuntimeError("no reads available for seed polishing")
    lengths = sorted(len(seq) for _, seq, _ in records)
    median_len = lengths[len(lengths) // 2]
    name, seq, qual = max(records, key=lambda rec: (-abs(len(rec[1]) - median_len), qscore(rec[2])))
    out_dir.mkdir(parents=True, exist_ok=True)
    seed = out_dir / "seed.fasta"
    write_fasta(seed, [(f"seed median_length={median_len} read_q={qscore(qual):.2f}", seq)])
    current = seed
    rounds_completed = 0
    if minimap2 and racon and len(records) >= 2:
        for i in range(1, 3):
            paf = out_dir / f"racon_seed_round{i}.paf"
            polished = out_dir / f"racon_seed_round{i}.fasta"
            run([minimap2, "-x", "map-ont", str(current), str(filtered_fastq)], log, stdout=paf)
            run([racon, "-t", str(threads), str(filtered_fastq), str(paf), str(current)], log, stdout=polished)
            if polished.exists() and polished.stat().st_size:
                current = polished
                rounds_completed = i
    meta = {
        "method": "read_seed_racon_low_confidence",
        "seed_read": name.split()[0].lstrip("@"),
        "seed_length": len(seq),
        "seed_qscore": round(qscore(qual), 3),
        "median_read_length": median_len,
        "polish_rounds": rounds_completed,
    }
    return current, meta


def trim_circular_overlap(seq: str, min_overlap: int = 80, max_overlap: int = 3000, min_identity: float = 0.94) -> tuple[str, int, float]:
    max_overlap = min(max_overlap, len(seq) // 2)
    best = (0, 0.0)
    for olen in range(max_overlap, min_overlap - 1, -1):
        left = seq[:olen]
        right = seq[-olen:]
        matches = sum(1 for a, b in zip(left, right) if a == b)
        ident = matches / olen
        if ident >= min_identity:
            best = (olen, ident)
            break
    if best[0]:
        return seq[:-best[0]], best[0], best[1]
    return seq, 0, 0.0


def collapse_tandem_self_alignment(seq: str, minimap2: str | None, work_dir: Path, log: Path,
                                   min_identity: float = 0.90) -> tuple[str, dict]:
    if not minimap2 or len(seq) < 3000:
        return seq, {}
    work_dir.mkdir(parents=True, exist_ok=True)
    query = work_dir / "self_query.fasta"
    paf = work_dir / "self_alignment.paf"
    write_fasta(query, [("self", seq)])
    try:
        run([minimap2, "-x", "asm5", "-DP", str(query), str(query)], log, stdout=paf)
    except Exception as exc:
        with log.open("a") as handle:
            handle.write(f"[{now()}] Tandem self-alignment failed: {exc}\n")
        return seq, {}

    best = None
    n = len(seq)
    with paf.open() as handle:
        for line in handle:
            fields = line.rstrip().split("\t")
            if len(fields) < 12:
                continue
            qlen = int(fields[1])
            qstart, qend = int(fields[2]), int(fields[3])
            strand = fields[4]
            tlen = int(fields[6])
            tstart, tend = int(fields[7]), int(fields[8])
            matches, aln_len = int(fields[9]), int(fields[10])
            if qlen != n or tlen != n or strand != "+":
                continue
            if abs(qstart - tstart) < 50:
                continue
            identity = matches / aln_len if aln_len else 0
            offset = abs(tstart - qstart)
            if offset < 1000 or offset > int(n * 0.85):
                continue
            if n / offset < 1.45:
                continue
            if aln_len < max(1000, int(offset * 0.70)):
                continue
            if identity < min_identity:
                continue
            score = (identity, aln_len, -offset)
            if best is None or score > best[0]:
                best = (score, min(qstart, tstart), max(qstart, tstart), identity, aln_len, offset)
    if best is None:
        return seq, {}

    _, start, end, identity, aln_len, offset = best
    collapsed = seq[start:end]
    if len(collapsed) < 1000:
        return seq, {}
    meta = {
        "method": "minimap2_self_tandem_collapse",
        "original_length": n,
        "collapsed_length": len(collapsed),
        "repeat_start_1": start,
        "repeat_start_2": end,
        "repeat_offset": offset,
        "alignment_length": aln_len,
        "identity": round(identity, 4),
    }
    return collapsed, meta


def canonical_rotate(seq: str, k: int = 31) -> tuple[str, int]:
    if len(seq) <= k:
        return seq, 0
    doubled = seq + seq
    candidates = [(doubled[i:i + k], i) for i in range(len(seq)) if "N" not in doubled[i:i + k]]
    if not candidates:
        return seq, 0
    _, pos = min(candidates)
    return doubled[pos:pos + len(seq)], pos


def parse_flye_info(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open() as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return {row.get("#seq_name") or row.get("seq_name"): row for row in reader}


def assembly_contigs(flye_dir: Path) -> Path | None:
    for name in ["assembly.fasta", "contigs.fasta"]:
        path = flye_dir / name
        if path.exists() and path.stat().st_size:
            return path
    return None


def choose_representative(fasta: Path, info: dict[str, dict], min_len: int) -> tuple[str, str, dict]:
    records = [(name, seq) for name, seq in read_fasta(fasta) if len(seq) >= min_len]
    if not records:
        raise RuntimeError("no contigs passed minimum length")
    def score(item):
        name, seq = item
        meta = info.get(name, {})
        circ = 1 if str(meta.get("circ.", meta.get("circular", ""))).lower() in {"y", "yes", "true", "1"} else 0
        cov = float(str(meta.get("cov.", meta.get("coverage", 0)) or 0).replace(",", ""))
        return (circ, cov, len(seq))
    name, seq = max(records, key=score)
    return name, seq, info.get(name, {})


def tool_table(tools: list[str]) -> str:
    lines = ["| Tool | Path | Version/status |", "|---|---|---|"]
    for tool in tools:
        path = find_tool(tool)
        if path:
            ver = tool_version(path)
            lines.append(f"| {tool} | `{path}` | {ver} |")
        else:
            lines.append(f"| {tool} | pending | not found |")
    return "\n".join(lines)


def sample_sheet_barcodes(run_dir: Path) -> set[str]:
    sheets = list(run_dir.rglob("*sample_sheet*.csv"))
    if not sheets:
        return set()
    barcodes = set()
    for sheet in sheets:
        text = sheet.read_text(errors="replace")
        barcodes.update(re.findall(r"\bbarcode\d{2,3}\b", text, re.I))
    return barcodes


def demux_fastqs(demux_dir: Path) -> list[Path]:
    patterns = ("*.fastq", "*.fq", "*.fastq.gz", "*.fq.gz")
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(demux_dir.rglob(pattern))
    return sorted(paths)


def barcode_name(path: Path) -> str:
    m = re.search(r"(barcode\d{2,3}|unclassified)", path.name, re.I)
    return m.group(1).lower() if m else path.stem


def render_html_png(html: Path, png: Path, log: Path) -> bool:
    browser = find_tool("firefox", prefer_env=False) or find_tool("chromium", prefer_env=False) or find_tool("chromium-browser", prefer_env=False)
    if not browser:
        with log.open("a") as handle:
            handle.write(f"[{now()}] No headless browser found for plasmid map PNG export.\n")
        return False
    png = png.resolve()
    png.parent.mkdir(parents=True, exist_ok=True)
    html_uri = html.resolve().as_uri()
    if Path(browser).name.startswith("firefox"):
        cmd = [browser, "--headless", "--window-size", "900,900", "--screenshot", str(png), html_uri]
    else:
        cmd = [
            browser, "--headless", "--disable-gpu", "--no-sandbox",
            "--window-size=900,900", f"--screenshot={png}", html_uri,
        ]
    try:
        env = os.environ.copy()
        env.setdefault("MOZ_HEADLESS", "1")
        line = " ".join(map(str, cmd))
        with log.open("a") as handle:
            handle.write(f"\n[{now()}] $ {line}\n")
        with log.open("ab") as err:
            proc = subprocess.run(cmd, stderr=err, stdout=err, env=env)
        if proc.returncode:
            raise RuntimeError(f"command failed ({proc.returncode}): {line}")
        return png.exists() and png.stat().st_size > 0
    except Exception as exc:
        with log.open("a") as handle:
            handle.write(f"[{now()}] Plasmid map PNG export failed: {exc}\n")
        return False


def annotate(plannotate: str, fasta: Path, out_dir: Path, log: Path, db: Path | None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {"status": "failed", "graphics": "failed", "html": [], "png": []}
    commands = [
        [plannotate, "batch", "-i", str(fasta), "-o", str(out_dir), "-c", "-h"],
    ]
    for cmd in commands:
        try:
            env = os.environ.copy()
            env.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
            line = " ".join(map(str, cmd))
            with log.open("a") as handle:
                handle.write(f"\n[{now()}] $ {line}\n")
            with log.open("ab") as err:
                proc = subprocess.run(cmd, stderr=err, stdout=err, env=env)
            if proc.returncode:
                raise RuntimeError(f"command failed ({proc.returncode}): {line}")
            result["status"] = "completed"
            html_files = sorted(out_dir.glob(f"{fasta.stem}*_pLann.html"))
            if not html_files:
                html_files = sorted(out_dir.glob("*.html"))
            result["html"] = [str(path) for path in html_files]
            png_files = []
            for html in html_files:
                png = html.with_suffix(".png")
                if render_html_png(html, png, log):
                    png_files.append(png)
            result["png"] = [str(path) for path in png_files]
            if png_files:
                result["graphics"] = "completed"
            elif html_files:
                result["graphics"] = "html_only"
            return result
        except Exception as exc:
            with log.open("a") as handle:
                handle.write(f"[{now()}] pLannotate attempt failed: {exc}\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-run", required=True, type=Path, help="ONT run directory containing pod5/ or a POD5 directory")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / f"plasmid_pipeline_{dt.datetime.now():%Y%m%d_%H%M%S}")
    parser.add_argument("--kit-name", default="SQK-RBK114-24")
    parser.add_argument("--model", default="sup", help="Dorado model selector, e.g. sup, hac, fast, or a model path")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--threads", type=int, default=max(1, os.cpu_count() or 1))
    parser.add_argument("--min-qscore", type=float, default=9)
    parser.add_argument("--min-read-length", type=int, default=700)
    parser.add_argument("--min-barcode-reads", type=int, default=100)
    parser.add_argument("--min-assembly-reads", type=int, default=3,
                        help="Minimum reads remaining after length/QC filtering before assembly is attempted")
    parser.add_argument("--min-barcode-fraction", type=float, default=0.01)
    parser.add_argument("--min-contig-length", type=int, default=1000)
    parser.add_argument("--skip-dorado-update", action="store_true")
    parser.add_argument("--basecalled-bam", type=Path, help="Reuse an existing Dorado BAM instead of basecalling POD5")
    parser.add_argument("--demux-dir", type=Path, help="Reuse an existing Dorado demux directory")
    parser.add_argument("--db-dir", type=Path, default=Path(os.environ.get("ARDEA_FUNC_DB", DEFAULT_DB)))
    args = parser.parse_args()

    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    log = out / "pipeline.log"
    log.write_text(f"Pipeline started: {now()}\n")
    progress_path = out / "progress.jsonl"
    progress_path.write_text("")
    progress_event(progress_path, "setup", "running", 0.02, "Preparing output folder and checking tools")

    kit = normalize_kit(args.kit_name)
    dorado = ensure_dorado(args, log)
    flye = find_tool("flye")
    minimap2 = find_tool("minimap2")
    samtools = find_tool("samtools")
    racon = find_tool("racon")
    plannotate = find_tool("plannotate")
    seqkit = find_tool("seqkit")
    required = {"flye": flye, "minimap2": minimap2, "samtools": samtools}
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"missing required tools after install attempt: {', '.join(missing)}. Run scripts/install_tools.sh")
    progress_event(progress_path, "setup", "completed", 0.08, "Required tools are available")

    run_dir = args.input_run.resolve()
    pod5_files = sorted(run_dir.rglob("*.pod5")) if run_dir.is_dir() else []
    if run_dir.is_file() and run_dir.suffix == ".pod5":
        pod5_dir = run_dir
    elif (run_dir / "pod5").exists() and list((run_dir / "pod5").rglob("*.pod5")):
        pod5_dir = run_dir / "pod5"
    elif pod5_files:
        pod5_dir = run_dir
    else:
        pod5_dir = run_dir / "pod5"
    completed, failed, pending = [], [], []

    if args.basecalled_bam:
        calls_bam = args.basecalled_bam.resolve()
        completed.append(f"Reused basecalled BAM: {calls_bam}")
        progress_event(progress_path, "basecalling", "completed", 0.18, f"Reusing basecalled BAM: {calls_bam}")
    else:
        if not pod5_dir.exists():
            raise RuntimeError(f"no POD5 directory found: {pod5_dir}")
        models_dir = TOOLS / "dorado_models"
        models_dir.mkdir(parents=True, exist_ok=True)
        bc_dir = out / "01_basecalling"
        bc_dir.mkdir(exist_ok=True)
        calls_bam = bc_dir / "calls.bam"
        if not calls_bam.exists():
            progress_event(progress_path, "basecalling", "running", 0.10, "Downloading/checking Dorado model")
            run([dorado, "download", "--model", args.model, "--data", str(pod5_dir), "--recursive",
                 "--models-directory", str(models_dir)], log)
            progress_event(progress_path, "basecalling", "running", 0.12, "Basecalling POD5 files with Dorado")
            run([dorado, "basecaller", args.model, str(pod5_dir), "--recursive", "--kit-name", kit,
                 "--device", args.device, "--models-directory", str(models_dir)], log, stdout=calls_bam)
        progress_event(progress_path, "basecalling", "completed", 0.18, f"Basecalled reads: {calls_bam}")
        completed.append(f"Basecalled POD5 with Dorado: {calls_bam}")

    demux_dir = args.demux_dir.resolve() if args.demux_dir else out / "02_demux"
    if not args.demux_dir:
        demux_dir.mkdir(exist_ok=True)
        if not demux_fastqs(demux_dir):
            progress_event(progress_path, "demultiplexing", "running", 0.20, "Demultiplexing reads with Dorado")
            run([dorado, "demux", "--kit-name", kit, "--emit-fastq", "--emit-summary",
                 "--threads", str(args.threads), "--output-dir", str(demux_dir), str(calls_bam)], log)
    progress_event(progress_path, "demultiplexing", "completed", 0.28, f"Demultiplexed reads: {demux_dir}")
    completed.append(f"Demultiplexed reads: {demux_dir}")

    progress_event(progress_path, "barcode_filtering", "running", 0.30, "Counting demultiplexed reads by barcode")
    fastqs = demux_fastqs(demux_dir)
    used_barcodes = sample_sheet_barcodes(run_dir)
    counts = []
    for fq in fastqs:
        raw = sum(1 for _ in fastq_records(fq))
        counts.append((barcode_name(fq), fq, raw))
    classified_counts = [raw for bc, _, raw in counts if bc != "unclassified"]
    max_reads = max(classified_counts, default=0)
    accepted = []
    barcode_table = out / "barcode_filtering.tsv"
    with barcode_table.open("w") as table:
        table.write("barcode\tfastq\traw_reads\tdecision\treason\n")
        for bc, fq, raw in counts:
            reasons = []
            if bc == "unclassified":
                reasons.append("unclassified")
            if raw < args.min_barcode_reads:
                reasons.append(f"reads<{args.min_barcode_reads}")
            if max_reads and raw / max_reads < args.min_barcode_fraction:
                reasons.append(f"fraction_of_max<{args.min_barcode_fraction}")
            if used_barcodes and bc not in {b.lower() for b in used_barcodes}:
                reasons.append("not_in_sample_sheet")
            decision = "accepted" if not reasons else "rejected"
            table.write(f"{bc}\t{fq}\t{raw}\t{decision}\t{';'.join(reasons)}\n")
            if decision == "accepted":
                accepted.append((bc, fq, raw))
    completed.append(f"Barcode abundance filtering: {barcode_table}")
    progress_event(progress_path, "barcode_filtering", "completed", 0.36,
                   f"Accepted {len(accepted)} barcode bins after abundance filtering")

    final_records = []
    per_barcode = []
    total_accepted = max(1, len(accepted))
    for index, (bc, fq, raw) in enumerate(accepted, start=1):
        base_progress = 0.36 + ((index - 1) / total_accepted) * 0.56
        step_progress = 0.56 / total_accepted
        bc_dir = out / "03_barcodes" / bc
        bc_dir.mkdir(parents=True, exist_ok=True)
        filtered = bc_dir / f"{bc}.filtered.fastq"
        progress_event(progress_path, "read_qc", "running", base_progress + step_progress * 0.05,
                       f"Filtering reads for {bc}", bc)
        stats = write_filtered_fastq(fq, filtered, args.min_read_length, args.min_qscore)
        if stats["kept_reads"] < args.min_assembly_reads:
            failed.append(f"{bc}: too few reads after read QC ({stats['kept_reads']} < {args.min_assembly_reads})")
            per_barcode.append({"barcode": bc, **stats, "status": "failed_read_qc"})
            progress_event(progress_path, "read_qc", "failed", base_progress + step_progress * 0.95,
                           f"{bc}: too few reads after QC ({stats['kept_reads']})", bc)
            continue
        flye_dir = bc_dir / "flye"
        contigs = assembly_contigs(flye_dir)
        assembly_method = "flye"
        fallback_meta: dict = {}
        if not contigs and stats["kept_reads"] >= 10:
            progress_event(progress_path, "assembly", "running", base_progress + step_progress * 0.20,
                           f"Assembling {bc} with Flye", bc)
            flye_attempts = [
                (flye_dir, [flye, "--nano-hq", str(filtered), "--out-dir", str(flye_dir), "--threads", str(args.threads)]),
                (bc_dir / "flye_minovlp3000", [flye, "--nano-hq", str(filtered), "--out-dir", str(bc_dir / "flye_minovlp3000"),
                                               "--threads", str(args.threads), "--min-overlap", "3000"]),
            ]
            for attempt_dir, cmd in flye_attempts:
                if assembly_contigs(attempt_dir):
                    flye_dir = attempt_dir
                    contigs = assembly_contigs(attempt_dir)
                    break
                try:
                    run(cmd, log)
                    flye_dir = attempt_dir
                    contigs = assembly_contigs(attempt_dir)
                    if contigs:
                        break
                except Exception as exc:
                    with log.open("a") as handle:
                        handle.write(f"[{now()}] Flye attempt failed for {bc}: {exc}\n")

        if contigs:
            source_for_final = contigs
            progress_event(progress_path, "assembly", "completed", base_progress + step_progress * 0.48,
                           f"Flye assembly available for {bc}", bc)
        else:
            try:
                progress_event(progress_path, "assembly", "running", base_progress + step_progress * 0.35,
                               f"Using read-seed fallback for {bc}", bc)
                source_for_final, fallback_meta = read_seed_polish(filtered, bc_dir / "read_seed_polish",
                                                                   minimap2, racon, args.threads, log)
                assembly_method = "read_seed_racon_low_confidence"
                progress_event(progress_path, "assembly", "completed", base_progress + step_progress * 0.48,
                               f"Read-seed fallback complete for {bc}", bc)
            except Exception as exc:
                failed.append(f"{bc}: assembly and read-seed fallback failed: {exc}")
                per_barcode.append({"barcode": bc, **stats, "status": "failed_assembly"})
                progress_event(progress_path, "assembly", "failed", base_progress + step_progress * 0.95,
                               f"{bc}: assembly failed", bc)
                continue

        polished = bc_dir / "polished.fasta"
        info = parse_flye_info(flye_dir / "assembly_info.txt") if assembly_method == "flye" else {}
        if assembly_method == "flye" and stats.get("n50", 0):
            try:
                raw_name, raw_seq, raw_meta = choose_representative(source_for_final, info, args.min_contig_length)
                circ = str(raw_meta.get("circ.", raw_meta.get("circular", ""))).lower() in {"y", "yes", "true", "1"}
                length_ratio = len(raw_seq) / stats["n50"] if stats["n50"] else 0
                if not circ and length_ratio > 1.2:
                    with log.open("a") as handle:
                        handle.write(
                            f"[{now()}] Flye representative for {bc} is non-circular and length/read_N50="
                            f"{length_ratio:.3f}; switching to read-seed fallback.\n"
                        )
                    source_for_final, fallback_meta = read_seed_polish(filtered, bc_dir / "read_seed_polish",
                                                                       minimap2, racon, args.threads, log)
                    fallback_meta["rejected_flye_length"] = len(raw_seq)
                    fallback_meta["rejected_flye_circular"] = circ
                    fallback_meta["rejected_flye_length_read_n50_ratio"] = round(length_ratio, 3)
                    assembly_method = "read_seed_racon_low_confidence"
                    info = {}
            except Exception as exc:
                with log.open("a") as handle:
                    handle.write(f"[{now()}] Flye sanity check failed for {bc}: {exc}\n")

        if assembly_method == "flye" and racon and minimap2:
            progress_event(progress_path, "polishing", "running", base_progress + step_progress * 0.52,
                           f"Polishing {bc} with racon", bc)
            round_in = contigs
            for i in range(1, 3):
                paf = bc_dir / f"racon_round{i}.paf"
                round_out = bc_dir / f"racon_round{i}.fasta"
                run([minimap2, "-x", "map-ont", str(round_in), str(filtered)], log, stdout=paf)
                run([racon, "-t", str(args.threads), str(filtered), str(paf), str(round_in)], log, stdout=round_out)
                round_in = round_out
            source_for_final = round_in
            shutil.copyfile(source_for_final, polished)
        else:
            if assembly_method == "flye":
                pending.append(f"{bc}: racon not available; using Flye-polished contig only")

        try:
            rep_name, rep_seq, meta = choose_representative(source_for_final, info, args.min_contig_length)
        except Exception as exc:
            failed.append(f"{bc}: no representative contig selected: {exc}")
            per_barcode.append({"barcode": bc, **stats, "status": "failed_representative"})
            progress_event(progress_path, "representative", "failed", base_progress + step_progress * 0.95,
                           f"{bc}: no representative contig selected", bc)
            continue
        progress_event(progress_path, "circularization", "running", base_progress + step_progress * 0.70,
                       f"Circularizing representative sequence for {bc}", bc)
        collapsed_seq, collapse_meta = collapse_tandem_self_alignment(
            rep_seq, minimap2, bc_dir / "circularization", log
        )
        trimmed_seq, overlap, ident = trim_circular_overlap(collapsed_seq)
        final_seq, rotation = canonical_rotate(trimmed_seq)
        final_name = (f"{bc}_representative length={len(final_seq)} method={assembly_method} "
                      f"circular_overlap_trimmed={overlap} rotation={rotation}")
        final_fasta = bc_dir / f"{bc}.representative.fasta"
        write_fasta(final_fasta, [(final_name, final_seq)])
        final_records.append((final_name, final_seq))
        progress_event(progress_path, "representative", "completed", base_progress + step_progress * 0.78,
                       f"Representative FASTA complete for {bc} ({len(final_seq)} bp)", bc)

        ann_status = "pending"
        ann_meta: dict = {}
        if plannotate:
            progress_event(progress_path, "annotation", "running", base_progress + step_progress * 0.82,
                           f"Annotating and drawing plasmid map for {bc}", bc)
            ann_meta = annotate(plannotate, final_fasta, bc_dir / "plannotate", log, args.db_dir)
            ann_status = str(ann_meta.get("status", "failed"))
            if ann_status == "failed":
                pending.append(f"{bc}: pLannotate command/database failed; representative FASTA is complete")
            elif ann_meta.get("graphics") == "html_only":
                pending.append(f"{bc}: pLannotate HTML plasmid map complete; PNG export unavailable or failed")
            elif ann_meta.get("graphics") == "failed":
                pending.append(f"{bc}: pLannotate annotation complete; plasmid map generation failed")
        else:
            pending.append(f"{bc}: pLannotate not installed")
        progress_event(progress_path, "barcode", "completed", base_progress + step_progress * 0.98,
                       f"{bc} complete", bc)
        per_barcode.append({"barcode": bc, **stats, "status": "completed", "representative": str(final_fasta),
                            "length": len(final_seq), "overlap_trimmed": overlap, "overlap_identity": round(ident, 4),
                            "rotation": rotation, "annotation": ann_status, "method": assembly_method,
                            "confidence": "standard" if assembly_method == "flye" else "low",
                            "flye_meta": meta, "fallback_meta": fallback_meta,
                            "circularization_meta": collapse_meta, "annotation_meta": ann_meta})

    combined = out / "representative_sequences.fasta"
    progress_event(progress_path, "finalizing", "running", 0.94, "Writing combined results and reports")
    if final_records:
        write_fasta(combined, final_records)
        completed.append(f"Representative sequences: {combined}")
    else:
        failed.append("No representative plasmid sequences were produced")

    summary_json = out / "summary.json"
    summary_json.write_text(json.dumps({"completed": completed, "failed": failed, "pending": pending,
                                        "barcodes": per_barcode}, indent=2))
    readme = out / "README.md"
    readme.write_text(
        "# Nanopore plasmid sequencing reporter run\n\n"
        f"Started: {now()}\n\n"
        "## Tool Availability\n\n"
        + tool_table(["dorado", "flye", "minimap2", "samtools", "seqkit", "racon", "plannotate"]) + "\n\n"
        "## Exact Rerun Command\n\n"
        "```bash\n"
        + " ".join(sys.argv) + "\n"
        "```\n\n"
        "## Completed Results\n\n"
        + "\n".join(f"- {x}" for x in completed) + "\n\n"
        "## Failed Or Pending Resources\n\n"
        + ("\n".join(f"- {x}" for x in failed + pending) if failed or pending else "- None") + "\n\n"
        "## Output Files\n\n"
        f"- `barcode_filtering.tsv`: accepted/rejected barcode evidence.\n"
        f"- `representative_sequences.fasta`: combined final plasmid representatives, when produced.\n"
        f"- `03_barcodes/<barcode>/plannotate/`: pLannotate CSV, GenBank, HTML plasmid map, and PNG plasmid graphic when available.\n"
        f"- `summary.json`: machine-readable run status.\n"
        f"- `pipeline.log`: exact command log and stderr.\n"
    )
    if final_records:
        progress_event(progress_path, "done", "completed", 1.0, f"Run complete: {out}")
    else:
        progress_event(progress_path, "done", "failed", 1.0, f"Run finished without representative plasmid sequences: {out}")
    print(f"Run complete: {out}")
    print(f"Summary: {readme}")
    return 0 if final_records else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
