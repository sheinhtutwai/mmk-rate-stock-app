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
| currency / gold news headlines | Google News RSS (kyat / CBM / inflation; gold price for gold pages), 3-hour cache | none |
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

## The currency verdict

The 1-year percentile alone is not the verdict. `calculate_recommendation`
starts from the percentile score (1-year rank + 30-day position + price vs
30-day average) and then adjusts it for the four known weaknesses of a
percentile-only currency signal:

* **Trend (one-way depreciation):** when the 6-month move is `>= +8%` and the
  3-month move is `>= +5%`, a high percentile no longer says "don't buy yet"
  for the buyer — it says "buy in parts (DCA)", because waiting for the old
  price can mean missing the buy entirely while the kyat keeps depreciating.
* **Dip confirmation (no news filter):** a "should buy" verdict from a dip is
  downgraded to "wait and see" when the dip is a single sharp one-day drop
  (today's sell price down `>= 2%` in one day and below its 30-day average for
  at most 1 of the last 7 days). The app cannot tell a temporary dip
  (crackdown / news) from a lasting fall, so it says so plainly and asks the
  user to check the news and wait ~3 days.
* **Spread / break-even:** the recommendation box shows today's spread and the
  break-even buy rate — the price the sell-back rate must reach before any
  profit. A small rise smaller than the spread is a loss.
* **Mean reversion:** a 6-month percentile is shown next to the 1-year one,
  and when the 1-year percentile is `>= 80%` with a 12-month rise of
  `>= +10%`, a warning says the old mean price may never return.
* **News:** every currency/gold page shows the latest headlines from Google
  News RSS (kyat / central bank / inflation for currencies, gold price for
  gold pages) in a "နောက်ဆုံး သတင်း" box, fetched server-side with an 8s
  timeout and a 3-hour cache, last 90 days only, title + source + link. The
  headlines help judge whether a dip is temporary or lasting, but the app
  still cannot classify a dip by itself — it says so in the UI. If the feed
  fails, the box says "သတင်း မရသေးဘူး" instead of showing anything made up.

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
