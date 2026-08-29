"""Engine 3 UI — Buyma listing (Line Flow · Launch)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import customtkinter as ctk

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.engine3_buyma.worker import run_buyma_list  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.csv_schema import read_products_csv  # noqa: E402
from core.ui_common import EngineApp  # noqa: E402
from core.ui_theme import INK, INK_MUTED, font  # noqa: E402
from core.ui_widgets import GhostButton, ListingQueueTable  # noqa: E402
from core.workbook.products_workbook import (  # noqa: E402
    export_sheet_to_csv,
    list_workbook_sheets,
    resolve_yesterday_sheet,
)


class Engine3App(EngineApp):
    def __init__(self) -> None:
        super().__init__("EC-Buyma · 出品 (Launch)", stage_key=3, geometry="1040x860")
        self.settings = get_settings()

        # Insert listing queue above the log console.
        self.grid_rowconfigure(3, weight=2)
        self.grid_rowconfigure(4, weight=1)
        self.list_queue = ListingQueueTable(self, height=220, on_retry=self._retry_queue_item)
        self.list_queue.grid(row=3, column=0, sticky="nsew", padx=22, pady=(4, 4))
        self.log_console.grid(row=4, column=0, sticky="nsew", padx=22, pady=6)
        self.status_dock.grid(row=5, column=0, sticky="ew", padx=22, pady=(4, 16))
        self._list_csv_path: Path | None = None

        for child in self.controls.winfo_children():
            if child is self.progress_frame:
                continue
            child.destroy()

        # Mode strip
        mode_bar = ctk.CTkFrame(self.controls, fg_color=self.stage.soft, corner_radius=12)
        mode_bar.pack(fill="x", pady=(0, 10), ipady=4)
        inner = ctk.CTkFrame(mode_bar, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=8)
        ctk.CTkLabel(inner, text="実行方式", text_color=INK, font=font(12, "bold")).pack(side="left", padx=(0, 10))
        self.run_mode = ctk.StringVar(value="manual")
        ctk.CTkRadioButton(
            inner,
            text="手動（CSV / シート指定）",
            variable=self.run_mode,
            value="manual",
            command=self._refresh_mode_ui,
            font=font(12),
            fg_color=self.stage.accent,
            hover_color=self.stage.accent_hover,
        ).pack(side="left", padx=6)
        ctk.CTkRadioButton(
            inner,
            text="自動（前日シート）",
            variable=self.run_mode,
            value="auto",
            command=self._refresh_mode_ui,
            font=font(12),
            fg_color=self.stage.accent,
            hover_color=self.stage.accent_hover,
        ).pack(side="left", padx=6)

        row1 = ctk.CTkFrame(self.controls, fg_color="transparent")
        row1.pack(fill="x", pady=4)
        ctk.CTkLabel(row1, text="入力 CSV", text_color=INK_MUTED, font=font(12), width=110, anchor="w").pack(
            side="left"
        )
        self.csv_entry = ctk.CTkEntry(row1, width=360, height=34, corner_radius=10, font=font(12))
        self.csv_entry.pack(side="left", padx=8)
        self.csv_pick_btn = GhostButton(row1, text="参照", width=72, command=self._pick_csv)
        self.csv_pick_btn.pack(side="left")

        row_sheet = ctk.CTkFrame(self.controls, fg_color="transparent")
        row_sheet.pack(fill="x", pady=4)
        ctk.CTkLabel(
            row_sheet, text="ワークブック", text_color=INK_MUTED, font=font(12), width=110, anchor="w"
        ).pack(side="left")
        sheets = list_workbook_sheets(self.settings.products_workbook_path) or ["(シートなし)"]
        self.sheet_var = ctk.StringVar(value=sheets[0] if sheets else "")
        self.sheet_menu = ctk.CTkOptionMenu(
            row_sheet,
            variable=self.sheet_var,
            values=sheets,
            width=260,
            height=34,
            font=font(12),
            fg_color="#D5E0E8",
            button_color=self.stage.accent,
            button_hover_color=self.stage.accent_hover,
            text_color=INK,
        )
        self.sheet_menu.pack(side="left", padx=8)
        GhostButton(row_sheet, text="再読込", width=72, command=self._reload_sheets).pack(side="left")
        GhostButton(row_sheet, text="シート→CSV", width=100, command=self._export_selected_sheet).pack(
            side="left", padx=6
        )

        row2 = ctk.CTkFrame(self.controls, fg_color="transparent")
        row2.pack(fill="x", pady=(8, 4))
        self.submit_var = ctk.BooleanVar(value=bool(self.settings.buyma_auto_submit))
        ctk.CTkCheckBox(
            row2,
            text="出品を確定する（オフ=入力のみ）",
            variable=self.submit_var,
            font=font(12),
            text_color=INK,
            fg_color=self.stage.accent,
            hover_color=self.stage.accent_hover,
        ).pack(side="left")
        ctk.CTkLabel(
            row2,
            text=(
                f"遅延 {self.settings.buyma_min_delay_seconds}-{self.settings.buyma_max_delay_seconds}s  /  "
                f"間隔 {self.settings.buyma_between_items_min_seconds}-{self.settings.buyma_between_items_max_seconds}s"
            ),
            text_color=INK_MUTED,
            font=font(11),
        ).pack(side="left", padx=16)

        tip = ctk.CTkLabel(
            self.controls,
            text="画像順: AI(0.jpg/0.png) → 98.png → EC画像 → 99.png（最後）",
            text_color=self.stage.accent,
            font=font(12, "bold"),
            anchor="w",
        )
        tip.pack(fill="x", pady=(8, 0))

        row = self.action_row()
        self.make_start_stop(
            row,
            on_start=self._start,
            extra=[("Buyma cookie ログイン", self._cookie_hint)],
        )

        self.log("— Launch デスク —")
        self.log("自動=前日ワークブックシート / 手動=CSVまたはシート指定。")
        self.log(
            f"Buyma cookies: {self.settings.buyma_cookies_path} "
            f"exists={self.settings.buyma_cookies_path.exists()}"
        )
        self._refresh_mode_ui()

    def _refresh_mode_ui(self) -> None:
        auto = self.run_mode.get() == "auto"
        state = "disabled" if auto else "normal"
        try:
            self.csv_entry.configure(state=state)
            self.csv_pick_btn.configure(state=state)
        except Exception:  # noqa: BLE001
            pass
        if auto:
            y = resolve_yesterday_sheet(self.settings.products_workbook_path)
            yday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            if y:
                self.sheet_var.set(y)
                self.log(f"自動モード: 前日シート [{y}]")
            else:
                self.log(f"自動モード: {yday} のシートなし — 手動で選択してください")

    def _reload_sheets(self) -> None:
        sheets = list_workbook_sheets(self.settings.products_workbook_path)
        if not sheets:
            sheets = ["(シートなし)"]
        self.sheet_menu.configure(values=sheets)
        self.sheet_var.set(sheets[0])
        self.log(f"シート一覧更新: {len(sheets)} 件")

    def _export_selected_sheet(self) -> None:
        name = (self.sheet_var.get() or "").strip()
        if not name or name.startswith("("):
            self.log("シートを選択してください。")
            return
        out = self.settings.workspace_dir / "generate" / f"sheet_{name.replace(' ', '_')}_export.csv"
        try:
            path = export_sheet_to_csv(self.settings.products_workbook_path, name, out)
            self.csv_entry.configure(state="normal")
            self.csv_entry.delete(0, "end")
            self.csv_entry.insert(0, str(path))
            self.log(f"シート→CSV: {path}")
            self._load_queue(path)
        except Exception as exc:  # noqa: BLE001
            self.log(f"シート出力失敗: {exc}")

    def _pick_csv(self) -> None:
        path = self.ask_open_csv(self)
        if path:
            self.csv_entry.delete(0, "end")
            self.csv_entry.insert(0, path)
            self._load_queue(path)

    def _load_queue(self, csv_path: Path | str | None) -> None:
        if not csv_path:
            self._list_csv_path = None
            self.list_queue.clear()
            return
        path = Path(csv_path)
        if not path.exists():
            return
        try:
            rows = read_products_csv(path)
            self._list_csv_path = path
            self.list_queue.load_rows(rows)
            self.log(f"出品リスト読込: {len(rows)} 件")
        except Exception as exc:  # noqa: BLE001
            self.log(f"リスト読込失敗: {exc}")

    def _retry_queue_item(self, idx: int) -> None:
        csv_path = self._list_csv_path
        if csv_path is None or not Path(csv_path).exists():
            self.log("CSVが読み込まれていません。")
            return
        if self._worker and self._worker.is_alive():
            self.log("別の処理が実行中です。完了後に再出品してください。")
            return
        submit = bool(self.submit_var.get())
        self.log(f"手動再出品: #{idx + 1}")
        self.set_status(f"再出品中… #{idx + 1}")
        self.set_progress(0, 1, label=f"再出品 #{idx + 1}")

        def work() -> None:
            try:
                def _on_progress(current: int, total: int, status: str) -> None:
                    self.after(0, lambda: self.set_progress(current, total, label=status))

                def _on_item(i: int, row: dict, status: str, error: str) -> None:
                    self.after(0, lambda ii=i, s=status, e=error: self.list_queue.set_status(ii, s, error=e))

                path = run_buyma_list(
                    csv_path=Path(csv_path),
                    submit=submit,
                    log=self.log,
                    should_stop=self.stop_requested,
                    on_progress=_on_progress,
                    on_item_status=_on_item,
                    only_indices=[idx],
                )
                self.set_status(f"再出品完了: {path}")
            except Exception as exc:  # noqa: BLE001
                self.log(f"再出品エラー: {exc}")
                self.set_status("再出品失敗")

        self.run_worker(work)

    def _cookie_hint(self) -> None:
        self.log("py -3 scripts/buyma_cookie_login.py")
        self.log("Chrome で Buyma に手動ログイン → Enter")
        self.log(f"保存先: {self.settings.buyma_cookies_path}")

    def _resolve_csv_for_run(self) -> Path | None:
        if self.run_mode.get() == "auto":
            sheet = resolve_yesterday_sheet(self.settings.products_workbook_path)
            if not sheet:
                sheet = (self.sheet_var.get() or "").strip()
            if not sheet or sheet.startswith("("):
                self.log("前日シートが見つかりません。")
                return None
            out = self.settings.workspace_dir / "generate" / f"yesterday_{sheet.replace(' ', '_')}.csv"
            try:
                path = export_sheet_to_csv(self.settings.products_workbook_path, sheet, out)
                self.log(f"前日シート読込: [{sheet}] → {path}")
                return path
            except Exception as exc:  # noqa: BLE001
                self.log(f"前日シート読込失敗: {exc}")
                return None

        csv_text = self.csv_entry.get().strip()
        if csv_text:
            path = Path(csv_text)
            if path.exists():
                if path.suffix.lower() in {".xlsx", ".xlsm", ".xls", ".xltx", ".xltm"}:
                    self.log(
                        "Excelファイルは直接出品できません。"
                        "「シート→CSV」でCSVを作成するか products_ready.csv を指定してください。"
                    )
                    return None
                return path
            self.log(f"CSV が見つかりません: {path}")
            return None

        name = (self.sheet_var.get() or "").strip()
        if not name or name.startswith("("):
            self.log("CSV かワークブックシートを指定してください。")
            return None
        out = self.settings.workspace_dir / "generate" / f"sheet_{name.replace(' ', '_')}_export.csv"
        try:
            path = export_sheet_to_csv(self.settings.products_workbook_path, name, out)
            self.log(f"シート読込: [{name}] → {path}")
            return path
        except Exception as exc:  # noqa: BLE001
            self.log(f"シート読込失敗: {exc}")
            return None

    def _start(self) -> None:
        csv_path = self._resolve_csv_for_run()
        if csv_path is None:
            return
        submit = bool(self.submit_var.get())
        self.set_status("出品実行中… LAUNCH")
        self.set_progress(0, 1, label="初期化")
        self._load_queue(csv_path)

        def work() -> None:
            try:
                def _on_progress(current: int, total: int, status: str) -> None:
                    self.after(0, lambda: self.set_progress(current, total, label=status))

                def _on_item(idx: int, row: dict, status: str, error: str) -> None:
                    self.after(0, lambda i=idx, s=status, e=error: self.list_queue.set_status(i, s, error=e))

                path = run_buyma_list(
                    csv_path=csv_path,
                    submit=submit,
                    log=self.log,
                    should_stop=self.stop_requested,
                    on_progress=_on_progress,
                    on_item_status=_on_item,
                )
                self.set_status(f"完了: {path}")
            except Exception as exc:  # noqa: BLE001
                self.log(f"エラー: {exc}")
                self.set_status("失敗")

        self.run_worker(work)


def main() -> None:
    app = Engine3App()
    app.mainloop()


if __name__ == "__main__":
    main()
