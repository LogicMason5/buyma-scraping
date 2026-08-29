"""Reusable CustomTkinter widgets for the Line Flow UI."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import customtkinter as ctk
from PIL import Image

from core.ui_theme import (
    DANGER,
    DANGER_HOVER,
    GHOST,
    GHOST_HOVER,
    INK,
    INK_MUTED,
    LINE,
    LOG_BG,
    LOG_FG,
    MIST,
    STAGES,
    SURFACE,
    SURFACE_RAISED,
    StagePalette,
    asset_path,
    font,
    font_display,
)


def _photo(path: Path, size: tuple[int, int]) -> ctk.CTkImage | None:
    if not path.exists():
        return None
    try:
        img = Image.open(path).convert("RGBA")
        return ctk.CTkImage(light_image=img, dark_image=img, size=size)
    except Exception:  # noqa: BLE001
        return None


class BrandHeader(ctk.CTkFrame):
    def __init__(self, master: Any, subtitle: str = "", *, stage: StagePalette | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)

        mark = _photo(asset_path("brand_mark.png"), (52, 52))
        if mark:
            ctk.CTkLabel(self, text="", image=mark).grid(row=0, column=0, rowspan=2, padx=(0, 14), sticky="w")
            self._mark_img = mark  # keep ref

        title_row = ctk.CTkFrame(self, fg_color="transparent")
        title_row.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(title_row, text="EC-Buyma", text_color=INK, font=font_display(26)).pack(side="left")
        if stage:
            pill = ctk.CTkLabel(
                title_row,
                text=f"  {stage.code}  ",
                font=font(11, "bold"),
                text_color=stage.accent,
                fg_color=stage.soft,
                corner_radius=999,
            )
            pill.pack(side="left", padx=(12, 0), pady=4)

        ctk.CTkLabel(
            self,
            text=subtitle or "EC新着 → スタジオ生成 → Buyma出品 の一筆書きフロー",
            text_color=INK_MUTED,
            font=font(13),
            anchor="w",
        ).grid(row=1, column=1, sticky="w", pady=(2, 0))


class SectionCard(ctk.CTkFrame):
    """Raised surface with accent strip + title."""

    def __init__(
        self,
        master: Any,
        title: str,
        *,
        hint: str = "",
        accent: str = INK,
        soft: str = MIST,
    ) -> None:
        super().__init__(
            master,
            fg_color=SURFACE_RAISED,
            corner_radius=16,
            border_width=1,
            border_color=LINE,
        )
        self.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(self, fg_color=soft, corner_radius=14, height=44)
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        head.grid_propagate(False)
        head.grid_columnconfigure(1, weight=1)

        bar = ctk.CTkFrame(head, fg_color=accent, width=5, corner_radius=4)
        bar.grid(row=0, column=0, sticky="ns", padx=(10, 10), pady=10)

        ctk.CTkLabel(head, text=title, text_color=INK, font=font(14, "bold"), anchor="w").grid(
            row=0, column=1, sticky="w"
        )
        if hint:
            ctk.CTkLabel(head, text=hint, text_color=INK_MUTED, font=font(11), anchor="e").grid(
                row=0, column=2, sticky="e", padx=(0, 12)
            )

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=14, pady=(4, 14))
        self.body.grid_columnconfigure(0, weight=1)


class PrimaryButton(ctk.CTkButton):
    def __init__(self, master: Any, text: str, command: Callable | None = None, *, accent: str, hover: str, **kw: Any):
        super().__init__(
            master,
            text=text,
            command=command,
            fg_color=accent,
            hover_color=hover,
            text_color="#FFFFFF",
            font=font(14, "bold"),
            height=42,
            corner_radius=12,
            **kw,
        )


class GhostButton(ctk.CTkButton):
    def __init__(self, master: Any, text: str, command: Callable | None = None, **kw: Any):
        super().__init__(
            master,
            text=text,
            command=command,
            fg_color=GHOST,
            hover_color=GHOST_HOVER,
            text_color=INK,
            font=font(13),
            height=38,
            corner_radius=11,
            **kw,
        )


class DangerButton(ctk.CTkButton):
    def __init__(self, master: Any, text: str, command: Callable | None = None, **kw: Any):
        super().__init__(
            master,
            text=text,
            command=command,
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
            text_color="#FFFFFF",
            font=font(13, "bold"),
            height=42,
            corner_radius=12,
            **kw,
        )


class FlowConnector(ctk.CTkCanvas):
    """Animated dashed line between stage cards."""

    def __init__(self, master: Any, width: int = 36, height: int = 120) -> None:
        super().__init__(master, width=width, height=height, bg=MIST, highlightthickness=0, bd=0)
        self._offset = 0
        self._draw()
        self.after(80, self._tick)

    def _draw(self) -> None:
        self.delete("all")
        mid = int(self["width"]) // 2
        h = int(self["height"])
        # soft rail
        self.create_line(mid, 8, mid, h - 8, fill="#A8BCC8", width=3)
        # moving dashes
        dash_len = 10
        gap = 8
        y = -dash_len + (self._offset % (dash_len + gap))
        while y < h:
            self.create_line(mid, y, mid, min(y + dash_len, h - 4), fill=INK, width=2)
            y += dash_len + gap
        # chevron
        cy = h // 2 + (self._offset // 2) % 20 - 10
        self.create_polygon(mid - 6, cy, mid + 6, cy, mid, cy + 10, fill=INK, outline="")

    def _tick(self) -> None:
        self._offset = (self._offset + 2) % 200
        self._draw()
        self.after(80, self._tick)


class StageFlowCard(ctk.CTkFrame):
    """Launcher stage card with icon, copy, and launch CTA."""

    def __init__(
        self,
        master: Any,
        stage: StagePalette,
        *,
        on_launch: Callable[[StagePalette], None],
        running: bool = False,
    ) -> None:
        super().__init__(
            master,
            fg_color=SURFACE_RAISED,
            corner_radius=20,
            border_width=2,
            border_color=stage.soft,
        )
        self.stage = stage
        self._on_launch = on_launch
        self._pulse = 0
        self.grid_columnconfigure(1, weight=1)

        icon = _photo(asset_path(stage.icon), (72, 72))
        if icon:
            lab = ctk.CTkLabel(self, text="", image=icon)
            lab.grid(row=0, column=0, rowspan=3, padx=(18, 12), pady=18, sticky="n")
            self._icon = icon

        ctk.CTkLabel(
            self,
            text=f"0{stage.key}  {stage.title_en}",
            text_color=stage.accent,
            font=font(12, "bold"),
            anchor="w",
        ).grid(row=0, column=1, sticky="w", pady=(18, 0))

        ctk.CTkLabel(
            self,
            text=stage.title_ja,
            text_color=INK,
            font=font_display(22),
            anchor="w",
        ).grid(row=1, column=1, sticky="w")

        ctk.CTkLabel(
            self,
            text=stage.blurb,
            text_color=INK_MUTED,
            font=font(12),
            anchor="w",
            wraplength=360,
            justify="left",
        ).grid(row=2, column=1, sticky="w", pady=(2, 18))

        self.launch_btn = PrimaryButton(
            self,
            text="起動する →",
            command=lambda: self._on_launch(stage),
            accent=stage.accent,
            hover=stage.accent_hover,
            width=120,
        )
        self.launch_btn.grid(row=0, column=2, rowspan=3, padx=(8, 18), pady=18)

        self.badge = ctk.CTkLabel(
            self,
            text="READY",
            font=font(10, "bold"),
            text_color=stage.accent,
            fg_color=stage.soft,
            corner_radius=8,
            width=64,
            height=22,
        )
        self.badge.place(relx=0.02, rely=0.08)
        self.set_running(running)

    def set_running(self, running: bool) -> None:
        if running:
            self.badge.configure(text="LIVE", text_color="#FFFFFF", fg_color=self.stage.accent)
            self.configure(border_color=self.stage.accent)
            self._start_pulse()
        else:
            self.badge.configure(text="READY", text_color=self.stage.accent, fg_color=self.stage.soft)
            self.configure(border_color=self.stage.soft)

    def _start_pulse(self) -> None:
        self._pulse = (self._pulse + 1) % 2
        if self.badge.cget("text") != "LIVE":
            return
        # subtle border breathe
        self.configure(border_color=self.stage.accent if self._pulse else self.stage.soft)
        self.after(700, self._start_pulse)


class MiniFlowRail(ctk.CTkFrame):
    """Compact horizontal SCRAPE → STUDIO → LIST indicator for engine windows."""

    def __init__(self, master: Any, active_key: int) -> None:
        super().__init__(master, fg_color=SURFACE, corner_radius=14, border_width=1, border_color=LINE)
        for i, stage in enumerate(STAGES):
            active = stage.key == active_key
            chip = ctk.CTkFrame(
                self,
                fg_color=stage.accent if active else stage.soft,
                corner_radius=10,
                height=34,
            )
            chip.pack(side="left", padx=(10 if i == 0 else 4, 4), pady=8)
            label = f"{stage.key} {stage.title_ja}"
            ctk.CTkLabel(
                chip,
                text=label,
                text_color="#FFFFFF" if active else stage.accent,
                font=font(12, "bold"),
                padx=12,
            ).pack(padx=4, pady=4)
            if i < len(STAGES) - 1:
                ctk.CTkLabel(self, text="›", text_color=INK_MUTED, font=font(16, "bold")).pack(
                    side="left", padx=2
                )


class LogConsole(ctk.CTkFrame):
    def __init__(self, master: Any) -> None:
        super().__init__(master, fg_color=LOG_BG, corner_radius=16)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 0))
        ctk.CTkLabel(head, text="LIVE LOG", text_color="#7FA3B8", font=font(11, "bold")).pack(side="left")
        self.box = ctk.CTkTextbox(
            self,
            fg_color=LOG_BG,
            text_color=LOG_FG,
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word",
            border_width=0,
            corner_radius=12,
        )
        self.box.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 10))

    def append(self, message: str) -> None:
        self.box.insert("end", message + "\n")
        self.box.see("end")


class StatusDock(ctk.CTkFrame):
    def __init__(self, master: Any, status_var: Any, *, accent: str = INK) -> None:
        super().__init__(master, fg_color=SURFACE_RAISED, corner_radius=12, border_width=1, border_color=LINE)
        self._dot = ctk.CTkLabel(self, text="●", text_color=accent, font=font(12), width=20)
        self._dot.pack(side="left", padx=(12, 4), pady=10)
        ctk.CTkLabel(self, textvariable=status_var, text_color=INK, font=font(13), anchor="w").pack(
            side="left", fill="x", expand=True, padx=(0, 12)
        )
        self._accent = accent
        self._tick = 0
        self.after(600, self._blink)

    def _blink(self) -> None:
        self._tick ^= 1
        self._dot.configure(text_color=self._accent if self._tick else LINE)
        self.after(600, self._blink)


class ListingQueueTable(ctk.CTkFrame):
    """Scrollable product queue with status marks (✓ / clickable red ↻)."""

    _STATUS_MARK = {
        "pending": ("・", INK_MUTED),
        "processing": ("…", "#C48A2A"),
        "ok": ("✓", "#2F9E6B"),
        "success": ("✓", "#2F9E6B"),
        "done": ("✓", "#2F9E6B"),
        "failed": ("↻", DANGER),
    }

    def __init__(
        self,
        master: Any,
        *,
        height: int = 220,
        on_retry: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color=LOG_BG, corner_radius=16)
        self._on_retry = on_retry
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 0))
        ctk.CTkLabel(head, text="出品リスト", text_color="#7FA3B8", font=font(11, "bold")).pack(side="left")
        self._count_var = ctk.StringVar(value="0 件")
        ctk.CTkLabel(head, textvariable=self._count_var, text_color=INK_MUTED, font=font(11)).pack(
            side="right"
        )

        cols = ctk.CTkFrame(self, fg_color="transparent")
        cols.grid(row=1, column=0, sticky="ew", padx=10, pady=(6, 0))
        for text, w in (("状態", 52), ("#", 36), ("ブランド", 120), ("商品名", 0), ("結果", 0)):
            lab = ctk.CTkLabel(
                cols,
                text=text,
                text_color="#7FA3B8",
                font=font(10, "bold"),
                width=w if w else 1,
                anchor="w",
            )
            if w:
                lab.pack(side="left", padx=(4, 0))
            else:
                lab.pack(side="left", fill="x", expand=True, padx=(4, 0))

        self._scroll = ctk.CTkScrollableFrame(self, fg_color=LOG_BG, height=height, corner_radius=10)
        self._scroll.grid(row=2, column=0, sticky="nsew", padx=8, pady=(2, 8))
        self._scroll.grid_columnconfigure(3, weight=1)
        self._rows: list[dict[str, Any]] = []
        self._widgets: list[dict[str, Any]] = []

    def set_on_retry(self, callback: Callable[[int], None] | None) -> None:
        self._on_retry = callback

    def clear(self) -> None:
        for w in self._widgets:
            try:
                w["frame"].destroy()
            except Exception:  # noqa: BLE001
                pass
        self._widgets.clear()
        self._rows.clear()
        self._count_var.set("0 件")

    def load_rows(self, rows: list[dict[str, Any]]) -> None:
        self.clear()
        for idx, row in enumerate(rows):
            status = (row.get("出品結果") or "pending").strip().lower() or "pending"
            if status not in self._STATUS_MARK:
                status = "pending"
            self._add_row(idx, row, status)
        self._count_var.set(f"{len(self._rows)} 件")

    def _retry_clicked(self, idx: int) -> None:
        if not self._on_retry:
            return
        if idx < 0 or idx >= len(self._widgets):
            return
        if self._widgets[idx].get("status") != "failed":
            return
        self._on_retry(idx)

    def _add_row(self, idx: int, row: dict[str, Any], status: str) -> None:
        mark, color = self._STATUS_MARK.get(status, self._STATUS_MARK["pending"])
        frame = ctk.CTkFrame(self._scroll, fg_color="#121820", corner_radius=8, height=32)
        frame.pack(fill="x", pady=2, padx=2)
        frame.grid_columnconfigure(3, weight=1)

        mark_btn = ctk.CTkButton(
            frame,
            text=mark,
            width=40,
            height=28,
            fg_color="#3A1515" if status == "failed" else "transparent",
            hover_color="#5A2020" if status == "failed" else "#1A222C",
            text_color=color,
            font=font(16, "bold"),
            corner_radius=8,
            command=lambda i=idx: self._retry_clicked(i),
        )
        mark_btn.grid(row=0, column=0, padx=(6, 2), pady=4)
        if status != "failed":
            mark_btn.configure(state="disabled")

        idx_lab = ctk.CTkLabel(frame, text=str(idx + 1), text_color=INK_MUTED, font=font(11), width=32)
        idx_lab.grid(row=0, column=1, padx=2, pady=4)
        brand = (row.get("ブランド") or "-")[:24]
        brand_lab = ctk.CTkLabel(frame, text=brand, text_color=LOG_FG, font=font(11), width=110, anchor="w")
        brand_lab.grid(row=0, column=2, padx=4, pady=4, sticky="w")
        name = (row.get("商品名") or row.get("フォルダ名") or "-")[:60]
        name_lab = ctk.CTkLabel(frame, text=name, text_color=LOG_FG, font=font(11), anchor="w")
        name_lab.grid(row=0, column=3, padx=4, pady=4, sticky="ew")
        err = (row.get("出品エラー") or "")[:40] if status == "failed" else ""
        result_lab = ctk.CTkLabel(
            frame,
            text=err or ("成功" if status in {"ok", "success", "done"} else ""),
            text_color=color,
            font=font(10),
            anchor="e",
            width=120,
        )
        result_lab.grid(row=0, column=4, padx=(4, 8), pady=4, sticky="e")

        self._rows.append(dict(row))
        self._widgets.append(
            {
                "frame": frame,
                "mark": mark_btn,
                "result": result_lab,
                "status": status,
            }
        )

    def set_status(self, idx: int, status: str, *, error: str = "") -> None:
        if idx < 0 or idx >= len(self._widgets):
            return
        status = (status or "pending").strip().lower() or "pending"
        mark, color = self._STATUS_MARK.get(status, self._STATUS_MARK["pending"])
        w = self._widgets[idx]
        w["status"] = status
        btn = w["mark"]
        btn.configure(text=mark, text_color=color)
        if status == "failed":
            btn.configure(state="normal", fg_color="#3A1515", hover_color="#5A2020")
            w["result"].configure(text=(error or "失敗・再出品")[:40], text_color=color)
        else:
            btn.configure(state="disabled", fg_color="transparent", hover_color="#1A222C")
            if status in {"ok", "success", "done"}:
                w["result"].configure(text="成功", text_color=color)
            elif status == "processing":
                w["result"].configure(text="出品中…", text_color=color)
            else:
                w["result"].configure(text="", text_color=color)
        try:
            w["frame"].update_idletasks()
        except Exception:  # noqa: BLE001
            pass
