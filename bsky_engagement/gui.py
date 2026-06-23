"""Tkinter/ttk GUI wrapper around the pipeline.

Cross-platform (Windows + macOS + Linux). The pipeline runs on a worker thread;
progress messages are marshalled back to the UI thread through a queue so the
log stays responsive and the window never freezes.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from .model import InboundMode, RunConfig, parse_window
from .pipeline import run

WINDOW_CHOICES = ["7d", "30d", "60d", "90d", "1y", "all"]
# Display label -> inbound mode. "Off" first so it's the default.
INBOUND_CHOICES = {"Off": InboundMode.OFF, "Top N": InboundMode.TOP, "All": InboundMode.ALL}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Bluesky Engagement Exporter")
        root.minsize(560, 520)

        self._log_q: "queue.Queue[tuple]" = queue.Queue()
        self._worker: threading.Thread | None = None
        self._last_out_dir: str | None = None

        self._build_widgets()
        self.root.after(100, self._drain_log)

    # -- layout ---------------------------------------------------------------

    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # Handles
        hframe = ttk.LabelFrame(self.root, text="Handles (one per line)")
        hframe.pack(fill="both", padx=10, pady=(10, 4))
        self.handles_txt = tk.Text(hframe, height=4, wrap="none")
        self.handles_txt.pack(fill="both", expand=True, padx=6, pady=6)
        self.handles_txt.insert("1.0", "wikisteff.bsky.social")

        # Settings
        sframe = ttk.LabelFrame(self.root, text="Settings")
        sframe.pack(fill="x", padx=10, pady=4)

        ttk.Label(sframe, text="Window:").grid(row=0, column=0, sticky="w", **pad)
        self.window_var = tk.StringVar(value="90d")
        ttk.Combobox(sframe, textvariable=self.window_var, values=WINDOW_CHOICES,
                     width=8, state="readonly").grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(sframe, text="Inbound (who engaged back):").grid(
            row=0, column=2, sticky="w", **pad)
        self.inbound_var = tk.StringVar(value="Off")
        inbound_cb = ttk.Combobox(sframe, textvariable=self.inbound_var,
                                  values=list(INBOUND_CHOICES), width=8, state="readonly")
        inbound_cb.grid(row=0, column=3, sticky="w", **pad)
        inbound_cb.bind("<<ComboboxSelected>>", lambda _e: self._sync_top_visibility())

        # Number box, shown only when "Top N" is selected.
        self.top_var = tk.IntVar(value=25)
        self.top_spin = ttk.Spinbox(sframe, from_=1, to=10000, textvariable=self.top_var, width=7)
        self.top_spin.grid(row=0, column=4, sticky="w", **pad)

        ttk.Label(sframe, text="Save to:").grid(row=1, column=0, sticky="w", **pad)
        self.out_var = tk.StringVar(value=os.path.abspath("output"))
        ttk.Entry(sframe, textvariable=self.out_var).grid(
            row=1, column=1, columnspan=3, sticky="we", **pad)
        ttk.Button(sframe, text="Browse...", command=self._browse).grid(
            row=1, column=4, sticky="w", **pad)
        sframe.columnconfigure(1, weight=1)
        self._sync_top_visibility()

        # Optional login (enables the fast inbound path for your own account)
        aframe2 = ttk.LabelFrame(
            self.root,
            text="Bluesky login (optional — much faster inbound for your own account)")
        aframe2.pack(fill="x", padx=10, pady=4)
        ttk.Label(aframe2, text="Handle:").grid(row=0, column=0, sticky="w", **pad)
        self.auth_handle_var = tk.StringVar()
        ttk.Entry(aframe2, textvariable=self.auth_handle_var, width=22).grid(
            row=0, column=1, sticky="w", **pad)
        ttk.Label(aframe2, text="App password:").grid(row=0, column=2, sticky="w", **pad)
        self.auth_pw_var = tk.StringVar()
        ttk.Entry(aframe2, textvariable=self.auth_pw_var, width=22, show="•").grid(
            row=0, column=3, sticky="w", **pad)
        ttk.Label(
            aframe2,
            text=("Leave empty to use the unauthenticated path: inbound is slower, "
                  "but is NOT limited to ~60 days of notification history."),
            foreground="gray40", wraplength=520, justify="left").grid(
            row=1, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 4))

        # Actions
        aframe = ttk.Frame(self.root)
        aframe.pack(fill="x", padx=10, pady=4)
        self.run_btn = ttk.Button(aframe, text="Export Engagement", command=self._start)
        self.run_btn.pack(side="left")
        self.progress = ttk.Progressbar(aframe, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True, padx=10)

        # Log
        lframe = ttk.LabelFrame(self.root, text="Progress")
        lframe.pack(fill="both", expand=True, padx=10, pady=(4, 4))
        self.log_txt = tk.Text(lframe, height=12, wrap="word", state="disabled")
        self.log_txt.pack(fill="both", expand=True, side="left", padx=(6, 0), pady=6)
        sb = ttk.Scrollbar(lframe, command=self.log_txt.yview)
        sb.pack(side="right", fill="y", pady=6)
        self.log_txt["yscrollcommand"] = sb.set

        # Footer
        fframe = ttk.Frame(self.root)
        fframe.pack(fill="x", padx=10, pady=(0, 10))
        self.open_btn = ttk.Button(fframe, text="Open Output Folder",
                                   command=self._open_output, state="disabled")
        self.open_btn.pack(side="left")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(fframe, textvariable=self.status_var).pack(side="right")

    # -- actions --------------------------------------------------------------

    def _sync_top_visibility(self) -> None:
        """Show the number box only in 'Top N' mode."""
        if INBOUND_CHOICES.get(self.inbound_var.get()) == InboundMode.TOP:
            self.top_spin.grid()
        else:
            self.top_spin.grid_remove()

    def _browse(self) -> None:
        path = filedialog.askdirectory(initialdir=self.out_var.get() or ".")
        if path:
            self.out_var.set(path)

    def _start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        handles = [h.strip() for h in self.handles_txt.get("1.0", "end").splitlines() if h.strip()]
        if not handles:
            self._log("error: enter at least one handle")
            return
        try:
            window = parse_window(self.window_var.get())
        except ValueError as e:
            self._log(f"error: {e}")
            return

        mode = INBOUND_CHOICES.get(self.inbound_var.get(), InboundMode.OFF)
        config = RunConfig(
            handles=handles,
            inbound_mode=mode,
            top=max(1, int(self.top_var.get())),
            window=window,
            out_dir=self.out_var.get() or "output",
            auth_handle=self.auth_handle_var.get().strip() or None,
            auth_app_password=self.auth_pw_var.get().strip() or None,
        )

        self.run_btn.config(state="disabled")
        self.open_btn.config(state="disabled")
        self.progress["value"] = 0
        self.status_var.set("Working...")
        self._clear_log()

        self._worker = threading.Thread(target=self._run_worker, args=(config,), daemon=True)
        self._worker.start()

    def _run_worker(self, config: RunConfig) -> None:
        def progress(msg: str, frac: float) -> None:
            self._log_q.put(("progress", msg, frac))

        try:
            results = run(config, progress=progress)
            for r in results:
                self._log_q.put(("log", f"DONE {r.handle}: {r.counterparties} counterparties, "
                                        f"{r.edges} edges -> {r.out_dir}"))
                self._last_out_dir = r.out_dir
            self._log_q.put(("done",))
        except Exception as e:  # noqa: BLE001
            self._log_q.put(("log", f"error: {e}"))
            self._log_q.put(("done",))

    # -- log / queue plumbing -------------------------------------------------

    def _drain_log(self) -> None:
        try:
            while True:
                item = self._log_q.get_nowait()
                kind = item[0]
                if kind == "done":
                    self._finish()
                elif kind == "progress":
                    self._log(item[1])
                    self.progress["value"] = max(0.0, min(item[2], 1.0)) * 100
                elif kind == "log":
                    self._log(item[1])
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log)

    def _finish(self) -> None:
        self.progress["value"] = 100
        self.run_btn.config(state="normal")
        self.status_var.set("Done.")
        if self._last_out_dir:
            self.open_btn.config(state="normal")

    def _log(self, msg: str) -> None:
        self.log_txt.config(state="normal")
        self.log_txt.insert("end", msg + "\n")
        self.log_txt.see("end")
        self.log_txt.config(state="disabled")

    def _clear_log(self) -> None:
        self.log_txt.config(state="normal")
        self.log_txt.delete("1.0", "end")
        self.log_txt.config(state="disabled")

    def _open_output(self) -> None:
        path = self._last_out_dir
        if not path or not os.path.isdir(path):
            return
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
