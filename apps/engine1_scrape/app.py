"""Engine 1 UI — EC scrape (Line Flow · Harvest)."""

from __future__ import annotations

import sys
from pathlib import Path

import customtkinter as ctk

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.engine1_scrape.worker import SITE_LABELS, run_scrape  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.ui_common import EngineApp  # noqa: E402
from core.ui_theme import INK, INK_MUTED, font  # noqa: E402


class Engine1App(EngineApp):
    def __init__(self) -> None:
        super().__init__("EC-Buyma · 収集 (Harvest)", stage_key=1)
        self.settings = get_settings()
        self._site_vars: dict[str, ctk.BooleanVar] = {}

        for child in self.controls.winfo_children():
            if child is self.progress_frame:
                continue
            child.destroy()

        # Sites
        ctk.CTkLabel(self.controls, text="対象 EC サイト", text_color=INK, font=font(13, "bold")).pack(
            anchor="w", pady=(0, 6)
        )
        sites = ctk.CTkFrame(self.controls, fg_color="transparent")
        sites.pack(fill="x", pady=(0, 10))
        for code, label in SITE_LABELS.items():
            var = ctk.BooleanVar(value=True)
            self._site_vars[code] = var
            ctk.CTkCheckBox(
                sites,
                text=label,
                variable=var,
                font=font(12),
                text_color=INK,
                fg_color=self.stage.accent,
                hover_color=self.stage.accent_hover,
            ).pack(side="left", padx=(0, 14))

        # Count / out
        row1 = ctk.CTkFrame(self.controls, fg_color="transparent")
        row1.pack(fill="x", pady=4)
        ctk.CTkLabel(row1, text="目標件数", text_color=INK_MUTED, font=font(12)).pack(side="left")
        self.count_entry = ctk.CTkEntry(row1, width=72, height=34, corner_radius=10, font=font(13))
        self.count_entry.insert(0, "5")
        self.count_entry.pack(side="left", padx=8)
        ctk.CTkLabel(row1, text="出力フォルダ", text_color=INK_MUTED, font=font(12)).pack(
            side="left", padx=(16, 4)
        )
        self.out_entry = ctk.CTkEntry(row1, width=340, height=34, corner_radius=10, font=font(12))
        self.out_entry.insert(0, str(self.settings.workspace_dir / "scrape"))
        self.out_entry.pack(side="left", padx=4)
        from core.ui_widgets import GhostButton

        GhostButton(row1, text="参照", width=72, command=self._pick_out).pack(side="left", padx=4)

        # Mode
        row_mode = ctk.CTkFrame(self.controls, fg_color=self.stage.soft, corner_radius=12)
        row_mode.pack(fill="x", pady=(10, 6), ipady=6)
        inner = ctk.CTkFrame(row_mode, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=6)
        ctk.CTkLabel(inner, text="実行方式", text_color=INK, font=font(12, "bold")).pack(side="left", padx=(0, 10))
        self.run_mode = ctk.StringVar(value="auto")
        ctk.CTkRadioButton(
            inner,
            text="手動（収集のみ）",
            variable=self.run_mode,
            value="manual",
            font=font(12),
            fg_color=self.stage.accent,
            hover_color=self.stage.accent_hover,
        ).pack(side="left", padx=6)
        ctk.CTkRadioButton(
            inner,
            text="自動（完了後 Studio 連鎖）",
            variable=self.run_mode,
            value="auto",
            font=font(12),
            fg_color=self.stage.accent,
            hover_color=self.stage.accent_hover,
        ).pack(side="left", padx=6)

        self.prefer_new_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            self.controls,
            text="新着のみ取得（/new · /newin · 前日相当の新規リスト）",
            variable=self.prefer_new_var,
            font=font(12),
            text_color=INK,
            fg_color=self.stage.accent,
            hover_color=self.stage.accent_hover,
        ).pack(anchor="w", pady=(4, 4))

        row = self.action_row()
        self.start_btn, self.stop_btn = self.make_start_stop(
            row,
            on_start=self._start,
            extra=[("EC cookie ログイン手順", self._cookie_hint)],
        )

        self.log("— Harvest デスク —")
        self.log("商品画像 + 98/99.png を保存（説明TXTなし）。CSV/ワークブックで重複除外。")
        self.log("手動=収集のみ / 自動=完了後に Engine2 を連鎖。出品は Launch 段階へ。")

    def _pick_out(self) -> None:
        path = self.ask_directory(self, self.settings.workspace_dir / "scrape")
        if path:
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, path)

    def _cookie_hint(self) -> None:
        self.log("選択中のサイトで手動ログイン:")
        selected = [c for c, v in self._site_vars.items() if v.get()] or list(SITE_LABELS)
        for code in selected:
            self.log(f"  py -3 scripts/ec_cookie_login.py {code}")
        self.log("Chrome でログイン → Enter → secrets/ec_sessions/<site>/")

    def _start(self) -> None:
        sites = [c for c, v in self._site_vars.items() if v.get()]
        if not sites:
            self.log("サイトを1つ以上選択してください。")
            return
        try:
            count = int(self.count_entry.get().strip() or "1")
        except ValueError:
            self.log("件数は整数で指定してください。")
            return
        from datetime import datetime

        base = Path(self.out_entry.get().strip() or str(self.settings.workspace_dir / "scrape"))
        out = base / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        prefer_new = bool(self.prefer_new_var.get())
        chain = self.run_mode.get() == "auto"
        self.set_status("収集実行中… HARVESTING")
        self.set_progress(0, max(1, count), label="初期化")
        self.log(f"開始: sites={sites} count={count} prefer_new={prefer_new} chain={chain}")

        def work() -> None:
            try:
                def _on_progress(current: int, total: int, status: str) -> None:
                    self.after(0, lambda: self.set_progress(current, total, label=status))

                path = run_scrape(
                    site_codes=sites,
                    target_count=count,
                    output_dir=out,
                    prefer_new=prefer_new,
                    chain_engine2=chain,
                    log=self.log,
                    should_stop=self.stop_requested,
                    on_progress=_on_progress,
                )
                self.set_status(f"完了: {path}")
            except Exception as exc:  # noqa: BLE001
                self.log(f"エラー: {exc}")
                self.set_status("失敗")

        self.run_worker(work)


def main() -> None:
    app = Engine1App()
    app.mainloop()


if __name__ == "__main__":
    main()
