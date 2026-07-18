"""Desktop training control panel for the cricket DRS detector."""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from config.settings import settings


class TrainingApplication(tk.Tk):
    """Local Tkinter app for launching YOLO training without a web UI."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Cricket DRS Training Studio")
        self.geometry("1180x760")
        self.minsize(980, 640)
        self.configure(bg="#0A0E1A")

        self.process: subprocess.Popen[str] | None = None
        self.output_queue: queue.Queue[str] = queue.Queue()
        self.status_var = tk.StringVar(value="Ready")
        self.data_var = tk.StringVar(value=str(settings.PROJECT_ROOT / "training" / "data.yaml"))
        self.model_var = tk.StringVar(value="yolo11l.pt")
        self.epochs_var = tk.StringVar(value="120")
        self.imgsz_var = tk.StringVar(value="1280")
        self.batch_var = tk.StringVar(value="8")
        self.device_var = tk.StringVar(value="0")
        self.project_var = tk.StringVar(value=str(settings.MODELS_DIR / "training_runs"))
        self.name_var = tk.StringVar(value="drs_yolov8")
        self.export_var = tk.BooleanVar(value=True)

        self._build_style()
        self._build_ui()
        self.after(120, self._drain_output)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background="#0A0E1A", foreground="#E8ECF0", font=("Segoe UI", 10))
        style.configure("TFrame", background="#0A0E1A")
        style.configure("Panel.TFrame", background="#101827", relief=tk.FLAT)
        style.configure("TLabel", background="#0A0E1A", foreground="#E8ECF0")
        style.configure("Panel.TLabel", background="#101827", foreground="#E8ECF0")
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"), foreground="#FFD700")
        style.configure("Hint.TLabel", foreground="#8DA7C4")
        style.configure(
            "TButton",
            background="#1B2333",
            foreground="#FFD700",
            borderwidth=0,
            padding=(12, 7),
            font=("Segoe UI", 10, "bold"),
        )
        style.map("TButton", background=[("active", "#2A3A5C")])
        style.configure("TEntry", fieldbackground="#070B12", foreground="#E8ECF0", bordercolor="#24324A")
        style.configure("TCheckbutton", background="#101827", foreground="#E8ECF0")

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Cricket DRS Training Studio", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Train and export the ball detector locally. No browser or web dashboard required.",
            style="Hint.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(header, textvariable=self.status_var, style="Hint.TLabel").grid(row=0, column=1, sticky="e")

        body = ttk.Frame(self)
        body.grid(row=1, column=0, sticky="nsew", padx=18, pady=8)
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        form = ttk.Frame(body, style="Panel.TFrame", padding=16)
        form.grid(row=0, column=0, sticky="ns", padx=(0, 12))

        self._path_row(form, 0, "Dataset YAML", self.data_var, self._choose_data)
        self._entry_row(form, 1, "Base model", self.model_var)
        self._entry_row(form, 2, "Epochs", self.epochs_var)
        self._entry_row(form, 3, "Image size", self.imgsz_var)
        self._entry_row(form, 4, "Batch", self.batch_var)
        self._entry_row(form, 5, "Device", self.device_var)
        self._path_row(form, 6, "Project folder", self.project_var, self._choose_project, directory=True)
        self._entry_row(form, 7, "Run name", self.name_var)

        ttk.Checkbutton(
            form,
            text="Copy best.pt to models/cricket_ball_yolov8.pt after training",
            variable=self.export_var,
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(12, 4))

        actions = ttk.Frame(form, style="Panel.TFrame")
        actions.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        ttk.Button(actions, text="Start Training", command=self.start_training).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="Stop", command=self.stop_training).pack(side=tk.LEFT, padx=8)
        ttk.Button(actions, text="Open Runs Folder", command=self.open_runs_folder).pack(side=tk.LEFT, padx=8)

        log_panel = ttk.Frame(body, style="Panel.TFrame", padding=12)
        log_panel.grid(row=0, column=1, sticky="nsew")
        log_panel.columnconfigure(0, weight=1)
        log_panel.rowconfigure(1, weight=1)
        ttk.Label(log_panel, text="Training Log", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.log_text = tk.Text(
            log_panel,
            bg="#050811",
            fg="#DCEBFF",
            insertbackground="#FFD700",
            relief=tk.FLAT,
            wrap=tk.WORD,
            font=("Consolas", 10),
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

    def _entry_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=7)
        ttk.Entry(parent, textvariable=variable, width=36).grid(row=row, column=1, columnspan=2, sticky="ew", pady=7)

    def _path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        command,
        directory: bool = False,
    ) -> None:
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=7)
        ttk.Entry(parent, textvariable=variable, width=30).grid(row=row, column=1, sticky="ew", pady=7)
        text = "Folder" if directory else "Browse"
        ttk.Button(parent, text=text, command=command).grid(row=row, column=2, sticky="e", padx=(8, 0), pady=7)

    def _choose_data(self) -> None:
        path = filedialog.askopenfilename(
            title="Select YOLO dataset YAML",
            initialdir=settings.PROJECT_ROOT / "training",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if path:
            self.data_var.set(path)

    def _choose_project(self) -> None:
        path = filedialog.askdirectory(title="Select training runs folder", initialdir=settings.MODELS_DIR)
        if path:
            self.project_var.set(path)

    def start_training(self) -> None:
        if self.process is not None and self.process.poll() is None:
            messagebox.showinfo("Training is running", "Stop the current run before starting another one.")
            return

        try:
            epochs = int(self.epochs_var.get())
            imgsz = int(self.imgsz_var.get())
            batch = int(self.batch_var.get())
        except ValueError:
            messagebox.showerror("Invalid settings", "Epochs, image size, and batch must be whole numbers.")
            return

        data_path = Path(self.data_var.get())
        if not data_path.exists():
            messagebox.showerror("Dataset missing", f"Dataset YAML not found:\n{data_path}")
            return

        command = [
            sys.executable,
            str(settings.PROJECT_ROOT / "scripts" / "train_yolo_drs.py"),
            "--data",
            str(data_path),
            "--base-model",
            self.model_var.get(),
            "--epochs",
            str(epochs),
            "--imgsz",
            str(imgsz),
            "--batch",
            str(batch),
            "--device",
            self.device_var.get(),
            "--project",
            self.project_var.get(),
            "--name",
            self.name_var.get(),
        ]
        if self.export_var.get():
            command.extend(["--export-best", str(settings.MODELS_DIR / "cricket_ball_yolov8.pt")])

        self._append_log("$ " + " ".join(command))
        self.status_var.set("Training running")
        self.process = subprocess.Popen(
            command,
            cwd=settings.PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read_process_output, daemon=True).start()

    def stop_training(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self.status_var.set("Stopping training")
            self._append_log("Stopping training process...")

    def open_runs_folder(self) -> None:
        path = Path(self.project_var.get())
        path.mkdir(parents=True, exist_ok=True)
        try:
            import os

            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Open folder failed", str(exc))

    def _read_process_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.output_queue.put(line)
        return_code = self.process.wait()
        self.output_queue.put(f"\nTraining process exited with code {return_code}\n")
        self.output_queue.put("__DRS_TRAINING_DONE__")

    def _drain_output(self) -> None:
        while True:
            try:
                line = self.output_queue.get_nowait()
            except queue.Empty:
                break
            if line == "__DRS_TRAINING_DONE__":
                self.status_var.set("Training finished")
            else:
                self._append_log(line.rstrip("\n"))
        self.after(120, self._drain_output)

    def _append_log(self, text: str) -> None:
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)


def run_training_app() -> None:
    TrainingApplication().mainloop()

