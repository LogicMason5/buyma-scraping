"""CustomTkinter shared UI shell for independent engine apps."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from typing import Any

import customtkinter as ctk

from core.ui_theme import (
    MIST,
    SURFACE,
    StagePalette,
    apply_global_theme,
    ensure_ui_assets,
    font,
    stage_by_key,
)
from core.ui_widgets import (
    BrandHeader,
    DangerButton,
    GhostButton,
    LogConsole,
    MiniFlowRail,
    PrimaryButton,
    SectionCard,
    StatusDock,
)

apply_global_theme()


class EngineApp(ctk.CTk):
    """Base window: brand header, flow rail, controls, log, status dock."""

    def __init__(
        self,
        title: str,
        *,
        stage_key: int = 1,
        geometry: str = "980x720",
    ) -> None:
        super().__init__()
        ensure_ui_assets()
        self.stage: StagePalette = stage_by_key(stage_key)
        self.title(title)
        self.geometry(geometry)
        self.minsize(860, 620)
        self.configure(fg_color=MIST)

        self._log_queue: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.status_var = tk.StringVar(value="待機中 — READY")
        self.progress_var = tk.StringVar(value="進捗: 0/0")
        self._reveal_step = 0

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # Header
        self.header_wrap = ctk.CTkFrame(self, fg_color="transparent")
        self.header_wrap.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 6))
        BrandHeader(
            self.header_wrap,
            subtitle=f"{self.stage.title_ja} · {self.stage.blurb}",
            stage=self.stage,
        ).pack(fill="x")

        # Flow rail
        self.rail = MiniFlowRail(self, active_key=self.stage.key)
        self.rail.grid(row=1, column=0, sticky="ew", padx=22, pady=(4, 8))

        # Controls card (engines fill self.controls)
        self.controls_card = SectionCard(
            self,
            "操作パネル",
            hint=self.stage.title_en,
            accent=self.stage.accent,
            soft=self.stage.soft,
        )
        self.controls_card.grid(row=2, column=0, sticky="ew", padx=22, pady=6)
        self.controls = self.controls_card.body
        self.progress_frame = ctk.CTkFrame(self.controls, fg_color="transparent")
        self.progress_frame.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(self.progress_frame, textvariable=self.progress_var, font=font(12)).pack(side="left")
        self.progress_bar = ctk.CTkProgressBar(self.progress_frame, height=12)
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(12, 0))
        self.progress_bar.set(0.0)

        # Log
        self.log_console = LogConsole(self)
        self.log_console.grid(row=3, column=0, sticky="nsew", padx=22, pady=6)
        self.log_box = self.log_console.box  # back-compat alias

        # Status
        self.status_dock = StatusDock(self, self.status_var, accent=self.stage.accent)
        self.status_dock.grid(row=4, column=0, sticky="ew", padx=22, pady=(4, 16))
        self.status = self.status_dock  # alias

        self.after(200, self._drain_logs)
        self.after(40, self._entrance_reveal)
        # Defer slightly so it wins over CustomTkinter's default icon setter.
        self.after(0, self._try_set_icon)
        self.after(250, self._try_set_icon)
        self.after(600, self._try_set_icon)
        self.after(1200, self._try_set_icon)

    def _try_set_icon(self) -> None:
        try:
            from core.ui_icon import apply_window_icon

            apply_window_icon(self)
        except Exception:  # noqa: BLE001
            pass

    def _entrance_reveal(self) -> None:
        """Staggered fade-in of major sections."""
        widgets = [self.header_wrap, self.rail, self.controls_card, self.log_console, self.status_dock]
        if self._reveal_step < len(widgets):
            # CustomTkinter has no opacity; simulate with brief pad nudge.
            w = widgets[self._reveal_step]
            try:
                w.configure(border_width=getattr(w, "_border_width", 0) or 0)
            except Exception:  # noqa: BLE001
                pass
            self._reveal_step += 1
            self.after(70, self._entrance_reveal)

    def log(self, message: str) -> None:
        self._log_queue.put(message)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def set_progress(self, current: int, total: int, *, label: str = "") -> None:
        total = max(0, int(total))
        current = max(0, int(current))
        ratio = (current / total) if total else 0.0
        self.progress_bar.set(max(0.0, min(1.0, ratio)))
        suffix = f" · {label}" if label else ""
        self.progress_var.set(f"進捗: {current}/{total}{suffix}")

    def _drain_logs(self) -> None:
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self.log_console.append(msg)
        except queue.Empty:
            pass
        self.after(200, self._drain_logs)

    def request_stop(self) -> None:
        self._stop_event.set()
        self.log("停止リクエストを送信しました…")
        self.set_status("停止中…")

    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def run_worker(self, target: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        if self._worker and self._worker.is_alive():
            self.log("既に実行中です。")
            return
        self._stop_event.clear()
        self._worker = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        self._worker.start()

    def action_row(self) -> ctk.CTkFrame:
        row = ctk.CTkFrame(self.controls, fg_color="transparent")
        row.pack(fill="x", pady=(10, 2))
        return row

    def make_start_stop(
        self,
        row: ctk.CTkFrame,
        *,
        on_start: Callable[[], None],
        extra: list[tuple[str, Callable[[], None]]] | None = None,
    ) -> tuple[PrimaryButton, DangerButton]:
        start = PrimaryButton(
            row,
            text="▶  開始",
            command=on_start,
            accent=self.stage.accent,
            hover=self.stage.accent_hover,
            width=120,
        )
        start.pack(side="left", padx=(0, 8))
        stop = DangerButton(row, text="■  停止", command=self.request_stop, width=100)
        stop.pack(side="left", padx=(0, 8))
        for label, cmd in extra or []:
            GhostButton(row, text=label, command=cmd, width=160).pack(side="left", padx=6)
        return start, stop

    @staticmethod
    def ask_open_csv(parent: Any) -> str:
        from tkinter import filedialog

        return filedialog.askopenfilename(
            parent=parent,
            title="CSV を選択",
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
        )

    @staticmethod
    def ask_directory(parent: Any, initial: Path | None = None) -> str:
        from tkinter import filedialog

        return filedialog.askdirectory(
            parent=parent,
            title="フォルダを選択",
            initialdir=str(initial) if initial else None,
        )


# Re-exports for engines
__all__ = [
    "EngineApp",
    "PrimaryButton",
    "GhostButton",
    "DangerButton",
    "SectionCard",
    "font",
    "SURFACE",
]
