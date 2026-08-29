# EC-Buyma — 3 Independent Engines

Three **separate** Python desktop apps (CustomTkinter). Each engine does one job and runs in its own process — they do not call each other.

**Recommended:** open the launcher, then pick a stage:

```powershell
py -3 run.py
```

| Stage | Command (direct) | Role |
|-------|------------------|------|
| 第1段階 | `py -3 run_engine1.py` | EC sites → `workspace/scrape/.../products.csv` |
| 第2段階 | `py -3 run_engine2.py` | CSV → model image `0.png` into Engine1 folder + update `商品コメント` → `workspace/generate/products_ready.csv` |
| 第3段階 | `py -3 run_engine3.py` | CSV → Buyma listing |

You can run Engine 1 while Engine 2 or 3 runs in another window. The launcher stays open so you can start another stage anytime.

## CSV-first data flow

Product text lives in the **final CSV schema** (`core/csv_schema.py` → `ALL_HEADERS`), not in description TXT files.

1. Empty template: [`templates/products_template.csv`](templates/products_template.csv)
2. Create a new empty CSV anytime:

```powershell
py -3 scripts/create_products_csv.py workspace/generate/products_empty.csv
```

3. Engine1 appends one row per scraped product (`商品コメント` left empty; scrape text kept under `出品メモ` → `===== ソース説明 =====`).
4. Engine2 writes AI/template text into CSV columns `商品コメント` and `色・サイズ補足情報`.
5. Engine3 reads those CSV columns for Buyma listing and writes `出品結果` / `出品URL`.

Per-product folder keeps images + `buyma_listing.csv` only (no description TXT sidecars).

### Batch workbook (local multi-sheet Excel)

One file; each **day** uses one sheet named ``YYYY-MM-DD (件数)``:

```text
workspace/generate/products_workbook.xlsx
  ├─ 2026-08-11 (250)   ← Engine1 作成/追記、Engine2 が同じシートを更新
  ├─ 2026-08-12 (180)   ← 翌日のバッチ
  └─ …
```

- Engine1: 本日シートを作成、または既存本日シートへ行を追加/更新
- Engine2: **新しいシートは作らない**。`フォルダ名` で既存行を更新（商品コメント等）
- Path: `PRODUCTS_WORKBOOK_PATH`（default `./workspace/generate/products_workbook.xlsx`）
- Engine2 は `workspace/generate/batches/<run_id>_products_ready.csv` も出力
- 最新スナップショット: `workspace/generate/products_ready.csv`

## Google Sheets sync (optional)

Local CSV remains the source of truth. When enabled, engines upsert/push the same columns to a shared Google Sheet.

1. Create a Google Cloud service account, enable **Google Sheets API** (+ Drive API), download JSON key to:

```text
secrets/google_service_account.json
```

2. Create a Google Spreadsheet, share it with the service account email (**Editor**).
3. Put the spreadsheet ID and enable sync in `.env`:

```env
GOOGLE_SHEETS_ENABLED=true
GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id
GOOGLE_SHEETS_WORKSHEET=products
GOOGLE_SERVICE_ACCOUNT_JSON=./secrets/google_service_account.json
```

4. Manual sync:

```powershell
py -3 -m pip install gspread google-auth
py -3 scripts/sync_google_sheets.py push workspace/generate/products_ready.csv
py -3 scripts/sync_google_sheets.py pull workspace/generate/products_ready.csv
```

Row key for upsert: `フォルダ名` (fallback `外部商品ID`).

## Setup

```powershell
cd C:\development\EC-Buyma
py -3 -m pip install -r requirements.txt
py -3 -m playwright install chrome
```

Copy `.env.example` → `.env` if needed. Secrets stay under `secrets/`:

- `secrets/chatgpt_cookies.json`
- `secrets/ec_sessions/<site>/storage_state.json`
- `secrets/buyma_cookies.json`
- `secrets/google_service_account.json` (optional Sheets)

### Manual login once (then fully automated)

Passwords are **never** auto-filled. Log in yourself, press Enter to save cookies, then engines reuse them.

```powershell
# EC sites (Engine1) — one site at a time
py -3 scripts/ec_cookie_login.py julian-fashion
py -3 scripts/ec_cookie_login.py montiboutique
py -3 scripts/ec_cookie_login.py minettiangeloonline
py -3 scripts/ec_cookie_login.py eleonorabonucci

# ChatGPT (Engine2)
py -3 scripts/chatgpt_cookie_login.py

# Buyma (Engine3)
py -3 scripts/buyma_cookie_login.py
```

Chrome profiles (unchanged):

- `C:/chrome-profiles/chatgpt-worker`
- `C:/chrome-profiles/buyma-worker`
- `C:/chrome-profiles/ec-*` (per EC site)

## Typical flow

1. `py -3 run.py` → **第1段階** → pick sites & count → get `products.csv`
2. Same launcher → **第2段階** → select that CSV → model image into scrape folders + `products_ready.csv`
3. Same launcher → **第3段階** → select **ready CSV** → list on Buyma (uncheck submit for dry fill)

## Layout

```
apps/           CustomTkinter UIs + workers
core/           scrapers, chatgpt, buyma, sheets, sessions, csv_schema
templates/      empty products_template.csv (final headers)
secrets/        cookies & EC sessions (do not delete)
assets/         provided / brand images
workspace/      scrape | generate | buyma outputs
scripts/        cookie login + CSV/Sheets helpers
```

No FastAPI, React, PostgreSQL, or Redis.
