"""EC-Buyma unified console (single-window management)."""

from __future__ import annotations

import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from apps.engine1_scrape.worker import SITE_LABELS, run_scrape  # noqa: E402
from apps.engine2_generate.worker import run_generate  # noqa: E402
from apps.engine3_buyma.worker import run_buyma_list  # noqa: E402
from core.buyma.buyma_listing_service import reprice_row_from_cost  # noqa: E402
from core.config import clear_settings_cache, get_settings  # noqa: E402
from core.csv_schema import read_products_csv, write_products_csv  # noqa: E402
from core.monitor.stock_monitor import (  # noqa: E402
    load_monitor_hits,
    resume_buyma_listings_for_hits,
    run_stock_monitor,
    stop_buyma_listings_for_hits,
)
from core.paths import runtime_root  # noqa: E402
from core.prompts import default_description_prompt, default_image_prompt  # noqa: E402
from core.scrapers.scrape_targets import (  # noqa: E402
    load_scrape_targets,
    save_scrape_targets,
    summarize_targets,
)
from core.sessions.ec_session_service import (  # noqa: E402
    clear_all_sessions,
    clear_site_session,
    has_saved_session,
    secrets_root,
)
from core.sessions.site_accounts import (  # noqa: E402
    env_updates_from_site_accounts,
    load_all_site_accounts,
)
from core.ui_scrape_picker import ScrapeTargetPickerModal  # noqa: E402
from core.ui_theme import INK, INK_MUTED, MIST, apply_global_theme, ensure_ui_assets, font  # noqa: E402
from core.workbook.products_workbook import export_sheet_to_csv, list_workbook_sheets, resolve_yesterday_sheet  # noqa: E402

ROOT = runtime_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

apply_global_theme()

EC_SITE_CODES = list(SITE_LABELS.keys())

TAB_SCRAPE = "1 収集"
TAB_GENERATE = "2 生成"
TAB_LIST = "3 出品"
TAB_SETTINGS = "4 設定"
TAB_MONITOR = "5 監視"

# Traffic-light alert colors (red blink)
_ALERT_RED_ON = "#FF1F2E"
_ALERT_RED_OFF = "#6B0F16"
_ALERT_TEXT = "#FFFFFF"


class LauncherApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ensure_ui_assets()
        self.title("EC-Buyma — Unified Console")
        self._fit_window_to_screen()
        self.configure(fg_color=MIST)
        # Apply Buyma icon (title bar + taskbar). Re-apply after CTk default icon hook.
        from core.ui_icon import apply_window_icon

        apply_window_icon(self)
        self.after(0, lambda: apply_window_icon(self))
        self.after(250, lambda: apply_window_icon(self))
        self.after(600, lambda: apply_window_icon(self))
        self.after(1200, lambda: apply_window_icon(self))
        self.settings = get_settings()
        self._worker: threading.Thread | None = None
        self._stop_requested = False
        self._ec_test_site = ctk.StringVar(value=EC_SITE_CODES[0])
        self._ec_site_label = ctk.StringVar(value=SITE_LABELS[EC_SITE_CODES[0]])
        self._ec_creds: dict[str, dict[str, str]] = {}
        self._cookie_busy = False
        self._tab_alerts: dict[str, str] = {}
        self._tab_btn_defaults: dict[str, dict] = {}
        self._blink_on = False
        self._blink_after_id: str | None = None
        self._alert_poll_id: str | None = None
        self._last_seen_tab = ""

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        ctk.CTkLabel(top, text="EC-Buyma", text_color=INK, font=font(28, "bold")).pack(side="left")
        ctk.CTkLabel(top, text="Single Window Console", text_color=INK_MUTED, font=font(13)).pack(
            side="left", padx=(10, 0), pady=(8, 0)
        )
        # Global traffic-light lamp (blinks when any tab needs attention)
        alert_box = ctk.CTkFrame(top, fg_color="transparent")
        alert_box.pack(side="right")
        self._alert_lamp = ctk.CTkLabel(
            alert_box,
            text="●",
            width=28,
            text_color="#2A323C",
            font=ctk.CTkFont(size=22),
            cursor="hand2",
        )
        self._alert_lamp.pack(side="left")
        self._alert_lamp.bind("<Button-1>", lambda _e: self._ack_current_tab_alert())
        self._alert_msg = ctk.CTkLabel(
            alert_box,
            text="",
            text_color=INK_MUTED,
            font=font(12),
            anchor="e",
            cursor="hand2",
        )
        self._alert_msg.pack(side="left", padx=(6, 0))
        self._alert_msg.bind("<Button-1>", lambda _e: self._ack_current_tab_alert())

        self.tabs = ctk.CTkTabview(self, fg_color="#0E1218")
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 14))
        self.tab1 = self.tabs.add(TAB_SCRAPE)
        self.tab2 = self.tabs.add(TAB_GENERATE)
        self.tab3 = self.tabs.add(TAB_LIST)
        self.tab4 = self.tabs.add(TAB_SETTINGS)
        self.tab5 = self.tabs.add(TAB_MONITOR)

        self._build_scrape_tab(self.tab1)
        self._build_generate_tab(self.tab2)
        self._build_buyma_tab(self.tab3)
        self._build_settings_tab(self.tab4)
        self._build_monitor_tab(self.tab5)
        self._init_tab_alert_system()
        self._build_busy_overlay()

    def _window_scale(self) -> float:
        try:
            scale = float(self._get_window_scaling())  # noqa: SLF001
        except Exception:  # noqa: BLE001
            scale = 1.0
        return scale if scale > 0.2 else 1.0

    def _fit_window_to_screen(self) -> None:
        """Size the window to the usable screen so 1366x768 / high-DPI PCs are usable.

        CustomTkinter scales geometry/minsize, so values here are *unscaled* logical sizes.
        """
        self.update_idletasks()
        try:
            sw = int(self.winfo_screenwidth() or 1280)
            sh = int(self.winfo_screenheight() or 720)
        except Exception:  # noqa: BLE001
            sw, sh = 1280, 720
        scale = self._window_scale()
        # Screen px → CTk logical units (geometry() will scale back up).
        usable_w = max(640, int((sw - 32) / scale))
        usable_h = max(480, int((sh - 80) / scale))
        width = min(1200, usable_w)
        height = min(900, usable_h)
        self.minsize(min(820, usable_w), min(520, usable_h))
        x = max(0, int((sw / scale - width) / 2))
        y = max(0, int((sh / scale - height) / 5))
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.resizable(True, True)

    def _scroll_body(self, tab) -> ctk.CTkScrollableFrame:
        """Vertical scroll host so tab content is never clipped on short screens."""
        body = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        body.pack(fill="both", expand=True)
        return body

    def _adaptive_log_height(self) -> int:
        try:
            sh = int(self.winfo_screenheight() or 900)
        except Exception:  # noqa: BLE001
            sh = 900
        if sh <= 768:
            return 140
        if sh <= 900:
            return 200
        return 260

    def _build_log(self, parent) -> tuple[ctk.CTkTextbox, ctk.CTkProgressBar, ctk.StringVar]:
        log = ctk.CTkTextbox(parent, height=self._adaptive_log_height(), font=ctk.CTkFont(family="Consolas", size=12))
        log.pack(fill="both", expand=True, padx=10, pady=(8, 6))
        info = ctk.StringVar(value="進捗: 0/0")
        ctk.CTkLabel(parent, textvariable=info, font=font(12), text_color=INK_MUTED).pack(anchor="w", padx=12)
        bar = ctk.CTkProgressBar(parent, height=8)
        bar.pack(fill="x", padx=12, pady=(0, 10))
        bar.set(0.0)
        return log, bar, info

    def _append_log(self, box: ctk.CTkTextbox, msg: str) -> None:
        def _ui():
            box.insert("end", msg + "\n")
            box.see("end")

        self.after(0, _ui)

    def _set_progress(self, bar: ctk.CTkProgressBar, info: ctk.StringVar, current: int, total: int, label: str) -> None:
        total = max(1, int(total))
        current = max(0, int(current))
        ratio = max(0.0, min(1.0, current / total))
        self.after(0, lambda: (bar.set(ratio), info.set(f"進捗: {current}/{total} · {label}")))

    def _init_tab_alert_system(self) -> None:
        """Capture default tab-button colors and start alert polling."""
        buttons = getattr(self.tabs._segmented_button, "_buttons_dict", {})  # noqa: SLF001
        for name, btn in buttons.items():
            self._tab_btn_defaults[name] = {
                "fg_color": btn.cget("fg_color"),
                "hover_color": btn.cget("hover_color"),
                "text_color": btn.cget("text_color"),
            }
        self._last_seen_tab = self.tabs.get()
        self._alert_poll_id = self.after(300, self._poll_tab_selection)

    def _tab_button(self, tab_name: str):
        buttons = getattr(self.tabs._segmented_button, "_buttons_dict", {})  # noqa: SLF001
        return buttons.get(tab_name)

    def raise_tab_alert(self, tab_name: str, reason: str = "") -> None:
        """Start red traffic-light blink on a tab until the user opens it."""

        def _ui() -> None:
            self._tab_alerts[tab_name] = reason or "要確認"
            self._refresh_alert_banner()
            self._ensure_blink_loop()
            try:
                self.bell()
            except Exception:  # noqa: BLE001
                pass
            # Nudge window so periodic checks are noticed even if unfocused.
            try:
                self.lift()
                self.attributes("-topmost", True)
                self.after(400, lambda: self.attributes("-topmost", False))
            except Exception:  # noqa: BLE001
                pass
            if hasattr(self, "mon_signal") and tab_name == TAB_MONITOR:
                self.mon_signal.configure(text_color=_ALERT_RED_ON)

        self.after(0, _ui)

    def clear_tab_alert(self, tab_name: str) -> None:
        if tab_name not in self._tab_alerts:
            return
        self._tab_alerts.pop(tab_name, None)
        self._restore_tab_button(tab_name)
        self._refresh_alert_banner()
        if hasattr(self, "mon_signal") and tab_name == TAB_MONITOR:
            self.mon_signal.configure(text_color="#2A323C")
        if not self._tab_alerts and self._blink_after_id:
            try:
                self.after_cancel(self._blink_after_id)
            except Exception:  # noqa: BLE001
                pass
            self._blink_after_id = None
            self._blink_on = False

    def _restore_tab_button(self, tab_name: str) -> None:
        btn = self._tab_button(tab_name)
        defaults = self._tab_btn_defaults.get(tab_name)
        if not btn or not defaults:
            return
        try:
            btn.configure(**defaults)
        except Exception:  # noqa: BLE001
            return
        sb = self.tabs._segmented_button  # noqa: SLF001
        try:
            if self.tabs.get() == tab_name:
                sb._select_button_by_value(tab_name)  # noqa: SLF001
            else:
                sb._unselect_button_by_value(tab_name)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass

    def _refresh_alert_banner(self) -> None:
        if not self._tab_alerts:
            self._alert_lamp.configure(text_color="#2A323C")
            self._alert_msg.configure(text="")
            return
        parts = [f"{name}: {reason}" for name, reason in self._tab_alerts.items()]
        self._alert_msg.configure(text=" / ".join(parts))

    def _ensure_blink_loop(self) -> None:
        if self._blink_after_id is not None:
            return
        self._blink_on = False
        self._blink_tick()

    def _blink_tick(self) -> None:
        self._blink_after_id = None
        if not self._tab_alerts:
            self._blink_on = False
            self._alert_lamp.configure(text_color="#2A323C")
            if hasattr(self, "mon_signal"):
                self.mon_signal.configure(text_color="#2A323C")
            return
        self._blink_on = not self._blink_on
        on = self._blink_on
        color = _ALERT_RED_ON if on else _ALERT_RED_OFF
        self._alert_lamp.configure(text_color=color)
        if TAB_MONITOR in self._tab_alerts and hasattr(self, "mon_signal"):
            self.mon_signal.configure(text_color=color)
        for name in list(self._tab_alerts):
            btn = self._tab_button(name)
            if not btn:
                continue
            try:
                if on:
                    btn.configure(fg_color=_ALERT_RED_ON, hover_color="#C41020", text_color=_ALERT_TEXT)
                else:
                    btn.configure(fg_color=_ALERT_RED_OFF, hover_color="#8B121C", text_color=_ALERT_TEXT)
            except Exception:  # noqa: BLE001
                continue
        self._blink_after_id = self.after(420, self._blink_tick)

    def _ack_current_tab_alert(self) -> None:
        """Acknowledge alert for the currently open tab (or jump to first alert)."""
        current = self.tabs.get()
        if current in self._tab_alerts:
            self.clear_tab_alert(current)
            return
        if self._tab_alerts:
            first = next(iter(self._tab_alerts))
            self.tabs.set(first)
            self.clear_tab_alert(first)

    def _poll_tab_selection(self) -> None:
        """Clear alert only when the user switches onto the blinking tab."""
        try:
            current = self.tabs.get()
        except Exception:  # noqa: BLE001
            current = ""
        if current and current != self._last_seen_tab:
            if current in self._tab_alerts:
                self.clear_tab_alert(current)
            self._last_seen_tab = current
        self._alert_poll_id = self.after(300, self._poll_tab_selection)

    def _start_thread(self, target) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("実行中", "別の処理が実行中です。停止後に再実行してください。")
            return
        self._stop_requested = False
        self._worker = threading.Thread(target=target, daemon=True)
        self._worker.start()

    def _should_stop(self) -> bool:
        return self._stop_requested

    def _build_scrape_tab(self, tab) -> None:
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(10, 0))
        self.scrape_sites: dict[str, ctk.BooleanVar] = {}
        for code, label in SITE_LABELS.items():
            var = ctk.BooleanVar(value=True)
            self.scrape_sites[code] = var
            ctk.CTkCheckBox(top, text=label, variable=var).pack(side="left", padx=6)

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=8)
        ctk.CTkLabel(row, text="目標件数").pack(side="left")
        self.scrape_count = ctk.CTkEntry(row, width=90)
        self.scrape_count.insert(0, str(self.settings.scrape_default_count))
        self.scrape_count.pack(side="left", padx=6)
        ctk.CTkLabel(row, text="出力先").pack(side="left", padx=(12, 0))
        self.scrape_out = ctk.CTkEntry(row, width=360)
        self.scrape_out.insert(0, str(self.settings.scrape_output_dir))
        self.scrape_out.pack(side="left", padx=6)
        ctk.CTkLabel(row, text="利益率%").pack(side="left", padx=(12, 0))
        self.scrape_profit = ctk.CTkEntry(row, width=70)
        self.scrape_profit.insert(0, f"{float(self.settings.buyma_profit_rate) * 100:g}")
        self.scrape_profit.pack(side="left", padx=6)

        row2 = ctk.CTkFrame(tab, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=6)
        self.scrape_mode = ctk.StringVar(value="auto")
        ctk.CTkRadioButton(row2, text="手動（収集のみ）", variable=self.scrape_mode, value="manual").pack(side="left", padx=6)
        ctk.CTkRadioButton(row2, text="自動（完了後 Studio 連鎖）", variable=self.scrape_mode, value="auto").pack(side="left", padx=6)
        self.scrape_prefer_new = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(row2, text="新着のみ取得", variable=self.scrape_prefer_new).pack(side="left", padx=14)
        ctk.CTkButton(row2, text="開始", command=self._start_scrape, width=100).pack(side="left", padx=(16, 6))
        ctk.CTkButton(row2, text="停止", command=lambda: setattr(self, "_stop_requested", True), width=100).pack(side="left")
        self.scrape_targets_summary = ctk.CTkLabel(row2, text="", text_color=INK_MUTED, font=font(11))
        self.scrape_targets_summary.pack(side="right", padx=8)

        # Compact 4-site overview (no scroll, no edit — settings tab only).
        overview = ctk.CTkFrame(tab, fg_color="#0B0F14", corner_radius=10)
        overview.pack(fill="x", padx=10, pady=(4, 6))
        head = ctk.CTkFrame(overview, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(10, 2))
        ctk.CTkLabel(head, text="収集ターゲット（設定タブで変更）", font=font(13, "bold")).pack(side="left")
        self.scrape_cards = ctk.CTkFrame(overview, fg_color="transparent")
        self.scrape_cards.pack(fill="x", padx=8, pady=(4, 12))
        for col in range(4):
            self.scrape_cards.grid_columnconfigure(col, weight=1, uniform="site")
        self._refresh_scrape_targets_summary()

        self.scrape_log, self.scrape_bar, self.scrape_info = self._build_log(tab)

    @staticmethod
    def _short_join(items: list[str], *, limit: int = 3) -> str:
        if not items:
            return "—"
        shown = items[:limit]
        text = " · ".join(shown)
        rest = len(items) - len(shown)
        if rest > 0:
            text += f"  他{rest}"
        return text

    def _build_target_card(
        self,
        parent,
        site_code: str,
        brands: list[str],
        categories: list[str],
        *,
        col: int = 0,
    ) -> None:
        from core.scrapers.site_catalog import categories_for
        from core.ui_theme import LINE, SURFACE_RAISED

        kw_map = {c.keywords.lower(): c.label for c in categories_for(site_code)}
        kw_map.update({c.id.lower(): c.label for c in categories_for(site_code)})
        cat_labels = [kw_map.get(raw.lower(), raw) for raw in categories]

        card = ctk.CTkFrame(parent, fg_color=SURFACE_RAISED, corner_radius=8, border_width=1, border_color=LINE)
        card.grid(row=0, column=col, sticky="nsew", padx=4, pady=2)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(top, text=SITE_LABELS.get(site_code, site_code), font=font(12, "bold")).pack(side="left")
        ctk.CTkLabel(
            top,
            text=f"{len(brands)} / {len(categories)}",
            text_color=INK_MUTED,
            font=font(10),
        ).pack(side="right")

        ctk.CTkLabel(card, text="ブランド", text_color=INK_MUTED, font=font(10)).pack(anchor="w", padx=10)
        ctk.CTkLabel(
            card,
            text=self._short_join(brands, limit=3),
            font=font(11),
            wraplength=240,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=10, pady=(0, 4))

        ctk.CTkLabel(card, text="カテゴリ", text_color=INK_MUTED, font=font(10)).pack(anchor="w", padx=10)
        ctk.CTkLabel(
            card,
            text=self._short_join(cat_labels, limit=3),
            font=font(11),
            wraplength=240,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=10, pady=(0, 10))

    def _parse_profit_rate(self, entry: ctk.CTkEntry) -> float | None:
        raw = (entry.get() or "").strip()
        if not raw:
            return None
        try:
            val = float(raw.replace("%", ""))
        except ValueError:
            raise ValueError("利益率は数値で指定してください。") from None
        if val > 1.0:
            val = val / 100.0
        return val

    def _start_scrape(self) -> None:
        sites = [c for c, v in self.scrape_sites.items() if v.get()]
        if not sites:
            messagebox.showwarning("入力", "サイトを1つ以上選択してください。")
            return
        try:
            target = int(self.scrape_count.get().strip() or "1")
        except ValueError:
            messagebox.showwarning("入力", "件数は整数で指定してください。")
            return
        try:
            profit = self._parse_profit_rate(self.scrape_profit)
        except ValueError as exc:
            messagebox.showwarning("入力", str(exc))
            return
        out = Path(self.scrape_out.get().strip() or str(self.settings.workspace_dir / "scrape"))
        auto_chain = self.scrape_mode.get() == "auto"
        run_dir = out / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._set_progress(self.scrape_bar, self.scrape_info, 0, max(1, target), "初期化")

        def _fill_gen_csv(path: Path) -> None:
            def _apply() -> None:
                if not hasattr(self, "gen_csv"):
                    return
                self.gen_csv.delete(0, "end")
                self.gen_csv.insert(0, str(path))

            self.after(0, _apply)

        def _work():
            csv_path = run_dir / "products.csv"
            try:
                csv_path = run_scrape(
                    site_codes=sites,
                    target_count=target,
                    output_dir=run_dir,
                    prefer_new=bool(self.scrape_prefer_new.get()),
                    profit_rate=profit,
                    chain_engine2=False,
                    log=lambda m: self._append_log(self.scrape_log, m),
                    should_stop=self._should_stop,
                    on_progress=lambda c, t, s: self._set_progress(self.scrape_bar, self.scrape_info, c, t, s),
                )
                _fill_gen_csv(csv_path)
                self._append_log(self.scrape_log, f"生成タブの入力CSV: {csv_path}")
                if auto_chain and not self._should_stop():
                    self.after(0, lambda: self.tabs.set(TAB_GENERATE))
                    self._append_log(self.gen_log, f"自動連鎖: Engine1完了 → Engine2開始 ({csv_path})")
                    self._set_progress(self.gen_bar, self.gen_info, 0, 1, "Engine2 初期化")
                    run_generate(
                        csv_path=csv_path,
                        output_dir=Path(self.gen_out.get().strip() or str(self.settings.workspace_dir / "generate")),
                        profit_rate=profit,
                        log=lambda m: self._append_log(self.gen_log, m),
                        should_stop=self._should_stop,
                        on_progress=lambda c, t, s: self._set_progress(self.gen_bar, self.gen_info, c, t, s),
                    )
                    self.raise_tab_alert(TAB_GENERATE, "生成完了")
                else:
                    self.raise_tab_alert(TAB_SCRAPE, "収集完了")
            except Exception as exc:  # noqa: BLE001
                self._append_log(self.scrape_log, f"エラー: {exc}")
                if csv_path.exists():
                    _fill_gen_csv(csv_path)
                    self._append_log(self.scrape_log, f"生成タブの入力CSV: {csv_path}")
                self.raise_tab_alert(TAB_SCRAPE, "収集エラー")

        self._start_thread(_work)

    def _build_generate_tab(self, tab) -> None:
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(10, 6))
        ctk.CTkLabel(row, text="入力CSV").pack(side="left")
        self.gen_csv = ctk.CTkEntry(row, width=480)
        self.gen_csv.pack(side="left", padx=6)
        ctk.CTkButton(row, text="参照", width=80, command=self._pick_gen_csv).pack(side="left", padx=(0, 8))
        row2 = ctk.CTkFrame(tab, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=6)
        ctk.CTkLabel(row2, text="出力先").pack(side="left")
        self.gen_out = ctk.CTkEntry(row2, width=360)
        self.gen_out.insert(0, str(self.settings.generate_output_dir))
        self.gen_out.pack(side="left", padx=6)
        ctk.CTkLabel(row2, text="利益率%").pack(side="left", padx=(10, 0))
        self.gen_profit = ctk.CTkEntry(row2, width=70)
        self.gen_profit.insert(0, f"{float(self.settings.buyma_profit_rate) * 100:g}")
        self.gen_profit.pack(side="left", padx=6)
        ctk.CTkButton(row2, text="価格再計算のみ", command=self._reprice_csv, width=120).pack(side="left", padx=(8, 6))
        ctk.CTkButton(row2, text="開始", command=self._start_generate, width=100).pack(side="left", padx=(4, 6))
        ctk.CTkButton(row2, text="停止", command=lambda: setattr(self, "_stop_requested", True), width=100).pack(side="left")
        self.gen_log, self.gen_bar, self.gen_info = self._build_log(tab)

    def _pick_gen_csv(self) -> None:
        from tkinter import filedialog

        initial = self.settings.workspace_dir / "scrape"
        if not initial.is_dir():
            initial = self.settings.workspace_dir
        path = filedialog.askopenfilename(
            parent=self,
            title="生成するCSVを選択",
            initialdir=str(initial) if initial.is_dir() else None,
            filetypes=[("CSV", "*.csv"), ("すべてのファイル", "*.*")],
        )
        if path:
            self.gen_csv.delete(0, "end")
            self.gen_csv.insert(0, path)

    def _reprice_csv(self) -> None:
        csv = Path(self.gen_csv.get().strip())
        if not csv.exists():
            messagebox.showwarning("入力", "有効なCSVを指定してください。")
            return
        try:
            profit = self._parse_profit_rate(self.gen_profit)
        except ValueError as exc:
            messagebox.showwarning("入力", str(exc))
            return

        def _work():
            try:
                rows = read_products_csv(csv)
                out_rows = [reprice_row_from_cost(r, profit_rate=profit) for r in rows]
                out = Path(self.gen_out.get().strip() or str(self.settings.workspace_dir / "generate")) / "products_repriced.csv"
                out.parent.mkdir(parents=True, exist_ok=True)
                write_products_csv(out, out_rows)
                write_products_csv(csv, out_rows)
                self._append_log(self.gen_log, f"価格再計算完了: {len(out_rows)} 行 → {out}")
                if profit is not None:
                    self._append_log(self.gen_log, f"適用利益率: {profit:.1%}")
            except Exception as exc:  # noqa: BLE001
                self._append_log(self.gen_log, f"エラー: {exc}")

        self._start_thread(_work)

    def _start_generate(self) -> None:
        csv = Path(self.gen_csv.get().strip())
        if not csv.exists():
            messagebox.showwarning("入力", "有効なCSVを指定してください。")
            return
        try:
            profit = self._parse_profit_rate(self.gen_profit)
        except ValueError as exc:
            messagebox.showwarning("入力", str(exc))
            return
        out = Path(self.gen_out.get().strip() or str(self.settings.workspace_dir / "generate"))
        self._set_progress(self.gen_bar, self.gen_info, 0, 1, "初期化")

        def _work():
            try:
                run_generate(
                    csv_path=csv,
                    output_dir=out,
                    profit_rate=profit,
                    log=lambda m: self._append_log(self.gen_log, m),
                    should_stop=self._should_stop,
                    on_progress=lambda c, t, s: self._set_progress(self.gen_bar, self.gen_info, c, t, s),
                )
                self.raise_tab_alert(TAB_GENERATE, "生成完了")
            except Exception as exc:  # noqa: BLE001
                self._append_log(self.gen_log, f"エラー: {exc}")
                self.raise_tab_alert(TAB_GENERATE, "生成エラー")

        self._start_thread(_work)

    def _build_buyma_tab(self, tab) -> None:
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(10, 6))
        ctk.CTkLabel(row, text="入力CSV").pack(side="left")
        self.list_csv = ctk.CTkEntry(row, width=420)
        self.list_csv.pack(side="left", padx=6)
        ctk.CTkButton(row, text="参照", width=80, command=self._pick_list_csv).pack(side="left", padx=(0, 8))
        self.list_mode = ctk.StringVar(value="manual")
        ctk.CTkRadioButton(row, text="手動CSV", variable=self.list_mode, value="manual").pack(side="left", padx=4)
        ctk.CTkRadioButton(row, text="シート出品", variable=self.list_mode, value="auto").pack(side="left", padx=4)
        row2 = ctk.CTkFrame(tab, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=6)
        sheets = list_workbook_sheets(self.settings.products_workbook_path) or ["(シートなし)"]
        self.list_sheet = ctk.StringVar(value=sheets[0])
        ctk.CTkLabel(row2, text="シート").pack(side="left")
        self.list_sheet_menu = ctk.CTkOptionMenu(row2, variable=self.list_sheet, values=sheets, width=240)
        self.list_sheet_menu.pack(side="left", padx=6)
        ctk.CTkButton(row2, text="再読込", width=72, command=self._reload_list_sheets).pack(side="left", padx=(0, 4))
        ctk.CTkButton(row2, text="シート→CSV", width=100, command=self._sheet_to_list_csv).pack(side="left", padx=(4, 8))
        self.list_submit = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(row2, text="出品確定", variable=self.list_submit).pack(side="left", padx=10)
        ctk.CTkButton(row2, text="開始", command=self._start_list, width=100).pack(side="left", padx=(8, 6))
        ctk.CTkButton(row2, text="停止", command=lambda: setattr(self, "_stop_requested", True), width=100).pack(side="left")

        from core.ui_widgets import ListingQueueTable

        self._list_csv_path: Path | None = None
        self.list_queue = ListingQueueTable(tab, height=240, on_retry=self._retry_list_item)
        self.list_queue.pack(fill="both", expand=True, padx=10, pady=(8, 4))
        self.list_log, self.list_bar, self.list_info = self._build_log(tab)
        self.list_log.configure(height=140)
        # Friendly default for manual mode: most recent generated CSV.
        default_csv = self.settings.workspace_dir / "generate" / "products_ready.csv"
        if default_csv.exists():
            self.list_csv.insert(0, str(default_csv))
            self._load_list_queue(default_csv)
    def _load_list_queue(self, csv_path: Path | str | None) -> None:
        from core.csv_schema import read_products_csv

        if not hasattr(self, "list_queue"):
            return
        if not csv_path:
            self._list_csv_path = None
            self.list_queue.clear()
            return
        path = Path(csv_path)
        if not path.exists() or path.suffix.lower() != ".csv":
            return
        try:
            rows = read_products_csv(path)
            self._list_csv_path = path
            self.list_queue.load_rows(rows)
            self._append_log(self.list_log, f"出品リスト読込: {len(rows)} 件 ← {path.name}")
        except Exception as exc:  # noqa: BLE001
            self._append_log(self.list_log, f"リスト読込失敗: {exc}")

    def _retry_list_item(self, idx: int) -> None:
        csv_path = self._list_csv_path
        if csv_path is None or not Path(csv_path).exists():
            messagebox.showwarning("再出品", "CSVが読み込まれていません。")
            return
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("実行中", "別の処理が実行中です。完了後に再出品してください。")
            return
        self._append_log(self.list_log, f"手動再出品: #{idx + 1}")
        self._set_progress(self.list_bar, self.list_info, 0, 1, f"再出品 #{idx + 1}")

        def _work():
            try:
                def _on_item(i: int, row: dict, status: str, error: str) -> None:
                    self.after(0, lambda ii=i, s=status, e=error: self.list_queue.set_status(ii, s, error=e))

                run_buyma_list(
                    csv_path=Path(csv_path),
                    submit=bool(self.list_submit.get()),
                    log=lambda m: self._append_log(self.list_log, m),
                    should_stop=self._should_stop,
                    on_progress=lambda c, t, s: self._set_progress(self.list_bar, self.list_info, c, t, s),
                    on_item_status=_on_item,
                    only_indices=[idx],
                )
            except Exception as exc:  # noqa: BLE001
                self._append_log(self.list_log, f"再出品エラー: {exc}")

        self._start_thread(_work)

    def _reload_list_sheets(self) -> None:
        sheets = list_workbook_sheets(self.settings.products_workbook_path) or ["(シートなし)"]
        self.list_sheet_menu.configure(values=sheets)
        self.list_sheet.set(sheets[0])
        self._append_log(self.list_log, f"シート一覧更新: {', '.join(sheets)}")

    def _pick_list_csv(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            parent=self,
            title="CSV を選択",
            filetypes=[("CSV", "*.csv")],
        )
        if path:
            self.list_csv.delete(0, "end")
            self.list_csv.insert(0, path)
            self._load_list_queue(path)

    def _sheet_to_list_csv(self) -> None:
        self._reload_list_sheets()
        sheet = (self.list_sheet.get() or "").strip()
        if not sheet or sheet.startswith("("):
            messagebox.showwarning("入力", "シートを選択してください。")
            return
        out = self.settings.workspace_dir / "generate" / f"sheet_{sheet.replace(' ', '_')}_export.csv"
        try:
            csv_path = export_sheet_to_csv(self.settings.products_workbook_path, sheet, out)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("エラー", f"シート出力失敗: {exc}")
            return
        self.list_csv.delete(0, "end")
        self.list_csv.insert(0, str(csv_path))
        self.list_mode.set("manual")
        self._append_log(self.list_log, f"シート→CSV: {csv_path}")
        self._load_list_queue(csv_path)

    def _start_list(self) -> None:
        csv_path: Path | None = None
        if self.list_mode.get() == "auto":
            self._reload_list_sheets()
            sheet = resolve_yesterday_sheet(self.settings.products_workbook_path) or (self.list_sheet.get() or "").strip()
            if not sheet or sheet.startswith("("):
                messagebox.showwarning("入力", "シートを選択してください。")
                return
            out = self.settings.workspace_dir / "generate" / f"sheet_{sheet.replace(' ', '_')}_list.csv"
            try:
                csv_path = export_sheet_to_csv(self.settings.products_workbook_path, sheet, out)
                self._append_log(self.list_log, f"シート読込: [{sheet}] → {csv_path}")
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("エラー", f"シート出力失敗: {exc}")
                return
        else:
            p = Path(self.list_csv.get().strip())
            if not p.exists():
                messagebox.showwarning("入力", "有効なCSVを指定してください。")
                return
            if p.suffix.lower() in {".xlsx", ".xlsm", ".xls", ".xltx", ".xltm"}:
                messagebox.showwarning(
                    "入力",
                    "Excelファイルは直接出品できません。\n"
                    "「再読込」→「シート→CSV」でCSVを作成するか、products_ready.csv を選んでください。",
                )
                return
            csv_path = p
        self._set_progress(self.list_bar, self.list_info, 0, 1, "初期化")
        self._load_list_queue(csv_path)

        def _work():
            try:
                def _on_item(idx: int, row: dict, status: str, error: str) -> None:
                    self.after(0, lambda i=idx, s=status, e=error: self.list_queue.set_status(i, s, error=e))

                run_buyma_list(
                    csv_path=csv_path,
                    submit=bool(self.list_submit.get()),
                    log=lambda m: self._append_log(self.list_log, m),
                    should_stop=self._should_stop,
                    on_progress=lambda c, t, s: self._set_progress(self.list_bar, self.list_info, c, t, s),
                    on_item_status=_on_item,
                )
                self.raise_tab_alert(TAB_LIST, "出品完了")
            except Exception as exc:  # noqa: BLE001
                self._append_log(self.list_log, f"エラー: {exc}")
                self.raise_tab_alert(TAB_LIST, "出品エラー")

        self._start_thread(_work)

    def _load_env_map(self) -> dict[str, str]:
        env_path = ROOT / ".env"
        data: dict[str, str] = {}
        if not env_path.exists():
            return data
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if not line or line.strip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v
        return data

    def _save_env_map(self, updates: dict[str, str]) -> None:
        env_path = ROOT / ".env"
        data = self._load_env_map()
        data.update(updates)
        # Preserve existing key order where possible; append new keys at end.
        lines: list[str] = []
        seen: set[str] = set()
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if not line.strip() or line.strip().startswith("#") or "=" not in line:
                    lines.append(line)
                    continue
                key = line.split("=", 1)[0].strip()
                if key in data:
                    lines.append(f"{key}={data[key]}")
                    seen.add(key)
                else:
                    lines.append(line)
        for key, value in data.items():
            if key not in seen:
                lines.append(f"{key}={value}")
                seen.add(key)
        env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        clear_settings_cache()
        self.settings = get_settings()

    def _entry_get(self, entry: ctk.CTkEntry) -> str:
        """Read CTkEntry even when temporarily disabled."""
        state = str(entry.cget("state") or "normal")
        if state != "normal":
            entry.configure(state="normal")
            try:
                return entry.get().strip()
            finally:
                entry.configure(state=state)
        return entry.get().strip()

    def _selected_ec_site(self) -> str:
        label = str(self._ec_site_label.get() or "")
        for code, name in SITE_LABELS.items():
            if name == label:
                return code
        code = str(self._ec_test_site.get() or "")
        return code if code in SITE_LABELS else EC_SITE_CODES[0]

    def _stash_ec_fields(self) -> None:
        if not hasattr(self, "ec_email"):
            return
        code = self._selected_ec_site()
        self._ec_creds[code] = {
            "email": self._entry_get(self.ec_email),
            "password": self._entry_get(self.ec_pass),
        }

    def _load_ec_fields(self, site_code: str) -> None:
        slot = self._ec_creds.get(site_code) or {"email": "", "password": ""}
        self.ec_email.delete(0, "end")
        self.ec_pass.delete(0, "end")
        self.ec_email.insert(0, slot.get("email") or "")
        self.ec_pass.insert(0, slot.get("password") or "")
        self.ec_site_title.configure(text=f"メール / パスワード（{SITE_LABELS.get(site_code, site_code)}）")

    def _on_ec_site_changed(self, label: str) -> None:
        if getattr(self, "_cookie_busy", False):
            return
        prev = str(self._ec_test_site.get() or "")
        if prev in SITE_LABELS and hasattr(self, "ec_email"):
            self._ec_creds[prev] = {
                "email": self._entry_get(self.ec_email),
                "password": self._entry_get(self.ec_pass),
            }
        code = prev
        for site_code, name in SITE_LABELS.items():
            if name == label:
                code = site_code
                break
        self._ec_test_site.set(code)
        self._ec_site_label.set(SITE_LABELS.get(code, code))
        self._load_ec_fields(code)
        self._refresh_lock_ui()

    def _account_env_updates(self) -> dict[str, str]:
        self._stash_ec_fields()
        updates = env_updates_from_site_accounts(self._ec_creds)
        updates["BUYMA_ACCOUNT_EMAIL"] = self._entry_get(self.buyma_email)
        updates["BUYMA_ACCOUNT_PASSWORD"] = self._entry_get(self.buyma_pass)
        return updates

    def _build_busy_overlay(self) -> None:
        self._busy_overlay = ctk.CTkFrame(self, fg_color="#10141A", corner_radius=0)
        inner = ctk.CTkFrame(self._busy_overlay, fg_color="#1B222C")
        inner.place(relx=0.5, rely=0.5, anchor="center")
        self._busy_label = ctk.CTkLabel(
            inner,
            text="クッキーを登録しています。完了するまで操作しないでください。",
            font=font(16, "bold"),
            wraplength=420,
            justify="center",
        )
        self._busy_label.pack(padx=36, pady=(28, 12))
        self._busy_detail = ctk.CTkLabel(inner, text="", text_color=INK_MUTED, font=font(13))
        self._busy_detail.pack(padx=36, pady=(0, 8))
        try:
            self._busy_bar = ctk.CTkProgressBar(inner, width=320, mode="indeterminate")
        except TypeError:
            self._busy_bar = ctk.CTkProgressBar(inner, width=320)
        self._busy_bar.pack(padx=36, pady=(8, 28))

    def _set_cookie_busy(self, busy: bool, detail: str = "") -> None:
        self._cookie_busy = bool(busy)
        if busy:
            self._busy_detail.configure(text=detail)
            self._busy_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._busy_overlay.lift()
            try:
                self._busy_bar.start()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._busy_overlay.grab_set()
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                self._busy_overlay.grab_release()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._busy_bar.stop()
            except Exception:  # noqa: BLE001
                pass
            self._busy_overlay.place_forget()

    def _build_settings_tab(self, tab) -> None:
        env = self._load_env_map()
        body = self._scroll_body(tab)

        section1 = ctk.CTkFrame(body)
        section1.pack(fill="x", padx=10, pady=(10, 8))
        env_accounts = load_all_site_accounts(env)
        self._ec_creds = env_accounts
        self.ec_email = ctk.CTkEntry(section1, width=320)
        self.ec_pass = ctk.CTkEntry(section1, width=320, show="*")
        ctk.CTkLabel(section1, text="ECアカウント（サイトごと）", font=font(14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=6
        )
        ctk.CTkLabel(section1, text="対象サイト").grid(row=1, column=0, sticky="w", padx=8)
        ctk.CTkOptionMenu(
            section1,
            variable=self._ec_site_label,
            values=[SITE_LABELS[code] for code in EC_SITE_CODES],
            command=self._on_ec_site_changed,
            width=220,
        ).grid(row=1, column=1, sticky="w", padx=8, pady=4)
        self.ec_site_title = ctk.CTkLabel(section1, text="メール / パスワード", font=font(13))
        self.ec_site_title.grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 0))
        ctk.CTkLabel(section1, text="Email").grid(row=3, column=0, sticky="w", padx=8)
        self.ec_email.grid(row=3, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(section1, text="Password").grid(row=4, column=0, sticky="w", padx=8)
        self.ec_pass.grid(row=4, column=1, sticky="w", padx=8, pady=4)
        self._load_ec_fields(self._selected_ec_site())
        self.ec_status = ctk.CTkLabel(section1, text="", text_color=INK_MUTED, font=font(12))
        self.ec_status.grid(row=5, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 0))
        self.ec_action_frame = ctk.CTkFrame(section1, fg_color="transparent")
        self.ec_action_frame.grid(row=6, column=0, columnspan=2, sticky="w", padx=8, pady=8)

        section2 = ctk.CTkFrame(body)
        section2.pack(fill="x", padx=10, pady=8)
        self.buyma_email = ctk.CTkEntry(section2, width=280)
        self.buyma_pass = ctk.CTkEntry(section2, width=280, show="*")
        self.buyma_email.insert(0, env.get("BUYMA_ACCOUNT_EMAIL", ""))
        self.buyma_pass.insert(0, env.get("BUYMA_ACCOUNT_PASSWORD", ""))
        ctk.CTkLabel(section2, text="Buymaアカウント", font=font(14, "bold")).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ctk.CTkLabel(section2, text="Email").grid(row=1, column=0, sticky="w", padx=8)
        self.buyma_email.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(section2, text="Password").grid(row=2, column=0, sticky="w", padx=8)
        self.buyma_pass.grid(row=2, column=1, sticky="w", padx=8, pady=4)
        self.buyma_status = ctk.CTkLabel(section2, text="", text_color=INK_MUTED, font=font(12))
        self.buyma_status.grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 0))
        self.buyma_action_frame = ctk.CTkFrame(section2, fg_color="transparent")
        self.buyma_action_frame.grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=8)

        section_gpt = ctk.CTkFrame(body)
        section_gpt.pack(fill="x", padx=10, pady=8)
        section_gpt.grid_columnconfigure(1, weight=1)
        self.chatgpt_desc_url = ctk.CTkEntry(section_gpt)
        self.chatgpt_image_url = ctk.CTkEntry(section_gpt)
        self.chatgpt_desc_url.insert(
            0, env.get("CHATGPT_DESCRIPTION_PROJECT_URL", str(self.settings.chatgpt_description_project_url or ""))
        )
        self.chatgpt_image_url.insert(
            0, env.get("CHATGPT_IMAGE_PROJECT_URL", str(self.settings.chatgpt_image_project_url or ""))
        )
        ctk.CTkLabel(section_gpt, text="ChatGPTチャンネル", font=font(14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=6
        )
        ctk.CTkLabel(section_gpt, text="説明文チャンネルURL").grid(row=1, column=0, sticky="w", padx=8)
        self.chatgpt_desc_url.grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        ctk.CTkLabel(section_gpt, text="画像チャンネルURL").grid(row=2, column=0, sticky="w", padx=8)
        self.chatgpt_image_url.grid(row=2, column=1, sticky="ew", padx=8, pady=4)
        ctk.CTkLabel(
            section_gpt,
            text="ログイン cookie は現在の保存分を使い続けます。チャンネルリンクだけここから変更できます。",
            text_color=INK_MUTED,
            font=font(11),
            wraplength=640,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 0))
        ctk.CTkButton(
            section_gpt, text="チャンネルURLを保存", command=self._save_chatgpt_channels, width=200
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=8)

        ctk.CTkButton(body, text="アカウント設定を保存", command=self._save_accounts, width=220).pack(
            anchor="w", padx=14, pady=(4, 6)
        )

        targets = ctk.CTkFrame(body)
        targets.pack(fill="x", padx=10, pady=8)
        ctk.CTkLabel(targets, text="収集ターゲット（ブランド / カテゴリ）", font=font(14, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=6
        )
        self.settings_targets_summary = ctk.CTkLabel(
            targets, text="", text_color=INK_MUTED, font=font(12), justify="left"
        )
        self.settings_targets_summary.grid(row=1, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 6))
        ctk.CTkButton(
            targets, text="ブランドを選択…", width=160, command=lambda: self._open_target_picker("brands")
        ).grid(row=2, column=0, sticky="w", padx=8, pady=8)
        ctk.CTkButton(
            targets, text="カテゴリを選択…", width=160, command=lambda: self._open_target_picker("categories")
        ).grid(row=2, column=1, sticky="w", padx=8, pady=8)
        ctk.CTkButton(targets, text="選択をクリア", width=120, command=self._clear_scrape_targets).grid(
            row=2, column=2, sticky="w", padx=8, pady=8
        )
        self._refresh_scrape_targets_summary()

        pricing = ctk.CTkFrame(body)
        pricing.pack(fill="x", padx=10, pady=8)
        pricing.grid_columnconfigure(1, weight=1)
        pricing.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(pricing, text="価格・取得・保存先", font=font(14, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=8, pady=6
        )
        self.set_profit = ctk.CTkEntry(pricing, width=100)
        self.set_eur = ctk.CTkEntry(pricing, width=100)
        self.set_overseas = ctk.CTkEntry(pricing, width=100)
        self.set_domestic = ctk.CTkEntry(pricing, width=100)
        self.set_count = ctk.CTkEntry(pricing, width=100)
        self.set_scrape_dir = ctk.CTkEntry(pricing)
        self.set_gen_dir = ctk.CTkEntry(pricing)
        self.set_profit.insert(0, f"{float(env.get('BUYMA_PROFIT_RATE', self.settings.buyma_profit_rate)) * 100:g}")
        self.set_eur.insert(0, str(env.get("EUR_TO_JPY_RATE", self.settings.eur_to_jpy_rate)))
        self.set_overseas.insert(0, str(env.get("BUYMA_OVERSEAS_SHIPPING_EUR", self.settings.buyma_overseas_shipping_eur)))
        self.set_domestic.insert(0, str(env.get("BUYMA_DOMESTIC_SHIPPING_JPY", self.settings.buyma_domestic_shipping_jpy)))
        self.set_count.insert(0, str(env.get("SCRAPE_DEFAULT_COUNT", self.settings.scrape_default_count)))
        self.set_scrape_dir.insert(0, str(env.get("SCRAPE_OUTPUT_DIR", self.settings.scrape_output_dir)))
        self.set_gen_dir.insert(0, str(env.get("GENERATE_OUTPUT_DIR", self.settings.generate_output_dir)))
        ctk.CTkLabel(pricing, text="利益率%").grid(row=1, column=0, sticky="w", padx=8)
        self.set_profit.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(pricing, text="EUR→JPY").grid(row=1, column=2, sticky="w", padx=8)
        self.set_eur.grid(row=1, column=3, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(pricing, text="海外送料EUR").grid(row=2, column=0, sticky="w", padx=8)
        self.set_overseas.grid(row=2, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(pricing, text="国内送料JPY").grid(row=2, column=2, sticky="w", padx=8)
        self.set_domestic.grid(row=2, column=3, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(pricing, text="取得件数既定").grid(row=3, column=0, sticky="w", padx=8)
        self.set_count.grid(row=3, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(pricing, text="収集保存先").grid(row=4, column=0, sticky="w", padx=8)
        self.set_scrape_dir.grid(row=4, column=1, columnspan=3, sticky="ew", padx=8, pady=4)
        ctk.CTkLabel(pricing, text="生成保存先").grid(row=5, column=0, sticky="w", padx=8)
        self.set_gen_dir.grid(row=5, column=1, columnspan=3, sticky="ew", padx=8, pady=4)
        ctk.CTkButton(pricing, text="価格・保存先を保存", command=self._save_pricing_settings, width=200).grid(
            row=6, column=0, columnspan=2, sticky="w", padx=8, pady=8
        )

        prompt_h = 120 if (self.winfo_screenheight() or 900) <= 800 else 160
        section3 = ctk.CTkFrame(body)
        section3.pack(fill="x", padx=10, pady=(2, 16))
        split = ctk.CTkFrame(section3, fg_color="transparent")
        split.pack(fill="x", padx=8, pady=(8, 4))
        left = ctk.CTkFrame(split)
        left.pack(side="left", fill="x", expand=True, padx=(0, 4))
        right = ctk.CTkFrame(split)
        right.pack(side="left", fill="x", expand=True, padx=(4, 0))
        ctk.CTkLabel(left, text="画像生成プロンプト", font=font(13, "bold")).pack(anchor="w", padx=8, pady=(8, 4))
        self.image_prompt_box = ctk.CTkTextbox(left, height=prompt_h)
        self.image_prompt_box.pack(fill="x", expand=False, padx=8, pady=(0, 8))
        self.image_prompt_box.insert("1.0", default_image_prompt())
        ctk.CTkLabel(right, text="説明文プロンプト", font=font(13, "bold")).pack(anchor="w", padx=8, pady=(8, 4))
        self.desc_prompt_box = ctk.CTkTextbox(right, height=prompt_h)
        self.desc_prompt_box.pack(fill="x", expand=False, padx=8, pady=(0, 8))
        self.desc_prompt_box.insert("1.0", default_description_prompt())
        ctk.CTkButton(section3, text="プロンプト保存", command=self._save_prompts, width=180).pack(anchor="w", padx=8, pady=(0, 8))

        self._refresh_lock_ui()

    def _save_pricing_settings(self) -> None:
        try:
            profit_pct = float((self.set_profit.get() or "3").replace("%", "").strip())
            profit = profit_pct / 100.0 if profit_pct > 1 else profit_pct
            eur = float(self.set_eur.get().strip())
            overseas = float(self.set_overseas.get().strip())
            domestic = int(float(self.set_domestic.get().strip()))
            count = int(self.set_count.get().strip())
        except ValueError:
            messagebox.showwarning("入力", "数値項目を確認してください。")
            return
        updates = {
            "BUYMA_PROFIT_RATE": str(profit),
            "EUR_TO_JPY_RATE": str(eur),
            "BUYMA_OVERSEAS_SHIPPING_EUR": str(overseas),
            "BUYMA_DOMESTIC_SHIPPING_JPY": str(domestic),
            "SCRAPE_DEFAULT_COUNT": str(count),
        }
        scrape_dir = self.set_scrape_dir.get().strip()
        gen_dir = self.set_gen_dir.get().strip()
        if scrape_dir:
            updates["SCRAPE_OUTPUT_DIR"] = scrape_dir
        if gen_dir:
            updates["GENERATE_OUTPUT_DIR"] = gen_dir
        self._save_env_map(updates)
        clear_settings_cache()
        self.settings = get_settings()
        # Reflect paths into scrape/generate tabs.
        if scrape_dir:
            self.scrape_out.delete(0, "end")
            self.scrape_out.insert(0, scrape_dir)
        if gen_dir:
            self.gen_out.delete(0, "end")
            self.gen_out.insert(0, gen_dir)
        self.scrape_count.delete(0, "end")
        self.scrape_count.insert(0, str(count))
        for entry in (self.scrape_profit, self.gen_profit):
            entry.delete(0, "end")
            entry.insert(0, f"{profit * 100:g}")
        messagebox.showinfo(
            "保存",
            f"価格設定を保存しました。\n利益率 {profit:.1%} / EUR {eur} / 海外送料 EUR{overseas}\n"
            f"（海外送料JPY目安: ¥{int(round(overseas * eur)):,}）\n"
            f"収集先: {scrape_dir or '(未変更)'}\n生成先: {gen_dir or '(未変更)'}",
        )

    def _refresh_scrape_targets_summary(self) -> None:
        text = summarize_targets()
        detail = load_scrape_targets()
        lines = [f"現在の設定: {text}"]
        for code, block in detail.items():
            brands = block.get("brands") or []
            cats = block.get("categories") or []
            if not brands and not cats:
                continue
            b = "、".join(brands[:6]) + ("…" if len(brands) > 6 else "")
            c = "、".join(cats[:6]) + ("…" if len(cats) > 6 else "")
            bits = []
            if brands:
                bits.append(f"ブランド[{len(brands)}]: {b}")
            if cats:
                bits.append(f"カテゴリ[{len(cats)}]: {c}")
            lines.append(f"  · {SITE_LABELS.get(code, code)} — " + " / ".join(bits))
        summary = "\n".join(lines)
        if hasattr(self, "scrape_targets_summary"):
            self.scrape_targets_summary.configure(text=text)
        if hasattr(self, "settings_targets_summary"):
            self.settings_targets_summary.configure(text=summary)
        if hasattr(self, "scrape_cards"):
            for child in self.scrape_cards.winfo_children():
                child.destroy()
            for col, code in enumerate(SITE_LABELS):
                block = detail.get(code) or {}
                self._build_target_card(
                    self.scrape_cards,
                    code,
                    list(block.get("brands") or []),
                    list(block.get("categories") or []),
                    col=col,
                )

    def _open_target_picker(self, kind: str, site: str | None = None) -> None:
        initial = site or (self._ec_test_site.get() if hasattr(self, "_ec_test_site") else None)
        ScrapeTargetPickerModal(
            self,
            kind="brands" if kind == "brands" else "categories",
            on_saved=self._refresh_scrape_targets_summary,
            initial_site=initial if initial in SITE_LABELS else None,
        )

    def _open_settings_targets(self) -> None:
        self.tabs.set(TAB_SETTINGS)

    def _clear_scrape_targets(self) -> None:
        if not messagebox.askyesno("確認", "全ECサイトのブランド／カテゴリ選択をクリアしますか？"):
            return
        save_scrape_targets({})
        self._refresh_scrape_targets_summary()
        messagebox.showinfo("クリア", "収集ターゲットをクリアしました。")

    def _build_monitor_tab(self, tab) -> None:
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(10, 6))
        self.mon_signal = ctk.CTkLabel(
            row,
            text="●",
            width=22,
            text_color="#2A323C",
            font=ctk.CTkFont(size=20),
            cursor="hand2",
        )
        self.mon_signal.pack(side="left", padx=(0, 6))
        self.mon_signal.bind("<Button-1>", lambda _e: self.clear_tab_alert(TAB_MONITOR))
        ctk.CTkLabel(row, text="監視データ").pack(side="left")
        self.mon_csv = ctk.CTkEntry(row, width=420)
        default_mon = self._default_monitor_csv()
        if default_mon:
            self.mon_csv.insert(0, str(default_mon))
        self.mon_csv.pack(side="left", padx=6)
        ctk.CTkButton(row, text="参照", width=80, command=self._pick_monitor_csv).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(row, text="既定: products_workbook.xlsx", text_color=INK_MUTED, font=font(11)).pack(
            side="left", padx=(4, 0)
        )
        self.mon_auto_stop = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            row,
            text="404時は公開一時停止 / 復活時は再公開",
            variable=self.mon_auto_stop,
        ).pack(side="left", padx=8)
        row2 = ctk.CTkFrame(tab, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=6)
        ctk.CTkLabel(row2, text="間隔(分)").pack(side="left")
        self.mon_interval = ctk.CTkEntry(row2, width=70)
        self.mon_interval.insert(0, str(self.settings.stock_monitor_interval_minutes))
        self.mon_interval.pack(side="left", padx=6)
        self.mon_periodic = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(row2, text="定期監視ON", variable=self.mon_periodic, command=self._toggle_periodic_monitor).pack(
            side="left", padx=8
        )
        ctk.CTkButton(row2, text="今すぐ確認", command=self._start_monitor, width=120).pack(side="left", padx=(8, 6))
        ctk.CTkButton(row2, text="404一覧再読込", command=self._reload_monitor_hits, width=120).pack(side="left", padx=6)
        ctk.CTkButton(row2, text="選択分を公開停止", command=self._stop_selected_404, width=140).pack(side="left", padx=6)
        ctk.CTkButton(row2, text="停止", command=lambda: setattr(self, "_stop_requested", True), width=80).pack(
            side="left", padx=6
        )
        self._monitor_after_id: str | None = None
        self.mon_hits = ctk.CTkTextbox(
            tab,
            height=min(180, self._adaptive_log_height()),
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.mon_hits.pack(fill="both", expand=False, padx=10, pady=(4, 6))
        self.mon_log, self.mon_bar, self.mon_info = self._build_log(tab)
        self._reload_monitor_hits()

    def _default_monitor_csv(self) -> Path | None:
        # Prefer the full products workbook (all Buyma-registered items).
        wb = Path(self.settings.products_workbook_path)
        if wb.is_file():
            return wb
        candidates = [
            self.settings.workspace_dir / "generate" / "products_workbook.xlsx",
            self.settings.workspace_dir / "generate" / "products_ready.csv",
            self.settings.workspace_dir / "generate" / "products_repriced.csv",
        ]
        for path in candidates:
            if path.is_file():
                return path
        scrape_root = self.settings.workspace_dir / "scrape"
        if scrape_root.is_dir():
            found = sorted(scrape_root.glob("run_*/products.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
            if found:
                return found[0]
        return None

    def _pick_monitor_csv(self) -> None:
        from tkinter import filedialog

        initial = Path(self.settings.products_workbook_path).parent
        if not initial.is_dir():
            initial = self.settings.workspace_dir / "generate"
        if not initial.is_dir():
            initial = self.settings.workspace_dir
        path = filedialog.askopenfilename(
            parent=self,
            title="監視するワークブック / CSV を選択",
            initialdir=str(initial) if initial.is_dir() else None,
            filetypes=[
                ("Excel / CSV", "*.xlsx *.xlsm *.csv"),
                ("Excel", "*.xlsx *.xlsm"),
                ("CSV", "*.csv"),
                ("すべてのファイル", "*.*"),
            ],
        )
        if path:
            self.mon_csv.delete(0, "end")
            self.mon_csv.insert(0, path)
            self._append_log(self.mon_log, f"監視データを選択: {path}")

    def _resolve_monitor_csv(self) -> Path | None:
        raw = (self.mon_csv.get() or "").strip().strip('"')
        if not raw:
            return None
        path = Path(raw)
        if path.is_file() and path.suffix.lower() in {".csv", ".xlsx", ".xlsm", ".xls"}:
            return path
        return None

    def _toggle_periodic_monitor(self) -> None:
        if self._monitor_after_id:
            try:
                self.after_cancel(self._monitor_after_id)
            except Exception:  # noqa: BLE001
                pass
            self._monitor_after_id = None
        if not self.mon_periodic.get():
            self._append_log(self.mon_log, "定期監視を停止しました")
            return
        if not self._resolve_monitor_csv():
            self.mon_periodic.set(False)
            messagebox.showwarning("入力", "先に「参照」から監視データ（xlsx/CSV）を選択してください。")
            return
        try:
            minutes = max(5, int(self.mon_interval.get().strip() or "60"))
        except ValueError:
            minutes = 60
        self._append_log(self.mon_log, f"定期監視を開始します（{minutes} 分間隔）")
        self._schedule_periodic_monitor(minutes)

    def _schedule_periodic_monitor(self, minutes: int) -> None:
        if not self.mon_periodic.get():
            return
        self._start_monitor()
        self._monitor_after_id = self.after(minutes * 60 * 1000, lambda: self._schedule_periodic_monitor(minutes))

    def _reload_monitor_hits(self) -> None:
        hits = load_monitor_hits()
        self.mon_hits.delete("1.0", "end")
        if not hits:
            self.mon_hits.insert("1.0", "404検知なし")
            return
        lines = ["#  商品名 / 理由 / URL"]
        for i, h in enumerate(hits, 1):
            lines.append(
                f"{i:02d}. [{h.get('brand','')}] {h.get('name','')}\n"
                f"    {h.get('reason','')} | HTTP {h.get('status_code')} | {h.get('source_url','')}"
            )
        self.mon_hits.insert("1.0", "\n".join(lines))

    def _start_monitor(self) -> None:
        if self._worker and self._worker.is_alive():
            self._append_log(self.mon_log, "前回処理が未完了のため監視をスキップします")
            return
        csv = self._resolve_monitor_csv()
        if csv is None:
            messagebox.showwarning("入力", "有効なワークブック(.xlsx)またはCSVを「参照」から選択してください。")
            if self.mon_periodic.get():
                self.mon_periodic.set(False)
                self._toggle_periodic_monitor()
            return
        auto_stop = bool(self.mon_auto_stop.get())
        self._set_progress(self.mon_bar, self.mon_info, 0, 1, "監視開始")

        def _work():
            try:
                result = run_stock_monitor(
                    source_path=csv,
                    log=lambda m: self._append_log(self.mon_log, m),
                    should_stop=self._should_stop,
                    refresh_on_recovery=True,
                )
                hits = result.hits
                recovered = result.recovered
                self.after(0, self._reload_monitor_hits)
                if hits:
                    self._append_log(self.mon_log, f"通知: 404/在庫なし {len(hits)} 件（公開一時停止対象）")
                if recovered:
                    self._append_log(self.mon_log, f"通知: 在庫復活 {len(recovered)} 件（再公開対象）")
                if auto_stop and not self._should_stop():
                    if hits:
                        stop_buyma_listings_for_hits(
                            hits,
                            log=lambda m: self._append_log(self.mon_log, m),
                            should_stop=self._should_stop,
                        )
                    if recovered:
                        resume_buyma_listings_for_hits(
                            recovered,
                            log=lambda m: self._append_log(self.mon_log, m),
                            should_stop=self._should_stop,
                        )
                self._set_progress(self.mon_bar, self.mon_info, 1, 1, "完了")
                # Blink only when action is needed (404 / recovery / error) — not quiet success.
                if hits and recovered:
                    self.raise_tab_alert(TAB_MONITOR, f"404 {len(hits)}件 / 復活 {len(recovered)}件")
                elif hits:
                    self.raise_tab_alert(TAB_MONITOR, f"404/在庫なし {len(hits)}件")
                elif recovered:
                    self.raise_tab_alert(TAB_MONITOR, f"在庫復活 {len(recovered)}件")
            except Exception as exc:  # noqa: BLE001
                self._append_log(self.mon_log, f"エラー: {exc}")
                self.raise_tab_alert(TAB_MONITOR, "監視エラー")

        self._start_thread(_work)

    def _stop_selected_404(self) -> None:
        from core.monitor.stock_monitor import MonitorHit

        raw = load_monitor_hits()
        if not raw:
            messagebox.showinfo("監視", "停止対象の404一覧がありません。")
            return
        hits = []
        for h in raw:
            try:
                hits.append(
                    MonitorHit(
                        index=int(h.get("index") or 0),
                        name=str(h.get("name") or ""),
                        brand=str(h.get("brand") or ""),
                        source_url=str(h.get("source_url") or ""),
                        buyma_url=str(h.get("buyma_url") or ""),
                        folder=str(h.get("folder") or ""),
                        status_code=h.get("status_code"),
                        reason=str(h.get("reason") or ""),
                        inventory=int(h.get("inventory") or 1),
                        sell_price=str(h.get("sell_price") or ""),
                        checked_at=str(h.get("checked_at") or ""),
                    )
                )
            except Exception:  # noqa: BLE001
                continue

        def _work():
            try:
                stop_buyma_listings_for_hits(
                    hits,
                    log=lambda m: self._append_log(self.mon_log, m),
                    should_stop=self._should_stop,
                )
            except Exception as exc:  # noqa: BLE001
                self._append_log(self.mon_log, f"エラー: {exc}")

        self._start_thread(_work)

    def _ec_site_status_text(self) -> str:
        site = self._selected_ec_site()
        saved = [code for code in EC_SITE_CODES if has_saved_session(code)]
        current = "保存済" if has_saved_session(site) else "未設定"
        others = ", ".join(SITE_LABELS.get(code, code) for code in saved) if saved else "なし"
        return f"選択中 [{SITE_LABELS.get(site, site)}]: {current}  /  保存済みサイト: {others}"

    def _buyma_status_text(self) -> str:
        path = self.settings.buyma_cookies_path
        if path.exists() and path.stat().st_size > 10:
            return f"cookie: 保存済 ({path.name})"
        return "cookie: 未設定"

    def _refresh_lock_ui(self) -> None:
        clear_settings_cache()
        self.settings = get_settings()
        for w in self.ec_action_frame.winfo_children():
            w.destroy()
        for w in self.buyma_action_frame.winfo_children():
            w.destroy()

        # Credentials stay editable so new accounts can be registered anytime.
        for e in (self.ec_email, self.ec_pass, self.buyma_email, self.buyma_pass):
            e.configure(state="normal")

        self.ec_status.configure(text=self._ec_site_status_text())
        self.buyma_status.configure(text=self._buyma_status_text())

        site = self._selected_ec_site()
        ctk.CTkButton(
            self.ec_action_frame,
            text="ログイン情報の保存",
            command=self._save_ec_cookie,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            self.ec_action_frame,
            text="テスト",
            command=self._verify_ec_cookie,
        ).pack(side="left", padx=4)
        if has_saved_session(site):
            ctk.CTkButton(
                self.ec_action_frame,
                text=f"初期化（{site}）",
                command=self._reset_ec_selected,
            ).pack(side="left", padx=4)
        if any(has_saved_session(code) for code in EC_SITE_CODES) or secrets_root().exists():
            ctk.CTkButton(
                self.ec_action_frame,
                text="全EC初期化",
                command=self._reset_ec,
            ).pack(side="left", padx=4)

        ctk.CTkButton(
            self.buyma_action_frame,
            text="ログイン情報の保存",
            command=self._save_buyma_cookie,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            self.buyma_action_frame,
            text="テスト",
            command=self._verify_buyma_cookie,
        ).pack(side="left", padx=4)
        if self.settings.buyma_cookies_path.exists():
            ctk.CTkButton(
                self.buyma_action_frame,
                text="初期化（Buyma cookie削除）",
                command=self._reset_buyma,
            ).pack(side="left", padx=4)
        ctk.CTkButton(self.buyma_action_frame, text="状態再読込", command=self._refresh_lock_ui).pack(side="left", padx=4)

    def _reset_ec_selected(self) -> None:
        site = self._selected_ec_site()
        if not messagebox.askyesno("初期化", f"EC [{site}] の cookie / session を削除しますか？"):
            return
        removed = clear_site_session(site, wipe_browser_profile=True)
        self._refresh_lock_ui()
        messagebox.showinfo("初期化", f"[{site}] を初期化しました（{len(removed)} ファイル）。")

    def _reset_ec(self) -> None:
        if not messagebox.askyesno("初期化", "すべての EC cookie / session を削除しますか？"):
            return
        removed = clear_all_sessions(wipe_browser_profile=True)
        self._refresh_lock_ui()
        messagebox.showinfo("初期化", f"EC session を初期化しました（{len(removed)} ファイル）。")

    def _reset_buyma(self) -> None:
        if not messagebox.askyesno("初期化", "Buyma cookie を削除しますか？\n新しいアカウントで再ログインできます。"):
            return
        from core.buyma.buyma_cookie_service import clear_buyma_session

        try:
            removed = clear_buyma_session(wipe_browser_profile=True)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("初期化失敗", str(exc))
            return
        self._refresh_lock_ui()
        messagebox.showinfo(
            "初期化",
            "Buyma cookie を削除しました。" if removed else "削除対象の cookie はありませんでした。",
        )

    def _save_chatgpt_channels(self) -> None:
        if not hasattr(self, "chatgpt_desc_url"):
            return
        updates = {
            "CHATGPT_DESCRIPTION_PROJECT_URL": self._entry_get(self.chatgpt_desc_url),
            "CHATGPT_IMAGE_PROJECT_URL": self._entry_get(self.chatgpt_image_url),
        }
        self._save_env_map(updates)
        messagebox.showinfo(
            "保存",
            "ChatGPT チャンネルURLを保存しました。\n次回の生成から反映されます。",
        )

    MSG_SAVE_OK = "登録に成功しました"
    MSG_TEST_OK = "登録成功"
    MSG_NEED_REGISTER = "ログイン情報を登録してください"

    def _launch_cookie_helper(
        self,
        cmd: list[str],
        *,
        watch_paths: list[Path],
        label: str,
        kind: str,
        intro: str | None = None,
    ) -> None:
        if self._cookie_busy:
            return
        self._save_env_map(self._account_env_updates())
        if kind == "save":
            if "Buyma" in label:
                email = self._entry_get(self.buyma_email)
                password = self._entry_get(self.buyma_pass)
            else:
                email = self._entry_get(self.ec_email)
                password = self._entry_get(self.ec_pass)
            if not email or not password:
                messagebox.showwarning(label, "このサイトのメールアドレスとパスワードを入力してください。")
                return
        if intro:
            messagebox.showinfo(label, intro)
        creationflags = 0
        if sys.platform == "win32":
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        busy_detail = f"{label}\n登録中です。完了するまで他の操作はできません。"
        if kind == "save":
            self._set_cookie_busy(True, busy_detail)
        try:
            proc = subprocess.Popen(cmd, cwd=str(ROOT), creationflags=creationflags)
        except Exception as exc:  # noqa: BLE001
            if kind == "save":
                self._set_cookie_busy(False)
            messagebox.showerror(label, f"起動に失敗しました:\n{exc}")
            return

        def _watch() -> None:
            before = {str(p): (p.exists(), p.stat().st_mtime if p.exists() else 0.0) for p in watch_paths}
            proc.wait()
            changed = False
            for p in watch_paths:
                key = str(p)
                exists = p.exists()
                mtime = p.stat().st_mtime if exists else 0.0
                prev_exists, prev_mtime = before.get(key, (False, 0.0))
                if exists and (not prev_exists or mtime > prev_mtime):
                    changed = True
                    break

            def _done() -> None:
                if kind == "save":
                    self._set_cookie_busy(False)
                self._refresh_lock_ui()
                if kind == "test":
                    if proc.returncode == 0:
                        messagebox.showinfo(label, self.MSG_TEST_OK)
                    else:
                        messagebox.showwarning(label, self.MSG_NEED_REGISTER)
                    return
                if proc.returncode == 0 and changed:
                    messagebox.showinfo(label, self.MSG_SAVE_OK)
                else:
                    messagebox.showwarning(label, self.MSG_NEED_REGISTER)

            self.after(0, _done)

        threading.Thread(target=_watch, daemon=True).start()

    def _save_ec_cookie(self) -> None:
        if self._cookie_busy:
            return
        site = self._selected_ec_site()
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--ec-cookie-login", site]
        else:
            cmd = [sys.executable, str(ROOT / "scripts" / "ec_cookie_login.py"), site]
        from core.sessions.ec_session_service import cookies_path, storage_state_path

        self._launch_cookie_helper(
            cmd,
            watch_paths=[storage_state_path(site), cookies_path(site)],
            label=f"EC cookie ({site})",
            kind="save",
        )

    def _verify_ec_cookie(self) -> None:
        site = self._selected_ec_site()
        if has_saved_session(site):
            messagebox.showinfo(f"EC cookie テスト ({site})", self.MSG_TEST_OK)
        else:
            messagebox.showwarning(f"EC cookie テスト ({site})", self.MSG_NEED_REGISTER)

    def _save_buyma_cookie(self) -> None:
        if self._cookie_busy:
            return
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--buyma-cookie-login"]
        else:
            cmd = [sys.executable, str(ROOT / "scripts" / "buyma_cookie_login.py")]
        self._launch_cookie_helper(
            cmd,
            watch_paths=[self.settings.buyma_cookies_path],
            label="Buyma cookie",
            kind="save",
        )

    def _verify_buyma_cookie(self) -> None:
        path = self.settings.buyma_cookies_path
        if path.exists() and path.stat().st_size > 10:
            messagebox.showinfo("Buyma cookie テスト", self.MSG_TEST_OK)
        else:
            messagebox.showwarning("Buyma cookie テスト", self.MSG_NEED_REGISTER)

    def _test_ec(self) -> None:
        self._save_ec_cookie()

    def _test_buyma(self) -> None:
        self._save_buyma_cookie()

    def _save_accounts(self) -> None:
        updates = self._account_env_updates()
        self._save_env_map(updates)
        messagebox.showinfo(
            "保存",
            "アカウント設定を保存しました。\n\n"
            "サイトを切り替えて、それぞれメール／パスワードを入力できます。\n"
            "次に各サイトで「ログイン情報の保存」を押すとクッキーを登録します。\n"
            "登録中は画面が操作できなくなります。保存後は横の「テスト」で確認できます。",
        )

    def _save_prompts(self) -> None:
        d = self.settings.secrets_dir / "prompts"
        d.mkdir(parents=True, exist_ok=True)
        (d / "buyma_image_prompt.txt").write_text(self.image_prompt_box.get("1.0", "end").strip() + "\n", encoding="utf-8")
        (d / "buyma_description_prompt.txt").write_text(
            self.desc_prompt_box.get("1.0", "end").strip() + "\n", encoding="utf-8"
        )
        messagebox.showinfo("保存", "プロンプトを保存しました。次回生成から反映されます。")


def main() -> None:
    app = LauncherApp()
    app.mainloop()


if __name__ == "__main__":
    main()
