"""Multi-asset MMK rate app — SGD / USD / GBP / Gold
Run: python app.py  ->  http://127.0.0.1:5000
"""
import csv
import html
import json
import os
import sys
import time
import statistics as st
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from flask import Flask, redirect, request, url_for

BASE = os.path.dirname(os.path.abspath(__file__))
# Writable data directory. On a serverless host the code directory is read-only,
# so set DATA_DIR=/tmp (or a mounted disk) there.
DATA_DIR = (os.environ.get("DATA_DIR")
            or ("/tmp" if os.environ.get("VERCEL") else BASE))
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception as e:
    print(f"Could not create DATA_DIR {DATA_DIR}: {e}", file=sys.stderr)
CSV_PATH = os.path.join(DATA_DIR, "rates_assets.csv")
FIELDS = ["date", "asset", "buy", "sell", "note"]

app = Flask(__name__)

MONTH_MM = ["", "ဇန်", "ဖေ", "မတ်", "ဧပြီ", "မေ", "ဇွန်",
            "ဇူလိုင်", "ဩ", "စက်", "အောက်", "နို", "ဒီ"]

# ---------- assets ----------
ASSETS = {
    "sgd": {"code": "SGD", "short": "SGD", "name": "SGD — စင်ကာပူဒေါ်လာ", "flag": "",
            "kind": "currency", "id": 3, "unit": "MMK", "unit_mm": "ကျပ်"},
    "usd": {"code": "USD", "short": "USD", "name": "USD — အမေရိကန်ဒေါ်လာ", "flag": "",
            "kind": "currency", "id": 1, "unit": "MMK", "unit_mm": "ကျပ်"},
    "gbp": {"code": "GBP", "short": "GBP", "name": "GBP — အင်္ဂလန်ပေါင်", "flag": "",
            "kind": "currency", "id": 9, "unit": "MMK", "unit_mm": "ကျပ်"},
    "gold16": {"code": "GOLD16", "short": "ရွှေ ၁၆", "name": "ရွှေ ၁၆ ပဲရည် (၁ ကျပ်သား)",
               "flag": "🥇 ", "kind": "gold", "id": 1, "unit": "MMK", "unit_mm": "ကျပ်/ကျပ်သား"},
    "gold15": {"code": "GOLD15", "short": "ရွှေ ၁၅", "name": "ရွှေ ၁၅ ပဲရည် (၁ ကျပ်သား)",
               "flag": "🥇 ", "kind": "gold", "id": 2, "unit": "MMK", "unit_mm": "ကျပ်/ကျပ်သား"},
    "goldnew16": {"code": "NEWGOLD16", "short": "ရွှေ ၁၆ သစ်", "name": "ရွှေ ၁၆ ပဲရည် အသစ် (၁ ကျပ်သား)",
                  "flag": "🥇 ", "kind": "gold", "id": 3, "unit": "MMK", "unit_mm": "ကျပ်/ကျပ်သား"},
    "goldnew15": {"code": "NEWGOLD15", "short": "ရွှေ ၁၅ သစ်", "name": "ရွှေ ၁၅ ပဲရည် အသစ် (၁ ကျပ်သား)",
                  "flag": "🥇 ", "kind": "gold", "id": 4, "unit": "MMK", "unit_mm": "ကျပ်/ကျပ်သား"},
}
ASSET_ORDER = ["sgd", "usd", "gbp", "gold16", "gold15", "goldnew16", "goldnew15"]


def asset_info(key):
    return ASSETS.get(key) or ASSETS["sgd"]


def asset_title(key):
    a = asset_info(key)
    return f"{a['name']}"


SEED_RATES_CSV = os.path.join(BASE, "seed", "rates_assets.csv")

# ---------- Vercel Blob Storage Integration ----------
BLOB_TOKEN = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
BLOB_CSV_PATHNAME = "rates_assets.csv"
BLOB_API_BASE = "https://blob.vercel-storage.com"


def _blob_sync_down():
    """Download rates_assets.csv from Vercel Blob if it exists."""
    if not BLOB_TOKEN:
        return False
    try:
        req = urllib.request.Request(
            f"{BLOB_API_BASE}/?prefix={BLOB_CSV_PATHNAME}",
            headers={"Authorization": f"Bearer {BLOB_TOKEN}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            blobs = data.get("blobs") or []
            target = next((b for b in blobs if b.get("pathname") == BLOB_CSV_PATHNAME), None)
            if target and target.get("url"):
                with urllib.request.urlopen(target["url"], timeout=15) as dresp:
                    content = dresp.read()
                    if content and len(content) > 100:
                        with open(CSV_PATH, "wb") as f:
                            f.write(content)
                        return True
    except Exception as e:
        print(f"Blob sync down failed: {e}", file=sys.stderr)
    return False


def _blob_sync_up():
    """Upload CSV_PATH to Vercel Blob to persist updates across instances."""
    if not BLOB_TOKEN or not os.path.exists(CSV_PATH):
        return False
    try:
        with open(CSV_PATH, "rb") as f:
            content = f.read()
        req = urllib.request.Request(
            f"{BLOB_API_BASE}/{BLOB_CSV_PATHNAME}",
            data=content,
            method="PUT",
        )
        req.add_header("Authorization", f"Bearer {BLOB_TOKEN}")
        req.add_header("x-api-version", "7")
        req.add_header("x-add-random-suffix", "false")
        req.add_header("content-type", "text/csv")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        print(f"Blob sync up failed: {e}", file=sys.stderr)
        return False


# ---------- storage ----------
def ensure_csv():
    if not os.path.exists(CSV_PATH):
        # 1. First try pulling existing persistent file from Vercel Blob
        if _blob_sync_down():
            return
        # 2. Otherwise seed from bundled CSV and upload to Blob
        if os.path.exists(SEED_RATES_CSV):
            import shutil
            try:
                shutil.copyfile(SEED_RATES_CSV, CSV_PATH)
                _blob_sync_up()
                return
            except Exception as e:
                print(f"Seed CSV copy failed: {e}", file=sys.stderr)
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(FIELDS)


def _parse_date(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def read_all_raw():
    """Every row in the CSV, verbatim (never drops anything)."""
    ensure_csv()
    with open(CSV_PATH, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if (r.get("date") or "").strip()]


def read_rows(asset="sgd", year=None):
    ensure_csv()
    rows = []
    with open(CSV_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("asset") or "").strip().lower() != asset:
                continue
            d = _parse_date(r.get("date", ""))
            if not d:
                continue
            if year and d.year != year:
                continue
            try:
                r["buy"] = float(r["buy"])
                r["sell"] = float(r["sell"])
            except Exception:
                continue
            r["date"] = d.isoformat()
            r["_date"] = d
            rows.append(r)
    rows.sort(key=lambda x: x["_date"])
    return rows


def save_rows(rows):
    """Rewrite the whole file from parsed rows plus untouched raw rows."""
    keep = list(rows)
    have = {(r["date"], r["asset"]) for r in keep}
    for r in read_all_raw():
        d = _parse_date(r.get("date", ""))
        if not d:
            continue
        key = (d.isoformat(), (r.get("asset") or "").strip().lower())
        if key in have:
            continue
        have.add(key)
        keep.append({"date": d.isoformat(), "asset": key[1],
                     "buy": r.get("buy", "0"), "sell": r.get("sell", "0"),
                     "note": r.get("note", "")})
    keep.sort(key=lambda r: (r["date"], r["asset"]))
    tmp = CSV_PATH + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for r in keep:
            w.writerow([r["date"], r["asset"], f"{float(r['buy']):.2f}",
                        f"{float(r['sell']):.2f}", r.get("note", "")])
    os.replace(tmp, CSV_PATH)
    _blob_sync_up()


def upsert_day(asset, day, buy, sell, note="auto"):
    day = day or date.today().isoformat()
    rows = read_rows(asset)
    found = False
    for r in rows:
        if r["date"] == day:
            r["buy"], r["sell"], r["note"] = buy, sell, note
            found = True
    if not found:
        rows.append({"date": day, "asset": asset, "buy": buy, "sell": sell, "note": note})
    save_rows([r for r in read_rows() if r["asset"] != asset] +
              [{"date": r["date"], "asset": asset, "buy": r["buy"],
                "sell": r["sell"], "note": r.get("note", "")} for r in rows])


def available_years(asset="sgd"):
    ys = sorted({r["_date"].year for r in read_rows(asset)}, reverse=True)
    return ys or [date.today().year]


# ---------- live fetch ----------
MMP_KEY = os.environ.get("MMP_API_KEY", "")  # set in the host's env vars; never commit
MMP_BASE = "https://data-provider.getmmp.link/v1"


def _mmp_get(url):
    import json, urllib.request
    req = urllib.request.Request(url, headers={"x-api-key": MMP_KEY, "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_mmp_today(asset):
    a = asset_info(asset)
    if a["kind"] == "gold":
        # latest-rates မှာ နေ့စွဲမပါလို့ history ရဲ့ နောက်ဆုံးရက်ကို ယူသည်
        data = _mmp_get(MMP_BASE + f"/myanmar-gold/rates?myanmarGoldId={a['id']}"
                        f"&startDate={date.today().isoformat()}&numberOfDays=7")
        best = None
        for day in data.get("rates", []) or []:
            entries = day.get("rates") or []
            if day.get("date") and entries:
                last = max(entries, key=lambda x: x.get("createdAt") or "")
                if best is None or day["date"] > best[0]:
                    best = (day["date"], float(last["buyRate"]), float(last["sellRate"]))
        if not best:
            raise RuntimeError("gold rate not found")
        return best[1], best[2], best[0]
    data = _mmp_get(MMP_BASE + "/currencies/latest-rates?isVisible=true")
    for r in data.get("latestRates", []):
        if r.get("currencyId") == a["id"]:
            upd = (r.get("updatedAt") or "")[:10]
            try:
                datetime.strptime(upd, "%Y-%m-%d")
            except Exception:
                upd = date.today().isoformat()
            return float(r["buyRate"]), float(r["sellRate"]), upd
    raise RuntimeError(f"{a['code']} rate not found")


def fetch_okx_fallback(asset):
    """MMP မရပါက OKX USDT/MMK + FX မှ တွက်သည်။ (ရွှေအတွက် မရပါ)"""
    import json, urllib.request
    a = asset_info(asset)
    if a["kind"] == "gold":
        raise RuntimeError("no fallback for gold")

    def gj(url):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    base = ("https://www.okx.com/v3/c2c/tradingOrders/books"
            "?quoteCurrency=mmk&baseCurrency=usdt&side={}"
            "&paymentMethod=all&userType=all&showTrade=false")
    b = gj(base.format("buy"))["data"]["buy"][:5]
    s = gj(base.format("sell"))["data"]["sell"][:5]
    ubuy = sum(float(x["price"]) for x in b) / len(b)
    usell = sum(float(x["price"]) for x in s) / len(s)
    rates = gj("https://open.er-api.com/v6/latest/USD")["rates"]
    if asset == "usd":
        div = 1.0
    elif asset == "gbp":
        div = float(rates["GBP"])
    else:
        div = float(rates["SGD"])
    return ubuy / div, usell / div


def fetch_live(asset):
    try:
        buy, sell, d = fetch_mmp_today(asset)
        return buy, sell, "mmp-live", d
    except Exception as e_mmp:
        try:
            buy, sell = fetch_okx_fallback(asset)
            return buy, sell, "OKX-live", date.today().isoformat()
        except Exception:
            raise RuntimeError(f"MMP: {e_mmp}")


# ---------- Recommendation Engine ----------
def calculate_recommendation(asset):
    all_rows = read_rows(asset)
    if not all_rows:
        return None
    a = asset_info(asset)
    latest = all_rows[-1]
    today_buy = latest["buy"]
    today_sell = latest["sell"]

    last_30 = all_rows[-30:] if len(all_rows) >= 30 else all_rows
    last_7 = all_rows[-7:] if len(all_rows) >= 7 else all_rows
    ma7 = st.mean([r["buy"] for r in last_7])
    ma30 = st.mean([r["buy"] for r in last_30])
    b30 = [r["buy"] for r in last_30]
    min30, max30 = min(b30), max(b30)
    pct30 = ((today_buy - min30) / (max30 - min30) * 100) if max30 > min30 else 50.0

    # တစ်နှစ်တာ = နောက်ဆုံး ၃၆၅ ရက် (percentile ကို rank အခြေခံ —
    # Conditional Forward Return box နှင့် တူညီအောင်)
    win = all_rows[-365:] if len(all_rows) >= 365 else all_rows

    def _rank_pct(vals, v):
        return sum(1 for x in vals if x <= v) / len(vals) * 100 if vals else 50.0

    byear = [r["buy"] for r in win]
    min_year, max_year = min(byear), max(byear)
    avg_year = st.mean(byear)
    pct_year = _rank_pct(byear, today_buy)

    # ဝယ်သူအတွက် (sellRate) ကိန်းဂဏန်းများ
    syear = [r["sell"] for r in win]
    s30 = [r["sell"] for r in last_30]
    s7 = [r["sell"] for r in last_7]
    ma7_s = st.mean(s7)
    ma30_s = st.mean(s30)
    min_s, max_s = min(syear), max(syear)
    avg_s = st.mean(syear)
    pct_year_s = _rank_pct(syear, today_sell)
    min30_s, max30_s = min(s30), max(s30)
    pct30_s = ((today_sell - min30_s) / (max30_s - min30_s) * 100) if max30_s > min30_s else 50.0

    score = 0
    if pct_year >= 80: score += 3
    elif pct_year >= 60: score += 1
    elif pct_year <= 20: score -= 3
    elif pct_year <= 40: score -= 1

    if pct30 >= 75: score += 2
    elif pct30 >= 55: score += 1
    elif pct30 <= 25: score -= 2
    elif pct30 <= 45: score -= 1

    score += 1 if today_buy >= ma30 else -1

    score_s = 0
    if pct_year_s >= 80: score_s += 3
    elif pct_year_s >= 60: score_s += 1
    elif pct_year_s <= 20: score_s -= 3
    elif pct_year_s <= 40: score_s -= 1

    if pct30_s >= 75: score_s += 2
    elif pct30_s >= 55: score_s += 1
    elif pct30_s <= 25: score_s -= 2
    elif pct30_s <= 45: score_s -= 1

    score_s += 1 if today_sell >= ma30_s else -1

    unit = a["unit_mm"]
    code = a["short"]
    if score >= 3:
        seller_action = f"{code} ရောင်းသင့်သည် (အလွန်ကောင်း) ⭐"
        seller_color, seller_bg = "#2e7d32", "#e8f5e9"
        summary_txt = (f"လက်ရှိရောင်းဈေး ({today_buy:,.0f} {unit}) သည် တစ်နှစ်တာ၏ "
                       f"အမြင့်ဆုံးအပိုင်း ({pct_year:.1f}% percentile) နှင့် ၃၀ ရက်ပျမ်းမျှ "
                       f"({ma30:,.0f}) အထက်တွင် ရှိနေပါသည်။ ရောင်းချမည့်သူများအတွက် "
                       f"ဈေးကောင်းရချိန် ဖြစ်သည်။ ဝယ်ယူလိုသူများအတွက် ဈေးအမြင့်ပိုင်းဖြစ်၍ "
                       f"စောင့်ကြည့်သင့်ပါသည်။")
    elif score <= -3:
        seller_action = f"{code} မရောင်းသင့်သေး (စောင့်ကြည့်သင့်) ⏳"
        seller_color, seller_bg = "#c62828", "#ffebee"
        summary_txt = (f"လက်ရှိရောင်းဈေး ({today_buy:,.0f} {unit}) သည် တစ်နှစ်တာ၏ "
                       f"အနိမ့်ပိုင်း ({pct_year:.1f}% percentile) သို့ ရောက်ရှိနေပါသည်။ "
                       f"ဝယ်ယူ/စုဆောင်းလိုသူများအတွက် ဈေးသက်သာချိန် ဖြစ်သည်။ "
                       f"ရောင်းချင်သူများအနေဖြင့် ဈေးပြန်တက်သည်အထိ စောင့်ဆိုင်းသင့်ပါသည်။")
    else:
        seller_action = "ပုံမှန်ဈေး (ခွဲရောင်းနိုင်သည်) ⚖️"
        seller_color, seller_bg = "#ef6c00", "#fff3e0"
        summary_txt = (f"လက်ရှိရောင်းဈေး ({today_buy:,.0f} {unit}) သည် ပုံမှန်"
                       f"အလယ်အလတ် အဆင့်တွင် ရှိနေပါသည်။ တစ်ပြိုင်နက်တည်း "
                       f"အကုန်မရောင်း/မဝယ်ဘဲ လိုအပ်သလို အပိုင်းလိုက် (DCA) "
                       f"လဲလှယ်သင့်ပါသည်။")

    # ဝယ်သူအတွက် ဆုံးဖြတ်ချက် (ဝယ်ဈေးအပေါ် အခြေခံ)
    if score_s >= 3:
        buyer_action = f"{code} ဝယ်ရန် မသင့်သေး (စောင့်ကြည့်သင့်) ⏳"
        buyer_color, buyer_bg = "#c62828", "#ffebee"
    elif score_s <= -3:
        buyer_action = f"{code} ဝယ်သင့်သည် (ဈေးကျချိန်) ⭐"
        buyer_color, buyer_bg = "#2e7d32", "#e8f5e9"
    else:
        buyer_action = "ပုံမှန်ဈေး (လိုအပ်သလောက်သာ ဝယ်သင့်) ⚖️"
        buyer_color, buyer_bg = "#ef6c00", "#fff3e0"

    # ---- ငွေဈေး အားနည်းချက် ၄ ခု ပြင်ဆင်ချက် ----
    # (၁) လမ်းကြောင်း — ကျပ်ငွေ တန်ဖိုးကျတဲ့ဘက် တစ်ဖက်တည်း ဦးတည်နေချိန်မှာ
    #     percentile မြင့်နေရုံနဲ့ "မဝယ်နဲ့" လို့ တားရင် ဝယ်ခွင့် လွတ်တယ်။
    # (၂) ဈေးကျတာ အတည်ပြုချက် — တစ်ရက်တည်း ဈေးကျတာနဲ့ "ဝယ်သင့်တယ်" လို့
    #     မပြောဘဲ၊ ဈေးနိမ့်တာ ရက်အနည်းငယ် ဆက်ဖြစ်မှ ဝယ်ဖို့ အကြံပေးတယ်။
    # (၃) Spread / break-even — ပြန်ရောင်းပြီး အမြတ်ရဖို့ ရောင်းဈေးက ဝယ်ဈေးအထိ
    #     တက်ရမယ်။
    # (၄) ၆ လ percentile — ဈေးဟောင်းဆီ ပြန်ကျမယ်လို့ ယူဆလို့ မရဘူးဆိုတာ ပြတယ်။
    def _chg(vals, days):
        if len(vals) <= days or not vals[-1 - days]:
            return None
        return (vals[-1] - vals[-1 - days]) / vals[-1 - days] * 100

    buys_all = [r["buy"] for r in all_rows]
    sells_all = [r["sell"] for r in all_rows]
    chg3m_b, chg6m_b, chg12m_b = _chg(buys_all, 90), _chg(buys_all, 180), _chg(buys_all, 365)
    chg3m_s, chg6m_s, chg12m_s = _chg(sells_all, 90), _chg(sells_all, 180), _chg(sells_all, 365)
    win6 = all_rows[-180:] if len(all_rows) >= 180 else all_rows
    pct6m_b = _rank_pct([r["buy"] for r in win6], today_buy)
    pct6m_s = _rank_pct([r["sell"] for r in win6], today_sell)
    spread = today_sell - today_buy
    spread_pct = (spread / today_buy * 100) if today_buy else 0.0
    breakeven = today_sell  # ဝယ်သူ ပြန်ရောင်းချိန်မှာ ရောင်းဈေးက ဒီအထိ တက်ရမှ အမြတ်ရမယ်
    dip_days = sum(1 for r in all_rows[-7:] if r["sell"] < ma30_s)
    drop1d_s = ((today_sell - sells_all[-2]) / sells_all[-2] * 100) if len(sells_all) >= 2 and sells_all[-2] else 0.0
    trend_up_s = (chg6m_s is not None and chg6m_s >= 8 and chg3m_s is not None and chg3m_s >= 5)
    trend_up_b = (chg6m_b is not None and chg6m_b >= 8 and chg3m_b is not None and chg3m_b >= 5)

    notes = []
    # (၁) လမ်းကြောင်း တက်နေရင် percentile မြင့်နေလည်း "မဝယ်နဲ့" လို့ မတားဘူး — DCA ပြောင်းတယ်
    if score_s >= 3 and trend_up_s:
        buyer_action = f"{code} ဈေးမြင့်ပေမယ့် လမ်းကြောင်း တက်နေတယ် — ခွဲဝယ်ပါ (DCA) ⚠️"
        buyer_color, buyer_bg = "#ef6c00", "#fff3e0"
        notes.append(f"📈 <b>လမ်းကြောင်း တက်နေတယ်:</b> ဝယ်ဈေး ၃ လအတွင်း {chg3m_s:+.1f}%၊ ၆ လအတွင်း {chg6m_s:+.1f}% တက်ထားတယ်။ "
                     f"ကျပ်ငွေ တန်ဖိုးကျနေတဲ့ဘက်ကို ဦးတည်နေလို့ ဈေးဟောင်းဆီ ပြန်ကျဖို့ စောင့်နေရင် ဝယ်ခွင့် လွတ်နိုင်တယ်။ "
                     f"တစ်ခါတည်း အကုန် မဝယ်ဘဲ နည်းနည်းချင်း ခွဲဝယ်ပါ (DCA)။")
        summary_txt += (f" ဝယ်ယူလိုသူအတွက် — ဈေးမြင့်နေပေမယ့် လမ်းကြောင်း တက်နေတဲ့အတွက် "
                        f"စောင့်နေရင် ဝယ်ခွင့် လွတ်နိုင်တယ်။ တစ်ခါတည်း အကုန် မဝယ်ဘဲ နည်းနည်းချင်း ခွဲဝယ်ပါ။")
    # (၄) တစ်နှစ်အတွင်း အများကြီး တက်ထားရင် ဈေးဟောင်းဆီ ပြန်ကျမယ်လို့ ယူဆလို့ မရဘူး
    if pct_year_s >= 80 and chg12m_s is not None and chg12m_s >= 10:
        notes.append(f"⚠️ <b>ဈေးဟောင်း ပြန်မရနိုင်ဘူး:</b> ဝယ်ဈေး တစ်နှစ်အတွင်း {chg12m_s:+.1f}% တက်ထားတယ်။ "
                     f"မူလ ပျမ်းမျှဈေးဟောင်းဆီ ပြန်ကျမယ်လို့ ယူဆလို့ မရဘူး (ငွေကြေးဖောင်းပွမှု / ကျပ်ငွေ တန်ဖိုးကျတာ)။ "
                     f"၆ လ percentile က {pct6m_s:.1f}% ရှိတယ် — နောက် ၆ လအတွင်းမှာလည်း ဈေးက ဒီအဆင့်မှာပဲ ရှိနေတယ်။")
    # (၂) ဈေးကျပြီး "ဝယ်သင့်တယ်" လို့ ပြောမယ့်အချိန်မှာ ဈေးကျတာ အတည်ပြုပြီးမှ ဝယ်ပါ
    if score_s <= -3:
        if drop1d_s <= -2 and dip_days <= 1:
            buyer_action = f"{code} ဈေးကျတယ် — ဒါပေမယ့် စောင့်ကြည့်ပါ ⏳"
            buyer_color, buyer_bg = "#ef6c00", "#fff3e0"
            notes.append("🔍 <b>ဈေးကျတာ မအတည်ပြုရသေးဘူး:</b> ဒီနေ့ တစ်ရက်တည်း ဈေးအများကြီး ကျသွားတာ — "
                         "ယာယီ (ဖမ်းဆီးစစ်ဆေးမှု / သတင်း) လား၊ တကယ် ဈေးကျတာလား app က မသိဘူး။ "
                         "၃ ရက်လောက် ဈေးနိမ့်နေတာ ဆက်ဖြစ်မှ ဝယ်ပါ။ သတင်းကိုပါ ကြည့်ပါ။")
        else:
            notes.append("🔍 <b>ဈေးကျတာ သတိထားပါ:</b> ဈေးကျလို့ ဝယ်သင့်တယ်လို့ app က ပြောပေမယ့်၊ "
                         "အကြောင်းက ယာယီလား (ဖမ်းဆီးစစ်ဆေးမှု / သတင်း)၊ ရေရှည်လား app က မစစ်နိုင်ဘူး။ "
                         "သတင်းကိုပါ ကြည့်ပြီး ခွဲဝယ်ပါ။")

    if a["kind"] == "gold":
        seller_tag = f"🥇 <b>{a['short']} ရောင်းချမည့်သူ</b> (ရွှေလက်ဝယ်ရှိသူ)"
        buyer_tag = f"🥇 <b>{a['short']} ဝယ်ယူမည့်သူ</b> (ရွှေဝယ်ရန်ရည်ရွယ်သူ)"
    else:
        seller_tag = f"{a['flag']}<b>{code} ရောင်းသူ</b> (လက်ဝယ်ရှိ {code} ရောင်းချမည့်သူ)"
        buyer_tag = f"{a['flag']}<b>{code} ဝယ်သူ</b> (MMK ပေး၍ {code} ဝယ်မည့်သူ)"

    return {
        "asset": asset,
        "code": code,
        "unit": unit,
        "date": latest["date"],
        "buy": today_buy,
        "sell": today_sell,
        "ma7": ma7,
        "ma30": ma30,
        "pct30": pct30,
        "pct_year": pct_year,
        "min_year": min_year,
        "max_year": max_year,
        "avg_year": avg_year,
        "seller_stats": {
            "ma7": ma7, "ma30": ma30,
            "min_year": min_year, "max_year": max_year,
            "avg_year": avg_year, "pct_year": pct_year,
        },
        "buyer_stats": {
            "ma7": ma7_s, "ma30": ma30_s,
            "min_year": min_s, "max_year": max_s,
            "avg_year": avg_s, "pct_year": pct_year_s,
        },
        "seller_action": seller_action,
        "seller_color": seller_color,
        "seller_bg": seller_bg,
        "buyer_action": buyer_action,
        "buyer_color": buyer_color,
        "buyer_bg": buyer_bg,
        "seller_tag": seller_tag,
        "buyer_tag": buyer_tag,
        "summary": summary_txt,
        "total_days": len(all_rows),
        "chg3m_b": chg3m_b, "chg6m_b": chg6m_b, "chg12m_b": chg12m_b,
        "chg3m_s": chg3m_s, "chg6m_s": chg6m_s, "chg12m_s": chg12m_s,
        "pct6m_b": pct6m_b, "pct6m_s": pct6m_s,
        "spread": spread, "spread_pct": spread_pct, "breakeven": breakeven,
        "trend_up_s": trend_up_s, "trend_up_b": trend_up_b,
        "dip_days": dip_days, "drop1d_s": drop1d_s,
        "notes": notes,
    }


# ---------- analytics ----------
WEEKDAY_MM = ["တနင်္လာ", "အင်္ဂါ", "ဗုဒ္ဓဟူး", "ကြာသပတေး", "သောကြာ", "စနေ", "တနင်္ဂနွေ"]


def _pctl(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def build_analytics(all_rows, a, target=None, asset_key="sgd", target_b=None):
    """ခေါက်ထားတဲ့ analysis section များ၏ HTML ကို ပြန်ပေးသည်။"""
    if len(all_rows) < 60:
        return ""
    dates = [r["_date"] for r in all_rows]
    buys = [r["buy"] for r in all_rows]
    sells = [r["sell"] for r in all_rows]
    n = len(buys)
    unit = a["unit_mm"]
    short = a["short"]
    parts = []

    def box(title, body, anchor=None, open_default=False):
        aid = f" id='{anchor}'" if anchor else ""
        op = " open" if open_default else ""
        parts.append(
            f"<details{aid}{op} style='margin:8px 0;border:1px solid #ddd;border-radius:10px;padding:10px 14px;background:#fff'>"
            f"<summary style='cursor:pointer;font-weight:bold;font-size:15px'>{title}</summary>"
            f"<div style='margin-top:10px'>{body}</div></details>")

    # 1. Conditional (percentile-based) forward return
    PCT_WIN = 365          # rolling window (days)
    PCT_MIN = 60           # window အနည်းဆုံး

    def _roll_pcts(vals):
        """Point-in-time percentile: အဲ့ရက်အထိ နောက်ဆုံး ၃၆၅ ရက်အတွင်း
        ဈေးတွေထက် ဘယ်နှစ်ရာခိုင်နှုန်းမြင့်နေလဲ။ အနာဂတ်ဒေတာ မပါဝင်ပါ။"""
        out = []
        for i in range(len(vals)):
            lo = max(0, i - PCT_WIN + 1)
            win = vals[lo:i + 1]
            if len(win) < PCT_MIN:
                out.append(None)
                continue
            v = vals[i]
            out.append(sum(1 for x in win if x <= v) / len(win) * 100)
        return out

    def _cond_rows(vals):
        pcts = _roll_pcts(vals)
        cur_p = pcts[-1]
        lo_p = max(0.0, cur_p - 8.0)
        hi_p = min(100.0, cur_p + 7.0)
        html = ""
        n_sel = 0
        for h in (7, 30, 90):
            hist = [(vals[i + h] - vals[i]) / vals[i] * 100
                    for i in range(n - h)
                    if vals[i] and pcts[i] is not None and lo_p <= pcts[i] <= hi_p]
            if not hist:
                html += (f"<tr><td>{h} ရက်</td><td>0</td><td>—</td>"
                         f"<td>—</td><td>—</td></tr>")
                continue
            n_sel = max(n_sel, len(hist))
            up = sum(1 for c in hist if c > 0) / len(hist) * 100
            html += (f"<tr><td>{h} ရက်</td><td>{len(hist):,}</td>"
                     f"<td><b>{up:.0f}%</b></td>"
                     f"<td>{st.mean(hist):+.2f}%</td><td>{st.median(hist):+.2f}%</td></tr>")
        return cur_p, lo_p, hi_p, n_sel, html

    cur_ps, lo_ps, hi_ps, cnt_s, rows_s = _cond_rows(buys)
    cur_pb, lo_pb, hi_pb, cnt_b, rows_b = _cond_rows(sells)
    cond_head = ("<tr><th>ကာလ</th><th>နမူနာ</th><th>တက်ခဲ့ %</th>"
                 "<th>Average</th><th>Median</th></tr>")
    thin_note = ("<p style='font-size:12px;color:#c62828;margin:4px 0 0'>⚠ နမူနာရက် ၂၀ အောက်ဖြစ်နေတဲ့ "
                 "ကာလများရှိသည် — ကိန်းဂဏန်းကို ခန့်မှန်းချက်အဖြစ်သာ ယူပါ။</p>"
                 if min(cnt_s, cnt_b) < 20 else "")
    chg_note = ""
    if cur_pb is not None:
        diff = cur_pb - cur_ps
        if abs(diff) >= 3:
            chg_note = (f"<p style='font-size:12px;color:#777;margin:4px 0 0'>ဝယ်/ရောင်း percentile "
                        f"{diff:+.0f} ကွာသည် (spread ကြောင့်)။</p>")
    box("🎯 လက်ရှိ ဈေးအဆင့်နှင့် တူခဲ့တဲ့ ရက်များသာ စစ်ထုတ် (Conditional Forward Return)",
        f"<p style='font-size:13px;color:#555;margin:0 0 8px'>သမိုင်းရက် <b>အားလုံး</b>ကို ရောကြိတ်တွက်တဲ့ ရိုးရိုး "
        f"Forward Return နှင့် မတူဘဲ၊ ဒီနေ့ percentile အဆင့်နှင့် <b>အလားတူခဲ့တဲ့ ရက်များ</b>ကိုသာ "
        f"စစ်ထုတ်ပြီး တွက်ထားသည်။ ဒီနေ့ percentile ကို <b>ပြီးခဲ့တဲ့ ၃၆၅ ရက်</b>အတွင်း "
        f"ဈေးတွေထက် မြင့်နေတဲ့ ရာခိုင်နှုန်းနဲ့ တွက်ထားသည် (အနာဂတ်ဒေတာ မပါအောင်)။</p>"
        f"<p style='font-size:13px;margin:8px 0 2px'>🥇 <b>ရောင်းဈေး (seller)</b> — ဒီနေ့ percentile "
        f"<b>{cur_ps:.1f}%</b> → စစ်ထုတ်ကာလ <b>{lo_ps:.0f}%–{hi_ps:.0f}%</b></p>"
        f"<div class='tscroll wide'><table>{cond_head}{rows_s}</table></div>"
        f"<p style='font-size:13px;margin:14px 0 2px'>🛒 <b>ဝယ်ဈေး (buyer)</b> — ဒီနေ့ percentile "
        f"<b>{cur_pb:.1f}%</b> → စစ်ထုတ်ကာလ <b>{lo_pb:.0f}%–{hi_pb:.0f}%</b></p>"
        f"<div class='tscroll wide'><table>{cond_head}{rows_b}</table></div>"
        f"{thin_note}{chg_note}"
        f"<p style='font-size:12px;color:#888;margin:8px 0 0'>Percentile = ပြီးခဲ့ ၃၆၅ ရက်အတွင်း "
        f"ဈေးတွေထက် မြင့်နေတဲ့ ရာခိုင်နှုန်း (point-in-time)။ ထိပ်က 'တစ်နှစ်တာ Percentile' "
        f"နဲ့ တူညီတဲ့ တွက်နည်း ဖြစ်တယ်။</p>")

    # 2. Spread trend
    spreads = [sells[i] - buys[i] for i in range(n)]
    cur_spread = spreads[-1]
    avg_spread = st.mean(spreads)
    by_month = {}
    for i, d in enumerate(dates):
        by_month.setdefault((d.year, d.month), []).append(spreads[i])
    keys = sorted(by_month)[-6:]
    trend = [((y, m), st.mean(by_month[(y, m)])) for y, m in keys]
    lo_key, lo_v = min(trend, key=lambda kv: kv[1])
    hi_key, hi_v = max(trend, key=lambda kv: kv[1])
    box("💱 ရောင်းဈေး − ဝယ်ဈေး ကွာချင်း (Spread)",
        f"<p style='font-size:13px;color:#555;margin:0 0 8px'>"
        f"<b>ဒီနေ့ ({dates[-1]})</b> spread: <b>{cur_spread:,.0f} {unit}</b> "
        f"({cur_spread / buys[-1] * 100:.2f}%) · သမိုင်းပျမ်းမျှ: <b>{avg_spread:,.0f}</b>. "
        f"Spread ကျဉ်းရင် လဲလှယ်ရ အဆင်ပြေသည်။</p>"
        f"<p style='font-size:13px;margin:0'>၆ လအတွင်း — အနိမ့်ဆုံး "
        f"<b>{lo_v:,.1f}</b> ({lo_key[0]}-{lo_key[1]:02d}) · အမြင့်ဆုံး "
        f"<b>{hi_v:,.1f}</b> ({hi_key[0]}-{hi_key[1]:02d}) · လက်ရှိ "
        f"<b>{trend[-1][1]:,.1f}</b></p>")

    # 3. Best weekday / day of month (30-day MA နှင့် ကွာချင်း)
    ma30 = []
    run = 0.0
    for i, v in enumerate(buys):
        run += v
        if i >= 30:
            run -= buys[i - 30]
            ma30.append(run / 30)
        else:
            ma30.append(None)
    dev = [(buys[i] / ma30[i] - 1) * 100 for i in range(n)
           if ma30[i] and ma30[i] > 0]
    idxs = [i for i in range(n) if ma30[i] and ma30[i] > 0]
    wd = {}
    dom = {}
    for i, dv in zip(idxs, dev):
        wd.setdefault(dates[i].weekday(), []).append(dv)
        dom.setdefault(dates[i].day, []).append(dv)
    wd_rows = "".join(
        f"<tr><td>{WEEKDAY_MM[k]}</td><td>{st.mean(wd[k]):+.2f}%</td><td>{len(wd[k])}</td></tr>"
        for k in sorted(wd, key=lambda k: -st.mean(wd[k])))
    dom_sorted = sorted(dom.items(), key=lambda kv: -st.mean(kv[1]))
    best = "".join(f"{d} ရက် ({st.mean(v):+.2f}%) · " for d, v in dom_sorted[:3])
    worst = "".join(f"{d} ရက် ({st.mean(v):+.2f}%) · " for d, v in dom_sorted[-3:])
    box("📅 ဘယ်နေ့ လဲလှယ်ရ ဈေးအကောင်းဆုံးလဲ (ရောင်းသူအတွက်)",
        f"<p style='font-size:13px;color:#555;margin:0 0 8px'>ဈေးတက်နေတဲ့ လမ်းကြောင်းကို ချေဖျက်ဖို့ "
        f"၃၀ ရက် ပျမ်းမျှနှင့် ကွာချင်း % ကို အသုံးပြုထားသည်။ + ဖြစ်ရင် အဲ့ရက်တွေမှာ ဈေးပိုမြင့်လေ့ရှိ။</p>"
        f"<div class='tscroll'><table><tr><th>အပတ်စဉ်ရက်</th><th>ပျမ်းမျှ ကွာချင်း</th><th>ရက်</th></tr>{wd_rows}</table></div>"
        f"<p style='font-size:13px;margin:8px 0 0'>ရက်စွဲအကောင်းဆုံး: <b>{best.rstrip(' · ')}</b><br>"
        f"အဆိုးဆုံး: <b>{worst.rstrip(' · ')}</b></p>")

    # 4. Seasonality heatmap (လ × နှစ်)
    my = {}
    for i, d in enumerate(dates):
        my.setdefault((d.year, d.month), []).append(buys[i])
    yrs = sorted({y for y, _ in my})
    year_avg = {y: st.mean([v for (yy, m), vs in my.items() if yy == y for v in vs]) for y in yrs}
    head = "".join(f"<th>{MONTH_MM[m]}</th>" for m in range(1, 13))
    body = ""
    for y in sorted(yrs, reverse=True):
        cells = ""
        for m in range(1, 13):
            vs = my.get((y, m))
            if not vs:
                cells += "<td style='color:#bbb'>—</td>"
                continue
            mv = st.mean(vs)
            dv = (mv / year_avg[y] - 1) * 100
            inten = min(abs(dv) / 10, 1)
            bg = (f"rgba(211,47,47,{0.12 + 0.5 * inten:.2f})" if dv > 0
                  else f"rgba(46,125,50,{0.12 + 0.5 * inten:.2f})")
            cells += f"<td style='background:{bg}' title='{dv:+.1f}%'>{mv:,.0f}</td>"
        body += f"<tr><th>{y}</th>{cells}</tr>"
    box(f"🗓️ {a['name']} · လ × နှစ် ရာသီအလိုက် ဈေးဇယား (Heatmap)",
        "<p style='font-size:13px;color:#555;margin:0 0 8px'>အနီ = အဲ့နှစ်ပျမ်းမျှထက် မြင့်၊ အစိမ်း = နိမ့်။ "
        "အနီရင့်ရင် ပိုမြင့်။</p>"
        f"<div class='tscroll xwide'><table style='font-size:12px'><tr><th>နှစ်</th>{head}</tr>{body}</table></div>")

    # 5. Volatility
    chg = [(buys[i] - buys[i - 1]) / buys[i - 1] * 100
           for i in range(1, n) if buys[i - 1]]
    vol_by_month = {}
    for i in range(1, n):
        if not buys[i - 1]:
            continue
        c = (buys[i] - buys[i - 1]) / buys[i - 1] * 100
        vol_by_month.setdefault((dates[i].year, dates[i].month), []).append(c)
    vols = [(k, st.pstdev(v), len(v)) for k, v in vol_by_month.items() if len(v) >= 5]
    vol_rows = "".join(
        f"<tr><td>{y}-{m:02d}</td><td>{sd:.2f}%</td><td>{cnt}</td></tr>"
        for (y, m), sd, cnt in sorted(vols, reverse=True)[:6])
    moves = sorted(((abs((buys[i] - buys[i - 1]) / buys[i - 1] * 100),
                     (buys[i] - buys[i - 1]) / buys[i - 1] * 100, dates[i])
                    for i in range(1, n) if buys[i - 1]), reverse=True)[:5]
    mv_rows = "".join(f"<tr><td>{d}</td><td>{c:+.2f}%</td></tr>" for _, c, d in moves)
    box("🌊 ဈေးလှုပ်ရှားမှု (Volatility)",
        f"<p style='font-size:13px;color:#555;margin:0 0 8px'>နေ့စဉ် ရောင်းဈေး ပြောင်းလဲ % ရဲ့ "
        f"standard deviation။ ကြီးရင် ဈေးခုန်တတ်တဲ့ လ။</p>"
        f"<div style='display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start'>"
        f"<div class='tscroll' style='flex:1;min-width:220px'><table><tr><th>လ</th><th>Volatility</th><th>ရက်</th></tr>{vol_rows}</table></div>"
        f"<div class='tscroll' style='flex:1;min-width:220px'><table><tr><th>အကြီးမားဆုံး တစ်ရက်ပြောင်းလဲ</th><th>%</th></tr>{mv_rows}</table></div></div>")

    # 6. Percentile band chart (နောက်ဆုံး ၁ နှစ်)
    start = max(0, n - 365)
    band_labels, price, p20s, p50s, p80s = [], [], [], [], []
    for i in range(start, n):
        lo = max(0, i - 90)
        window = sorted(buys[lo:i + 1])
        if len(window) < 30:
            continue
        band_labels.append(dates[i].isoformat())
        price.append(round(buys[i], 1))
        p20s.append(round(_pctl(window, 20), 1))
        p50s.append(round(_pctl(window, 50), 1))
        p80s.append(round(_pctl(window, 80), 1))
    box("📊 Percentile Band (၉၀ ရက်အတွင်း ၂၀/၅၀/၈၀ ရာခိုင်နှုန်း အကွက်)",
        f"<p style='font-size:13px;color:#555;margin:0 0 8px'>ဈေးမျဉ်းက အပေါ်အကွက် (၈၀%) နားရင် ဈေးမြင့်ချိန်၊ "
        f"အောက်အကွက် (၂၀%) နားရင် ဈေးနိမ့်ချိန်။</p>"
        f"<canvas id='band' height='120'></canvas>"
        f"<script>new Chart(document.getElementById('band'),{{type:'line',data:{{labels:{band_labels},"
        f"datasets:[{{label:'ဈေး',data:{price},borderColor:'#111',pointRadius:0,borderWidth:2}},"
        f"{{label:'P80',data:{p80s},borderColor:'#d32f2f',borderDash:[4,4],pointRadius:0}},"
        f"{{label:'P50',data:{p50s},borderColor:'#f9a825',pointRadius:0}},"
        f"{{label:'P20',data:{p20s},borderColor:'#2e7d32',borderDash:[4,4],pointRadius:0}}]}},"
        f"options:{{plugins:{{legend:{{display:true,labels:{{boxWidth:12,font:{{size:10}}}}}}}}}}}});</script>")

    # 7. Target alert
    def _tline(name, tval, vals, lower_is_hit):
        cur = vals[-1]
        dist = (tval - cur) / cur * 100
        hit = (cur <= tval) if lower_is_hit else (cur >= tval)
        if lower_is_hit:
            last_hit = next((dates[i] for i in range(n - 1, -1, -1) if vals[i] <= tval), None)
            cnt = sum(1 for v in vals[-365:] if v <= tval)
        else:
            last_hit = next((dates[i] for i in range(n - 1, -1, -1) if vals[i] >= tval), None)
            cnt = sum(1 for v in vals[-365:] if v >= tval)
        return (f"<p style='font-size:14px;margin:8px 0 0'>{name} — သတ်မှတ်ဈေး: <b>{tval:,.0f}</b> · "
                f"လက်ရှိ: <b>{cur:,.0f}</b> · ကွာချင်း: <b>{dist:+.1f}%</b><br>"
                f"အခြေအနေ: <b style='color:{'#2e7d32' if hit else '#c62828'}'>"
                f"{'ရောက်ပြီ ✔' if hit else 'မရောက်သေး'}</b>"
                + (f" · နောက်ဆုံးရောက်ခဲ့: {last_hit}" if last_hit else "")
                + f" · ပြီးခဲ့ ၁ နှစ်အတွင်း ရောက်ခဲ့ {cnt} ရက်</p>")

    tb = ""
    for raw, name, vals, lower_is_hit in (
        (target, "ရောင်းမယ် (seller)", buys, False),
        (target_b, "ဝယ်မယ် (buyer)", sells, True),
    ):
        if not raw:
            continue
        try:
            tval = float(raw)
        except ValueError:
            continue
        if tval:
            tb += _tline(name, tval, vals, lower_is_hit)
    box("🎯 ဈေးသတိပေးချက် (Target Price)",
        f"<form method='get' style='margin:0 0 4px'><input type='hidden' name='asset' value='{asset_key}'>"
        f"<input type='hidden' name='year' value='{dates[-1].year}'>"
        f"<div style='font-size:13px;margin:2px 0 4px'>ရောင်းမယ့်ဈေး (seller): "
        f"<input name='target' value='{target or ''}' placeholder='ဥပမာ 3700' style='width:130px'></div>"
        f"<div style='font-size:13px;margin:2px 0 6px'>ဝယ်မယ့်ဈေး (buyer): "
        f"<input name='target_b' value='{target_b or ''}' placeholder='ဥပမာ 3500' style='width:130px'></div>"
        f"<button style='font-size:13px'>စစ်မယ်</button></form>"
        f"<p style='font-size:12px;color:#888;margin:0'>ရောင်းမယ့်ဈေး = ရောင်းဈေး (buy) မြင့်လာရင် ရောက်သည်။ "
        f"ဝယ်မယ့်ဈေး = ဝယ်ဈေး (sell) နိမ့်လာရင် ရောက်သည်။</p>{tb}",
        anchor="targetbox", open_default=True)

    # 8. YoY / recent comparison
    def nearest_before(dtarget):
        best = None
        for i, d in enumerate(dates):
            if d <= dtarget:
                best = i
        return best

    from datetime import timedelta
    last_d = dates[-1]
    comp = []
    for label, delta in (("၇ ရက်", 7), ("၃၀ ရက်", 30), ("၉၀ ရက်", 90), ("၁ နှစ်", 365)):
        i = nearest_before(last_d - timedelta(days=delta))
        if i is not None and i < n - 1:
            ch = (buys[-1] - buys[i]) / buys[i] * 100
            comp.append(f"<tr><td>{label} က</td><td>{dates[i]}</td><td>{buys[i]:,.0f}</td>"
                        f"<td>{buys[-1] - buys[i]:+,.0f} ({ch:+.1f}%)</td></tr>")
    box("🔁 မကြာသေးမီ / မနှစ်က ဈေးနှင့် နှိုင်းယှဉ်",
        f"<p style='font-size:13px;color:#555;margin:0 0 8px'>လက်ရှိ {last_d} ရောင်းဈေး {buys[-1]:,.0f} နှင့် နှိုင်းယှဉ်။</p>"
        f"<div class='tscroll'><table><tr><th>ကာလ</th><th>ရက်စွဲ</th><th>ဈေး</th><th>ကွာခြင်း</th></tr>{''.join(comp)}</table></div>")

    return ("<h3 style='margin-top:24px'>🔍 ထပ်ဆောင်း ခွဲခြမ်းစိတ်ဖြာမှုများ</h3>"
            + "".join(parts)
            + "<script>(function(){"
              "if('scrollRestoration' in history)history.scrollRestoration='manual';"
              "var M={};try{M=JSON.parse(localStorage.getItem('boxOpen')||'{}')}catch(e){}"
              "document.querySelectorAll('details').forEach(function(d){var s=d.querySelector('summary');"
              "var k=s?s.textContent.trim():'';"
              "if(k&&k in M){d.open=M[k];}"
              "d.addEventListener('toggle',function(){if(k){M[k]=d.open;"
              "try{localStorage.setItem('boxOpen',JSON.stringify(M))}catch(e){}}"
              "window.dispatchEvent(new Event('resize'));});});"
              "document.addEventListener('submit',function(ev){var f=ev.target;"
              "if(!f.closest||!f.closest('#targetbox'))return;ev.preventDefault();"
              "var url=f.action.split('?')[0]+'?'+new URLSearchParams(new FormData(f)).toString();"
              "fetch(url).then(function(r){return r.text();}).then(function(h){"
              "var nb=new DOMParser().parseFromString(h,'text/html').getElementById('targetbox');"
              "var ob=document.getElementById('targetbox');"
              "if(nb&&ob){ob.innerHTML=nb.innerHTML;ob.open=true;"
              "try{history.replaceState(null,'',url);}catch(e){}}"
              "}).catch(function(){f.submit();});});"
              "if(/[?&](target|target_b)=/.test(location.search)){"
              "var t=document.getElementById('targetbox');"
              "if(t){t.open=true;t.scrollIntoView({block:'start'});}}"
              "})();</script>")


# ---------- stocks ----------
STOCK_CACHE = os.path.join(DATA_DIR, "stock_cache.json")
SEED_STOCK_CACHE = os.path.join(BASE, "seed", "stock_cache.json")
STOCKS = [
    ("^GSPC", "S&P 500", "USD", "index"),
    ("^DJI", "Dow Jones", "USD", "index"),
    ("^IXIC", "Nasdaq Composite", "USD", "index"),
    ("^N225", "Nikkei 225", "JPY", "index"),
    ("^HSI", "Hang Seng", "HKD", "index"),
    ("AAPL", "Apple", "USD", "stock"), ("MSFT", "Microsoft", "USD", "stock"),
    ("NVDA", "NVIDIA", "USD", "stock"), ("GOOGL", "Alphabet", "USD", "stock"),
    ("AMZN", "Amazon", "USD", "stock"), ("META", "Meta", "USD", "stock"),
    ("TSLA", "Tesla", "USD", "stock"), ("NFLX", "Netflix", "USD", "stock"),
    ("AMD", "AMD", "USD", "stock"), ("JPM", "JPMorgan", "USD", "stock"),
]


def _yahoo_history(ticker):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + ticker +
           "?range=2y&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = json.load(urllib.request.urlopen(req, timeout=20))
    res = raw["chart"]["result"][0]
    ts = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    out = []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        out.append([datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat(),
                    round(float(c), 2)])
    return out


def load_stock_cache():
    try:
        with open(STOCK_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        pass  # first run — no cache yet, seed fallback below is expected
    except Exception as e:
        print(f"Stock cache load failed: {e} — falling back to seed", file=sys.stderr)
    # first run on a fresh host: start from the bundled seed prices, if any
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SEED_STOCK_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
        save_stock_cache(cache)
        return cache
    except Exception:
        return {}


def save_stock_cache(cache):
    tmp = STOCK_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(tmp, STOCK_CACHE)


def get_stock_series(ticker, cache, force=False, max_age_h=6):
    ent = cache.get(ticker)
    now = datetime.now()
    if ent and not force:
        try:
            age = (now - datetime.fromisoformat(ent["fetched"])).total_seconds()
        except Exception:
            age = 1e9
        if age < max_age_h * 3600 and ent.get("rows"):
            return ent["rows"], False
    try:
        rows = _yahoo_history(ticker)
        cache[ticker] = {"fetched": now.isoformat(timespec="seconds"), "rows": rows}
        return rows, True
    except Exception as e:
        if ent and ent.get("rows"):
            return ent["rows"], False
        raise e


def stock_stats(rows):
    dates = [r[0] for r in rows]
    closes = [r[1] for r in rows]
    n = len(closes)
    cur = closes[-1]
    WIN = 252  # ကုန်သွယ်ရက် ၂၅၂ = ပြက္ခဒိန် ၁ နှစ်တိတိ

    def roll(i):
        a = max(0, i - WIN + 1)
        w = closes[a:i + 1]
        return sum(1 for v in w if v <= closes[i]) / len(w) * 100

    pcts = [roll(i) for i in range(n)]
    pct = pcts[-1]
    lo, hi = max(0.0, pct - 8.0), min(100.0, pct + 7.0)
    fwd = {}
    for h in (7, 30, 90):
        hist = [(closes[i + h] - closes[i]) / closes[i] * 100
                for i in range(n - h) if lo <= pcts[i] <= hi]
        if hist:
            fwd[h] = {"n": len(hist),
                      "avg": st.mean(hist),
                      "up": sum(1 for c in hist if c > 0) / len(hist) * 100}
        else:
            fwd[h] = {"n": 0, "avg": None, "up": None}
    dchg = ((cur - closes[-2]) / closes[-2] * 100) if n > 1 else None
    return {"dates": dates, "closes": closes, "cur": cur, "pct": pct,
            "lo": lo, "hi": hi, "fwd": fwd, "dchg": dchg, "n": n}


def stock_verdict(pct):
    if pct >= 80:
        return "မဝယ်သေးတာ ကောင်း", "#c62828", "၁ နှစ်အတွင်း အမြင့်ပိုင်း"
    if pct >= 60:
        return "စောင့်ကြည့် (မြင့်ဘက်)", "#ef6c00", "ဈေးက မြင့်ဘက်ပိုင်း"
    if pct >= 40:
        return "ပုံမှန်", "#555", "အလယ်အလတ် — အရေးတကြီး မဟုတ်"
    if pct >= 20:
        return "ဝယ်လို့ ရတယ် (နိမ့်ဘက်)", "#2e7d32", "ဈေးက နိမ့်ဘက်ပိုင်း"
    return "ဝယ်သင့်တယ် (ဈေးနိမ့်)", "#1b5e20", "၁ နှစ်အတွင်း အနိမ့်ပိုင်း"


STOCK_CSS = ("*{box-sizing:border-box}"
             "body{font-family:sans-serif;max-width:1000px;margin:20px auto;padding:0 12px;font-size:16px;line-height:1.6;overflow-x:hidden}"
             "table{border-collapse:collapse;width:100%;margin-top:10px}"
             "td,th{border:1px solid #ddd;padding:8px;text-align:center;font-size:14px}"
             ".tscroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:10px 0}"
             ".tscroll table{margin-top:0}"
             ".num{text-align:right}.badge{display:inline-block;padding:3px 10px;"
             "border-radius:12px;color:#fff;font-size:12px;white-space:nowrap}"
             ".menu a{display:inline-flex;align-items:center;justify-content:center;"
             "height:38px;padding:0 14px;border-radius:19px;border:1px solid #bbb;"
             "text-decoration:none;color:#333;font-size:13px}"
             "@media (max-width:640px){"
             "body{padding:0 10px}"
             "h2{font-size:20px;line-height:1.4}"
             "td,th{font-size:13px;padding:6px 5px}"
             ".menu a{height:44px;font-size:14px;padding:0 16px}"
             ".tscroll::after{content:'⟵ ဘေးတိုက်လှိမ့်ကြည့်နိုင်ပါတယ် ⟶';display:block;text-align:right;font-size:11px;color:#888;padding-top:2px}"
             "input,select,button{font-size:16px;min-height:44px}"
             "}")


@app.route("/stocks")
def stocks():
    force = request.args.get("refresh") == "1"
    cache = load_stock_cache()
    dirty = False
    body = ""
    errs = []
    updated = []
    for tk, nm, cur_code, kind in STOCKS:
        try:
            rows, fetched = get_stock_series(tk, cache, force)
        except Exception as e:
            body += (f"<tr><td colspan='7' style='color:#c62828'>"
                     f"{tk.lstrip('^')} — ဒေတာမရ ({type(e).__name__})</td></tr>")
            errs.append(f"{tk}: {type(e).__name__}")
            continue
        if fetched:
            dirty = True
        if not rows or len(rows) < 60:
            body += (f"<tr><td colspan='7' style='color:#999'>"
                     f"{tk.lstrip('^')} — ဒေတာမလုံလောက်</td></tr>")
            continue
        s = stock_stats(rows)
        verdict, color, why = stock_verdict(s["pct"])
        f30 = s["fwd"][30]
        f90 = s["fwd"][90]
        dchg = f"{s['dchg']:+.2f}%" if s["dchg"] is not None else "—"
        dcol = "#2e7d32" if (s["dchg"] or 0) > 0 else "#c62828"
        avg30 = f"{f30['avg']:+.2f}%" if f30["avg"] is not None else "—"
        up30 = f"{f30['up']:.0f}%" if f30["up"] is not None else "—"
        avg90 = f"{f90['avg']:+.2f}%" if f90["avg"] is not None else "—"

        def note(f, extra=""):
            if f["n"] == 0:
                return "<small style='color:#999'>နမူနာ မရှိ</small>"
            if f["n"] < 30:
                return (f"<small style='color:#ef6c00'>⚠ {extra}နမူနာ {f['n']}</small>")
            return f"<small>{extra}နမူနာ {f['n']}</small>"

        body += (f"<tr><td><b>{tk.lstrip('^')}</b><br><small>{nm}"
                 + (" <span style='font-size:11px;color:#1565c0'>INDEX</span>"
                    if kind == "index" else "") + "</small></td>"
                 f"<td class='num'>{s['cur']:,.2f}<br><small>{cur_code}</small></td>"
                 f"<td style='color:{dcol}'>{dchg}</td>"
                 f"<td><b>{s['pct']:.0f}%</b></td>"
                 f"<td class='num'>{avg30}<br>{note(f30, up30 + ' တက် · ')}</td>"
                 f"<td class='num'>{avg90}<br>{note(f90)}</td>"
                 f"<td><span class='badge' style='background:{color}'>{verdict}</span>"
                 f"<br><small style='color:#777'>{why}</small></td></tr>")
        updated.append(f"{tk.lstrip('^')} {s['dates'][-1]}")
    if dirty:
        save_stock_cache(cache)
    stamp = ""
    if updated:
        stamp = ("<p style='font-size:12px;color:#777'>နောက်ဆုံး ဈေးရက်စွဲ: "
                 + "; ".join(updated[:3]) + (" ..." if len(updated) > 3 else "") + "</p>")
    err_box = ""
    if errs:
        err_box = (f"<div style='background:#ffebee;border:1px solid #ef9a9a;"
                   f"border-radius:8px;padding:8px 12px;font-size:13px;margin:8px 0'>"
                   f"တချို့ ဒေတာ မရ: {', '.join(errs)}</div>")
    return ("<!doctype html><html lang='my'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>နာမည်ကြီး Stock ဈေး</title>"
            f"<style>{STOCK_CSS}</style></head><body>"
            "<h2>📈 နာမည်ကြီး Stock & Index — အခုဝယ်သင့်လား?</h2>"
            "<div class='menu' style='display:flex;gap:6px;flex-wrap:wrap;margin:8px 0'>"
            "<a href='/'>← ငွေဈေး စာမျက်နှာ</a>"
            "<a href='/stocks?refresh=1'>🔄 ဈေးအသစ် ဆွဲ</a></div>"
            "<p style='font-size:13px;color:#555'>"
            "Percentile = ဒီနေ့ဈေးက ပြီးခဲ့ ၁ နှစ်တိတိ (ကုန်သွယ်ရက် ၂၅၂ ရက်) အတွင်း "
            "ဈေးတွေထက် ဘယ်နှရာခိုင်နှုန်း မြင့်နေလဲ။ "
            "နိမ့်ရင် ဝယ်ရ အခွင့်အလမ်းကောင်း၊ မြင့်ရင် စောင့်ရ။ "
            "နောက် ၃၀/၉၀ ရက် ပျမ်းမျှဆိုတာ အလားတူ percentile ရှိခဲ့တဲ့ ရက်တွေမှာ "
            "ဈေး ဘယ်လောက် ရွေ့ခဲ့လဲ (သမိုင်း)။ "
            "<span style='color:#ef6c00'>⚠ = နမူနာ ၃၀ ရက်အောက် — မယုံနဲ့၊ ခန့်မှန်းချေသာ</span>။</p>"
            f"{err_box}{stamp}"
            "<div class='tscroll'><table><tr><th>Stock / Index</th><th>ဈေး</th>"
            "<th>မနေ့ကနေ ပြောင်း</th><th>၁ နှစ် Percentile</th>"
            "<th>နောက် ၃၀ ရက် ပျမ်းမျှ</th><th>နောက် ၉၀ ရက် ပျမ်းမျှ</th>"
            "<th>အကြံပြုချက်</th></tr>"
            f"{body}</table></div>"
            "<p style='font-size:12px;color:#888;margin-top:14px'>"
            "ဒါက စာရင်းအင်း (percentile + သမိုင်းရွေ့လျားမှု) အပေါ် အခြေခံတဲ့ သတိပေးချက်သာ ဖြစ်တယ်။ "
            "ရင်းနှီးမြှုပ်နှံ အကြံပြုချက် မဟုတ်ပါ။ ဈေးဒေတာ — Yahoo Finance (နောက်ကျချိန် ~15 မိနစ်)။"
            "</p></body></html>")


# ---------- news (Google News RSS — အခမဲ့၊ key မလို) ----------
# မြန်မာ ငွေဈေး / ဗဟိုဘဏ် / ရွှေဈေး သတင်း ခေါင်းစဉ်တွေကို Google News RSS ကနေ
# server-side ဆွဲယူတယ်။ သတင်း အတု မဖန်တီးဘူး — feed ပျက်ရင် [] ပဲ ပြန်ပေးတယ်။
NEWS_FEEDS = {
    "currency": ("https://news.google.com/rss/search"
                 "?q=Myanmar+kyat+OR+Myanmar+exchange+rate+OR+Myanmar+central+bank+OR+Myanmar+inflation"
                 "&hl=en-US&gl=US&ceid=US:en"),
    "gold": ("https://news.google.com/rss/search"
             "?q=Myanmar+gold+price+OR+Myanmar+gold+rate"
             "&hl=en-US&gl=US&ceid=US:en"),
}
NEWS_TTL = 3 * 3600  # ၃ နာရီ cache — စာမျက်နှာ မြန်မြန်ပွင့်ဖို့
NEWS_MAX_DAYS = 90   # ဒီရက်အတွင်းက သတင်းပဲ ပြတယ် (အဟောင်း မပြဘူး)
NEWS_MAX_ITEMS = 5
# Google News ရဲ့ OR query က တခါတလေ မဆိုင်တဲ့ သတင်း ပါလာတတ်လို့
# ခေါင်းစဉ်မှာ ဒီစကားလုံးတွေ ပါမှ ပြတယ် (ဥပမာ Myanmar အကြောင်း ယေဘုယျ သတင်း မပါဘူး)
NEWS_KEYWORDS = {
    "currency": ("kyat", "dollar", "exchange", "central bank", "inflat", "currenc"),
    "gold": ("gold",),
}
_NEWS_CACHE = {}  # kind -> {"at": epoch, "items": [...]}


def fetch_currency_news(kind="currency"):
    """Google News RSS ကနေ သတင်းခေါင်းစဉ် (title + link + ရက်စွဲ + source) ယူတယ်.
    feed ပျက်ရင် / သတင်းအသစ် မရှိရင် [] ပြန်ပေးတယ် — ဘာမှ အတု မထည့်ဘူး."""
    now = time.time()
    hit = _NEWS_CACHE.get(kind)
    if hit and now - hit["at"] < NEWS_TTL:
        return hit["items"]
    items = []
    try:
        req = urllib.request.Request(
            NEWS_FEEDS.get(kind) or NEWS_FEEDS["currency"],
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
        root = ET.fromstring(data)
        cutoff = datetime.now(timezone.utc) - timedelta(days=NEWS_MAX_DAYS)
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub = (it.findtext("pubDate") or "").strip()
            source = (it.findtext("source") or "").strip()
            if not title or not link:
                continue
            _keys = NEWS_KEYWORDS.get(kind) or NEWS_KEYWORDS["currency"]
            if not any(k in title.lower() for k in _keys):
                continue  # မဆိုင်တဲ့ သတင်း (ဥပမာ Myanmar ဆိုရုံနဲ့ ပါလာတာ) ကို ဖြတ်တယ်
            # Google News title မှာ " - Source" ပါတတ်တယ် — source သီးသန့် ပြမှာမို့ ဖြတ်တယ်
            if source and title.endswith(" - " + source):
                title = title[: -len(" - " + source)].strip()
            try:
                dt = parsedate_to_datetime(pub) if pub else None
            except Exception:
                dt = None
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < cutoff:
                    continue
                date_txt = dt.strftime("%d %b %Y")
            else:
                date_txt = ""
            items.append({"title": title, "link": link,
                          "date": date_txt, "source": source})
            if len(items) >= NEWS_MAX_ITEMS:
                break
    except Exception as e:
        print(f"News fetch failed ({kind}): {e}", file=sys.stderr)
        items = []
    _NEWS_CACHE[kind] = {"at": now, "items": items}
    return items


# ---------- routes ----------
@app.route("/")
def index():
    asset = (request.args.get("asset") or "sgd").lower()
    if asset not in ASSETS:
        asset = "sgd"
    a = asset_info(asset)

    day_param = (request.args.get("day") or "").strip()
    day_parsed = _parse_date(day_param) if day_param else None
    try:
        year = int(request.args.get("year", date.today().year))
    except ValueError:
        year = date.today().year
    if day_parsed:
        year = day_parsed.year

    years = available_years(asset)
    if year not in years:
        years = sorted(set(years + [year]), reverse=True)
    rows = read_rows(asset, year)
    all_rows = read_rows(asset)
    min_day = all_rows[0]["date"] if all_rows else ""
    target = (request.args.get("target") or "").strip()
    target_b = (request.args.get("target_b") or "").strip()
    analytics_html = build_analytics(all_rows, a, target or None, asset, target_b or None)

    monthly = {}
    for r in rows:
        monthly.setdefault(r["_date"].month, []).append(r["buy"])
    mavgs = {m: sum(v) / len(v) for m, v in monthly.items()}
    yavg = (sum(r["buy"] for r in rows) / len(rows)) if rows else 0
    sell_yavg = (sum(r["sell"] for r in rows) / len(rows)) if rows else 0

    day_box = ""
    if day_parsed:
        dstr = day_parsed.isoformat()
        found = next((r for r in all_rows if r["date"] == dstr), None)
        if found:
            diff_txt = ""
            if yavg:
                db = found["buy"] - yavg
                ds_ = found["sell"] - sell_yavg
                diff_txt = (f"<div style='font-size:12px;color:#666;margin-top:6px'>"
                            f"{year} ပျမ်းမျှနှင့် — ရောင်းဈေး {db:+,.0f} ({db / yavg * 100:+.1f}%) · "
                            f"ဝယ်ဈေး {ds_:+,.0f} ({ds_ / sell_yavg * 100:+.1f}%)</div>")
            day_box = f"""
<div style="background:#fffde7;border:2px solid #fbc02d;border-radius:12px;padding:14px;margin:12px 0;">
  <div style="font-size:13px;color:#777;">📅 ရွေးထားတဲ့ ရက်စွဲ ဈေးနှုန်း</div>
  <div style="font-size:20px;font-weight:bold;margin:4px 0;">{dstr} — {a['flag']}{a['short']}</div>
  <div style="font-size:16px;">ရောင်းဈေး: <b>{found['buy']:,.0f} {a['unit_mm']}</b> &nbsp;|&nbsp;
       ဝယ်ဈေး: <b>{found['sell']:,.0f} {a['unit_mm']}</b></div>
  <div style="font-size:12px;color:#888;margin-top:4px">note: {found.get('note', '')}</div>
  {diff_txt}
</div>"""
        else:
            prev_row = None
            next_row = None
            for r in all_rows:
                if r["date"] < dstr:
                    prev_row = r
                elif r["date"] > dstr and next_row is None:
                    next_row = r
            near = []
            if prev_row:
                near.append(f"အရှေ့ {prev_row['date']}: {prev_row['buy']:,.0f}/{prev_row['sell']:,.0f}")
            if next_row:
                near.append(f"နောက် {next_row['date']}: {next_row['buy']:,.0f}/{next_row['sell']:,.0f}")
            near_txt = " · ".join(near) if near else "အနီးအနား ဒေတာလည်း မရှိပါ"
            day_box = f"""
<div style="background:#ffebee;border:2px solid #e57373;border-radius:12px;padding:14px;margin:12px 0;">
  <div style="font-size:13px;color:#777;">📅 ရွေးထားတဲ့ ရက်စွဲ ဈေးနှုန်း</div>
  <div style="font-size:18px;font-weight:bold;margin:4px 0;">{dstr} — {a['flag']}{a['short']}</div>
  <div style="font-size:15px;color:#c62828;">ဒီရက်အတွက် ဈေးဒေတာ မရှိပါ။</div>
  <div style="font-size:13px;color:#555;margin-top:6px">အနီးဆုံး — {near_txt}</div>
</div>"""

    table = ""
    for m in range(1, 13):
        if m in mavgs:
            diff = mavgs[m] - yavg
            pct = diff / yavg * 100 if yavg else 0
            badge = ("<span style='color:#fff;background:#d32f2f;padding:2px 10px;border-radius:10px'>▲ ပျမ်းမျှထက်တက်</span>"
                     if diff > 0 else
                     "<span style='color:#fff;background:#2e7d32;padding:2px 10px;border-radius:10px'>▼ ပျမ်းမျှထက်ကျ</span>")
            table += (f"<tr><td>{MONTH_MM[m]}</td><td>{mavgs[m]:,.0f}</td>"
                      f"<td>{len(monthly[m])} ရက်</td>"
                      f"<td>{diff:+,.0f} ({pct:+.1f}%)</td><td>{badge}</td></tr>")
        else:
            table += (f"<tr style='color:#999'><td>{MONTH_MM[m]}</td><td>—</td>"
                      f"<td>0</td><td>—</td><td>ဒေတာမရှိ</td></tr>")

    labels = [MONTH_MM[m] for m in range(1, 13)]
    vals = [round(mavgs[m], 1) if m in mavgs else None for m in range(1, 13)]
    colors = (["#d32f2f" if (m in mavgs and mavgs[m] > yavg)
               else ("#2e7d32" if m in mavgs else "#ccc")
               for m in range(1, 13)])
    vals_js = "[" + ",".join("null" if v is None else str(v) for v in vals) + "]"

    year_opts = "".join(
        f"<option value='{y}' {'selected' if y == year else ''}>{y}</option>" for y in years)

    recent = "".join(
        f"<tr><td>{r['date']}</td><td>{r['buy']:,.0f}</td><td>{r['sell']:,.0f}</td>"
        f"<td>{r.get('note', '')}</td></tr>"
        for r in rows[-10:][::-1])

    rec = calculate_recommendation(asset)
    rec_box = ""
    if rec:
        _fmt = lambda v: f"{v:+.1f}%" if v is not None else "—"
        trend_box = f"""
<div style="font-size:13px;line-height:1.7;background:#fff8e1;border:1px solid #ffe082;border-radius:8px;padding:10px 12px;margin:8px 0;">
  <b>📈 လမ်းကြောင်း (၃ လ / ၆ လ အရွေ့):</b> ဝယ်ဈေး {_fmt(rec['chg3m_s'])} / {_fmt(rec['chg6m_s'])} · ရောင်းဈေး {_fmt(rec['chg3m_b'])} / {_fmt(rec['chg6m_b'])}<br>
  <b>💱 Spread:</b> {rec['spread']:,.0f} {rec['unit']} ({rec['spread_pct']:.2f}%) — ပြန်ရောင်းပြီး အမြတ်ရဖို့ ရောင်းဈေးက <b>{rec['breakeven']:,.0f}</b> အထိ တက်ရမယ် (ဈေး {rec['spread_pct']:.2f}% ထက် မတက်ရင် အမြတ် မကျန်ဘူး)<br>
  <b>📊 ၆ လ Percentile:</b> ရောင်းသူ {rec['pct6m_b']:.1f}% · ဝယ်သူ {rec['pct6m_s']:.1f}% (၁ နှစ် percentile နဲ့ ယှဉ်ကြည့်ပါ — ၆ လအတွင်း ဈေးက ဒီအဆင့်မှာပဲ ရှိနေရင် ဈေးဟောင်း ပြန်မရနိုင်ဘူး)
</div>"""
        notes_box = ("".join(f"<div style='font-size:13px;line-height:1.6;background:#fff3e0;border:1px solid #ffcc80;border-radius:8px;padding:10px 12px;margin:8px 0;'>{n}</div>" for n in rec['notes'])) if rec['notes'] else ""
        rec_box = f"""
<div style="background:#fafafa;border:2px solid #2196f3;border-radius:12px;padding:16px;margin:15px 0;">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;border-bottom:1px solid #e0e0e0;padding-bottom:8px;">
    <h3 style="margin:0;color:#1565c0;">💡 ဒီနေ့အတွက် {rec['code']} အရောင်း/အဝယ် ဆုံးဖြတ်ချက် အကြံပြုချက်</h3>
    <small style="color:#666;">နေ့စွဲ: <b>{rec['date']}</b> | စုစုပေါင်း သမိုင်းဒေတာ: <b>{rec['total_days']:,}</b> ရက်</small>
  </div>
  <div style="display:flex;gap:12px;flex-wrap:wrap;margin:12px 0;">
    <div style="flex:1;min-width:240px;padding:12px;border-radius:8px;background:{rec['seller_bg']};border:1px solid {rec['seller_color']};">
      <div style="font-size:13px;color:#555;">{rec['seller_tag']}</div>
      <div style="font-size:18px;font-weight:bold;color:{rec['seller_color']};margin:6px 0;">{rec['seller_action']}</div>
      <div style="font-size:13px;">လက်ရှိရောင်းဈေး: <b>{rec['buy']:,.0f} {rec['unit']}</b></div>
    </div>
    <div style="flex:1;min-width:240px;padding:12px;border-radius:8px;background:{rec['buyer_bg']};border:1px solid {rec['buyer_color']};">
      <div style="font-size:13px;color:#555;">{rec['buyer_tag']}</div>
      <div style="font-size:18px;font-weight:bold;color:{rec['buyer_color']};margin:6px 0;">{rec['buyer_action']}</div>
      <div style="font-size:13px;">လက်ရှိဝယ်ဈေး: <b>{rec['sell']:,.0f} {rec['unit']}</b></div>
    </div>
  </div>
  <p style="margin:8px 0 6px 0;line-height:1.5;color:#333;"><b>သုံးသပ်ချက်:</b> {rec['summary']}</p>
  {trend_box}
  {notes_box}
  <div style="font-size:12px;color:#555;background:#fff;padding:8px 12px;border-radius:6px;border:1px solid #eee;">
    <div class="tscroll wide"><table style="margin:0;font-size:12px;">
      <tr>
        <th style="text-align:left">ကိန်းဂဏန်း</th>
        <th>ရောင်းသူ<br><small>(သင် {a['short']} ရောင်း → ရမည့်ဈေး)</small></th>
        <th>ဝယ်သူ<br><small>(သင် MMK ပေး → {a['short']} ဝယ်ရမည့်ဈေး)</small></th>
      </tr>
      <tr><td style="text-align:left">၇ ရက် ပျမ်းမျှ</td>
          <td>{rec['seller_stats']['ma7']:,.0f}</td>
          <td>{rec['buyer_stats']['ma7']:,.0f}</td></tr>
      <tr><td style="text-align:left">၃၀ ရက် ပျမ်းမျှ</td>
          <td>{rec['seller_stats']['ma30']:,.0f}</td>
          <td>{rec['buyer_stats']['ma30']:,.0f}</td></tr>
      <tr><td style="text-align:left">တစ်နှစ်တာ (၃၆၅ ရက်) အနိမ့် - အမြင့်</td>
          <td>{rec['seller_stats']['min_year']:,.0f} - {rec['seller_stats']['max_year']:,.0f}</td>
          <td>{rec['buyer_stats']['min_year']:,.0f} - {rec['buyer_stats']['max_year']:,.0f}</td></tr>
      <tr><td style="text-align:left">တစ်နှစ်တာ ပျမ်းမျှ (၃၆၅ ရက်)</td>
          <td>{rec['seller_stats']['avg_year']:,.0f}</td>
          <td>{rec['buyer_stats']['avg_year']:,.0f}</td></tr>
      <tr><td style="text-align:left">တစ်နှစ်တာ Percentile (၃၆၅ ရက်)</td>
          <td>{rec['seller_stats']['pct_year']:.1f}%</td>
          <td>{rec['buyer_stats']['pct_year']:.1f}%</td></tr>
      <tr><td style="text-align:left">၆ လ Percentile (၁၈၀ ရက်)</td>
          <td>{rec['pct6m_b']:.1f}%</td>
          <td>{rec['pct6m_s']:.1f}%</td></tr>
    </table></div>
  </div>
</div>
"""

    # ---- နောက်ဆုံး သတင်း (stock လိုမျိုး ခေါင်းစဉ်တွေ) ----
    news_kind = "gold" if a["kind"] == "gold" else "currency"
    news_items = fetch_currency_news(news_kind)
    if news_items:
        _rows = "".join(
            f"<li style='margin:6px 0;line-height:1.5;'><a href='{html.escape(n['link'], quote=True)}' target='_blank' rel='noopener'>{html.escape(n['title'])}</a>"
            f"<br><small style='color:#777;'>{html.escape(n['source'])}{(' &middot; ' + n['date']) if n['date'] else ''}</small></li>"
            for n in news_items)
        news_box = f"""
<div style="background:#fff;border:2px solid #4caf50;border-radius:12px;padding:14px 16px;margin:15px 0;">
  <h3 style="margin:0 0 4px 0;color:#2e7d32;">&#128240; နောက်ဆုံး သတင်း — ငွေဈေး / ဗဟိုဘဏ်</h3>
  <div style="font-size:12px;color:#777;margin-bottom:6px;">Google News က ဆွဲယူထားတာ (ခေါင်းစဉ်ပဲ ပြထားတယ် — အသေးစိတ်ဖတ်ဖို့ ခေါင်းစဉ်ကို နှိပ်ပါ) &middot; ၃ နာရီတစ်ခါ အသစ်ပြန်ဆွဲတယ်</div>
  <ul style="margin:0;padding-left:18px;">{_rows}</ul>
  <div style="font-size:12px;color:#555;margin-top:8px;">&#9888;&#65039; သတင်းက အကြောင်းရင်း (ယာယီလား၊ ရေရှည်လား) ကို ဆုံးဖြတ်ဖို့ အကူအညီပဲ — ခေါင်းစဉ်တစ်ခုတည်းနဲ့ ဝယ်ဖို့ မဆုံးဖြတ်ပါနဲ့။</div>
</div>"""
    else:
        news_box = """
<div style="background:#fff;border:1px solid #ccc;border-radius:12px;padding:14px 16px;margin:15px 0;">
  <h3 style="margin:0;color:#555;">&#128240; နောက်ဆုံး သတင်း</h3>
  <div style="font-size:13px;color:#777;margin-top:6px;">သတင်း မရသေးဘူး — feed ပျက်နေတာ ဒါမှမဟုတ် သတင်းအသစ် မရှိသေးတာပါ။ နောက်မှ ပြန်စစ်ကြည့်ပါ။</div>
</div>"""

    msg = request.args.get("msg")
    banner = (f"<div style='background:#e3f2fd;border:1px solid #90caf9;padding:8px 12px;"
              f"border-radius:8px;margin:10px 0;font-size:14px'>{msg}</div>" if msg else "")

    unit = a["unit_mm"]
    return f"""<!doctype html><html lang="my"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{a['name']}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>*{{box-sizing:border-box}}
body{{font-family:sans-serif;max-width:900px;margin:20px auto;padding:0 12px;font-size:16px;line-height:1.6;overflow-x:hidden}}
.cards{{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}}
.card{{flex:1;min-width:180px;border:1px solid #ddd;border-radius:10px;padding:12px}}
table{{border-collapse:collapse;width:100%;margin-top:10px}}td,th{{border:1px solid #ddd;padding:8px;text-align:center}}
.tscroll{{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:10px 0}}
.tscroll table{{margin-top:0}}
.tscroll.wide table{{min-width:560px}}
.tscroll.xwide table{{min-width:680px}}
.btn{{display:inline-block;padding:8px 14px;border-radius:8px;border:1px solid #333;text-decoration:none;color:#000;margin:4px 4px 4px 0}}
.btn.primary{{background:#111;color:#fff}}
.menu{{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 4px 0}}
.menu a{{display:inline-flex;align-items:center;justify-content:center;height:40px;width:118px;box-sizing:border-box;text-align:center;padding:0 6px;line-height:1.1;border-radius:20px;border:1px solid #bbb;text-decoration:none;color:#333;font-size:13px;white-space:nowrap}}
.menu a.on{{background:#1565c0;color:#fff;border-color:#1565c0}}
details{{margin:8px 0}}details summary{{list-style:none}}details summary::-webkit-details-marker{{display:none}}
details summary::after{{content:' ▾';color:#888}}details[open] summary::after{{content:' ▴'}}
#band{{max-height:260px}}
@media (max-width:640px){{
body{{padding:0 10px}}
h2{{font-size:20px;line-height:1.4}}h3{{font-size:17px;line-height:1.4}}
td,th{{padding:6px 5px;font-size:13px}}
.menu a{{height:44px;width:auto;flex:1 1 30%;min-width:96px;font-size:14px}}
.btn{{padding:12px 16px;font-size:15px;min-height:44px}}
.card{{flex:1 1 100%;min-width:0}}
details summary{{font-size:16px}}
input,select,button{{font-size:16px;min-height:44px}}
.tscroll::after{{content:'⟵ ဘေးတိုက်လှိမ့်ကြည့်နိုင်ပါတယ် ⟶';display:block;text-align:right;font-size:11px;color:#888;padding-top:2px}}
canvas{{max-width:100%}}
}}</style></head><body>
<h2>{a['flag']}{a['name']} — {year} : နှစ်ပျမ်းမျှထက် ဘယ်လတက်/ကျ?</h2>

<div class="menu">
{''.join(f"<a class='{'on' if k == asset else ''}' href='/?asset={k}&year={year}'>{ASSETS[k]['flag']}{ASSETS[k]['short']}</a>" for k in ASSET_ORDER)}
<a href='/stocks'>📈 Stock</a>
</div>
<div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-top:8px;">
  <form method="get" style="margin:0;">
    <input type="hidden" name="asset" value="{asset}">
    နှစ်: <select name="year" onchange="this.form.submit()">{year_opts}</select>
  </form>
  <form method="get" style="margin:0;">
    <input type="hidden" name="asset" value="{asset}">
    📅 ရက်စွဲ: <input type="date" name="day" value="{day_param}" min="{min_day}" max="{date.today().isoformat()}" onchange="this.form.submit()">
  </form>
  {f"<a href='/?asset={asset}&year={year}' style='font-size:12px;color:#666;text-decoration:none;border:1px solid #ddd;border-radius:12px;padding:3px 10px;background:#fafafa'>✕ ရက်စွဲ ရှင်းလင်း</a>" if day_parsed else ""}
</div>

{day_box}

{banner}
{rec_box}
{news_box}

<div class="cards">
<div class="card"><b>{year} နှစ်ပျမ်းမျှ</b><br>ရောင်းဈေး: <span style="font-size:20px">{yavg:,.0f}</span><br>ဝယ်ဈေး: <span style="font-size:20px">{sell_yavg:,.0f}</span><br><small>{len(rows)} ရက်ဒေတာအပေါ်အခြေခံ</small></div>
<div class="card"><b>နေ့စဉ်ဈေးဖြည့်</b><br>
<a class="btn primary" href="/fetch?asset={asset}&year={year}">⚡ ဒီနေ့ဈေး ယူ (MMP)</a>
<a class="btn" href="/fetch?asset=all&cur={asset}&year={year}" style="margin-top:6px">⚡ အားလုံး ယူ (All)</a></div>
<div class="card"><b>ကိုယ်တိုင်မှတ်</b>
<form method="post" action="/add">
<input type="hidden" name="asset" value="{asset}">
<input type="date" name="d" value="{date.today().isoformat()}" required><br><br>
<input name="buy" placeholder="ရောင်းဈေး ({unit})" required style="width:calc(50% - 4px);max-width:160px">
<input name="sell" placeholder="ဝယ်ဈေး" style="width:calc(50% - 4px);max-width:120px">
<button style="min-height:44px;padding:8px 18px;margin-top:8px">သိမ်း</button></form></div></div>
<canvas id="c" height="110"></canvas>
<script>new Chart(document.getElementById('c'),{{type:'bar',
data:{{labels:{labels},datasets:[{{data:{vals_js},backgroundColor:{colors}}},
{{type:'line',label:'နှစ်ပျမ်းမျှ',data:Array(12).fill({round(yavg,1)}),borderColor:'#111',pointRadius:0}}]}},
options:{{plugins:{{legend:{{display:false}}}}}}}});</script>
<h3>လအလိုက် (ရောင်းဈေး အခြေခံ — {unit})</h3>
<div class="tscroll wide"><table><tr><th>လ</th><th>လပျမ်းမျှ</th><th>မှတ်တမ်းရက်</th><th>နှစ်ပျမ်းမျှနှင့်ကွာခြားချက်</th><th>အခြေအနေ</th></tr>{table}</table></div>
<h3>နောက်ဆုံးမှတ်တမ်းများ</h3>
<div class="tscroll"><table><tr><th>နေ့</th><th>ရောင်းဈေး</th><th>ဝယ်ဈေး</th><th>note</th></tr>{recent or '<tr><td colspan=4>ဒေတာမရှိသေး — ဈေးယူ ကိုနှိပ်ပါ</td></tr>'}</table></div>
{analytics_html}
<p><small>ရောင်းဈေး = သင် {a['short']} ရောင်းလျှင် ရမည့်ဈေး။ ဝယ်ဈေး = သင် MMK ပေး၍ {a['short']} ဝယ်လျှင် ပေးရမည့်ဈေး။
Source: Myanmar Market Price (MMP) API — rates_assets.csv (Vercel Blob) ထဲသိမ်းသည်။</small></p>
</body></html>"""


@app.route("/cron/fetch-daily")
def cron_fetch_daily():
    """Daily automated cron job: fetches all currency/gold rates and persists to Vercel Blob."""
    ensure_csv()
    updated = []
    errs = []
    for k in ASSET_ORDER:
        try:
            buy, sell, note, d = fetch_live(k)
            upsert_day(k, d, round(buy, 2), round(sell, 2), note)
            updated.append(f"{k}: {d} {buy:,.0f}/{sell:,.0f} ({note})")
        except Exception as e:
            errs.append(f"{k}: {e}")
    synced = _blob_sync_up()

    # Browser UI မှ ခေါ်ပါက JSON မပြဘဲ မူရင်းစာမျက်နှာဆီ redirect လုပ်ပေးသည်
    if request.args.get("ui") or "text/html" in request.headers.get("Accept", ""):
        cur_asset = request.args.get("cur") or "sgd"
        cur_year = request.args.get("year", date.today().year)
        msg = f"✓ အားလုံး (၇ မျိုး) ဒေတာ အသစ် ရယူပြီးပါပြီ (Vercel Blob ထဲ အမြဲတမ်း သိမ်းပြီး)"
        return redirect(url_for("index", asset=cur_asset, year=cur_year, msg=msg))

    return {
        "ok": True,
        "date": date.today().isoformat(),
        "updated": updated,
        "errors": errs,
        "blob_synced": synced,
    }


@app.route("/fetch")
def do_fetch():
    asset = request.args.get("asset", "sgd")
    year = request.args.get("year", date.today().year)
    cur = request.args.get("cur") or asset
    if asset == "all":
        ensure_csv()
        updated = []
        failed = []
        for k in ASSET_ORDER:
            try:
                buy, sell, note, d = fetch_live(k)
                upsert_day(k, d, round(buy, 2), round(sell, 2), note)
                updated.append(k)
            except Exception as e:
                failed.append(f"{k} ({e})")
                print(f"Fetch failed ({k}): {e}", file=sys.stderr)
        _blob_sync_up()
        if not failed:
            msg = f"✓ အားလုံး ({len(updated)} မျိုး) ဒေတာ အသစ် ရယူပြီးပါပြီ (Vercel Blob ထဲ အမြဲတမ်း သိမ်းပြီး)"
        elif updated:
            msg = (
                f"⚠ {len(updated)} မျိုး ရပြီး၊ {len(failed)} မျိုး မရပါ: "
                + ", ".join(failed)
            )
        else:
            msg = "✗ ဒေတာ တစ်ခုမှ မရပါ: " + ", ".join(failed)
        return redirect(url_for("index", asset=cur if cur in ASSETS else "sgd", year=year, msg=msg))

    if asset not in ASSETS:
        asset = "sgd"
    try:
        buy, sell, note, d = fetch_live(asset)
        upsert_day(asset, d, round(buy, 2), round(sell, 2), note)
        msg = f"ok {d} {asset.upper()} {buy:,.0f}/{sell:,.0f}"
    except Exception as e:
        msg = f"error {e}"
    return redirect(url_for("index", asset=asset, year=year, msg=msg))


@app.route("/add", methods=["POST"])
def do_add():
    asset = (request.form.get("asset") or "sgd").lower()
    if asset not in ASSETS:
        asset = "sgd"
    d = request.form.get("d") or date.today().isoformat()
    try:
        buy = float(request.form.get("buy", 0))
        sell = float(request.form.get("sell", 0) or buy)
    except ValueError:
        return redirect(url_for("index", asset=asset, year=d[:4]))
    upsert_day(asset, d, buy, sell, "manual")
    return redirect(url_for("index", asset=asset, year=d[:4]))


if __name__ == "__main__":
    ensure_csv()
    for _k in ASSET_ORDER:
        try:
            _b, _s, _n, _d = fetch_live(_k)
            upsert_day(_k, _d, round(_b, 2), round(_s, 2), _n)
            print(f"Today sync {_k}: {_d} buy {_b:,.0f} / sell {_s:,.0f} ({_n})")
        except Exception as _e:
            print(f"Today sync {_k} skipped: {_e}")
    print("Open: http://127.0.0.1:5000")
    app.run(debug=False, port=5000)
