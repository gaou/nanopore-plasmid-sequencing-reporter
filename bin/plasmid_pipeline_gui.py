#!/usr/bin/env python3
"""Tkinter GUI for the ONT plasmid sequencing pipeline."""

from __future__ import annotations

import datetime as dt
import json
import os
import queue
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from tkinter import BOTH, END, HORIZONTAL, LEFT, RIGHT, VERTICAL, X, Y, filedialog, messagebox
import tkinter as tk
from tkinter import ttk


ROOT = Path(__file__).resolve().parents[1]
ENV_PYTHON = ROOT / "envs" / "plasmid-pipeline" / "bin" / "python"
PIPELINE = ROOT / "bin" / "plasmid_pipeline.py"


def collect_package_files(output_dir: Path) -> list[Path]:
    include_files: list[Path] = []
    top_names = [
        "README.md", "summary.json", "progress.jsonl", "pipeline.log",
        "barcode_filtering.tsv", "representative_sequences.fasta",
    ]
    for name in top_names:
        path = output_dir / name
        if path.exists():
            include_files.append(path)
    for pattern in ("02_demux/**/*.fastq", "02_demux/**/*.fq", "02_demux/**/*.fastq.gz", "02_demux/**/*.fq.gz"):
        include_files.extend(output_dir.glob(pattern))
    barcode_root = output_dir / "03_barcodes"
    for pattern in (
        "barcode*/barcode*.filtered.fastq",
        "barcode*/barcode*.representative.fasta",
        "barcode*/plannotate/*",
    ):
        include_files.extend(barcode_root.glob(pattern))
    return sorted({path.resolve() for path in include_files if path.is_file()})


def write_result_package(output_dir: Path, target: Path) -> int:
    unique = collect_package_files(output_dir)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in unique:
            archive.write(path, arcname=path.relative_to(output_dir))
    return len(unique)


class PipelineGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Nanopore plasmid sequencing reporter")
        self.geometry("1280x860")
        self.minsize(1100, 720)

        self.process: subprocess.Popen | None = None
        self.output_dir: Path | None = None
        self.progress_file: Path | None = None
        self.progress_offset = 0
        self.stdout_queue: queue.Queue[str] = queue.Queue()
        self.task_rows: dict[str, str] = {}
        self.summary: dict = {}
        self.barcode_records: list[dict] = []
        self.current_image: tk.PhotoImage | None = None

        self.run_folder = tk.StringVar(value=str(ROOT))
        self.output_folder = tk.StringVar(value="")
        self.kit_name = tk.StringVar(value="SQK-RBK114-24")
        self.model = tk.StringVar(value="sup")
        self.threads = tk.IntVar(value=max(1, (os.cpu_count() or 1) // 2))
        self.min_qscore = tk.DoubleVar(value=9.0)
        self.min_barcode_reads = tk.IntVar(value=100)
        self.skip_dorado_update = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="Select a run folder to begin.")
        self.progress_value = tk.DoubleVar(value=0.0)

        self._build_ui()
        self.after(300, self._poll)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=BOTH, expand=True)

        controls = ttk.LabelFrame(root, text="Run Setup", padding=10)
        controls.pack(fill=X)

        ttk.Label(controls, text="Run folder").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.run_folder).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(controls, text="Browse...", command=self.choose_run_folder).grid(row=0, column=2)

        ttk.Label(controls, text="Output folder").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(controls, textvariable=self.output_folder).grid(row=1, column=1, sticky="ew", padx=8, pady=(6, 0))
        ttk.Button(controls, text="Browse...", command=self.choose_output_folder).grid(row=1, column=2, pady=(6, 0))

        options = ttk.Frame(controls)
        options.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Label(options, text="Kit").pack(side=LEFT)
        ttk.Entry(options, textvariable=self.kit_name, width=18).pack(side=LEFT, padx=(4, 14))
        ttk.Label(options, text="Model").pack(side=LEFT)
        ttk.Combobox(options, textvariable=self.model, values=("sup", "hac", "fast"), width=8).pack(side=LEFT, padx=(4, 14))
        ttk.Label(options, text="Threads").pack(side=LEFT)
        ttk.Spinbox(options, textvariable=self.threads, from_=1, to=max(1, os.cpu_count() or 64), width=6).pack(side=LEFT, padx=(4, 14))
        ttk.Label(options, text="Min Q").pack(side=LEFT)
        ttk.Spinbox(options, textvariable=self.min_qscore, from_=0, to=30, increment=0.5, width=6).pack(side=LEFT, padx=(4, 14))
        ttk.Label(options, text="Min barcode reads").pack(side=LEFT)
        ttk.Spinbox(options, textvariable=self.min_barcode_reads, from_=1, to=100000, increment=10, width=8).pack(side=LEFT, padx=(4, 14))
        ttk.Checkbutton(options, text="Skip Dorado update check", variable=self.skip_dorado_update).pack(side=LEFT)

        buttons = ttk.Frame(controls)
        buttons.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.run_button = ttk.Button(buttons, text="Run Pipeline", command=self.run_pipeline)
        self.run_button.pack(side=LEFT)
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop_pipeline, state="disabled")
        self.stop_button.pack(side=LEFT, padx=6)
        ttk.Button(buttons, text="Open Existing Result...", command=self.open_existing_result).pack(side=LEFT, padx=6)
        self.save_button = ttk.Button(buttons, text="Save As...", command=self.save_package, state="disabled")
        self.save_button.pack(side=RIGHT)

        controls.columnconfigure(1, weight=1)

        progress_frame = ttk.LabelFrame(root, text="Progress", padding=10)
        progress_frame.pack(fill=X, pady=(10, 0))
        ttk.Label(progress_frame, textvariable=self.status_text).pack(anchor="w")
        ttk.Progressbar(progress_frame, variable=self.progress_value, maximum=100, orient=HORIZONTAL).pack(fill=X, pady=(6, 0))

        body = ttk.PanedWindow(root, orient=HORIZONTAL)
        body.pack(fill=BOTH, expand=True, pady=(10, 0))

        left = ttk.Frame(body)
        body.add(left, weight=1)
        right = ttk.Frame(body)
        body.add(right, weight=3)

        task_box = ttk.LabelFrame(left, text="Tasks", padding=6)
        task_box.pack(fill=BOTH, expand=True)
        self.task_tree = ttk.Treeview(task_box, columns=("status", "message"), show="headings", height=12)
        self.task_tree.heading("status", text="Status")
        self.task_tree.heading("message", text="Task")
        self.task_tree.column("status", width=90, stretch=False)
        self.task_tree.column("message", width=360)
        self.task_tree.pack(fill=BOTH, expand=True)

        barcode_box = ttk.LabelFrame(left, text="Barcodes", padding=6)
        barcode_box.pack(fill=BOTH, expand=True, pady=(10, 0))
        self.barcode_list = tk.Listbox(barcode_box, exportselection=False, height=10)
        self.barcode_list.pack(side=LEFT, fill=BOTH, expand=True)
        scroll = ttk.Scrollbar(barcode_box, orient=VERTICAL, command=self.barcode_list.yview)
        scroll.pack(side=RIGHT, fill=Y)
        self.barcode_list.configure(yscrollcommand=scroll.set)
        self.barcode_list.bind("<<ListboxSelect>>", self.on_barcode_selected)

        result_tabs = ttk.Notebook(right)
        result_tabs.pack(fill=BOTH, expand=True)

        image_tab = ttk.Frame(result_tabs)
        result_tabs.add(image_tab, text="Plasmid Graphic")
        self.image_canvas = tk.Canvas(image_tab, bg="white")
        self.image_canvas.pack(fill=BOTH, expand=True)

        fasta_tab = ttk.Frame(result_tabs)
        result_tabs.add(fasta_tab, text="FASTA")
        self.fasta_text = tk.Text(fasta_tab, wrap="none")
        self.fasta_text.pack(side=LEFT, fill=BOTH, expand=True)
        fasta_y = ttk.Scrollbar(fasta_tab, orient=VERTICAL, command=self.fasta_text.yview)
        fasta_y.pack(side=RIGHT, fill=Y)
        self.fasta_text.configure(yscrollcommand=fasta_y.set)

        log_tab = ttk.Frame(result_tabs)
        result_tabs.add(log_tab, text="Run Log")
        self.log_text = tk.Text(log_tab, wrap="word", height=8)
        self.log_text.pack(fill=BOTH, expand=True)

    def choose_run_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.run_folder.get() or str(ROOT), title="Select ONT run folder")
        if folder:
            self.run_folder.set(folder)

    def choose_output_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=str(ROOT / "results"), title="Select output folder")
        if folder:
            self.output_folder.set(folder)

    def default_output(self) -> Path:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        return ROOT / "results" / f"plasmid_pipeline_gui_{stamp}"

    def run_pipeline(self) -> None:
        run_dir = Path(self.run_folder.get()).expanduser()
        if not run_dir.exists():
            messagebox.showerror("Run folder not found", f"Folder does not exist:\n{run_dir}")
            return

        out = Path(self.output_folder.get()).expanduser() if self.output_folder.get().strip() else self.default_output()
        self.output_dir = out.resolve()
        self.output_folder.set(str(self.output_dir))
        self.progress_file = self.output_dir / "progress.jsonl"
        self.progress_offset = 0
        self.summary = {}
        self.barcode_records = []
        self.barcode_list.delete(0, END)
        self.fasta_text.delete("1.0", END)
        self.image_canvas.delete("all")
        self.log_text.delete("1.0", END)
        self.task_tree.delete(*self.task_tree.get_children())
        self.task_rows.clear()
        self.progress_value.set(0)
        self.status_text.set("Starting pipeline...")

        python = ENV_PYTHON if ENV_PYTHON.exists() else Path(sys.executable)
        cmd = [
            str(python), str(PIPELINE),
            "--input-run", str(run_dir),
            "--output", str(self.output_dir),
            "--kit-name", self.kit_name.get(),
            "--model", self.model.get(),
            "--threads", str(self.threads.get()),
            "--min-qscore", str(self.min_qscore.get()),
            "--min-barcode-reads", str(self.min_barcode_reads.get()),
        ]
        if self.skip_dorado_update.get():
            cmd.append("--skip-dorado-update")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PATH"] = f"{ROOT / 'envs' / 'plasmid-pipeline' / 'bin'}:{ROOT / 'tools' / 'dorado' / 'current' / 'bin'}:{env.get('PATH', '')}"
        self.process = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        self.run_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.save_button.configure(state="disabled")
        threading.Thread(target=self._read_stdout, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            self.stdout_queue.put(line)
        self.process.wait()
        self.stdout_queue.put(f"\nProcess exited with code {self.process.returncode}\n")

    def stop_pipeline(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.status_text.set("Stopping pipeline...")

    def _poll(self) -> None:
        self._drain_stdout()
        self._read_progress_events()
        if self.process and self.process.poll() is not None:
            code = self.process.returncode
            self.process = None
            self.run_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            if self.output_dir and (self.output_dir / "summary.json").exists():
                self.load_result(self.output_dir)
                if code == 0:
                    self.status_text.set(f"Run complete: {self.output_dir}")
                else:
                    self.status_text.set(f"Run ended with code {code}; partial results loaded.")
            elif code != 0:
                self.status_text.set(f"Run failed before summary was written (exit {code}).")
        self.after(300, self._poll)

    def _drain_stdout(self) -> None:
        while True:
            try:
                line = self.stdout_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert(END, line)
            self.log_text.see(END)

    def _read_progress_events(self) -> None:
        if not self.progress_file or not self.progress_file.exists():
            return
        with self.progress_file.open() as handle:
            handle.seek(self.progress_offset)
            lines = handle.readlines()
            self.progress_offset = handle.tell()
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.apply_progress_event(event)

    def apply_progress_event(self, event: dict) -> None:
        phase = event.get("phase", "task")
        barcode = event.get("barcode")
        key = f"{barcode}:{phase}" if barcode else phase
        label = f"{barcode} - {phase}" if barcode else phase
        status = event.get("status", "")
        message = event.get("message", label)
        progress = float(event.get("progress", 0.0)) * 100
        self.progress_value.set(progress)
        self.status_text.set(message)
        values = (status, message)
        if key in self.task_rows:
            self.task_tree.item(self.task_rows[key], values=values)
        else:
            self.task_rows[key] = self.task_tree.insert("", END, values=values)
        self.task_tree.see(self.task_rows[key])

    def open_existing_result(self) -> None:
        folder = filedialog.askdirectory(initialdir=str(ROOT / "results"), title="Open pipeline result folder")
        if folder:
            self.load_result(Path(folder))

    def load_result(self, folder: Path) -> None:
        summary_path = folder / "summary.json"
        if not summary_path.exists():
            messagebox.showerror("Not a result folder", f"No summary.json found in:\n{folder}")
            return
        self.output_dir = folder.resolve()
        self.output_folder.set(str(self.output_dir))
        self.summary = json.loads(summary_path.read_text())
        self.barcode_records = sorted(self.summary.get("barcodes", []), key=lambda item: item.get("barcode", ""))
        self.barcode_list.delete(0, END)
        for record in self.barcode_records:
            bc = record.get("barcode", "unknown")
            status = record.get("status", "unknown")
            length = record.get("length")
            label = f"{bc}  {status}"
            if length:
                label += f"  {length} bp"
            self.barcode_list.insert(END, label)
        self.save_button.configure(state="normal")
        if self.barcode_records:
            self.barcode_list.selection_set(0)
            self.on_barcode_selected()
        self.status_text.set(f"Loaded result: {self.output_dir}")

    def on_barcode_selected(self, _event: object | None = None) -> None:
        selection = self.barcode_list.curselection()
        if not selection or not self.output_dir:
            return
        record = self.barcode_records[selection[0]]
        bc = record.get("barcode")
        if not bc:
            return
        bc_dir = self.output_dir / "03_barcodes" / bc
        fasta = Path(record.get("representative", "")) if record.get("representative") else bc_dir / f"{bc}.representative.fasta"
        if not fasta.is_absolute():
            fasta = ROOT / fasta
        self.show_fasta(fasta)
        self.show_image(self.find_barcode_png(record, bc_dir))

    def find_barcode_png(self, record: dict, bc_dir: Path) -> Path | None:
        meta = record.get("annotation_meta") or {}
        for item in meta.get("png", []):
            path = Path(item)
            if not path.is_absolute():
                path = ROOT / path
            if path.exists():
                return path
        pngs = sorted((bc_dir / "plannotate").glob("*.png"))
        return pngs[0] if pngs else None

    def show_fasta(self, fasta: Path) -> None:
        self.fasta_text.delete("1.0", END)
        if fasta.exists():
            self.fasta_text.insert(END, fasta.read_text(errors="replace"))
        else:
            self.fasta_text.insert(END, f"FASTA not available:\n{fasta}\n")

    def show_image(self, image: Path | None) -> None:
        self.image_canvas.delete("all")
        if not image or not image.exists():
            self.current_image = None
            self.image_canvas.create_text(20, 20, anchor="nw", text="No plasmid PNG image available.")
            return
        try:
            photo = tk.PhotoImage(file=str(image))
        except tk.TclError as exc:
            self.current_image = None
            self.image_canvas.create_text(20, 20, anchor="nw", text=f"Could not load image:\n{exc}")
            return
        width = max(1, photo.width())
        height = max(1, photo.height())
        canvas_w = max(1, self.image_canvas.winfo_width() or 900)
        canvas_h = max(1, self.image_canvas.winfo_height() or 700)
        factor = max(1, int(max(width / canvas_w, height / canvas_h)))
        if factor > 1:
            photo = photo.subsample(factor, factor)
        self.current_image = photo
        self.image_canvas.create_image(10, 10, anchor="nw", image=self.current_image)
        self.image_canvas.configure(scrollregion=(0, 0, photo.width() + 20, photo.height() + 20))

    def save_package(self) -> None:
        if not self.output_dir:
            return
        default_name = f"{self.output_dir.name}_package.zip"
        target = filedialog.asksaveasfilename(
            title="Save result package",
            defaultextension=".zip",
            initialfile=default_name,
            filetypes=[("Zip archives", "*.zip"), ("All files", "*.*")],
        )
        if not target:
            return
        target_path = Path(target)
        try:
            count = self.write_package(target_path)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        messagebox.showinfo("Package saved", f"Saved {count} files:\n{target_path}")

    def write_package(self, target: Path) -> int:
        assert self.output_dir
        return write_result_package(self.output_dir, target)


def main() -> int:
    app = PipelineGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
