# MMK rate + stock percentile app

A small Flask app in Burmese with two pages:

* `/` — Myanmar MMK exchange rates: SGD, USD, GBP and four gold grades, with a
  per-day chart, a comparison against the yearly average, and a "target price"
  checker that asks how often a price was reached in the past year.
* `/stocks` — 10 US mega-caps and 5 indices (S&P 500, Dow, Nasdaq, Nikkei, Hang
  Seng) with a 1-year rolling price percentile and a plain "is this price low or
  high for the year?" verdict.

Live: <https://mmk-rate-stock-app.vercel.app>

## Run locally

```bash
pip install -r requirements.txt
python app.py          # http://127.0.0.1:5000
```

Python 3.10+. No build step.

## Data sources

| what | source | key |
|---|---|---|
| gold + currency reference rates | `data-provider.getmmp.link` | `MMP_API_KEY` env var |
| currency fallback | OKX USDT/MMK P2P books + `open.er-api.com` FX | none |
| stock / index closes | Yahoo Finance chart API, 2 years daily | none |
| charting | Chart.js from jsDelivr | none |

## Configuration

| env var | default | meaning |
|---|---|---|
| `MMP_API_KEY` | empty | API key for the MMP rate provider. Without it, currencies fall back to OKX and gold has no source. |
| `DATA_DIR` | code dir, or `/tmp` when `VERCEL` is set | where `rates_assets.csv` and `stock_cache.json` live |

No secret is stored in this repository.

## The stock calculation

Single signal: the 1-year price percentile. `WIN = 252` trading days.

```text
window  = close[max(0, i - 251) .. i]
pct[i]  = count(v in window where v <= close[i]) / len(window) * 100
```

Point-in-time, no look-ahead. Forward 7/30/90-day returns for historical days
whose percentile is within `-8 / +7` of today's are shown for context only; they
do not change the verdict. Thresholds: `>=80` high, `60-80` watch, `40-60`
normal, `20-40` lower half, `<20` low.

## Deploy (Vercel)

`vercel.json` builds `app.py` with `@vercel/python` and routes everything to it.

```bash
vercel deploy --prod
```

Set `MMP_API_KEY` in the project's environment variables first.

## Storage caveat on Vercel

Serverless functions get a read-only code directory and a temporary `/tmp`, so
`DATA_DIR=/tmp`. Anything the app writes — fetched rates, manual entries, the
Yahoo cache — **disappears between cold starts**. The bundled
`seed/stock_cache.json` only prevents a slow first load of the stock page.

For a permanent history, point `DATA_DIR` at a mounted disk or replace the CSV
helpers (`read_rows`, `save_rows`, `upsert_day`) with a database or Vercel Blob.
The local run has no such limit.

## Known limitations

* Prices are delayed (~15 min) and exclude after-hours.
* The percentile ignores earnings, valuation and news; a stock that is cheap
  because the business is deteriorating still scores "low for the year".
* 2 years of data is a bull-market sample for US mega-caps.
* The verdict thresholds are a hand-picked rule of thumb, not a backtested model.
* The GitHub repo does not include the rate history CSV (`rates_assets.csv`), so
  a fresh deploy starts with an empty rate table.

## Disclaimer

Personal tool, statistical heuristic only. Not investment advice.
