"""Engine 2 UI — model image + CSV (Line Flow · Studio)."""

from __future__ import annotations

import sys
from pathlib import Path

import customtkinter as ctk

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.engine2_generate.worker import run_generate  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.ui_common import EngineApp  # noqa: E402
from core.ui_theme import INK, INK_MUTED, font  # noqa: E402
from core.ui_widgets import GhostButton  # noqa: E402


class Engine2App(EngineApp):
    def __init__(self) -> None:
        super().__init__("EC-Buyma · 生成 (Studio)", stage_key=2)
        self.settings = get_settings()
        for child in self.controls.winfo_children():
            if child is self.progress_frame:
                continue
            child.destroy()

        ctk.CTkLabel(self.controls, text="入力ソース", text_color=INK, font=font(13, "bold")).pack(
            anchor="w", pady=(0, 6)
        )

        row1 = ctk.CTkFrame(self.controls, fg_color="transparent")
        row1.pack(fill="x", pady=4)
        ctk.CTkLabel(row1, text="入力 CSV", text_color=INK_MUTED, font=font(12), width=80, anchor="w").pack(
            side="left"
        )
        self.csv_entry = ctk.CTkEntry(row1, width=420, height=34, corner_radius=10, font=font(12))
        self.csv_entry.pack(side="left", padx=8)
        GhostButton(row1, text="参照", width=72, command=self._pick_csv).pack(side="left")

        row2 = ctk.CTkFrame(self.controls, fg_color="transparent")
        row2.pack(fill="x", pady=4)
        ctk.CTkLabel(row2, text="出力先", text_color=INK_MUTED, font=font(12), width=80, anchor="w").pack(
            side="left"
        )
        self.out_entry = ctk.CTkEntry(row2, width=420, height=34, corner_radius=10, font=font(12))
        self.out_entry.insert(0, str(self.settings.workspace_dir / "generate"))
        self.out_entry.pack(side="left", padx=8)
        GhostButton(row2, text="参照", width=72, command=self._pick_out).pack(side="left")

        note = ctk.CTkFrame(self.controls, fg_color=self.stage.soft, corner_radius=12)
        note.pack(fill="x", pady=(12, 4), ipady=4)
        ctk.CTkLabel(
            note,
            text="モデル画像だけを生成し、Engine1 フォルダへ 0.png を保存します（説明TXT不要）。",
            text_color=INK,
            font=font(12),
            wraplength=720,
            justify="left",
        ).pack(anchor="w", padx=14, pady=10)

        row = self.action_row()
        self.make_start_stop(
            row,
            on_start=self._start,
            extra=[("ChatGPT cookie 手順", self._cookie_hint)],
        )

        self.log("— Studio デスク —")
        self.log("商品コメントは CSV / スクレイプ説明から更新（ChatGPT説明チャンネルは使いません）。")
        self.log(
            f"cookies={self.settings.chatgpt_cookies_path.exists()}  "
            f"transport={self.settings.chatgpt_transport}"
        )

    def _pick_csv(self) -> None:
        path = self.ask_open_csv(self)
        if path:
            self.csv_entry.delete(0, "end")
            self.csv_entry.insert(0, path)

    def _pick_out(self) -> None:
        path = self.ask_directory(self, self.settings.workspace_dir / "generate")
        if path:
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, path)

    def _cookie_hint(self) -> None:
        self.log("py -3 scripts/chatgpt_cookie_login.py")
        self.log("Chrome で手動ログイン → Enter で cookie 保存")
        self.log(f"保存先: {self.settings.chatgpt_cookies_path}")

    def _start(self) -> None:
        csv_path = Path(self.csv_entry.get().strip())
        if not csv_path.exists():
            self.log("CSV を選択してください。")
            return
        out = Path(self.out_entry.get().strip() or str(self.settings.workspace_dir / "generate"))
        self.set_status("生成実行中… STUDIO")
        self.set_progress(0, 1, label="初期化")

        def work() -> None:
            try:
                def _on_progress(current: int, total: int, status: str) -> None:
                    self.after(0, lambda: self.set_progress(current, total, label=status))

                path = run_generate(
                    csv_path=csv_path,
                    output_dir=out,
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
    app = Engine2App()
    app.mainloop()


if __name__ == "__main__":
    main()
