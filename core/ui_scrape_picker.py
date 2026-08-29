"""Modal multi-select picker for scrape brands / categories (per EC site)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import customtkinter as ctk

from core.scrapers.scrape_targets import load_scrape_targets, save_scrape_targets
from core.scrapers.site_catalog import (
    CatalogItem,
    all_site_codes,
    brands_for,
    categories_for,
    site_label,
)
from core.ui_theme import ACCENT_TEAL, INK, INK_MUTED, SURFACE, SURFACE_RAISED, font

Kind = Literal["brands", "categories"]


class ScrapeTargetPickerModal(ctk.CTkToplevel):
    """Searchable multi-select modal; opens with persisted selections restored."""

    def __init__(
        self,
        master,
        *,
        kind: Kind,
        on_saved: Callable[[], None] | None = None,
        initial_site: str | None = None,
    ) -> None:
        super().__init__(master)
        self.kind = kind
        self.on_saved = on_saved
        title = "ブランド選択" if kind == "brands" else "カテゴリ選択"
        self.title(f"収集ターゲット — {title}")
        try:
            sw = int(self.winfo_screenwidth() or 1280)
            sh = int(self.winfo_screenheight() or 720)
        except Exception:  # noqa: BLE001
            sw, sh = 1280, 720
        try:
            scale = float(self._get_window_scaling())  # noqa: SLF001
        except Exception:  # noqa: BLE001
            scale = 1.0
        if scale <= 0.2:
            scale = 1.0
        w = min(760, max(560, int((sw - 48) / scale)))
        h = min(660, max(420, int((sh - 96) / scale)))
        self.geometry(f"{w}x{h}")
        self.minsize(min(560, w), min(400, h))
        self.configure(fg_color=SURFACE)
        try:
            from core.ui_icon import apply_window_icon

            apply_window_icon(self)
            self.after(250, lambda: apply_window_icon(self))
        except Exception:  # noqa: BLE001
            pass
        self.transient(master)
        self.grab_set()
        self.focus_force()

        # Always reload latest saved state when opening.
        self._targets = load_scrape_targets()
        self._selected_ids: dict[str, set[str]] = {code: set() for code in all_site_codes()}
        self._hydrate_selected_ids()
        self._dirty = False

        site0 = initial_site if initial_site in all_site_codes() else all_site_codes()[0]
        self._site = ctk.StringVar(value=site0)
        self._search = ctk.StringVar(value="")
        self._vars: dict[str, ctk.BooleanVar] = {}
        self._item_by_id: dict[str, CatalogItem] = {}
        self._suppress_search_trace = False

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(16, 4))
        ctk.CTkLabel(head, text=title, font=font(18, "bold"), text_color=INK).pack(side="left")
        ctk.CTkLabel(
            head,
            text="保存済みの選択を表示 · 追加/削除しても他サイトの設定は維持",
            text_color=INK_MUTED,
            font=font(12),
        ).pack(side="left", padx=(12, 0), pady=(4, 0))

        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.pack(fill="x", padx=16, pady=6)
        ctk.CTkLabel(tools, text="ECサイト").pack(side="left")
        self._site_display = {f"{c} — {site_label(c)}": c for c in all_site_codes()}
        self._site_menu = ctk.CTkOptionMenu(
            tools,
            values=list(self._site_display.keys()),
            width=300,
            command=self._on_site_menu,
        )
        self._site_menu.set(f"{self._site.get()} — {site_label(self._site.get())}")
        self._site_menu.pack(side="left", padx=8)
        ctk.CTkLabel(tools, text="検索").pack(side="left", padx=(16, 0))
        self._search_entry = ctk.CTkEntry(tools, width=200, textvariable=self._search, placeholder_text="キーワード")
        self._search_entry.pack(side="left", padx=6)
        self._search.trace_add("write", self._on_search_changed)
        ctk.CTkButton(tools, text="全選択", width=80, command=self._select_all_visible).pack(side="left", padx=(12, 4))
        ctk.CTkButton(tools, text="表示分のみ解除", width=120, command=self._clear_visible).pack(side="left", padx=4)

        self._count_label = ctk.CTkLabel(self, text="", text_color=INK_MUTED, font=font(12))
        self._count_label.pack(anchor="w", padx=18, pady=(0, 2))

        self._chips = ctk.CTkFrame(self, fg_color=SURFACE_RAISED, corner_radius=8)
        self._chips.pack(fill="x", padx=16, pady=(0, 6))

        self._list_host = ctk.CTkScrollableFrame(self, fg_color=SURFACE_RAISED)
        self._list_host.pack(fill="both", expand=True, padx=16, pady=6)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=16, pady=(8, 16))
        ctk.CTkButton(foot, text="キャンセル", width=120, command=self._on_cancel).pack(side="right", padx=6)
        ctk.CTkButton(foot, text="このサイトを保存", width=160, command=self._save_current_site).pack(side="right", padx=6)
        ctk.CTkButton(foot, text="すべて保存して閉じる", width=180, command=self._save_all_and_close).pack(
            side="right", padx=6
        )

        self._rebuild_list(sync_first=False)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _catalog(self, site: str | None = None) -> tuple[CatalogItem, ...]:
        code = site or self._site.get()
        return brands_for(code) if self.kind == "brands" else categories_for(code)

    def _hydrate_selected_ids(self) -> None:
        """Map saved keyword/label strings back onto catalog item ids."""
        for code in all_site_codes():
            saved = {(x or "").strip().lower() for x in (self._targets.get(code, {}).get(self.kind) or [])}
            ids: set[str] = set()
            for item in self._catalog(code):
                if (
                    item.id.lower() in saved
                    or item.keywords.lower() in saved
                    or item.label.lower() in saved
                ):
                    ids.add(item.id)
            self._selected_ids[code] = ids

    def _on_search_changed(self, *_args) -> None:
        if self._suppress_search_trace:
            return
        self._rebuild_list(sync_first=True)

    def _on_site_menu(self, display: str) -> None:
        self._sync_visible_checkboxes()
        code = self._site_display.get(display) or all_site_codes()[0]
        self._site.set(code)
        self._suppress_search_trace = True
        try:
            self._search.set("")
        finally:
            self._suppress_search_trace = False
        self._rebuild_list(sync_first=False)

    def _sync_visible_checkboxes(self) -> None:
        """Update in-memory selection for current site from visible checkboxes only.

        Hidden (filtered-out) items keep their previous selection — search must not wipe them.
        """
        code = self._site.get()
        selected = self._selected_ids.setdefault(code, set())
        for item_id, var in self._vars.items():
            if var.get():
                selected.add(item_id)
            else:
                selected.discard(item_id)

    def _toggle(self, item_id: str) -> None:
        code = self._site.get()
        selected = self._selected_ids.setdefault(code, set())
        var = self._vars.get(item_id)
        if not var:
            return
        if var.get():
            selected.add(item_id)
        else:
            selected.discard(item_id)
        self._dirty = True
        self._update_count()
        self._refresh_chips()

    def _rebuild_list(self, *, sync_first: bool = True) -> None:
        if sync_first:
            self._sync_visible_checkboxes()
        for child in self._list_host.winfo_children():
            child.destroy()
        self._vars.clear()
        self._item_by_id.clear()

        q = (self._search.get() or "").strip().lower()
        selected = self._selected_ids.get(self._site.get(), set())
        shown = 0
        for item in self._catalog():
            hay = f"{item.label} {item.keywords} {item.id}".lower()
            if q and q not in hay:
                continue
            self._item_by_id[item.id] = item
            var = ctk.BooleanVar(value=item.id in selected)
            self._vars[item.id] = var
            row = ctk.CTkFrame(self._list_host, fg_color="transparent")
            row.pack(fill="x", padx=6, pady=2)
            ctk.CTkCheckBox(
                row,
                text=item.label,
                variable=var,
                command=lambda i=item.id: self._toggle(i),
            ).pack(side="left", anchor="w")
            if self.kind == "categories" and item.keywords:
                ctk.CTkLabel(row, text=item.keywords, text_color=INK_MUTED, font=font(11)).pack(
                    side="left", padx=(10, 0)
                )
            shown += 1
        if shown == 0:
            ctk.CTkLabel(self._list_host, text="検索結果なし", text_color=INK_MUTED).pack(pady=20)
        self._update_count()
        self._refresh_chips()

    def _selected_labels(self, code: str) -> list[str]:
        ids = self._selected_ids.get(code, set())
        out: list[str] = []
        for item in self._catalog(code):
            if item.id in ids:
                out.append(item.label if self.kind == "brands" else item.label)
        return out

    def _refresh_chips(self) -> None:
        for child in self._chips.winfo_children():
            child.destroy()
        code = self._site.get()
        labels = self._selected_labels(code)
        ctk.CTkLabel(
            self._chips,
            text="現在の選択:",
            text_color=INK_MUTED,
            font=font(11),
        ).pack(side="left", padx=(10, 6), pady=8)
        if not labels:
            ctk.CTkLabel(self._chips, text="（なし）", text_color=INK_MUTED, font=font(11)).pack(
                side="left", padx=4, pady=8
            )
            return
        # Wrap chips in nested frame with wrap via pack left
        wrap = ctk.CTkFrame(self._chips, fg_color="transparent")
        wrap.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=6)
        for lab in labels:
            chip = ctk.CTkLabel(
                wrap,
                text=f"  {lab}  ",
                fg_color=ACCENT_TEAL,
                corner_radius=6,
                text_color=INK,
                font=font(11),
            )
            chip.pack(side="left", padx=3, pady=2)

    def _update_count(self) -> None:
        code = self._site.get()
        n = len(self._selected_ids.get(code, set()))
        kind_ja = "ブランド" if self.kind == "brands" else "カテゴリ"
        mark = " · 未保存の変更あり" if self._dirty else " · 保存済み状態"
        self._count_label.configure(text=f"{site_label(code)} の選択中{kind_ja}: {n} 件{mark}")

    def _select_all_visible(self) -> None:
        code = self._site.get()
        selected = self._selected_ids.setdefault(code, set())
        for item_id, var in self._vars.items():
            var.set(True)
            selected.add(item_id)
        self._dirty = True
        self._update_count()
        self._refresh_chips()

    def _clear_visible(self) -> None:
        """Clear only currently visible (filtered) items; keep others for this site."""
        code = self._site.get()
        selected = self._selected_ids.setdefault(code, set())
        for item_id, var in self._vars.items():
            var.set(False)
            selected.discard(item_id)
        self._dirty = True
        self._update_count()
        self._refresh_chips()

    def _apply_ids_to_targets(self) -> None:
        self._sync_visible_checkboxes()
        for code in all_site_codes():
            ids = self._selected_ids.get(code, set())
            values: list[str] = []
            for item in self._catalog(code):
                if item.id in ids:
                    values.append(item.label if self.kind == "brands" else item.keywords)
            self._targets.setdefault(code, {"brands": [], "categories": []})
            other = "categories" if self.kind == "brands" else "brands"
            existing_other = list(self._targets[code].get(other) or [])
            self._targets[code] = {self.kind: values, other: existing_other}

    def _save_current_site(self) -> None:
        self._apply_ids_to_targets()
        full = load_scrape_targets()
        code = self._site.get()
        full.setdefault(code, {"brands": [], "categories": []})
        full[code][self.kind] = list(self._targets[code][self.kind])
        # Preserve other sites / other kind untouched
        save_scrape_targets(full)
        self._targets = full
        self._dirty = False
        if self.on_saved:
            self.on_saved()
        self._update_count()
        self._count_label.configure(text=f"保存しました: {site_label(code)}")

    def _save_all_and_close(self) -> None:
        self._apply_ids_to_targets()
        full = load_scrape_targets()
        for code in all_site_codes():
            full.setdefault(code, {"brands": [], "categories": []})
            full[code][self.kind] = list(self._targets.get(code, {}).get(self.kind) or [])
        save_scrape_targets(full)
        self._dirty = False
        if self.on_saved:
            self.on_saved()
        self.destroy()

    def _on_cancel(self) -> None:
        if self._dirty:
            from tkinter import messagebox

            if not messagebox.askyesno("確認", "未保存の変更があります。破棄して閉じますか？", parent=self):
                return
        self.destroy()
