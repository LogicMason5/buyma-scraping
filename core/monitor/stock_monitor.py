"""Periodic stock URL monitor: member-session check; 404 / OOS → delist candidates."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.csv_schema import ensure_row, read_products_csv, write_products_csv
from core.paths import runtime_root

LogFn = Callable[[str], None]
StopFn = Callable[[], bool]

_404_HINTS = (
    "404",
    "not found",
    "page not available",
    "no longer available",
    "articolo non disponibile",
    "product not found",
    "page not found",
    "このページは見つかりません",
    "ページが見つかりません",
)

_OOS_HINTS = (
    "out of stock",
    "sold out",
    "unavailable",
    "non disponibile",
    "esaurito",
    "esauri",
    "notify me",
    "notify when available",
    "在庫なし",
    "売り切れ",
    "完売",
)


@dataclass
class MonitorHit:
    index: int
    name: str
    brand: str
    source_url: str
    buyma_url: str
    folder: str
    status_code: int | None
    reason: str
    inventory: int = 1
    sell_price: str = ""
    checked_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass
class MonitorResult:
    hits: list[MonitorHit]
    recovered: list[MonitorHit] = field(default_factory=list)


def monitor_state_path() -> Path:
    path = runtime_root() / "workspace" / "buyma" / "stock_monitor_hits.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_monitor_hits() -> list[dict[str, Any]]:
    path = monitor_state_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def save_monitor_hits(hits: list[MonitorHit | dict[str, Any]]) -> Path:
    path = monitor_state_path()
    payload = [asdict(h) if isinstance(h, MonitorHit) else h for h in hits]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def paused_listings_path() -> Path:
    path = runtime_root() / "workspace" / "buyma" / "buyma_paused_listings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_paused_listings() -> list[dict[str, Any]]:
    path = paused_listings_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def save_paused_listings(items: list[dict[str, Any]]) -> Path:
    path = paused_listings_path()
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def detect_site_code(url: str, row_site: str | None = None) -> str | None:
    code = (row_site or "").strip()
    if code in {
        "julian-fashion",
        "montiboutique",
        "minettiangeloonline",
        "eleonorabonucci",
    }:
        return code
    host = (urlparse(url).netloc or "").lower()
    if "julian-fashion" in host:
        return "julian-fashion"
    if "montiboutique" in host:
        return "montiboutique"
    if "angelominetti" in host or "minettiangelo" in host:
        return "minettiangeloonline"
    if "eleonorabonucci" in host:
        return "eleonorabonucci"
    return None


def _page_status(page) -> int | None:
    try:
        return int(page.evaluate("() => performance.getEntriesByType('navigation')[0]?.responseStatus || 0") or 0) or None
    except Exception:  # noqa: BLE001
        return None


def evaluate_product_availability(page, *, http_status: int | None) -> tuple[int, bool, str]:
    """Return (reported_status, is_gone, reason).

    - 200: page opens and product looks purchasable (stock > 0)
    - 404: page missing / unavailable / out of stock
    """
    from core.scrapers.playwright_base import count_available_inventory

    body = ""
    try:
        body = (page.inner_text("body") or "").lower()
    except Exception:  # noqa: BLE001
        body = ""

    if http_status == 404 or http_status in {410, 451}:
        return 404, True, f"HTTP {http_status}"

    for hint in _404_HINTS:
        if hint in body:
            return 404, True, f"missing:{hint}"

    # Soft 404: empty / error shells without product chrome.
    title = ""
    try:
        title = (page.title() or "").lower()
    except Exception:  # noqa: BLE001
        title = ""
    if any(x in title for x in ("404", "not found", "page not available")):
        return 404, True, "title:not_found"

    stock = 0
    try:
        stock = int(count_available_inventory(page, fallback=0) or 0)
    except Exception:  # noqa: BLE001
        stock = 0

    if stock <= 0:
        for hint in _OOS_HINTS:
            if hint in body:
                return 404, True, f"oos:{hint}"
        # No sizes / qty and no clear add-to-cart → treat as unavailable.
        has_cart = False
        try:
            has_cart = bool(
                page.evaluate(
                    """() => {
                      const t = (document.body && document.body.innerText || '').toLowerCase();
                      if (/add to (bag|cart)|aggiungi|カートに入れる|バッグに追加/.test(t)) return true;
                      const btn = document.querySelector(
                        'button[name*=\"add\" i], button.add-to-cart, .add-to-cart, #product-addtocart-button, button.js-add-to-cart'
                      );
                      return !!(btn && !btn.disabled);
                    }"""
                )
            )
        except Exception:  # noqa: BLE001
            has_cart = False
        if not has_cart:
            return 404, True, "oos:no_stock_or_cart"
        # Cart/buy button exists — do not pause on inventory helper alone (false OOS).
        return 200, False, "ok:cart_present"

    # Purchasable.
    return 200, False, "ok"


def check_product_with_session(scraper, url: str) -> tuple[int, bool, str]:
    """Open URL with logged-in scraper session. Returns (status, is_gone, reason)."""
    assert scraper.page is not None
    page = scraper.page
    http_status: int | None = None
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if resp is not None:
            http_status = resp.status
        page.wait_for_timeout(2200)
        if hasattr(scraper, "_dismiss_popups"):
            try:
                scraper._dismiss_popups(page)
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        # Transient network/nav failures must NOT trigger Buyma pause.
        return 0, False, f"nav_error:{exc}"

    if http_status is None:
        http_status = _page_status(page)
    return evaluate_product_availability(page, http_status=http_status)


def load_monitor_rows(source_path: Path) -> tuple[list[dict[str, str]], str]:
    """Load product rows from workbook (.xlsx) or CSV. Returns (rows, kind)."""
    path = Path(source_path)
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        from core.workbook.products_workbook import read_all_workbook_rows

        rows = read_all_workbook_rows(path)
        return rows, "workbook"
    rows = read_products_csv(path)
    return rows, "csv"


def persist_monitor_rows(
    source_path: Path,
    rows: list[dict[str, str]],
    *,
    kind: str,
    changed_indices: set[int] | None = None,
) -> None:
    """Persist monitor results. Workbook: patch only changed rows/fields (no full wipe)."""
    path = Path(source_path)
    if kind == "workbook" or path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
        from core.workbook.products_workbook import patch_workbook_fields, update_workbook_rows

        indices = changed_indices if changed_indices is not None else set(range(len(rows)))
        status_keys = (
            "在庫監視",
            "Buyma公開",
            "在庫",
            "出品価格",
            "価格",
            "出品価格_再掲",
            "品代(海外仕入)",
            "送料(仕入)",
            "梱包",
            "送料(出品)",
            "関税(10%)",
            "消費税(10%)",
            "仕入合計",
            "BUYMA手数料",
            "利益%",
            "利益",
            "価格計算式",
            "通貨",
            "商品名",
            "ブランド",
            "色",
            "サイズ",
            "商品コメント",
            "色・サイズ補足情報",
        )
        patches: list[tuple[str, dict[str, str]]] = []
        rescrape_rows: list[dict[str, str]] = []
        for idx in sorted(indices):
            if idx < 0 or idx >= len(rows):
                continue
            row = rows[idx]
            folder = (row.get("フォルダ名") or "").strip()
            mon = str(row.get("在庫監視") or "")
            if mon.startswith("200:rescraped"):
                rescrape_rows.append(row)
                continue
            if not folder:
                continue
            fields = {k: row.get(k, "") for k in status_keys if row.get(k) not in (None, "")}
            if fields:
                patches.append((folder, fields))
        if patches:
            patch_workbook_fields(path, patches)
        if rescrape_rows:
            # Full row merge for rescraped products (prices/stock/copy may change).
            update_workbook_rows(path, rescrape_rows)
        # Compact snapshot of changed rows only (faster / smaller).
        if indices:
            snap = path.with_name(path.stem + "_monitor_snapshot.csv")
            write_products_csv(snap, [rows[i] for i in sorted(indices) if 0 <= i < len(rows)])
    else:
        write_products_csv(path, rows)


def run_stock_monitor(
    *,
    csv_path: Path | None = None,
    source_path: Path | None = None,
    log: LogFn | None = None,
    should_stop: StopFn | None = None,
    refresh_on_recovery: bool = True,
) -> MonitorResult:
    """Scan all products (default: products_workbook.xlsx) with EC member session.

    On recovery: re-scrape PDP, update workbook/CSV, then caller resumes Buyma with new stock.
    """
    from core.monitor.product_refresh import rescrape_product_with_scraper
    from core.scrapers.sites import SCRAPER_REGISTRY

    _log = log or (lambda _m: None)
    _stop = should_stop or (lambda: False)
    path = Path(source_path or csv_path or "")
    if not path.exists():
        raise FileNotFoundError(f"監視データがありません: {path}")

    rows, kind = load_monitor_rows(path)
    hits: list[MonitorHit] = []
    _log(f"在庫監視(会員セッション): {path} [{kind}] ({len(rows)} 行)")

    by_site: dict[str, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    skipped = 0
    for idx, row in enumerate(rows):
        url = (row.get("仕入先URL") or "").strip()
        if not url:
            skipped += 1
            continue
        site = detect_site_code(url, row.get("サイトコード"))
        if not site:
            _log(f"  スキップ [{idx + 1}] 未知サイト: {url}")
            skipped += 1
            continue
        by_site[site].append((idx, row))

    open_scrapers: dict[str, object] = {}
    recovered: list[MonitorHit] = []
    changed: set[int] = set()
    try:
        for site, items in by_site.items():
            if _stop():
                break
            scraper_cls = SCRAPER_REGISTRY.get(site)
            if not scraper_cls:
                _log(f"  未知サイトコード: {site}")
                continue
            _log(f"[{site}] {len(items)} 件を会員セッションで確認")
            scraper = scraper_cls()
            open_scrapers[site] = scraper
            try:
                scraper.ensure_browser()
                if getattr(scraper, "require_member_login", True):
                    scraper.ensure_member_login()
                else:
                    try:
                        scraper.login()
                    except Exception as exc:  # noqa: BLE001
                        _log(f"  ログイン警告: {exc}")
                for idx, row in items:
                    if _stop():
                        break
                    url = (row.get("仕入先URL") or "").strip()
                    name = (row.get("商品名") or "")[:80]
                    _status, gone, reason = check_product_with_session(scraper, url)
                    if reason.startswith("nav_error:"):
                        _log(f"  通信エラー(保留) [{idx + 1}] {name} → {reason}")
                        continue
                    if gone:
                        hits.append(
                            MonitorHit(
                                index=idx,
                                name=name,
                                brand=row.get("ブランド") or "",
                                source_url=url,
                                buyma_url=row.get("出品URL") or "",
                                folder=row.get("フォルダ名") or "",
                                status_code=404,
                                reason=reason,
                                inventory=0,
                            )
                        )
                        row["在庫監視"] = f"404:{reason}"
                        row["在庫"] = "0"
                        row["Buyma公開"] = "一時停止"
                        rows[idx] = ensure_row(row)
                        changed.add(idx)
                        _log(f"  404検知 [{idx + 1}] {name} → {reason}")
                    else:
                        prev_mon = str(row.get("在庫監視") or "")
                        row["在庫監視"] = f"200:{reason}"
                        rows[idx] = ensure_row(row)
                        if not prev_mon.startswith("200:") or prev_mon != row["在庫監視"]:
                            changed.add(idx)
                        _log(f"  OK [{idx + 1}] HTTP 200 ({reason})")
            except Exception as exc:  # noqa: BLE001
                _log(f"  サイト失敗 {site}: {exc}")

        paused = load_paused_listings()
        by_url = {
            str(p.get("source_url") or "").strip(): p
            for p in paused
            if str(p.get("source_url") or "").strip()
        }

        for idx, row in enumerate(rows):
            if _stop():
                break
            url = (row.get("仕入先URL") or "").strip()
            if not url:
                continue
            mon = str(row.get("在庫監視") or "")
            if mon.startswith("404:"):
                by_url[url] = {
                    "source_url": url,
                    "name": (row.get("商品名") or "")[:120],
                    "brand": row.get("ブランド") or "",
                    "buyma_url": row.get("出品URL") or "",
                    "folder": row.get("フォルダ名") or "",
                    "reason": mon[4:],
                    "paused_at": datetime.now().isoformat(timespec="seconds"),
                    "index": idx,
                    "state": "paused",
                }
                row["Buyma公開"] = "一時停止"
                rows[idx] = ensure_row(row)
                changed.add(idx)
            elif mon.startswith("200:") and url in by_url:
                site = detect_site_code(url, row.get("サイトコード"))
                refreshed = None
                if refresh_on_recovery and site and site in open_scrapers:
                    _log(f"  復活→再取得 [{idx + 1}] {url}")
                    try:
                        refreshed = rescrape_product_with_scraper(
                            open_scrapers[site], url, existing=row
                        )
                    except Exception as exc:  # noqa: BLE001
                        _log(f"  再取得失敗: {exc}")
                if refreshed:
                    rows[idx] = refreshed
                    row = refreshed
                    _log(f"  再取得完了 在庫={row.get('在庫')} 価格={row.get('出品価格')}")
                try:
                    inv = int(str(row.get("在庫") or "0").replace(",", "") or "0")
                except ValueError:
                    inv = 0
                prev = by_url[url]
                if inv <= 0:
                    try:
                        inv = max(1, int(prev.get("inventory") or 1))
                    except (TypeError, ValueError):
                        inv = 1
                    if refreshed:
                        inv = max(1, inv)
                sell_price = str(row.get("出品価格") or row.get("価格") or "").strip()
                by_url[url] = {
                    **prev,
                    "state": "recoverable",
                    "index": idx,
                    "inventory": inv,
                    "sell_price": sell_price,
                }
                recovered.append(
                    MonitorHit(
                        index=idx,
                        name=(row.get("商品名") or prev.get("name") or "")[:80],
                        brand=row.get("ブランド") or prev.get("brand") or "",
                        source_url=url,
                        buyma_url=row.get("出品URL") or prev.get("buyma_url") or "",
                        folder=row.get("フォルダ名") or prev.get("folder") or "",
                        status_code=200,
                        reason="recovered",
                        inventory=inv,
                        sell_price=sell_price,
                    )
                )
                row["Buyma公開"] = "再公開候補"
                rows[idx] = ensure_row(row)
                changed.add(idx)
                _log(f"  復活検知 [{idx + 1}] {(row.get('商品名') or '')[:60]} 在庫={inv}")

        save_paused_listings(list(by_url.values()))
        persist_monitor_rows(path, rows, kind=kind, changed_indices=changed)

        if hits:
            save_monitor_hits(hits)
            gone_csv = path.with_name(path.stem + "_404.csv")
            write_products_csv(
                gone_csv,
                [ensure_row(rows[h.index]) for h in hits if 0 <= h.index < len(rows)],
            )
            _log(f"404一覧CSV: {gone_csv}")
        else:
            save_monitor_hits([])
            _log("404 なし" + (f"（スキップ {skipped}）" if skipped else ""))
        if recovered:
            _log(f"在庫復活（再取得・再公開候補）: {len(recovered)} 件")
        return MonitorResult(hits=hits, recovered=recovered)
    finally:
        for scraper in open_scrapers.values():
            try:
                scraper.close_browser()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass



def _buyma_toggle_listings(
    items: list[MonitorHit],
    *,
    action: str,
    log: LogFn | None = None,
    should_stop: StopFn | None = None,
) -> int:
    """Pause or resume Buyma listings (never hard-delete).

    action='pause'  → 出品停止 / 公開停止
    action='resume' → open edit → update stock qty → 出品再開 / 再公開
    """
    _log = log or (lambda _m: None)
    _stop = should_stop or (lambda: False)
    if not items:
        _log("対象なし")
        return 0

    if action == "pause":
        labels = ("出品停止", "公開停止", "販売停止", "停止")
        conf_labels = ("OK", "はい", "実行", "停止する")
        verb = "公開一時停止"
    else:
        labels = ("出品再開", "再公開", "公開する", "販売再開", "再開")
        conf_labels = ("OK", "はい", "実行", "再開する", "公開する", "保存", "更新")
        verb = "再公開"

    from core.buyma.buyma_browser_service import BuymaBrowserSession

    done = 0
    session = BuymaBrowserSession()
    session.start()
    try:
        if not session.ensure_logged_in(timeout_seconds=60):
            raise RuntimeError("Buyma にログインできません")
        assert session.page is not None
        page = session.page
        page.goto("https://www.buyma.com/my/sell/", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        for hit in items:
            if _stop():
                break
            keyword = (hit.name or hit.folder or "").replace("関税無＆送料無料", "").strip()[:40]
            if not keyword:
                continue
            qty = max(1, int(hit.inventory or 1))
            _log(f"{verb}を試行: {keyword}" + (f" 在庫={qty}" if action == "resume" else ""))
            try:
                for sel in (
                    "input[name*='keyword']",
                    "input[placeholder*='検索']",
                    "input[type='search']",
                    "input[name='q']",
                ):
                    loc = page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=800):
                        loc.fill(keyword)
                        loc.press("Enter")
                        page.wait_for_timeout(2000)
                        break

                if action == "resume":
                    # Prefer opening edit form to update stock before resume.
                    edited = False
                    for edit_label in ("編集", "変更", "詳細を見る", "商品を編集"):
                        for group in (
                            page.get_by_role("link", name=edit_label),
                            page.get_by_role("button", name=edit_label),
                        ):
                            try:
                                if group.count() > 0:
                                    group.first.click(timeout=3000)
                                    page.wait_for_timeout(2000)
                                    edited = True
                                    break
                            except Exception:  # noqa: BLE001
                                continue
                        if edited:
                            break
                    if edited:
                        try:
                            if session._fill_stock_quantity(str(qty)):  # noqa: SLF001
                                _log(f"  在庫を {qty} に更新")
                            else:
                                _log("  在庫入力欄が見つかりません（再開のみ続行）")
                        except Exception as exc:  # noqa: BLE001
                            _log(f"  在庫更新失敗: {exc}")
                        price = str(getattr(hit, "sell_price", "") or "").strip()
                        if price:
                            try:
                                session._scroll_to_summary("商品価格")  # noqa: SLF001
                                if session._fill_by_selectors("price", price, paste=False):  # noqa: SLF001
                                    _log(f"  出品価格を {price} に更新")
                            except Exception as exc:  # noqa: BLE001
                                _log(f"  価格更新スキップ: {exc}")

                clicked = False
                for label in labels:
                    btns = page.get_by_role("button", name=label)
                    links = page.get_by_role("link", name=label)
                    for group in (btns, links):
                        try:
                            if group.count() > 0:
                                group.first.click(timeout=3000)
                                page.wait_for_timeout(1000)
                                for conf in conf_labels:
                                    c = page.get_by_role("button", name=conf)
                                    if c.count() > 0:
                                        c.first.click(timeout=2000)
                                        break
                                clicked = True
                                break
                        except Exception:  # noqa: BLE001
                            continue
                    if clicked:
                        break
                # After edit, also try save buttons if resume button missing.
                if action == "resume" and not clicked:
                    for label in ("保存する", "変更を保存", "更新する", "公開する"):
                        try:
                            btn = page.get_by_role("button", name=label)
                            if btn.count() > 0:
                                btn.first.click(timeout=3000)
                                clicked = True
                                break
                        except Exception:  # noqa: BLE001
                            continue
                if clicked:
                    done += 1
                    _log(f"  {verb}完了: {keyword}")
                else:
                    _log(f"  {verb}ボタンが見つかりません: {keyword}（手動確認が必要）")
            except Exception as exc:  # noqa: BLE001
                _log(f"  {verb}失敗: {exc}")
            page.goto("https://www.buyma.com/my/sell/", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
    finally:
        session.close()
    _log(f"{verb} 完了: {done}/{len(items)}")
    return done


def stop_buyma_listings_for_hits(
    hits: list[MonitorHit],
    *,
    log: LogFn | None = None,
    should_stop: StopFn | None = None,
) -> int:
    """Temporarily pause Buyma publication (not delete)."""
    return _buyma_toggle_listings(hits, action="pause", log=log, should_stop=should_stop)


def resume_buyma_listings_for_hits(
    hits: list[MonitorHit],
    *,
    log: LogFn | None = None,
    should_stop: StopFn | None = None,
) -> int:
    """Re-publish previously paused Buyma listings."""
    done = _buyma_toggle_listings(hits, action="resume", log=log, should_stop=should_stop)
    if done > 0:
        done_urls = {h.source_url for h in hits if h.source_url}
        paused = load_paused_listings()
        remaining = [
            p
            for p in paused
            if not (
                str(p.get("source_url") or "") in done_urls
                and str(p.get("state") or "") == "recoverable"
            )
        ]
        save_paused_listings(remaining)
    return done
