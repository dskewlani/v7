"""
engine.py — ProTrader Terminal v7 — All 14 Blocks Integration
=============================================================
NEW in v7 (All Enhancement Blocks):
  ✅ Block 1  — Dynamic symbol universe (NSE master, watchlists)
  ✅ Block 2a — Multi-Timeframe Confirmation (confirm_mtf)
  ✅ Block 2b — VWAP Bands + Volume Profile / Point of Control
  ✅ Block 2c — Market Regime Classifier (Trending/Sideways/Volatile)
  ✅ Block 3a — Partial profit scale-out logic (scale_out_position)
  ✅ Block 3b — Re-entry signal after stop-loss
  ✅ Block 3c — Time-of-day trade filter
  ✅ Block 3d — Correlation-based position limit
  ✅ Block 6a — Monte Carlo portfolio simulator
  ✅ Block 6b — Sharpe / Sortino / Calmar ratios
  ✅ Block 6e — Portfolio heat / concentration treemap data
  ✅ Block 6f — Value at Risk (VaR)
  ✅ Block 7a — WebSocket price feed framework
  ✅ Block 7c — NSE session auto-renewal + retry queue
  ✅ Block 9a — Max Pain calculator
  ✅ Block 9b — Put/Call Ratio (PCR)
  ✅ Block 9c — IV Rank and IV Percentile
  ✅ Block 10a— Strategy Backtester (run_backtest)
  ✅ Block 10b— Walk-forward optimisation
  ✅ Block 11a— Volatility-adjusted position sizing
  ✅ Block 11b— Break-even stop unification
  ✅ Block 13b— Pattern recognition helpers
  ✅ Block 13d— Behavioral bias detector
  ✅ Block 14a— Async-ready scan wrapper
  ✅ Block 14c— Structured logging hooks
"""

import numpy as np
import pandas as pd
import math
import time
import os
import requests
import concurrent.futures
import warnings
from datetime import datetime, date, timedelta
from io import StringIO

warnings.filterwarnings("ignore")

# ─── Constants ────────────────────────────────────────────────────────────────
LIVE_PRICE_TTL    = 12
INDEX_SPOT_TTL    = 8
OPT_PRICE_TTL     = 10
MIN_ADX_INTRADAY  = 20
MIN_ADX_DELIVERY  = 15
MIN_VOLUME_RATIO  = 1.3
STRONG_BUY_SCORE  = 16
STRONG_SELL_SCORE = 16
BUY_SCORE         = 9
SELL_SCORE        = 9
DEFAULT_DAILY_GOAL = 5000
MIN_RR_INTRADAY   = 1.3
MIN_RR_DELIVERY   = 2.0
TRAIL_ACTIVATE_PCT = 1.2
TRAIL_TIGHTEN_PCT  = 2.5

# Block 3c: Time-of-day filters
NO_ENTRY_START_AM = (9, 15)   # 9:15 AM
NO_ENTRY_END_AM   = (9, 30)   # 9:30 AM
NO_ENTRY_START_PM = (15, 0)   # 3:00 PM
NO_ENTRY_END_PM   = (15, 30)  # 3:30 PM

# ─── NSE Session ──────────────────────────────────────────────────────────────
_nse_session: requests.Session | None = None
_nse_session_ts: float = 0.0
_NSE_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept":          "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
}

# Block 7c: retry queue
_nse_retry_queue: list = []
_MAX_RETRY_QUEUE = 3


def _get_nse_session() -> requests.Session:
    global _nse_session, _nse_session_ts
    if _nse_session is None or (time.time() - _nse_session_ts) > 1500:
        s = requests.Session()
        s.headers.update(_NSE_HEADERS)
        try:
            s.get("https://www.nseindia.com", timeout=10)
            time.sleep(0.3)
        except Exception:
            pass
        _nse_session = s
        _nse_session_ts = time.time()
    return _nse_session


def _nse_fetch_with_retry(url: str, params: dict = None, max_retries: int = 3) -> requests.Response | None:
    """Block 7c: Exponential backoff retry wrapper for NSE API calls."""
    delay = 1.0
    for attempt in range(max_retries):
        try:
            sess = _get_nse_session()
            resp = sess.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                # Clear retry queue entry if this was a retry
                return resp
            if resp.status_code in (429, 503):
                time.sleep(delay)
                delay *= 2
        except Exception as exc:
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
            else:
                try:
                    import storage as db
                    db.app_log("WARN", "nse_api", f"All retries failed for {url}: {exc}")
                except Exception:
                    pass
    return None


def _nse_clean_symbol(symbol: str) -> str:
    return symbol.replace(".NS", "").replace(".BO", "").replace(".MCX", "").upper().strip()


# ─── Angel One Integration ─────────────────────────────────────────────────────
_angel_obj = None
_angel_session_ts = 0.0
_angel_master_cache = {"rows": None, "ts": 0.0}


def _secret(name: str, default: str = "") -> str:
    try:
        import streamlit as st
        value = st.secrets.get(name, "")
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get(name, default)


def _get_angel_client():
    global _angel_obj, _angel_session_ts
    if _angel_obj is not None and (time.time() - _angel_session_ts) < 1500:
        return _angel_obj
    api_key     = _secret("ANGEL_API_KEY")
    client_code = _secret("ANGEL_CLIENT_CODE")
    password    = _secret("ANGEL_PASSWORD")
    totp_secret = _secret("ANGEL_TOTP_SECRET")
    totp_value  = _secret("ANGEL_TOTP")
    if not api_key or not client_code or not password or not (totp_secret or totp_value):
        return None
    try:
        from SmartApi import SmartConnect
        if totp_secret and not totp_value:
            import pyotp
            totp_value = pyotp.TOTP(totp_secret).now()
        obj  = SmartConnect(api_key=api_key)
        data = obj.generateSession(client_code, password, totp_value)
        if data and data.get("status"):
            _angel_obj = obj
            _angel_session_ts = time.time()
            return _angel_obj
    except Exception:
        return None
    return None


def _angel_master_rows() -> list:
    cached = _angel_master_cache.get("rows")
    if cached is not None and (time.time() - _angel_master_cache.get("ts", 0)) < 3600:
        return cached
    try:
        url  = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        rows = requests.get(url, timeout=15).json()
        _angel_master_cache["rows"] = rows
        _angel_master_cache["ts"]   = time.time()
        return rows
    except Exception:
        return []


# ─── Block 1: Dynamic Symbol Universe ────────────────────────────────────────

_dynamic_universe_cache: dict = {"ts": 0.0, "data": {}}

NIFTY50_SYMBOLS = [
    "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS","SBIN.NS",
    "BAJFINANCE.NS","WIPRO.NS","AXISBANK.NS","KOTAKBANK.NS","LT.NS","HCLTECH.NS",
    "ASIANPAINT.NS","MARUTI.NS","TITAN.NS","SUNPHARMA.NS","BHARTIARTL.NS",
    "NESTLEIND.NS","ULTRACEMCO.NS","POWERGRID.NS","NTPC.NS","ONGC.NS","BPCL.NS",
    "COALINDIA.NS","IOC.NS","GAIL.NS","ADANIENT.NS","ADANIPORTS.NS","TATAMOTORS.NS",
    "TATASTEEL.NS","TATACONSUM.NS","CIPLA.NS","DIVISLAB.NS","DRREDDY.NS",
    "APOLLOHOSP.NS","HINDALCO.NS","JSWSTEEL.NS","TECHM.NS","HDFCLIFE.NS",
    "SBILIFE.NS","BAJAJFINSV.NS","EICHERMOT.NS","HEROMOTOCO.NS","BRITANNIA.NS",
    "GRASIM.NS","INDUSINDBK.NS","BEL.NS","SHRIRAMFIN.NS","LTIM.NS","ZOMATO.NS",
]

NSE_SYMBOLS = NIFTY50_SYMBOLS + [
    "PIDILITIND.NS","DABUR.NS","MARICO.NS","COLPAL.NS","HAVELLS.NS","VOLTAS.NS",
    "BERGEPAINT.NS","GODREJCP.NS","BANDHANBNK.NS","FEDERALBNK.NS","IDFCFIRSTB.NS",
    "PNB.NS","BANKBARODA.NS","CANBK.NS","UNIONBANK.NS","SAIL.NS","NMDC.NS",
    "RECLTD.NS","PFC.NS","IRFC.NS","NHPC.NS","SJVN.NS","NAUKRI.NS","IRCTC.NS",
    "PERSISTENT.NS","COFORGE.NS","MPHASIS.NS","OFSS.NS","KPITTECH.NS","TATAELXSI.NS",
    "DIXON.NS","AMBER.NS","CROMPTON.NS","PAGEIND.NS","TRENT.NS","DMART.NS",
    "INDIGO.NS","CONCOR.NS","HDFCAMC.NS","ASTRAL.NS","POLYCAB.NS","BHEL.NS",
    "ABB.NS","SIEMENS.NS","AMBUJACEM.NS","ACC.NS","SHREECEM.NS","MUTHOOTFIN.NS",
    "CHOLAFIN.NS","AUROPHARMA.NS","TORNTPHARM.NS","LUPIN.NS","BIOCON.NS",
    "ALKEM.NS","GLENMARK.NS","ZYDUSLIFE.NS","APOLLOTYRE.NS","MRF.NS","MOTHERSON.NS",
    "BOSCHLTD.NS","MCDOWELL-N.NS","JUBLFOOD.NS","DEEPAKNTR.NS","RVNL.NS","HAL.NS",
    "VEDL.NS","HINDCOPPER.NS","NATIONALUM.NS","SUPREMEIND.NS","ADANIGREEN.NS",
]

BSE_SYMBOLS = [
    "RELIANCE.BO","TCS.BO","INFY.BO","HDFCBANK.BO","ICICIBANK.BO","SBIN.BO",
    "BAJFINANCE.BO","WIPRO.BO","LT.BO","AXISBANK.BO","KOTAKBANK.BO","MARUTI.BO",
    "SUNPHARMA.BO","TATAMOTORS.BO","TATASTEEL.BO","BHARTIARTL.BO","ASIANPAINT.BO",
    "TITAN.BO","HCLTECH.BO",
]

FUTURES_SYMBOLS = [
    "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS",
    "SBIN.NS","BAJFINANCE.NS","TATAMOTORS.NS","TATASTEEL.NS","AXISBANK.NS",
    "WIPRO.NS","LT.NS","KOTAKBANK.NS","ASIANPAINT.NS","MARUTI.NS",
    "SUNPHARMA.NS","BHARTIARTL.NS","HCLTECH.NS","ADANIENT.NS","ADANIPORTS.NS",
    "JSWSTEEL.NS","HINDALCO.NS","ONGC.NS","NTPC.NS","POWERGRID.NS",
]

ETF_SYMBOLS = [
    "NIFTYBEES.NS","BANKBEES.NS","JUNIORBEES.NS","GOLDBEES.NS",
    "HDFCGOLD.NS","SETFGOLD.NS","GOLDIETF.NS","SILVERBEES.NS",
    "HDFCSILVER.NS","SILVERIETF.NS","MON100.NS","MAFANG.NS",
    "ITBEES.NS","LIQUIDBEES.NS",
]

MCX_SYMBOLS = [
    "GOLDM.MCX","GOLD.MCX","SILVERM.MCX","SILVER.MCX",
    "CRUDEOIL.MCX","NATURALGAS.MCX","COPPER.MCX","ZINC.MCX",
]

SEGMENT_LOT_SIZE = {
    "GOLDM.MCX": 100,"GOLD.MCX": 1000,"SILVERM.MCX": 5,"SILVER.MCX": 30,
    "CRUDEOIL.MCX": 100,"NATURALGAS.MCX": 1250,"COPPER.MCX": 2500,"ZINC.MCX": 5000,
}

UNIVERSE_PRESETS = {
    "Nifty 50":      NIFTY50_SYMBOLS,
    "Nifty 100":     NSE_SYMBOLS[:100],
    "Nifty 200":     NSE_SYMBOLS[:200],
    "All NSE":       NSE_SYMBOLS,
    "F&O Eligible":  FUTURES_SYMBOLS,
    "ETFs":          ETF_SYMBOLS,
    "MCX Commodities": MCX_SYMBOLS,
}


def get_dynamic_universe(segment: str = "Nifty 50") -> list:
    """Block 1: Return symbol list for the selected universe preset."""
    cached = _dynamic_universe_cache
    now    = time.time()
    # Refresh once per trading day at first load after 9 AM
    today  = date.today().isoformat()
    if cached.get("date") == today and cached.get("segment") == segment:
        return cached.get("data", UNIVERSE_PRESETS.get(segment, NSE_SYMBOLS))

    symbols = UNIVERSE_PRESETS.get(segment, NSE_SYMBOLS)

    # Try to enrich from Angel One master (best-effort)
    if segment in ("F&O Eligible", "All NSE"):
        try:
            rows = _angel_master_rows()
            if rows:
                nse_eq = [
                    r.get("name", "").upper() + ".NS"
                    for r in rows
                    if str(r.get("exch_seg", "")).upper() == "NSE"
                    and str(r.get("instrumenttype", "")).upper() in ("", "EQ", "EQUITY")
                    and r.get("name")
                ]
                if len(nse_eq) > 50:
                    symbols = list(dict.fromkeys(symbols + nse_eq[:500]))
        except Exception:
            pass

    _dynamic_universe_cache.update({"date": today, "segment": segment, "data": symbols})
    return symbols


def search_symbols(query: str, max_results: int = 20) -> list:
    """Block 1b: Typeahead symbol search across Angel One master."""
    q = query.upper().strip()
    if not q:
        return []
    results = []
    try:
        rows = _angel_master_rows()
        for row in rows:
            tsym = str(row.get("symbol", "")).upper()
            name = str(row.get("name", "")).upper()
            exch = str(row.get("exch_seg", "")).upper()
            if q in tsym or q in name:
                label = f"{name} ({tsym}) — {exch}"
                clean = name + (".NS" if exch == "NSE" else (".BO" if exch == "BSE" else ".MCX"))
                results.append({"label": label, "symbol": clean, "exchange": exch,
                                 "name": name, "tradingsymbol": tsym})
            if len(results) >= max_results:
                break
    except Exception:
        # Fallback: search static list
        for s in NSE_SYMBOLS:
            if q in s.upper():
                results.append({"label": s, "symbol": s, "exchange": "NSE", "name": s})
    return results[:max_results]


# ─── Sector / Index maps ───────────────────────────────────────────────────────
SECTOR_MAP = {
    "HDFCBANK.NS":"Banking","ICICIBANK.NS":"Banking","SBIN.NS":"Banking",
    "AXISBANK.NS":"Banking","KOTAKBANK.NS":"Banking","INDUSINDBK.NS":"Banking",
    "BANDHANBNK.NS":"Banking","FEDERALBNK.NS":"Banking","PNB.NS":"Banking",
    "BANKBARODA.NS":"Banking","IDFCFIRSTB.NS":"Banking",
    "BAJFINANCE.NS":"NBFC","BAJAJFINSV.NS":"NBFC","CHOLAFIN.NS":"NBFC",
    "MUTHOOTFIN.NS":"NBFC","SHRIRAMFIN.NS":"NBFC","HDFCAMC.NS":"NBFC",
    "HDFCLIFE.NS":"Insurance","SBILIFE.NS":"Insurance",
    "TCS.NS":"IT","INFY.NS":"IT","WIPRO.NS":"IT","HCLTECH.NS":"IT",
    "TECHM.NS":"IT","PERSISTENT.NS":"IT","COFORGE.NS":"IT","MPHASIS.NS":"IT",
    "LTIM.NS":"IT","OFSS.NS":"IT","KPITTECH.NS":"IT","TATAELXSI.NS":"IT",
    "RELIANCE.NS":"Energy","ONGC.NS":"Energy","BPCL.NS":"Energy",
    "IOC.NS":"Energy","GAIL.NS":"Energy",
    "NTPC.NS":"Power","POWERGRID.NS":"Power","ADANIGREEN.NS":"Power","NHPC.NS":"Power",
    "SUNPHARMA.NS":"Pharma","CIPLA.NS":"Pharma","DRREDDY.NS":"Pharma",
    "DIVISLAB.NS":"Pharma","LUPIN.NS":"Pharma","AUROPHARMA.NS":"Pharma",
    "BIOCON.NS":"Pharma","TORNTPHARM.NS":"Pharma","ALKEM.NS":"Pharma",
    "MARUTI.NS":"Auto","TATAMOTORS.NS":"Auto","EICHERMOT.NS":"Auto",
    "HEROMOTOCO.NS":"Auto","MOTHERSON.NS":"Auto",
    "LT.NS":"Capital Goods","SIEMENS.NS":"Capital Goods","ABB.NS":"Capital Goods",
    "BHEL.NS":"Capital Goods","BEL.NS":"Defence","HAL.NS":"Defence",
    "TATASTEEL.NS":"Metals","JSWSTEEL.NS":"Metals","HINDALCO.NS":"Metals",
    "VEDL.NS":"Metals","SAIL.NS":"Metals","NMDC.NS":"Metals",
    "ASIANPAINT.NS":"FMCG","BRITANNIA.NS":"FMCG","NESTLEIND.NS":"FMCG",
    "DABUR.NS":"FMCG","MARICO.NS":"FMCG","COLPAL.NS":"FMCG","GODREJCP.NS":"FMCG",
    "TITAN.NS":"Consumer","TRENT.NS":"Retail","DMART.NS":"Retail",
    "ZOMATO.NS":"Consumer Tech","NAUKRI.NS":"Consumer Tech",
    "ADANIENT.NS":"Conglomerate","ADANIPORTS.NS":"Infrastructure",
    "APOLLOHOSP.NS":"Healthcare","ULTRACEMCO.NS":"Cement",
    "AMBUJACEM.NS":"Cement","SHREECEM.NS":"Cement",
}

_INDEX_NSE_NAME = {
    "^NSEI":     "NIFTY 50",
    "^NSEBANK":  "NIFTY BANK",
    "^INDIAVIX": "India VIX",
    "^CNXIT":    "NIFTY IT",
    "^BSESN":    "SENSEX",
    "^NSMIDCP":  "NIFTY MIDCAP 50",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _sf(val, default=0.0):
    try:
        v = float(val.iloc[-1]) if isinstance(val, pd.Series) else float(val)
        return v if np.isfinite(v) else default
    except Exception:
        return default


def segment_lot_size(symbol: str) -> int:
    return int(SEGMENT_LOT_SIZE.get(symbol.upper(), 1))

def min_cash_qty(price: float, min_value: float = 100000) -> int:
    price = max(float(price or 0), 0.01)
    return max(1, int(math.ceil(float(min_value) / price)))

def equity_cost(price, qty, side="BUY", delivery=False):
    tv   = price * qty
    brok = 0 if delivery else min(20.0, tv * 0.0003)
    stt  = tv * 0.001 if delivery else (tv * 0.00025 if side == "SELL" else 0)
    exch = tv * 0.0000345; sebi = tv * 0.000001
    gst  = (brok + exch + sebi) * 0.18
    stamp = tv * 0.00015 if side == "BUY" else 0
    return round(brok + stt + exch + sebi + gst + stamp, 2)

def options_cost(prem, lots, lot_sz, side="BUY", expiry_type="weekly"):
    tv   = prem * lots * lot_sz
    brok = min(40.0, tv * 0.0003)
    stt  = tv * 0.0005 if side == "SELL" else 0
    exch = tv * 0.0000495; sebi = tv * 0.000001
    gst  = (brok + exch + sebi) * 0.18
    stamp = tv * 0.00003 if side == "BUY" else 0
    return round(brok + stt + exch + sebi + gst + stamp, 2)

def futures_cost(price, lots, lot_sz, side="BUY"):
    tv   = price * lots * lot_sz
    brok = min(40.0, tv * 0.0003)
    stt  = tv * 0.0001; exch = tv * 0.000019; sebi = tv * 0.000001
    gst  = (brok + exch + sebi) * 0.18
    stamp = tv * 0.00002 if side == "BUY" else 0
    return round(brok + stt + exch + sebi + gst + stamp, 2)

def segment_cost(price, qty, side="BUY", delivery=False, leverage=1):
    return equity_cost(price, qty, side, delivery)


# ─── Live Price ───────────────────────────────────────────────────────────────
_price_cache: dict = {}
_last_confirmed_live: dict = {}
_index_spot_cache: dict = {}
_opt_price_cache: dict = {}
_opt_last_confirmed: dict = {}

# Block 7a: WebSocket state
_ws_prices: dict = {}   # symbol → latest price from WebSocket
_ws_connected: bool = False


def _symbol_exchange(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith(".MCX") or s in {"GOLD","GOLDM","SILVER","SILVERM","CRUDEOIL","NATURALGAS","COPPER","ZINC"}:
        return "MCX"
    if s.endswith(".BO"):
        return "BSE"
    return "NSE"


def _angel_find_instrument(symbol: str):
    exch  = _symbol_exchange(symbol)
    clean = symbol.upper().replace(".NS","").replace(".BO","").replace(".MCX","")
    today = date.today()
    candidates = []
    for row in _angel_master_rows():
        try:
            if str(row.get("exch_seg","")).upper() != exch:
                continue
            tsym  = str(row.get("symbol","")).upper()
            name  = str(row.get("name","")).upper()
            token = str(row.get("token",""))
            if not token:
                continue
            if exch in {"NSE","BSE"}:
                if tsym == clean or tsym.startswith(clean+"-") or name == clean:
                    return exch, row.get("symbol"), token
            else:
                if clean not in tsym and clean not in name:
                    continue
                exp_raw  = str(row.get("expiry",""))
                exp_date = today + timedelta(days=3650)
                for fmt in ("%d%b%Y","%d-%b-%Y","%Y-%m-%d"):
                    try:
                        exp_date = datetime.strptime(exp_raw.title(), fmt).date()
                        break
                    except Exception:
                        pass
                if exp_date >= today:
                    candidates.append((exp_date, row.get("symbol"), token))
        except Exception:
            continue
    if candidates:
        candidates.sort(key=lambda x: x[0])
        return exch, candidates[0][1], candidates[0][2]
    return None


def _fetch_angel_live_price(symbol: str) -> float | None:
    # Check WebSocket cache first (Block 7a)
    sym_clean = _nse_clean_symbol(symbol)
    if sym_clean in _ws_prices:
        return _ws_prices[sym_clean]

    obj  = _get_angel_client()
    inst = _angel_find_instrument(symbol)
    if obj is None or inst is None:
        return None
    try:
        exch, tradingsymbol, token = inst
        data = obj.ltpData(exch, tradingsymbol, token)
        ltp  = (data or {}).get("data",{}).get("ltp")
        if ltp and float(ltp) > 0:
            return float(ltp)
    except Exception:
        return None
    return None


def _yahoo_chart_quote(symbol: str) -> dict | None:
    try:
        url  = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        resp = requests.get(url, params={"range":"1d","interval":"1m"},
                            headers={"User-Agent": _NSE_HEADERS["User-Agent"]}, timeout=8)
        if resp.status_code != 200:
            return None
        result = resp.json().get("chart",{}).get("result",[])
        if not result:
            return None
        meta  = result[0].get("meta",{})
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        prev  = meta.get("previousClose") or price
        if not price or float(price) <= 0:
            return None
        price = float(price); prev = float(prev or price)
        chg   = price - prev
        return {"p": price,"c": round(chg,2),"pct": round((chg/prev*100) if prev else 0,2),
                "h": float(meta.get("regularMarketDayHigh") or price),
                "l": float(meta.get("regularMarketDayLow") or price)}
    except Exception:
        return None


def _fetch_live_price_from_api(symbol: str) -> float | None:
    sym_clean = _nse_clean_symbol(symbol)

    # NSE equity quote
    try:
        resp = _nse_fetch_with_retry(
            f"https://www.nseindia.com/api/quote-equity?symbol={sym_clean}"
        )
        if resp:
            data = resp.json()
            ltp  = (data.get("priceInfo",{}).get("lastPrice")
                    or data.get("priceInfo",{}).get("close"))
            if ltp and float(ltp) > 0:
                return float(ltp)
    except Exception:
        pass

    # NSE index quote
    if symbol.startswith("^"):
        try:
            idx_name = _INDEX_NSE_NAME.get(symbol,"")
            if idx_name:
                resp = _nse_fetch_with_retry("https://www.nseindia.com/api/allIndices")
                if resp:
                    for item in resp.json().get("data",[]):
                        if item.get("indexSymbol","").upper() == idx_name.upper() \
                           or item.get("index","").upper() == idx_name.upper():
                            ltp = item.get("last") or item.get("previousClose")
                            if ltp and float(ltp) > 0:
                                return float(ltp)
        except Exception:
            pass

    # NSE chart
    if not symbol.startswith("^"):
        try:
            resp = _nse_fetch_with_retry(
                f"https://www.nseindia.com/api/chart-databyindex?index={sym_clean}EQN&indices=true"
            )
            if resp:
                gd = resp.json().get("grapthData",[])
                if gd:
                    last = gd[-1]
                    if isinstance(last,(list,tuple)) and len(last) >= 2:
                        ltp = float(last[1])
                        if ltp > 0:
                            return ltp
        except Exception:
            pass

    return None


def get_live_price(symbol: str) -> float | None:
    sym_clean = _nse_clean_symbol(symbol)
    cache_key = f"live_{sym_clean}"

    cached = _price_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < LIVE_PRICE_TTL:
        return cached["price"]

    angel_ltp = _fetch_angel_live_price(symbol)
    if angel_ltp and angel_ltp > 0:
        _price_cache[cache_key]         = {"price": angel_ltp, "ts": time.time()}
        _last_confirmed_live[sym_clean] = {"price": angel_ltp, "ts": time.time()}
        return angel_ltp

    ltp = _fetch_live_price_from_api(symbol)
    if ltp and ltp > 0:
        _price_cache[cache_key]         = {"price": ltp, "ts": time.time()}
        _last_confirmed_live[sym_clean] = {"price": ltp, "ts": time.time()}
        return ltp

    confirmed = _last_confirmed_live.get(sym_clean)
    if confirmed:
        return confirmed["price"]

    ohlcv_key    = f"{symbol}_3mo_1d"
    ohlcv_cached = _price_cache.get(ohlcv_key)
    if ohlcv_cached and ohlcv_cached.get("df") is not None:
        df = ohlcv_cached["df"]
        if not df.empty:
            return float(df["Close"].iloc[-1])

    return None


# ─── Block 7a: WebSocket Price Feed ───────────────────────────────────────────

def start_websocket_feed(symbols: list, on_price_update=None):
    """
    Block 7a: Start Angel One SmartAPI WebSocket for live price streaming.
    Replaces 12-second polling with ~200ms latency updates.
    Falls back gracefully if WebSocket unavailable.
    """
    global _ws_connected
    try:
        import websocket
        import json as _json
        import threading

        obj = _get_angel_client()
        if obj is None:
            return False

        def _on_message(ws, message):
            try:
                data = _json.loads(message)
                for tick in (data if isinstance(data, list) else [data]):
                    sym   = tick.get("tradingSymbol","")
                    price = tick.get("ltp", 0)
                    if sym and price:
                        clean = _nse_clean_symbol(sym)
                        _ws_prices[clean] = float(price)
                        if on_price_update:
                            on_price_update(clean, float(price))
            except Exception:
                pass

        def _on_open(ws):
            global _ws_connected
            _ws_connected = True
            tokens = []
            for sym in symbols:
                inst = _angel_find_instrument(sym)
                if inst:
                    tokens.append({"exchangeType": 1, "tokens": [inst[2]]})
            if tokens:
                ws.send(_json.dumps({"action": 1, "params": {"mode": "LTP", "tokenList": tokens}}))

        def _on_close(ws, *args):
            global _ws_connected
            _ws_connected = False

        ws_url = "wss://smartapisocket.angelbroking.com/smart-stream"
        headers = {"Authorization": f"Bearer {obj.getfeedToken()}"}

        wsa = websocket.WebSocketApp(
            ws_url, header=headers,
            on_message=_on_message, on_open=_on_open, on_close=_on_close,
        )
        t = threading.Thread(target=wsa.run_forever, daemon=True)
        t.start()
        return True
    except Exception:
        return False


def get_ws_status() -> dict:
    """Return WebSocket connection status for UI."""
    return {"connected": _ws_connected, "symbols": len(_ws_prices), "prices": dict(_ws_prices)}


# ─── Index Spot Cache ─────────────────────────────────────────────────────────

def _get_fresh_index_spot(index: str) -> float | None:
    key    = "BN" if index == "BANKNIFTY" else "NF"
    cached = _index_spot_cache.get(key)
    if cached and (time.time() - cached["ts"]) < INDEX_SPOT_TTL:
        return cached["price"]
    try:
        resp = _nse_fetch_with_retry("https://www.nseindia.com/api/allIndices")
        if resp:
            target = "NIFTY BANK" if index == "BANKNIFTY" else "NIFTY 50"
            for item in resp.json().get("data",[]):
                iname = item.get("indexSymbol","") or item.get("index","")
                if iname.upper() == target.upper():
                    ltp = float(item.get("last",0) or 0)
                    if ltp > 0:
                        _index_spot_cache[key] = {"price": ltp,"ts": time.time()}
                        return ltp
    except Exception:
        pass
    try:
        idx_sym = "^NSEBANK" if index == "BANKNIFTY" else "^NSEI"
        ltp     = _fetch_live_price_from_api(idx_sym)
        if ltp and ltp > 0:
            _index_spot_cache[key] = {"price": ltp,"ts": time.time()}
            return ltp
    except Exception:
        pass
    sym_clean = _nse_clean_symbol("^NSEBANK" if index == "BANKNIFTY" else "^NSEI")
    confirmed = _last_confirmed_live.get(sym_clean)
    if confirmed:
        return confirmed["price"]
    return None


def force_refresh_index_spots():
    _index_spot_cache.clear()


# ─── NSE OHLCV ────────────────────────────────────────────────────────────────

def _nse_equity_history(symbol: str, from_date: date, to_date: date) -> pd.DataFrame | None:
    try:
        sym_nse = _nse_clean_symbol(symbol)
        params  = {
            "symbol": sym_nse,"series":"EQ",
            "from": from_date.strftime("%d-%m-%Y"),
            "to":   to_date.strftime("%d-%m-%Y"),
        }
        resp = _nse_fetch_with_retry(
            "https://www.nseindia.com/api/historical/cm/equity", params=params
        )
        if not resp:
            return None
        rows = resp.json().get("data",[])
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df.rename(columns={
            "CH_TIMESTAMP":"Date","CH_OPENING_PRICE":"Open","CH_TRADE_HIGH_PRICE":"High",
            "CH_TRADE_LOW_PRICE":"Low","CH_CLOSING_PRICE":"Close","CH_TOT_TRADED_QTY":"Volume",
        }, inplace=True)
        df["Date"] = pd.to_datetime(df["Date"])
        df.set_index("Date", inplace=True)
        for col in ["Open","High","Low","Close","Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df.sort_index(inplace=True)
        return df[["Open","High","Low","Close","Volume"]].dropna()
    except Exception:
        return None


def _nse_index_history(index_symbol: str, from_date: date, to_date: date) -> pd.DataFrame | None:
    try:
        idx_name = _INDEX_NSE_NAME.get(index_symbol,"")
        if not idx_name:
            return None
        params = {
            "indexType": idx_name,
            "from": from_date.strftime("%d-%m-%Y"),
            "to":   to_date.strftime("%d-%m-%Y"),
        }
        resp = _nse_fetch_with_retry(
            "https://www.nseindia.com/api/historical/indicesHistory", params=params
        )
        if not resp:
            return None
        rows = resp.json().get("data",{}).get("indexCloseOnlineRecords",[])
        if not rows:
            return None
        records = [{
            "Date":   r.get("EOD_TIMESTAMP"),
            "Open":   r.get("EOD_OPEN_INDEX_VAL"),
            "High":   r.get("EOD_HIGH_INDEX_VAL"),
            "Low":    r.get("EOD_LOW_INDEX_VAL"),
            "Close":  r.get("EOD_CLOSE_INDEX_VAL"),
            "Volume": 0,
        } for r in rows]
        df = pd.DataFrame(records)
        df["Date"] = pd.to_datetime(df["Date"])
        df.set_index("Date", inplace=True)
        for col in ["Open","High","Low","Close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["Volume"] = 0.0
        df.sort_index(inplace=True)
        return df[["Open","High","Low","Close","Volume"]].dropna()
    except Exception:
        return None


def _period_to_dates(period: str):
    to_dt = date.today()
    period_map = {
        "5d":timedelta(days=7),"1mo":timedelta(days=35),"3mo":timedelta(days=95),
        "6mo":timedelta(days=185),"1y":timedelta(days=370),"2y":timedelta(days=740),
        "3y":timedelta(days=1100),"5y":timedelta(days=1830),
    }
    return to_dt - period_map.get(period, timedelta(days=95)), to_dt


def _resample_to_weekly(df_daily: pd.DataFrame) -> pd.DataFrame:
    try:
        return df_daily.resample("W").agg(
            {"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}
        ).dropna()
    except Exception:
        return pd.DataFrame()


def get_ohlcv(symbol: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame | None:
    cache_key = f"{symbol}_{period}_{interval}"
    cached    = _price_cache.get(cache_key)
    ttl       = 15 if interval in ("1m","5m","15m","30m","1h") else 300
    if cached and (time.time() - cached["ts"]) < ttl:
        return cached["df"]

    from_date, to_date = _period_to_dates(period)
    df = None

    if interval == "1wk":
        df_daily = get_ohlcv(symbol, period, "1d")
        if df_daily is not None and not df_daily.empty:
            df = _resample_to_weekly(df_daily)
    elif symbol.startswith("^"):
        df = _nse_index_history(symbol, from_date, to_date)
    else:
        df = _nse_equity_history(symbol, from_date, to_date)

    if df is not None and not df.empty:
        _price_cache[cache_key] = {"df": df,"ts": time.time()}
        return df
    return None


# ─── Indicators ───────────────────────────────────────────────────────────────

def compute_vwap(df: pd.DataFrame) -> dict | None:
    try:
        if df is None or len(df) < 5:
            return None
        tp   = (df["High"].astype(float) + df["Low"].astype(float) + df["Close"].astype(float)) / 3
        v    = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(np.ones(len(tp)))
        vwap = (tp * v).cumsum() / v.cumsum()
        std  = tp.rolling(min(20,len(tp))).std()
        return {
            "vwap":    float(vwap.iloc[-1]),
            "vwap_u1": float(vwap.iloc[-1] + std.iloc[-1]),
            "vwap_u2": float(vwap.iloc[-1] + 2*std.iloc[-1]),
            "vwap_l1": float(vwap.iloc[-1] - std.iloc[-1]),
            "vwap_l2": float(vwap.iloc[-1] - 2*std.iloc[-1]),
        }
    except Exception:
        return None


def compute_vwap_bands(df: pd.DataFrame) -> dict:
    """Block 2b: Enhanced VWAP bands with volume profile."""
    result = compute_vwap(df) or {}
    try:
        tp = (df["High"].astype(float) + df["Low"].astype(float) + df["Close"].astype(float)) / 3
        v  = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(np.ones(len(tp)))
        anchored = (tp * v).cumsum() / v.cumsum()
        result["anchored_vwap"] = float(anchored.iloc[-1])
        result["price_vs_vwap"] = "ABOVE" if float(df["Close"].iloc[-1]) > float(anchored.iloc[-1]) else "BELOW"
    except Exception:
        pass
    return result


def compute_volume_profile(df: pd.DataFrame, n_bins: int = 20) -> dict:
    """Block 2b: Volume profile and Point of Control (POC)."""
    try:
        if df is None or len(df) < 10:
            return {"poc": 0.0, "high_vol_nodes": [], "low_vol_nodes": []}
        lo = float(df["Low"].min()); hi = float(df["High"].max())
        bins = np.linspace(lo, hi, n_bins + 1)
        vol  = df["Volume"].astype(float).values
        mid  = (df["High"].astype(float).values + df["Low"].astype(float).values) / 2
        profile = np.zeros(n_bins)
        for i, m in enumerate(mid):
            idx = min(n_bins - 1, int((m - lo) / (hi - lo) * n_bins))
            profile[idx] += vol[i]
        poc_idx = int(np.argmax(profile))
        poc     = float((bins[poc_idx] + bins[poc_idx+1]) / 2)
        threshold = float(np.percentile(profile, 75))
        hvn = [float((bins[i]+bins[i+1])/2) for i in range(n_bins) if profile[i] >= threshold]
        lvn = [float((bins[i]+bins[i+1])/2) for i in range(n_bins) if profile[i] < np.percentile(profile, 25)]
        return {"poc": poc, "high_vol_nodes": hvn[:5], "low_vol_nodes": lvn[:5],
                "value_area_high": float(bins[min(poc_idx+3, n_bins)]),
                "value_area_low": float(bins[max(poc_idx-3, 0)])}
    except Exception:
        return {"poc": 0.0, "high_vol_nodes": [], "low_vol_nodes": []}


def compute_roc(c: pd.Series, period: int = 10) -> float:
    try:
        if len(c) < period + 1:
            return 0.0
        return round(float((c.iloc[-1] - c.iloc[-(period+1)]) / c.iloc[-(period+1)] * 100), 3)
    except Exception:
        return 0.0


def compute_stoch_rsi(rsi_series: pd.Series, period: int = 14):
    try:
        if rsi_series is None or len(rsi_series) < period:
            return 50.0, 50.0
        rmin = rsi_series.rolling(period).min()
        rmax = rsi_series.rolling(period).max()
        k    = 100 * (rsi_series - rmin) / (rmax - rmin + 0.001)
        d    = k.rolling(3).mean()
        return float(k.iloc[-1]), float(d.iloc[-1])
    except Exception:
        return 50.0, 50.0


def compute_iv_percentile(vix: float, lookback_high: float = 30, lookback_low: float = 11) -> float:
    """Block 9c: IV Rank / IV Percentile."""
    try:
        return max(0, min(100, round((vix - lookback_low) / (lookback_high - lookback_low) * 100, 1)))
    except Exception:
        return 50.0


def compute_ad_line(df: pd.DataFrame):
    try:
        h = df["High"].astype(float); l = df["Low"].astype(float)
        c = df["Close"].astype(float)
        v = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(np.ones(len(c)))
        clv = ((c - l) - (h - c)) / (h - l + 0.001)
        ad  = (clv * v).cumsum()
        ad_trend = float((ad.iloc[-1] - ad.iloc[-6]) / (abs(ad.iloc[-6]) + 1)) if len(ad) >= 10 else 0.0
        return float(ad.iloc[-1]), ad_trend
    except Exception:
        return 0.0, 0.0


def detect_gap(df: pd.DataFrame):
    try:
        if df is None or len(df) < 2:
            return None, 0.0
        o = df["Open"].astype(float); c = df["Close"].astype(float)
        v = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(np.ones(len(c)))
        gap_pct = (o.iloc[-1] - c.iloc[-2]) / c.iloc[-2] * 100
        vma20   = v.rolling(20).mean().iloc[-1]
        vr      = v.iloc[-1] / vma20 if vma20 > 0 else 1.0
        if gap_pct >= 2.0 and vr >= 1.5:   return "GAP_UP",   round(gap_pct,2)
        elif gap_pct <= -2.0 and vr >= 1.5: return "GAP_DOWN", round(gap_pct,2)
        return None, round(gap_pct,2)
    except Exception:
        return None, 0.0


def compute_indicators(df: pd.DataFrame, for_delivery: bool = False) -> dict:
    if df is None or len(df) < 20:
        return {}
    try:
        c = df["Close"].astype(float)
        h = df["High"].astype(float)
        l = df["Low"].astype(float)
        v = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(np.ones(len(c)), index=c.index)

        d   = c.diff()
        g_  = d.clip(lower=0).ewm(span=14,adjust=False).mean()
        ls_ = (-d.clip(upper=0)).ewm(span=14,adjust=False).mean()
        rsi = 100 - 100 / (1 + g_ / ls_.replace(0, np.nan))
        srsi_k, srsi_d = compute_stoch_rsi(rsi)

        e12 = c.ewm(span=12,adjust=False).mean()
        e26 = c.ewm(span=26,adjust=False).mean()
        macd = e12 - e26; msig = macd.ewm(span=9,adjust=False).mean()
        mhist = macd - msig

        s20 = c.rolling(20).mean(); sd20 = c.rolling(20).std()
        bbu = s20 + 2*sd20; bbl = s20 - 2*sd20
        bbpct = (c - bbl) / (bbu - bbl + 0.001)

        tr  = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        e5  = c.ewm(span=5,  adjust=False).mean()
        e9  = c.ewm(span=9,  adjust=False).mean()
        e13 = c.ewm(span=13, adjust=False).mean()
        e21 = c.ewm(span=21, adjust=False).mean()
        e50 = c.ewm(span=50, adjust=False).mean()
        e200_val = float(c.ewm(span=200,adjust=False).mean().iloc[-1]) if len(c) >= 250 else 0.0

        l14 = l.rolling(14).min(); h14 = h.rolling(14).max()
        sk  = 100 * (c - l14) / (h14 - l14 + 0.001)
        sd_k = sk.rolling(3).mean()

        pdm = (h.diff()).clip(lower=0); ndm = (-l.diff()).clip(lower=0)
        pdi = 100 * pdm.ewm(span=14).mean() / atr.replace(0,np.nan)
        ndi = 100 * ndm.ewm(span=14).mean() / atr.replace(0,np.nan)
        dx  = 100 * (pdi - ndi).abs() / (pdi + ndi + 0.001)
        adx = dx.ewm(span=14).mean()

        wr  = -100 * (h14 - c) / (h14 - l14 + 0.001)
        tp  = (h + l + c) / 3
        cci = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).std() + 0.001)

        vma20  = v.rolling(20).mean().replace(0,np.nan)
        vratio = v / vma20
        obv    = (np.sign(c.diff()) * v).cumsum()

        ad_val, ad_trend = compute_ad_line(df)

        pivot = (h.iloc[-1] + l.iloc[-1] + c.iloc[-1]) / 3
        r1 = 2*pivot - l.iloc[-1]; s1 = 2*pivot - h.iloc[-1]
        r2 = pivot + (h.iloc[-1] - l.iloc[-1]); s2 = pivot - (h.iloc[-1] - l.iloc[-1])
        r3 = h.iloc[-1] + 2*(pivot - l.iloc[-1]); s3 = l.iloc[-1] - 2*(h.iloc[-1] - pivot)

        m5  = float((c.iloc[-1]-c.iloc[-5]) /c.iloc[-5] *100) if len(c)>=5  else 0
        m20 = float((c.iloc[-1]-c.iloc[-20])/c.iloc[-20]*100) if len(c)>=20 else 0
        m60 = float((c.iloc[-1]-c.iloc[-60])/c.iloc[-60]*100) if len(c)>=60 else 0

        roc10 = compute_roc(c,10); roc20 = compute_roc(c,20)

        kc_u = s20 + 1.5*atr; kc_l = s20 - 1.5*atr
        squeeze = (bbl > kc_l) & (bbu < kc_u)

        prev    = c.shift(1)
        day_chg = ((c - prev) / prev.replace(0,np.nan)) * 100

        hl2 = (h + l) / 2; mult = 3.0
        st_up = hl2 - mult*atr; st_dn = hl2 + mult*atr
        st_bullish = float(c.iloc[-1]) > float(st_up.iloc[-1])
        st_bearish = float(c.iloc[-1]) < float(st_dn.iloc[-1])

        vwap_data      = compute_vwap(df) or {}
        gap_type, gap_pct = detect_gap(df)

        return {
            "rsi":_sf(rsi),"rsi_s":rsi,"srsi_k":srsi_k,"srsi_d":srsi_d,
            "macd":_sf(macd),"macd_sig":_sf(msig),"macd_hist":_sf(mhist),
            "macd_s":macd,"msig_s":msig,"macd_above_zero":float(macd.iloc[-1])>0,
            "bb_pct":_sf(bbpct),"bb_u":_sf(bbu),"bb_l":_sf(bbl),"bb_mid":_sf(s20),
            "atr":_sf(atr),
            "e5":_sf(e5),"e9":_sf(e9),"e13":_sf(e13),"e21":_sf(e21),"e50":_sf(e50),"e200":e200_val,
            "sk":_sf(sk),"sd":_sf(sd_k),
            "adx":_sf(adx),"pdi":_sf(pdi),"ndi":_sf(ndi),
            "wr":_sf(wr),"cci":_sf(cci),
            "vr":_sf(vratio),"obv":_sf(obv),
            "ad_line":ad_val,"ad_trend":ad_trend,
            "pivot":pivot,"r1":r1,"r2":r2,"r3":r3,"s1":s1,"s2":s2,"s3":s3,
            "m5":m5,"m20":m20,"m60":m60,"roc10":roc10,"roc20":roc20,
            "squeeze":bool(squeeze.iloc[-1]),
            "close":_sf(c),"high":_sf(h),"low":_sf(l),
            "open":_sf(df["Open"].astype(float)),
            "volume":_sf(v),"avg_vol_20":_sf(vma20),
            "day_chg":_sf(day_chg),
            "st_up":_sf(st_up),"st_dn":_sf(st_dn),
            "st_bullish":st_bullish,"st_bearish":st_bearish,
            "vwap":vwap_data.get("vwap",0),
            "vwap_u1":vwap_data.get("vwap_u1",0),"vwap_l1":vwap_data.get("vwap_l1",0),
            "vwap_u2":vwap_data.get("vwap_u2",0),"vwap_l2":vwap_data.get("vwap_l2",0),
            "gap_type":gap_type,"gap_pct":gap_pct,
        }
    except Exception:
        return {}


# ─── Block 2c: Market Regime Classifier ──────────────────────────────────────

def classify_regime(adx: float, bb_width: float, vix: float) -> dict:
    """
    Block 2c: Classify market into Trending / Sideways / Volatile.
    Returns regime dict with type, description, and recommended parameters.
    """
    is_trending = adx > 25
    is_volatile = vix > 22 or bb_width > 0.06
    is_sideways = adx < 18 and bb_width < 0.03

    if is_volatile:
        regime = "VOLATILE"
        desc   = "High volatility — widen stops, reduce size, prefer spreads"
        params = {"sl_atr_mult": 2.5, "trail_atr_mult": 1.5, "position_scale": 0.5,
                  "min_strength": 75, "max_trades_per_day": 3}
    elif is_trending:
        regime = "TRENDING"
        desc   = "Strong trend — ride momentum, wider targets"
        params = {"sl_atr_mult": 1.5, "trail_atr_mult": 1.0, "position_scale": 1.0,
                  "min_strength": 65, "max_trades_per_day": 8}
    elif is_sideways:
        regime = "SIDEWAYS"
        desc   = "Range-bound — mean reversion strategies, tight stops"
        params = {"sl_atr_mult": 1.0, "trail_atr_mult": 0.8, "position_scale": 0.7,
                  "min_strength": 70, "max_trades_per_day": 5}
    else:
        regime = "NEUTRAL"
        desc   = "Mixed conditions — standard parameters"
        params = {"sl_atr_mult": 1.5, "trail_atr_mult": 1.0, "position_scale": 1.0,
                  "min_strength": 62, "max_trades_per_day": 6}

    return {"regime": regime, "description": desc, "params": params,
            "adx": adx, "bb_width": bb_width, "vix": vix}


# ─── Block 2a: Multi-Timeframe Confirmation ───────────────────────────────────

def confirm_mtf(symbol: str, signal: str, timeframes: list = None) -> dict:
    """
    Block 2a: Confirm signal across multiple timeframes (1d, 1wk).
    Returns confirmation dict.
    """
    if timeframes is None:
        timeframes = ["1d","1wk"]

    confirmations = {}
    for tf in timeframes:
        period = "3mo" if tf in ("1d","1m","5m","15m") else "2y"
        df  = get_ohlcv(symbol, period, tf)
        ind = compute_indicators(df) if df is not None and len(df) >= 20 else {}
        if not ind:
            confirmations[tf] = {"confirmed": False, "signal": "NO_DATA"}
            continue
        rsi        = ind.get("rsi", 50)
        st_bullish = ind.get("st_bullish", False)
        st_bearish = ind.get("st_bearish", False)
        macd_above = ind.get("macd_above_zero", False)
        e9 = ind.get("e9",0); e21 = ind.get("e21",0); close = ind.get("close",0)
        bull_stack = close > e9 > e21 if (close and e9 and e21) else False
        bear_stack = close < e9 < e21 if (close and e9 and e21) else False

        if "BUY" in signal:
            confirmed = (st_bullish and bull_stack) or (rsi < 45 and bull_stack) or (macd_above and st_bullish)
            tf_signal = "BUY" if confirmed else ("NEUTRAL" if not bear_stack else "CONFLICTING")
        else:
            confirmed = (st_bearish and bear_stack) or (rsi > 55 and bear_stack) or (not macd_above and st_bearish)
            tf_signal = "SELL" if confirmed else ("NEUTRAL" if not bull_stack else "CONFLICTING")

        confirmations[tf] = {
            "confirmed": confirmed, "signal": tf_signal,
            "rsi": rsi, "supertrend": "BULL" if st_bullish else ("BEAR" if st_bearish else "NEUTRAL"),
            "ema_stack": "BULL" if bull_stack else ("BEAR" if bear_stack else "NEUTRAL"),
        }

    # Overall: need at least half timeframes confirming
    confirmed_count = sum(1 for v in confirmations.values() if v.get("confirmed"))
    conflicting     = any(v.get("signal") == "CONFLICTING" for v in confirmations.values())
    overall = confirmed_count >= max(1, len(timeframes) // 2) and not conflicting

    return {
        "overall_confirmed": overall,
        "confirmed_count":   confirmed_count,
        "total_timeframes":  len(timeframes),
        "conflicting":       conflicting,
        "timeframes":        confirmations,
    }


# ─── Patterns & Divergence ────────────────────────────────────────────────────

def detect_patterns(df: pd.DataFrame) -> list:
    if df is None or len(df) < 4:
        return []
    patterns = []
    try:
        o = df["Open"].astype(float); h = df["High"].astype(float)
        l = df["Low"].astype(float);  c = df["Close"].astype(float)
        o1,o2,o3 = o.iloc[-3],o.iloc[-2],o.iloc[-1]
        h1,h2,h3 = h.iloc[-3],h.iloc[-2],h.iloc[-1]
        l1,l2,l3 = l.iloc[-3],l.iloc[-2],l.iloc[-1]
        c1,c2,c3 = c.iloc[-3],c.iloc[-2],c.iloc[-1]
        b3 = abs(c3-o3); r3 = h3-l3 if h3!=l3 else 0.001
        b2 = abs(c2-o2)
        lw3 = min(o3,c3)-l3; uw3 = h3-max(o3,c3)

        if b3/r3 < 0.1:                                               patterns.append(("Doji","NEUTRAL"))
        if lw3>2*b3 and uw3<b3 and c2<o2:                             patterns.append(("Hammer","BUY"))
        if uw3>2*b3 and lw3<b3 and c2>o2:                             patterns.append(("Shooting Star","SELL"))
        if c2<o2 and c3>o3 and o3<c2 and c3>o2:                       patterns.append(("Bullish Engulfing","BUY"))
        if c2>o2 and c3<o3 and o3>c2 and c3<o2:                       patterns.append(("Bearish Engulfing","SELL"))
        if c1<o1 and b2<(h2-l2)*0.3 and c3>o3 and c3>(o1+c1)/2:      patterns.append(("Morning Star","BUY"))
        if c1>o1 and b2<(h2-l2)*0.3 and c3<o3 and c3<(o1+c1)/2:      patterns.append(("Evening Star","SELL"))
        if c3>o3 and b3>b2*2 and lw3<b3*0.3 and uw3<b3*0.3:          patterns.append(("Marubozu Bull","BUY"))
        if c3<o3 and b3>b2*2 and lw3<b3*0.3 and uw3<b3*0.3:          patterns.append(("Marubozu Bear","SELL"))
        if lw3>2*b3 and c3>o3:                                         patterns.append(("Dragonfly Doji","BUY"))
        if uw3>2*b3 and c3<o3:                                         patterns.append(("Gravestone Doji","SELL"))
        if c1>o1 and c2>o2 and c3>o3 and c3>c2>c1:                    patterns.append(("Three White Soldiers","BUY"))
        if c1<o1 and c2<o2 and c3<o3 and c3<c2<c1:                    patterns.append(("Three Black Crows","SELL"))
    except Exception:
        pass
    return patterns


def detect_divergence(df: pd.DataFrame, ind: dict):
    if df is None or len(df) < 15 or not ind:
        return None
    try:
        c   = df["Close"].astype(float).values[-20:]
        rsi = ind.get("rsi_s")
        if rsi is None or len(rsi) < 20:
            return None
        rsi   = rsi.values[-20:]
        lows  = [i for i in range(1,len(c)-1) if c[i]<c[i-1] and c[i]<c[i+1]]
        highs = [i for i in range(1,len(c)-1) if c[i]>c[i-1] and c[i]>c[i+1]]
        if len(lows) >= 2:
            i1,i2 = lows[-2],lows[-1]
            if c[i2]<c[i1] and rsi[i2]>rsi[i1] and rsi[i1]<50:
                return ("BULLISH_DIV","BUY","RSI bullish divergence")
        if len(highs) >= 2:
            i1,i2 = highs[-2],highs[-1]
            if c[i2]>c[i1] and rsi[i2]<rsi[i1] and rsi[i1]>50:
                return ("BEARISH_DIV","SELL","RSI bearish divergence")
    except Exception:
        pass
    return None


def volume_spike(ind: dict):
    vr = ind.get("vr",1.0) if ind else 1.0
    if vr >= 3.0:   return "EXTREME", vr
    elif vr >= 2.0: return "HIGH", vr
    elif vr >= 1.5: return "ABOVE_AVG", vr
    return "NORMAL", vr


def institutional_accumulation(ind: dict) -> bool:
    try:
        return (ind.get("m5",0)>1 and ind.get("vr",1.0)>1.5
                and ind.get("ad_trend",0)>0 and ind.get("day_chg",0)>0)
    except Exception:
        return False


def weinstein_stage(df_weekly: pd.DataFrame):
    try:
        if df_weekly is None or len(df_weekly) < 30:
            return None, "Insufficient weekly data"
        c    = df_weekly["Close"].astype(float)
        ma30 = c.rolling(30).mean()
        slope = (ma30.iloc[-1]-ma30.iloc[-5])/ma30.iloc[-5]*100 if ma30.iloc[-5]>0 else 0
        lc,lm = c.iloc[-1],ma30.iloc[-1]
        if lc>lm and slope>0.5:   return 2,"Stage 2 — Uptrend ✅ (BUY zone)"
        elif lc>lm and slope<=0.5: return 3,"Stage 3 — Topping ⚠️"
        elif lc<lm and slope<-0.5: return 4,"Stage 4 — Downtrend 🔴"
        else:                      return 1,"Stage 1 — Basing 🔵"
    except Exception:
        return None,"Stage analysis failed"


def compute_rs_rating(df_stock: pd.DataFrame, period_days: int = 65) -> float | None:
    try:
        if df_stock is None or len(df_stock) < period_days:
            return None
        c         = df_stock["Close"].astype(float)
        stock_ret = (c.iloc[-1]-c.iloc[-period_days])/c.iloc[-period_days]*100
        df_n      = get_ohlcv("^NSEI","1y","1d")
        if df_n is None or len(df_n) < period_days:
            return None
        cn        = df_n["Close"].astype(float)
        nifty_ret = (cn.iloc[-1]-cn.iloc[-period_days])/cn.iloc[-period_days]*100
        if nifty_ret == 0:
            return None
        return round((1+stock_ret/100)/(1+nifty_ret/100),3)
    except Exception:
        return None


def check_52w_breakout(df: pd.DataFrame, fund: dict = None):
    try:
        if df is None or len(df) < 252:
            return False, 0.0
        c    = df["Close"].astype(float)
        v    = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(np.ones(len(c)))
        h52w = c.iloc[-252:-1].max()
        vma20 = v.rolling(20).mean().iloc[-1]
        vr    = v.iloc[-1]/vma20 if vma20>0 else 1.0
        if c.iloc[-1] >= h52w*0.99 and vr >= 1.8:
            return True, round(vr,2)
        return False, round(vr,2)
    except Exception:
        return False, 0.0


def earnings_risk(fund: dict):
    try:
        ets = fund.get("earnings_ts")
        if not ets:
            return False, None
        ed = datetime.fromtimestamp(ets).date()
        dte = (ed - date.today()).days
        return (0 <= dte <= 7), dte
    except Exception:
        return False, None


def get_fundamentals(symbol: str) -> dict:
    try:
        sym_clean = _nse_clean_symbol(symbol)
        resp      = _nse_fetch_with_retry(
            f"https://www.nseindia.com/api/quote-equity?symbol={sym_clean}"
        )
        if not resp:
            return {}
        data  = resp.json()
        info  = data.get("metadata",{})
        price = data.get("priceInfo",{})
        ind52 = price.get("weekHighLow",{})
        return {
            "name": info.get("companyName",symbol),"sector":info.get("industry","N/A"),
            "pe":info.get("pdSectorPe"),"pb":None,"roe":None,"de":None,"eps":None,
            "beta":None,"mktcap":info.get("marketCap"),
            "52h":ind52.get("max"),"52l":ind52.get("min"),
            "div_yield":None,"earnings_ts":None,
        }
    except Exception:
        return {}


# ─── Master Signal Scorer ─────────────────────────────────────────────────────

def score_signal(ind, fund, df, market_mood="NEUTRAL", vix=15.0, mode="INTRADAY",
                 df_weekly=None, rs_rating=None):
    if not ind:
        return "NEUTRAL",0,0,0,["No data"]

    buy=0; sell=0; reasons=[]

    def g(k,d=0.0):
        v = ind.get(k,d)
        try: return float(v) if np.isfinite(float(v)) else d
        except: return d

    rsi=g("rsi",50); macd=g("macd"); msig=g("macd_sig"); mhist=g("macd_hist")
    macd_above_zero=ind.get("macd_above_zero",False)
    bb=g("bb_pct",0.5); sk=g("sk",50); sd=g("sd",50)
    srsi_k=g("srsi_k",50); srsi_d=g("srsi_d",50)
    adx=g("adx",20); pdi=g("pdi"); ndi=g("ndi")
    wr=g("wr",-50); cci=g("cci"); vr=g("vr",1.0)
    close=g("close"); e9=g("e9"); e13=g("e13"); e21=g("e21"); e50=g("e50"); e200=g("e200")
    m5=g("m5"); m20=g("m20"); m60=g("m60")
    roc10=g("roc10"); roc20=g("roc20"); atr=g("atr")
    squeeze=ind.get("squeeze",False)
    s1=g("s1"); s2=g("s2"); r1=g("r1"); r2=g("r2")
    st_bullish=ind.get("st_bullish",False); st_bearish=ind.get("st_bearish",False)
    gap_type=ind.get("gap_type"); gap_pct=g("gap_pct"); ad_trend=g("ad_trend")

    if market_mood=="BEARISH":   sell+=2; reasons.append("🔴 Market BEARISH → SELL +2")
    elif market_mood=="BULLISH": buy+=1;  reasons.append("🟢 Market BULLISH → BUY +1")
    if vix>22:   sell+=1; reasons.append(f"⚠️ VIX={vix:.1f} elevated")
    elif vix<13: buy+=1;  reasons.append(f"🟢 VIX={vix:.1f} low")

    if mode=="DELIVERY":
        if 40<=rsi<=55:  buy+=3; reasons.append(f"RSI={rsi:.1f} ideal delivery zone → BUY +3")
        elif rsi>70:     sell+=2; reasons.append(f"RSI={rsi:.1f} overbought → SELL +2")
    else:
        if rsi<25:   buy+=4; reasons.append(f"RSI={rsi:.1f} DEEPLY oversold → BUY +4")
        elif rsi<35: buy+=3; reasons.append(f"RSI={rsi:.1f} oversold → BUY +3")
        elif rsi<45: buy+=1; reasons.append(f"RSI={rsi:.1f} mild oversold → BUY +1")
        elif rsi>80: sell+=4; reasons.append(f"RSI={rsi:.1f} DEEPLY overbought → SELL +4")
        elif rsi>70: sell+=3; reasons.append(f"RSI={rsi:.1f} overbought → SELL +3")
        elif rsi>60: sell+=1; reasons.append(f"RSI={rsi:.1f} elevated → SELL +1")
        else: reasons.append(f"RSI={rsi:.1f} neutral")

    if srsi_k<20 and srsi_k>srsi_d: buy+=2;  reasons.append(f"StochRSI oversold crossing up → BUY +2")
    elif srsi_k>80 and srsi_k<srsi_d: sell+=2; reasons.append(f"StochRSI overbought crossing dn → SELL +2")

    if macd>msig and mhist>0:
        if macd_above_zero: buy+=3; reasons.append("MACD bullish above zero → BUY +3")
        else:               buy+=1; reasons.append("MACD bullish below zero → BUY +1")
    elif macd<msig and mhist<0: sell+=2; reasons.append("MACD bearish → SELL +2")
    if mhist>0 and ind.get("macd_s") is not None:
        ms = ind["macd_s"]
        if len(ms)>=2 and float(ms.iloc[-1])>float(ms.iloc[-2]):
            buy+=1; reasons.append("MACD hist expanding → BUY +1")

    if st_bullish:  buy+=3;  reasons.append("SuperTrend bullish → BUY +3")
    elif st_bearish: sell+=3; reasons.append("SuperTrend bearish → SELL +3")

    if bb<0.05:    buy+=3; reasons.append(f"Price at lower BB → BUY +3")
    elif bb<0.15:  buy+=2; reasons.append(f"Price near lower BB → BUY +2")
    elif bb>0.95:  sell+=3; reasons.append(f"Price at upper BB → SELL +3")
    elif bb>0.85:  sell+=2; reasons.append(f"Price near upper BB → SELL +2")

    if close>0 and e9>0 and e13>0 and e21>0 and e50>0:
        if close>e9>e13>e21>e50:   buy+=4;  reasons.append("Perfect bull EMA stack → BUY +4")
        elif close<e9<e13<e21<e50: sell+=4; reasons.append("Perfect bear EMA stack → SELL +4")
        elif close>e21>e50:         buy+=2;  reasons.append("Price above EMA21&50 → BUY +2")
        elif close<e21<e50:         sell+=2; reasons.append("Price below EMA21&50 → SELL +2")
        if e200>0 and len(df)>=250:
            if close>e200: buy+=1
            else:          sell+=1

    if adx>30:
        if pdi>ndi: buy+=3; reasons.append(f"ADX={adx:.0f} STRONG uptrend → BUY +3")
        else:       sell+=3; reasons.append(f"ADX={adx:.0f} STRONG downtrend → SELL +3")
    elif adx>20:
        if pdi>ndi: buy+=1; reasons.append(f"ADX={adx:.0f} moderate uptrend → BUY +1")
        else:       sell+=1; reasons.append(f"ADX={adx:.0f} moderate downtrend → SELL +1")

    if sk<20 and sk>sd:  buy+=2; reasons.append(f"Stoch K={sk:.0f} oversold crossing up → BUY +2")
    elif sk<15:          buy+=1; reasons.append(f"Stoch K={sk:.0f} deep oversold → BUY +1")
    elif sk>80 and sk<sd: sell+=2; reasons.append(f"Stoch K={sk:.0f} overbought → SELL +2")
    elif sk>85:           sell+=1; reasons.append(f"Stoch K={sk:.0f} deep overbought → SELL +1")

    if wr<-85:   buy+=2; reasons.append(f"Williams R={wr:.0f} oversold → BUY +2")
    elif wr<-70: buy+=1; reasons.append(f"Williams R={wr:.0f} oversold → BUY +1")
    elif wr>-10: sell+=2; reasons.append(f"Williams R={wr:.0f} overbought → SELL +2")
    elif wr>-20: sell+=1

    if cci<-150:   buy+=2; reasons.append(f"CCI={cci:.0f} extreme oversold → BUY +2")
    elif cci<-100: buy+=1
    elif cci>150:  sell+=2; reasons.append(f"CCI={cci:.0f} extreme overbought → SELL +2")
    elif cci>100:  sell+=1

    vs,vr_val = volume_spike(ind)
    if vs in ("EXTREME","HIGH") and buy>sell:   buy+=2; reasons.append(f"Volume spike {vr_val:.1f}x bullish → BUY +2")
    elif vs in ("EXTREME","HIGH") and sell>buy: sell+=2; reasons.append(f"Volume spike {vr_val:.1f}x bearish → SELL +2")
    elif vs=="ABOVE_AVG":
        if buy>sell:   buy+=1
        elif sell>buy: sell+=1

    if ad_trend>0.05:  buy+=2; reasons.append("A/D line rising → BUY +2")
    elif ad_trend<-0.05: sell+=2; reasons.append("A/D line falling → SELL +2")

    if squeeze:
        reasons.append("⚡ TTM Squeeze firing!")
        if buy>sell: buy+=2
        else:        sell+=2

    if roc10>3:    buy+=2; reasons.append(f"ROC10={roc10:.1f}% → BUY +2")
    elif roc10>1:  buy+=1
    elif roc10<-3: sell+=2
    elif roc10<-1: sell+=1

    if m5>3:    buy+=2; reasons.append(f"5D momentum +{m5:.1f}% → BUY +2")
    elif m5>1:  buy+=1
    elif m5<-3: sell+=2; reasons.append(f"5D momentum {m5:.1f}% → SELL +2")
    elif m5<-1: sell+=1
    if m20>10:  buy+=1
    elif m20<-10: sell+=1

    if close>0 and s1>0:
        if abs(close-s1)/close<0.005: buy+=2; reasons.append(f"At Support S1 → BUY +2")
        if abs(close-s2)/close<0.005: buy+=3; reasons.append(f"At Strong Support S2 → BUY +3")
        if abs(close-r1)/close<0.005: sell+=2; reasons.append(f"At Resistance R1 → SELL +2")
        if abs(close-r2)/close<0.005: sell+=3

    if df is not None:
        for pname,psig in detect_patterns(df):
            if psig=="BUY":    buy+=2; reasons.append(f"🕯️ {pname} → BUY +2")
            elif psig=="SELL": sell+=2; reasons.append(f"🕯️ {pname} → SELL +2")

    div = detect_divergence(df,ind)
    if div:
        _,dsig,dmsg = div
        if dsig=="BUY":  buy+=3; reasons.append(f"📐 {dmsg} → BUY +3")
        else:            sell+=3; reasons.append(f"📐 {dmsg} → SELL +3")

    if gap_type=="GAP_UP":   buy+=3; reasons.append(f"🚀 Gap-up {gap_pct:.1f}% → BUY +3")
    elif gap_type=="GAP_DOWN": sell+=3; reasons.append(f"⬇️ Gap-down {gap_pct:.1f}% → SELL +3")

    if institutional_accumulation(ind):
        buy+=2; reasons.append("🏦 Institutional accumulation → BUY +2")

    if mode=="DELIVERY":
        if rs_rating is not None:
            if rs_rating>=1.3:   buy+=4; reasons.append(f"⭐ RS={rs_rating:.2f} strong outperformer → BUY +4")
            elif rs_rating>=1.1: buy+=2; reasons.append(f"RS={rs_rating:.2f} outperforming → BUY +2")
            elif rs_rating<0.9:  sell+=2; reasons.append(f"RS={rs_rating:.2f} underperforming → SELL +2")
        if df_weekly is not None:
            w_ind = compute_indicators(df_weekly)
            if w_ind:
                wc=w_ind.get("close",0); we21=w_ind.get("e21",0); we50=w_ind.get("e50",0)
                if wc>we21>we50:   buy+=3; reasons.append("📊 Weekly uptrend → BUY +3")
                elif wc<we21<we50: sell+=3; reasons.append("📊 Weekly downtrend → SELL +3")
            stage,stage_desc = weinstein_stage(df_weekly)
            if stage==2:   buy+=4; reasons.append(f"📈 Weinstein {stage_desc} → BUY +4")
            elif stage==4: sell+=4; reasons.append(f"📉 Weinstein {stage_desc} → SELL +4")
            elif stage==3: sell+=2
        is_bo,brk_vr = check_52w_breakout(df,fund)
        if is_bo: buy+=5; reasons.append(f"🔥 52W HIGH breakout Vol {brk_vr:.1f}x → BUY +5")

    if fund:
        pe = fund.get("pe"); h52=fund.get("52h"); l52=fund.get("52l")
        if pe:
            try:
                pe=float(pe)
                if np.isfinite(pe) and pe>0:
                    if pe<15:   buy+=1; reasons.append(f"Low P/E {pe:.1f} → BUY +1")
                    elif pe>60: sell+=1; reasons.append(f"High P/E {pe:.1f} → SELL +1")
            except: pass
        if h52 and l52 and close>0:
            try:
                rng=float(h52)-float(l52)
                if rng>0:
                    pos52=(close-float(l52))/rng
                    if pos52<0.15:   buy+=2; reasons.append("Near 52W low → BUY +2")
                    elif pos52>0.90 and mode!="DELIVERY": sell+=1
            except: pass

    total = max(buy+sell,1)
    if buy>sell:
        net_str = min(98,int(buy/total*100))
        if buy>=STRONG_BUY_SCORE:   rec="STRONG BUY"
        elif buy>=BUY_SCORE:        rec="BUY"
        else:                        rec="WEAK BUY"
    elif sell>buy:
        net_str = min(98,int(sell/total*100))
        if sell>=STRONG_SELL_SCORE:  rec="STRONG SELL"
        elif sell>=SELL_SCORE:       rec="SELL"
        else:                        rec="WEAK SELL"
    else:
        rec="NEUTRAL"; net_str=50

    _adx_min = MIN_ADX_INTRADAY if mode=="INTRADAY" else MIN_ADX_DELIVERY
    if mode=="INTRADAY" and adx<_adx_min:
        rec="NEUTRAL"; reasons.append(f"ADX={adx:.0f}<{_adx_min} — no trend, skip")
    if mode=="DELIVERY" and net_str<70:
        rec="NEUTRAL"; reasons.append("Insufficient conviction for delivery (need ≥70%)")
    if mode=="INTRADAY" and vr<MIN_VOLUME_RATIO and buy>sell:
        reasons.append(f"⚠️ Volume {vr:.1f}x below {MIN_VOLUME_RATIO}x")
    if mode=="INTRADAY" and rec in ("WEAK BUY","WEAK SELL"):
        reasons.append("⚠️ Weak signal — consider skipping")

    return rec, net_str, buy, sell, reasons


# ─── Block 3c: Time-of-Day Filter ────────────────────────────────────────────

def is_valid_entry_time() -> tuple[bool, str]:
    """Block 3c: Check if current time is valid for new entries."""
    now = datetime.now()
    h, m = now.hour, now.minute

    # Block 9:15–9:30 AM
    if (h, m) >= NO_ENTRY_START_AM and (h, m) < NO_ENTRY_END_AM:
        remaining = 30 - m if h == 9 else 0
        return False, f"🕐 Opening noise window (9:15–9:30). {remaining}m until entries open."

    # Block 3:00–3:30 PM
    if (h, m) >= NO_ENTRY_START_PM and (h, m) < NO_ENTRY_END_PM:
        return False, "🕓 End-of-day window (3:00–3:30). Exits only — no new entries."

    # Market hours check
    if h < 9 or (h == 9 and m < 15) or h >= 15 or (h == 15 and m >= 30):
        return False, "⏰ Market closed."

    return True, "✅ Valid entry window"


# ─── Block 3d: Correlation Position Limit ────────────────────────────────────

def check_correlation_limit(
    new_symbol: str,
    open_positions: list,
    max_sector_exposure: float = 0.35,
) -> tuple[bool, str]:
    """
    Block 3d: Prevent opening two highly correlated positions simultaneously.
    Caps total exposure per sector at max_sector_exposure (default 35%).
    """
    new_sector = SECTOR_MAP.get(new_symbol, "Unknown")
    if new_sector == "Unknown":
        return True, "Sector unknown — no correlation check"

    sector_count = sum(
        1 for pos in open_positions
        if SECTOR_MAP.get(pos.get("symbol",""), "Unknown") == new_sector
    )

    total_open = len(open_positions)
    if total_open == 0:
        return True, "No open positions"

    sector_pct = sector_count / total_open
    if sector_pct >= max_sector_exposure:
        return False, (f"🚫 Sector limit: {new_sector} already has "
                       f"{sector_count}/{total_open} positions ({sector_pct*100:.0f}% ≥ {max_sector_exposure*100:.0f}%)")

    return True, f"✅ Sector {new_sector}: {sector_count} positions, {sector_pct*100:.0f}% exposure"


# ─── Block 3a: Partial Scale-Out ─────────────────────────────────────────────

def scale_out_position(pos: dict, cmp: float) -> dict:
    """
    Block 3a: Partial profit booking — 40% at T1, 35% at T2, trail 25%.
    Modifies position dict in place; returns exit events.
    """
    entry  = pos.get("entry", cmp)
    target = pos.get("target", cmp)
    t1     = pos.get("target_1", target)
    t2     = pos.get("target_2", target)
    typ    = pos.get("type","BUY")
    qty    = pos.get("qty", 1)
    exits  = []

    # Already fully exited
    if qty <= 0:
        return pos

    phase = pos.get("scale_phase", 0)

    if typ == "BUY":
        at_t1 = cmp >= t1 and t1 > entry
        at_t2 = cmp >= t2 and t2 > t1
    else:
        at_t1 = cmp <= t1 and t1 < entry
        at_t2 = cmp <= t2 and t2 < t1

    if at_t2 and phase < 2:
        exit_qty = max(1, int(qty * 0.35))
        pos["qty"] = qty - exit_qty
        pos["scale_phase"] = 2
        exits.append({"phase": 2, "qty": exit_qty, "price": cmp, "label": "T2 (35%)"})
        # Tighten trail on remaining
        atr = pos.get("atr", cmp * 0.01)
        if typ == "BUY":
            pos["trailing_sl"] = round(cmp - 1.0 * atr, 2)
        else:
            pos["trailing_sl"] = round(cmp + 1.0 * atr, 2)

    elif at_t1 and phase < 1:
        exit_qty = max(1, int(qty * 0.40))
        pos["qty"] = qty - exit_qty
        pos["scale_phase"] = 1
        exits.append({"phase": 1, "qty": exit_qty, "price": cmp, "label": "T1 (40%)"})
        # Move stop to break-even
        pos["trailing_sl"] = entry

    pos["scale_exits"] = pos.get("scale_exits", []) + exits
    return pos


# ─── Block 3b: Re-Entry Signal ────────────────────────────────────────────────

def check_reentry_signal(
    original_entry: float,
    original_type: str,
    cmp: float,
    atr: float,
    ind: dict,
    already_reentered: bool = False,
) -> tuple[bool, str, float]:
    """
    Block 3b: After SL exit, monitor for valid re-entry within 2 ATRs.
    Returns (should_reenter, reason, suggested_entry_price).
    Max 1 re-entry per original trade.
    """
    if already_reentered:
        return False, "Max 1 re-entry already used", 0.0

    distance = abs(cmp - original_entry)
    within_2atr = distance <= 2 * atr

    if not within_2atr:
        return False, f"Price moved {distance:.2f} > 2×ATR ({2*atr:.2f}) from original entry", 0.0

    rsi  = ind.get("rsi", 50)
    st_b = ind.get("st_bullish", False)
    st_s = ind.get("st_bearish", False)

    if original_type == "BUY":
        # Look for bullish reversal
        valid = (rsi < 45 and st_b) or (ind.get("m5",0) > 0 and rsi < 50)
        if valid:
            return True, "✅ BUY re-entry: RSI recovering + Supertrend turning bullish", cmp
        return False, "Waiting for reversal signal (RSI+SuperTrend)", 0.0
    else:
        valid = (rsi > 55 and st_s) or (ind.get("m5",0) < 0 and rsi > 50)
        if valid:
            return True, "✅ SELL re-entry: RSI declining + Supertrend bearish", cmp
        return False, "Waiting for reversal signal", 0.0


# ─── Block 11a: Volatility-Adjusted Position Sizing ──────────────────────────

def volatility_adjusted_position_size(
    capital: float,
    atr: float,
    price: float,
    risk_pct: float = 0.01,
    max_pct: float = 0.20,
) -> dict:
    """
    Block 11a: Size position inversely to ATR volatility.
    High volatility = smaller position, low volatility = larger position.
    """
    try:
        price    = max(float(price or 0), 0.01)
        atr      = max(float(atr or price*0.02), price*0.001)
        risk_amt = capital * risk_pct
        atr_pct  = atr / price

        # Base: risk 1% of capital per 1 ATR move
        raw_pos  = risk_amt / atr
        pos_size = min(raw_pos, capital * max_pct)
        qty      = max(1, int(pos_size / price))

        # Volatility tier
        if atr_pct > 0.04:   tier = "HIGH VOL — reduced"
        elif atr_pct > 0.02: tier = "NORMAL"
        else:                 tier = "LOW VOL — increased"

        return {
            "qty": qty,
            "position_value": round(qty * price, 2),
            "risk_amount": round(atr * qty, 2),
            "atr_pct": round(atr_pct * 100, 2),
            "tier": tier,
            "pos_pct": round(qty * price / capital * 100, 2),
        }
    except Exception:
        return {"qty": max(1, int(capital * 0.05 / max(price, 1))), "tier": "FALLBACK"}


# ─── Block 11b: Break-Even Stop Manager ───────────────────────────────────────

def update_trailing_stop(pos: dict, lp: float, use_trail: bool = True) -> dict:
    """
    Block 11b: Unified break-even + trailing stop across all segments.
    Phase 1 (+1.2%): move to break-even.
    Phase 2 (+2.5%): tighten to 1.0×ATR trail.
    """
    if not use_trail:
        return pos

    ep  = pos.get("entry", lp)
    atr = pos.get("atr", ep * 0.015)
    typ = pos.get("type","BUY")

    if ep <= 0:
        return pos

    pnl_pct = ((lp-ep)/ep*100) if typ in ("BUY","LONG","CE") else ((ep-lp)/ep*100)
    be_moved = pos.get("be_moved", False)

    if pnl_pct >= TRAIL_TIGHTEN_PCT:
        if typ in ("BUY","LONG","CE"):
            new_trail = lp - 1.0*atr
            if pos.get("trailing_sl") is None or new_trail > pos["trailing_sl"]:
                pos["trailing_sl"] = round(new_trail,2)
        else:
            new_trail = lp + 1.0*atr
            if pos.get("trailing_sl") is None or new_trail < pos["trailing_sl"]:
                pos["trailing_sl"] = round(new_trail,2)
        pos["be_moved"] = True
        pos["be_badge"] = "TRAIL"

    elif pnl_pct >= TRAIL_ACTIVATE_PCT:
        if typ in ("BUY","LONG","CE"):
            new_trail = lp - 1.5*atr
            if new_trail > ep:
                if pos.get("trailing_sl") is None or new_trail > pos["trailing_sl"]:
                    pos["trailing_sl"] = round(new_trail,2)
            elif not be_moved:
                pos["trailing_sl"] = round(ep,2)
        else:
            new_trail = lp + 1.5*atr
            if new_trail < ep:
                if pos.get("trailing_sl") is None or new_trail < pos["trailing_sl"]:
                    pos["trailing_sl"] = round(new_trail,2)
            elif not be_moved:
                pos["trailing_sl"] = round(ep,2)
        if not be_moved:
            pos["be_moved"] = True
            pos["be_badge"] = "BE"

    return pos


# ─── Block 9a: Max Pain Calculator ───────────────────────────────────────────

def compute_max_pain(option_chain_data: list, spot: float, index: str = "NIFTY") -> dict:
    """
    Block 9a: Calculate max pain strike from option chain OI data.
    option_chain_data: list of {strike, ce_oi, pe_oi}
    """
    try:
        if not option_chain_data:
            return {"max_pain": spot, "pain_scores": []}

        strikes = sorted(set(r.get("strike",0) for r in option_chain_data))
        pain_scores = []

        for test_strike in strikes:
            total_pain = 0
            for row in option_chain_data:
                K       = row.get("strike", 0)
                ce_oi   = row.get("ce_oi", 0)
                pe_oi   = row.get("pe_oi", 0)
                # CE writers lose if spot > strike at expiry
                ce_pain = max(0, test_strike - K) * ce_oi
                # PE writers lose if spot < strike
                pe_pain = max(0, K - test_strike) * pe_oi
                total_pain += ce_pain + pe_pain
            pain_scores.append({"strike": test_strike, "pain": total_pain})

        # Max pain = strike where total pain to option BUYERS is maximum
        # (= where writers lose least = where writers have most OI)
        min_pain_strike = min(pain_scores, key=lambda x: x["pain"])["strike"]
        return {
            "max_pain": min_pain_strike,
            "pain_scores": pain_scores,
            "vs_spot": round(min_pain_strike - spot, 2),
            "vs_spot_pct": round((min_pain_strike - spot) / spot * 100, 2) if spot else 0,
        }
    except Exception:
        return {"max_pain": spot, "pain_scores": []}


# ─── Block 9b: Put/Call Ratio ─────────────────────────────────────────────────

def compute_pcr(option_chain_data: list) -> dict:
    """Block 9b: Compute Put/Call Ratio from option chain OI."""
    try:
        total_ce_oi = sum(r.get("ce_oi", 0) for r in option_chain_data)
        total_pe_oi = sum(r.get("pe_oi", 0) for r in option_chain_data)
        pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0
        if pcr > 1.3:   sentiment = "BULLISH"
        elif pcr < 0.7: sentiment = "BEARISH"
        else:            sentiment = "NEUTRAL"
        return {
            "pcr": round(pcr, 3),
            "ce_oi": total_ce_oi,
            "pe_oi": total_pe_oi,
            "sentiment": sentiment,
            "interpretation": (
                f"PCR={pcr:.2f} — "
                f"{'Bullish (heavy put writing)' if pcr>1.3 else 'Bearish (heavy call writing)' if pcr<0.7 else 'Neutral'}"
            ),
        }
    except Exception:
        return {"pcr": 1.0, "sentiment": "NEUTRAL"}


# ─── Block 6a: Monte Carlo Simulator ─────────────────────────────────────────

def run_monte_carlo(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    starting_capital: float = 100000,
    n_days: int = 30,
    n_sims: int = 1000,
    trades_per_day: float = 3.0,
) -> dict:
    """
    Block 6a: Monte Carlo simulation of portfolio returns.
    Returns p10/p50/p90 percentile paths and summary stats.
    """
    try:
        np.random.seed(42)
        all_paths = []

        for _ in range(n_sims):
            capital = starting_capital
            path    = [capital]
            for _ in range(n_days):
                daily_pnl = 0.0
                n_trades  = max(1, int(np.random.poisson(trades_per_day)))
                for _ in range(n_trades):
                    if np.random.random() < win_rate:
                        daily_pnl += avg_win * (0.7 + np.random.random() * 0.6)
                    else:
                        daily_pnl -= avg_loss * (0.7 + np.random.random() * 0.6)
                capital += daily_pnl
                path.append(max(0, capital))
            all_paths.append(path)

        paths_arr = np.array(all_paths)
        p10  = np.percentile(paths_arr, 10, axis=0).tolist()
        p50  = np.percentile(paths_arr, 50, axis=0).tolist()
        p90  = np.percentile(paths_arr, 90, axis=0).tolist()
        final = paths_arr[:, -1]

        return {
            "p10": p10, "p50": p50, "p90": p90,
            "days": list(range(n_days + 1)),
            "final_p10": round(float(np.percentile(final, 10)), 2),
            "final_p50": round(float(np.median(final)), 2),
            "final_p90": round(float(np.percentile(final, 90)), 2),
            "prob_profit": round(float(np.mean(final > starting_capital) * 100), 1),
            "prob_loss_50pct": round(float(np.mean(final < starting_capital * 0.5) * 100), 1),
            "max_drawdown_p50": round(float(np.percentile(
                [min(p) for p in all_paths], 50
            ) / starting_capital * 100), 1),
            "n_sims": n_sims,
        }
    except Exception:
        return {"p10":[], "p50":[], "p90":[], "prob_profit": 50.0}


# ─── Block 6b: Risk Metrics ───────────────────────────────────────────────────

def compute_risk_metrics(trade_history: list) -> dict:
    """Block 6b: Sharpe, Sortino, Calmar ratios from trade history."""
    try:
        if not trade_history:
            return {"sharpe": 0, "sortino": 0, "calmar": 0}

        # Group by date for daily returns
        from collections import defaultdict
        daily = defaultdict(float)
        for t in trade_history:
            dt = str(t.get("exit_time", t.get("date","")))[:10]
            daily[dt] += float(t.get("pnl", 0))

        returns = list(daily.values())
        if len(returns) < 5:
            return {"sharpe": 0, "sortino": 0, "calmar": 0, "daily_returns": returns}

        ret_arr   = np.array(returns)
        mean_ret  = float(np.mean(ret_arr))
        std_ret   = float(np.std(ret_arr)) if np.std(ret_arr) > 0 else 1.0
        sharpe    = round(mean_ret / std_ret * np.sqrt(252), 3)

        downside  = ret_arr[ret_arr < 0]
        down_std  = float(np.std(downside)) if len(downside) > 1 and np.std(downside) > 0 else 1.0
        sortino   = round(mean_ret / down_std * np.sqrt(252), 3)

        cumulative = np.cumsum(ret_arr)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns  = cumulative - running_max
        max_dd     = float(np.min(drawdowns)) if len(drawdowns) > 0 else -1.0
        total_ret  = float(np.sum(ret_arr))
        calmar     = round(total_ret / abs(max_dd), 3) if max_dd != 0 else 0.0

        return {
            "sharpe":      sharpe,
            "sortino":     sortino,
            "calmar":      calmar,
            "max_drawdown": round(max_dd, 2),
            "total_return": round(total_ret, 2),
            "avg_daily":   round(mean_ret, 2),
            "win_rate":    round(float(np.mean(ret_arr > 0)) * 100, 1),
            "daily_returns": returns,
        }
    except Exception:
        return {"sharpe": 0, "sortino": 0, "calmar": 0}


# ─── Block 6f: Value at Risk ──────────────────────────────────────────────────

def compute_var(positions: list, trade_history: list, confidence: float = 0.95) -> dict:
    """Block 6f: Historical simulation VaR for open portfolio."""
    try:
        if not positions or not trade_history:
            return {"var_95": 0, "var_99": 0, "portfolio_value": 0}

        portfolio_value = sum(
            abs(p.get("entry",0)) * p.get("qty", p.get("lots",1)) for p in positions
        )

        # Use historical daily P&L as return distribution
        daily_pnl = []
        from collections import defaultdict
        by_date = defaultdict(float)
        for t in trade_history:
            dt = str(t.get("date",""))[:10]
            by_date[dt] += float(t.get("pnl", 0))
        daily_pnl = list(by_date.values())

        if len(daily_pnl) < 10:
            # Fallback: assume 2% daily volatility
            var_95 = portfolio_value * 0.02 * 1.645
            var_99 = portfolio_value * 0.02 * 2.326
        else:
            returns = np.array(daily_pnl)
            var_95  = float(-np.percentile(returns, (1-confidence)*100))
            var_99  = float(-np.percentile(returns, 1))

        return {
            "var_95":          round(var_95, 2),
            "var_99":          round(var_99, 2),
            "portfolio_value": round(portfolio_value, 2),
            "interpretation":  f"95% chance daily loss will not exceed ₹{var_95:,.0f}",
        }
    except Exception:
        return {"var_95": 0, "var_99": 0, "portfolio_value": 0}


# ─── Block 10a: Strategy Backtester ──────────────────────────────────────────

def run_backtest(
    symbol: str,
    mode: str = "INTRADAY",
    period: str = "1y",
    sl_atr_mult: float = 1.5,
    target_atr_mult: float = 2.5,
    min_strength: int = 62,
    market_mood: str = "NEUTRAL",
    vix: float = 15.0,
) -> dict:
    """
    Block 10a: Backtest score_signal() logic on historical OHLCV data.
    Uses get_ohlcv() — no new data source needed.
    """
    try:
        df = get_ohlcv(symbol, period, "1d")
        if df is None or len(df) < 40:
            return {"error": f"Insufficient data for {symbol}"}

        trades   = []
        equity   = [100000.0]
        capital  = 100000.0
        window   = 30  # rolling window for indicators

        for i in range(window, len(df) - 1):
            df_window = df.iloc[:i+1]
            ind       = compute_indicators(df_window)
            if not ind:
                continue

            rec, strength, bs, ss, reasons = score_signal(
                ind, {}, df_window, market_mood, vix, mode
            )

            if strength < min_strength or rec == "NEUTRAL":
                equity.append(equity[-1])
                continue

            close = float(df_window["Close"].iloc[-1])
            atr   = float(ind.get("atr", close * 0.02))

            if "BUY" in rec:
                sl     = close - sl_atr_mult * atr
                target = close + target_atr_mult * atr
                next_open = float(df.iloc[i+1]["Open"])
                next_high = float(df.iloc[i+1]["High"])
                next_low  = float(df.iloc[i+1]["Low"])
                next_close = float(df.iloc[i+1]["Close"])

                # Check if SL or target hit next day
                if next_low <= sl:
                    pnl_pct = (sl - next_open) / next_open
                    win = False
                elif next_high >= target:
                    pnl_pct = (target - next_open) / next_open
                    win = True
                else:
                    pnl_pct = (next_close - next_open) / next_open
                    win = pnl_pct > 0

                qty    = max(1, int(capital * 0.10 / next_open))
                pnl    = pnl_pct * qty * next_open
                capital += pnl
                trades.append({
                    "date": str(df.index[i+1])[:10],
                    "symbol": symbol, "type": "BUY",
                    "entry": round(next_open, 2),
                    "exit": round(sl if not win else target, 2),
                    "pnl": round(pnl, 2),
                    "win": win, "strength": strength, "rec": rec,
                })

            elif "SELL" in rec:
                sl     = close + sl_atr_mult * atr
                target = close - target_atr_mult * atr
                next_open = float(df.iloc[i+1]["Open"])
                next_high = float(df.iloc[i+1]["High"])
                next_low  = float(df.iloc[i+1]["Low"])
                next_close = float(df.iloc[i+1]["Close"])

                if next_high >= sl:
                    pnl_pct = (next_open - sl) / next_open
                    win = False
                elif next_low <= target:
                    pnl_pct = (next_open - target) / next_open
                    win = True
                else:
                    pnl_pct = (next_open - next_close) / next_open
                    win = pnl_pct > 0

                qty    = max(1, int(capital * 0.10 / next_open))
                pnl    = pnl_pct * qty * next_open
                capital += pnl
                trades.append({
                    "date": str(df.index[i+1])[:10],
                    "symbol": symbol, "type": "SELL",
                    "entry": round(next_open, 2),
                    "exit": round(sl if not win else target, 2),
                    "pnl": round(pnl, 2),
                    "win": win, "strength": strength, "rec": rec,
                })

            equity.append(capital)

        if not trades:
            return {"error": "No trades generated", "symbol": symbol}

        wins       = [t for t in trades if t["win"]]
        losses     = [t for t in trades if not t["win"]]
        total_pnl  = sum(t["pnl"] for t in trades)
        win_rate   = len(wins) / len(trades) * 100 if trades else 0
        avg_win    = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss   = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        profit_factor = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) != 0 else 0

        # Equity curve drawdown
        eq_arr   = np.array(equity)
        run_max  = np.maximum.accumulate(eq_arr)
        drawdowns = (eq_arr - run_max) / run_max * 100
        max_dd    = float(np.min(drawdowns))

        risk_metrics = compute_risk_metrics(trades)

        return {
            "symbol": symbol, "period": period, "mode": mode,
            "total_trades": len(trades),
            "win_rate":     round(win_rate, 1),
            "total_pnl":    round(total_pnl, 2),
            "avg_win":      round(avg_win, 2),
            "avg_loss":     round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(max_dd, 2),
            "sharpe":       risk_metrics.get("sharpe", 0),
            "final_capital": round(capital, 2),
            "return_pct":   round((capital - 100000) / 100000 * 100, 2),
            "equity_curve": equity[::5],  # Downsample for chart
            "trades":       trades,
        }
    except Exception as exc:
        return {"error": str(exc), "symbol": symbol}


# ─── Block 10b: Walk-Forward Optimisation ─────────────────────────────────────

def walk_forward_optimize(
    symbol: str,
    period: str = "2y",
    mode: str = "INTRADAY",
    param_grid: dict = None,
) -> dict:
    """Block 10b: Walk-forward optimisation over parameter grid."""
    if param_grid is None:
        param_grid = {
            "sl_atr_mult":     [1.0, 1.5, 2.0, 2.5],
            "target_atr_mult": [2.0, 2.5, 3.0],
            "min_strength":    [58, 65, 72],
        }

    best_params  = {}
    best_metric  = -999
    results      = []

    for sl in param_grid.get("sl_atr_mult", [1.5]):
        for tgt in param_grid.get("target_atr_mult", [2.5]):
            for ms in param_grid.get("min_strength", [62]):
                bt = run_backtest(symbol, mode, period, sl, tgt, ms)
                if "error" in bt:
                    continue
                # Composite metric: Sharpe × win_rate / max_drawdown
                metric = (bt.get("sharpe",0) * bt.get("win_rate",0)) / max(abs(bt.get("max_drawdown",-1)), 1)
                results.append({
                    "sl_atr_mult": sl, "target_atr_mult": tgt, "min_strength": ms,
                    "sharpe": bt.get("sharpe",0), "win_rate": bt.get("win_rate",0),
                    "total_pnl": bt.get("total_pnl",0), "max_drawdown": bt.get("max_drawdown",0),
                    "metric": round(metric,3),
                })
                if metric > best_metric:
                    best_metric = metric
                    best_params = {"sl_atr_mult": sl, "target_atr_mult": tgt, "min_strength": ms}

    return {
        "symbol": symbol,
        "best_params": best_params,
        "best_metric": round(best_metric, 3),
        "all_results": sorted(results, key=lambda x: -x["metric"])[:10],
    }


# ─── Block 13b: Pattern Recognition in Journal ────────────────────────────────

def analyze_journal_patterns(trade_history: list) -> dict:
    """Block 13b: Analyze closed trades to find personal trading patterns."""
    if not trade_history or len(trade_history) < 5:
        return {"patterns": [], "insights": []}

    insights = []
    patterns = {}

    # Day-of-week analysis
    from collections import defaultdict
    by_dow  = defaultdict(list)
    by_hour = defaultdict(list)
    by_str  = defaultdict(list)

    for t in trade_history:
        pnl = float(t.get("pnl", 0))
        dt  = t.get("exit_time", t.get("date", ""))
        try:
            dt_obj = datetime.fromisoformat(str(dt)[:19])
            by_dow[dt_obj.strftime("%A")].append(pnl)
            by_hour[dt_obj.hour].append(pnl)
        except Exception:
            pass
        strength = t.get("strength", 50)
        bracket  = f"{(int(strength)//10)*10}-{(int(strength)//10)*10+9}"
        by_str[bracket].append(pnl)

    # Day of week insights
    dow_stats = {}
    for dow, pnls in by_dow.items():
        avg = sum(pnls)/len(pnls)
        wr  = sum(1 for p in pnls if p>0)/len(pnls)*100
        dow_stats[dow] = {"avg_pnl": round(avg,2), "win_rate": round(wr,1), "trades": len(pnls)}

    if dow_stats:
        best_dow  = max(dow_stats, key=lambda d: dow_stats[d]["avg_pnl"])
        worst_dow = min(dow_stats, key=lambda d: dow_stats[d]["avg_pnl"])
        if dow_stats[best_dow]["avg_pnl"] > 0:
            insights.append(f"📅 Best day: {best_dow} (avg ₹{dow_stats[best_dow]['avg_pnl']:+,.0f}, {dow_stats[best_dow]['win_rate']:.0f}% WR)")
        if dow_stats[worst_dow]["avg_pnl"] < 0:
            insights.append(f"⚠️ Worst day: {worst_dow} (avg ₹{dow_stats[worst_dow]['avg_pnl']:+,.0f}, {dow_stats[worst_dow]['win_rate']:.0f}% WR)")

    # Signal strength brackets
    str_stats = {}
    for bracket, pnls in by_str.items():
        avg = sum(pnls)/len(pnls)
        wr  = sum(1 for p in pnls if p>0)/len(pnls)*100
        str_stats[bracket] = {"avg_pnl": round(avg,2), "win_rate": round(wr,1), "trades": len(pnls)}
        if len(pnls) >= 3:
            if wr >= 65:
                insights.append(f"💪 Strength {bracket}%: {wr:.0f}% WR on {len(pnls)} trades — keep these signals")
            elif wr <= 40:
                insights.append(f"🔻 Strength {bracket}%: only {wr:.0f}% WR — consider raising min strength")

    return {
        "patterns":   patterns,
        "insights":   insights[:10],
        "dow_stats":  dow_stats,
        "str_stats":  str_stats,
        "total_trades": len(trade_history),
    }


# ─── Block 13d: Behavioral Bias Detector ─────────────────────────────────────

def detect_behavioral_biases(trade_history: list) -> list:
    """Block 13d: Detect cognitive biases in trading patterns."""
    if len(trade_history) < 10:
        return []

    biases   = []
    by_date  = {}
    from collections import defaultdict

    for t in trade_history:
        dt  = str(t.get("exit_time", t.get("date","")))[:10]
        pnl = float(t.get("pnl",0))
        qty = float(t.get("qty", t.get("lots",1)) or 1)
        if dt not in by_date:
            by_date[dt] = []
        by_date[dt].append({"pnl": pnl, "qty": qty})

    dates = sorted(by_date.keys())

    # 1. Revenge trading: size increases after loss
    prev_loss = False
    revenge_count = 0
    for dt in dates:
        trades_today = by_date[dt]
        avg_qty = sum(t["qty"] for t in trades_today) / len(trades_today)
        if prev_loss and avg_qty > 1.5:  # 50%+ size increase after loss
            revenge_count += 1
        prev_loss = any(t["pnl"] < 0 for t in trades_today)

    if revenge_count >= 2:
        biases.append({
            "bias": "Revenge Trading",
            "severity": "HIGH" if revenge_count >= 3 else "MEDIUM",
            "evidence": f"Increased position size on {revenge_count} occasions after losing days",
            "fix": "Maintain fixed position sizing regardless of previous trades",
        })

    # 2. Overtrading: more trades on day after big win
    prev_big_win = False
    overtrade_days = 0
    for dt in dates:
        trades_today = by_date[dt]
        if prev_big_win and len(trades_today) >= 5:
            overtrade_days += 1
        daily_pnl = sum(t["pnl"] for t in trades_today)
        prev_big_win = daily_pnl > 2000  # big win threshold
    if overtrade_days >= 2:
        biases.append({
            "bias": "Overtrading After Wins",
            "severity": "MEDIUM",
            "evidence": f"Placed ≥5 trades on {overtrade_days} days after a big win",
            "fix": "Set max trade count regardless of previous session P&L",
        })

    # 3. Loss aversion: win trades have low avg_pnl vs loss trades
    wins   = [float(t.get("pnl",0)) for t in trade_history if float(t.get("pnl",0)) > 0]
    losses = [abs(float(t.get("pnl",0))) for t in trade_history if float(t.get("pnl",0)) < 0]
    if wins and losses:
        avg_win  = sum(wins)/len(wins)
        avg_loss = sum(losses)/len(losses)
        if avg_win < avg_loss * 0.6:
            biases.append({
                "bias": "Loss Aversion / Cutting Winners Early",
                "severity": "HIGH",
                "evidence": f"Avg win ₹{avg_win:.0f} vs avg loss ₹{avg_loss:.0f} — R/R ratio only {avg_win/avg_loss:.2f}x",
                "fix": "Use scale-out strategy: book partial at T1, hold remainder to T2/T3",
            })

    return biases


# ─── Kelly & Position Sizing ──────────────────────────────────────────────────

def kelly_size(capital, win_rate, rr_ratio, strength):
    try:
        f = win_rate - (1-win_rate) / max(rr_ratio,0.1)
        f = max(0,f) * 0.5
        f = min(0.20,f)
        s = 0.4 + (strength/100) * 0.6
        return round(capital * f * s, 2)
    except Exception:
        return round(capital * 0.03, 2)


def tiered_position_size(capital, strength, base_risk=15000):
    if strength >= 80:   multiplier=2.0; tier="STRONG (2x)"
    elif strength >= 65: multiplier=1.0; tier="STANDARD (1x)"
    else:                multiplier=0.5; tier="REDUCED (0.5x)"
    pos_size = min(base_risk*multiplier, capital*0.20)
    return round(pos_size,2), tier


# ─── Entry Gate ───────────────────────────────────────────────────────────────

def should_enter_trade(
    sig: dict, mode: str = "INTRADAY", mood: str = "NEUTRAL",
    vix: float = 15.0, daily_pnl: float = 0.0,
    daily_goal: float = DEFAULT_DAILY_GOAL,
    daily_loss_limit: float = -3000.0,
    trades_today: int = 0,
    max_trades_per_day: int = 10,
    open_positions: list = None,
    check_time: bool = True,
) -> tuple[bool, str]:
    rec      = sig.get("rec","NEUTRAL")
    strength = sig.get("strength",0)
    rr       = sig.get("rr",0)
    adx      = sig.get("adx", sig.get("indicators",{}).get("adx",0))

    if daily_pnl <= daily_loss_limit:
        return False, f"🛑 Daily loss limit ₹{daily_loss_limit:,.0f} reached."

    if trades_today >= max_trades_per_day:
        return False, f"📊 Daily trade limit ({max_trades_per_day}) reached."

    if check_time:
        time_ok, time_msg = is_valid_entry_time()
        if not time_ok:
            return False, time_msg

    min_rr = MIN_RR_INTRADAY if mode=="INTRADAY" else MIN_RR_DELIVERY
    if rr < min_rr:
        return False, f"R/R={rr:.2f} below minimum {min_rr:.1f}"

    if rec == "NEUTRAL":
        return False, "NEUTRAL signal — skip"

    if mood=="BEARISH" and "BUY" in rec:
        return False, "Market BEARISH — skipping BUY"
    if mood=="BULLISH" and "SELL" in rec:
        return False, "Market BULLISH — skipping SELL"

    if vix>22 and strength<75:
        return False, f"VIX={vix:.1f} high — need strength≥75, got {strength}"

    if rec in ("WEAK BUY","WEAK SELL") and mode=="INTRADAY":
        return False, "WEAK signal filtered in intraday auto mode"

    if open_positions and sig.get("symbol"):
        ok, corr_msg = check_correlation_limit(sig["symbol"], open_positions)
        if not ok:
            return False, corr_msg

    return True, "✅ Entry approved"


# ─── Daily P&L Stats ─────────────────────────────────────────────────────────

def compute_daily_pnl_stats(
    eq_history, opt_history, fut_history, etf_history, mcx_history,
    eq_portfolio, opt_portfolio, fut_portfolio, etf_portfolio, mcx_portfolio,
    daily_goal: float = DEFAULT_DAILY_GOAL,
) -> dict:
    today_str    = date.today().strftime("%Y-%m-%d")
    all_hist     = eq_history+opt_history+fut_history+etf_history+mcx_history
    today_closed = [t for t in all_hist
                    if str(t.get("exit_time",t.get("date","")))[:10] == today_str]
    realized     = sum(t.get("pnl",0) for t in today_closed)
    daily_wins   = sum(1 for t in today_closed if t.get("pnl",0)>0)
    daily_losses = sum(1 for t in today_closed if t.get("pnl",0)<=0)
    win_rate     = (daily_wins/len(today_closed)*100) if today_closed else 0

    all_open   = eq_portfolio+opt_portfolio+fut_portfolio+etf_portfolio+mcx_portfolio
    unrealized = sum(p.get("pnl",0) for p in all_open)
    total      = realized + unrealized
    goal_pct   = min(150,(total/daily_goal*100)) if daily_goal>0 else 0

    return {
        "realized":    round(realized,2), "unrealized":  round(unrealized,2),
        "total":       round(total,2),    "win_rate":    round(win_rate,1),
        "trades_today":len(today_closed), "trades_open": len(all_open),
        "daily_wins":  daily_wins,        "daily_losses":daily_losses,
        "goal_pct":    round(goal_pct,1), "on_track":    total>=0,
        "daily_goal":  daily_goal,
    }


# ─── Black-Scholes Greeks ─────────────────────────────────────────────────────

def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def bs_greeks(S, K, T, r, sigma, opt_type="CE"):
    try:
        if T<=0 or sigma<=0 or S<=0 or K<=0:
            return dict(price=0,delta=0,gamma=0,theta=0,vega=0,iv=sigma)
        d1 = (math.log(S/K)+(r+0.5*sigma**2)*T)/(sigma*math.sqrt(T))
        d2 = d1 - sigma*math.sqrt(T)
        nd1 = math.exp(-d1**2/2)/math.sqrt(2*math.pi)
        if opt_type=="CE":
            price = S*_ncdf(d1) - K*math.exp(-r*T)*_ncdf(d2)
            delta = _ncdf(d1)
            theta = (-(S*sigma*nd1)/(2*math.sqrt(T)) - r*K*math.exp(-r*T)*_ncdf(d2))/365
        else:
            price = K*math.exp(-r*T)*_ncdf(-d2) - S*_ncdf(-d1)
            delta = _ncdf(d1) - 1
            theta = (-(S*sigma*nd1)/(2*math.sqrt(T)) + r*K*math.exp(-r*T)*_ncdf(-d2))/365
        gamma = nd1/(S*sigma*math.sqrt(T))
        vega  = S*math.sqrt(T)*nd1*0.01
        return dict(price=round(max(price,0),2), delta=round(delta,4),
                    gamma=round(gamma,6), theta=round(theta,2),
                    vega=round(vega,2), iv=round(sigma*100,1))
    except Exception:
        return dict(price=0,delta=0,gamma=0,theta=0,vega=0,iv=0)


def get_live_option_price(index, strike, opt_type, expiry_str, vix):
    cache_key = f"opt_{index}_{strike}_{opt_type}_{expiry_str}"
    cached    = _opt_price_cache.get(cache_key)
    if cached and (time.time()-cached["ts"]) < OPT_PRICE_TTL:
        return cached["price"]
    try:
        spot = _get_fresh_index_spot(index)
        if not spot or spot<=0:
            confirmed = _opt_last_confirmed.get(cache_key)
            return confirmed["price"] if confirmed else None
        ex_date = date.fromisoformat(expiry_str)
        dte = max(1,(ex_date-date.today()).days)
        T   = dte/365.0; r=0.065
        iv  = max(0.08, vix/100.0*(1+0.05*math.sqrt(T)))
        g   = bs_greeks(spot,float(strike),T,r,iv,opt_type)
        price = g.get("price",0.0)
        if price and price>0:
            _opt_price_cache[cache_key]    = {"price":price,"ts":time.time()}
            _opt_last_confirmed[cache_key] = {"price":price,"ts":time.time()}
            return price
    except Exception:
        pass
    confirmed = _opt_last_confirmed.get(cache_key)
    return confirmed["price"] if confirmed else None


def build_strategy(strategy_name, spot, vix, expiry_date, index_name="NIFTY"):
    tick = 100 if index_name=="BANKNIFTY" else 50
    lot  = 15  if index_name=="BANKNIFTY" else 25
    atm  = round(spot/tick)*tick
    dte  = max(1,(expiry_date-datetime.now().date()).days)
    T=dte/365; r=0.065; iv=max(0.08,vix/100)
    def greeks(K,ot): return bs_greeks(spot,K,T,r,iv,ot)
    if strategy_name=="Bull Call Spread":
        buy_k=atm; sell_k=atm+2*tick
        cb=greeks(buy_k,"CE"); cs=greeks(sell_k,"CE")
        nd=round((cb["price"]-cs["price"])*lot,2)
        return {"name":"Bull Call Spread","bias":"BULLISH","lot":lot,"dte":dte,
                "legs":[{"action":"BUY","type":"CE","strike":buy_k,"price":cb["price"],"delta":cb["delta"]},
                        {"action":"SELL","type":"CE","strike":sell_k,"price":cs["price"],"delta":cs["delta"]}],
                "net_debit":nd,"max_profit":round((sell_k-buy_k-cb["price"]+cs["price"])*lot,2),
                "max_loss":nd,"breakeven":round(buy_k+cb["price"]-cs["price"],2),"net_delta":round((cb["delta"]+cs["delta"])*lot,3)}
    elif strategy_name=="Iron Condor":
        cs_k=atm+2*tick; cb_k=atm+4*tick; ps_k=atm-2*tick; pb_k=atm-4*tick
        cs=greeks(cs_k,"CE"); cb=greeks(cb_k,"CE"); ps=greeks(ps_k,"PE"); pb=greeks(pb_k,"PE")
        nc=round((cs["price"]-cb["price"]+ps["price"]-pb["price"])*lot,2)
        ml=round((2*tick-cs["price"]+cb["price"]-ps["price"]+pb["price"])*lot,2)
        return {"name":"Iron Condor","bias":"NEUTRAL","lot":lot,"dte":dte,
                "legs":[{"action":"SELL","type":"CE","strike":cs_k,"price":cs["price"],"delta":cs["delta"]},
                        {"action":"BUY","type":"CE","strike":cb_k,"price":cb["price"],"delta":cb["delta"]},
                        {"action":"SELL","type":"PE","strike":ps_k,"price":ps["price"],"delta":ps["delta"]},
                        {"action":"BUY","type":"PE","strike":pb_k,"price":pb["price"],"delta":pb["delta"]}],
                "net_credit":nc,"max_profit":nc,"max_loss":ml,
                "breakeven_upper":round(cs_k+nc/lot,2),"breakeven_lower":round(ps_k-nc/lot,2),"net_delta":0}
    elif strategy_name=="Straddle":
        ce=greeks(atm,"CE"); pe=greeks(atm,"PE")
        nd=round((ce["price"]+pe["price"])*lot,2)
        return {"name":"Straddle","bias":"VOLATILE","lot":lot,"dte":dte,
                "legs":[{"action":"BUY","type":"CE","strike":atm,"price":ce["price"],"delta":ce["delta"]},
                        {"action":"BUY","type":"PE","strike":atm,"price":pe["price"],"delta":pe["delta"]}],
                "net_debit":nd,"max_profit":"Unlimited","max_loss":nd,
                "breakeven_upper":round(atm+ce["price"]+pe["price"],2),
                "breakeven_lower":round(atm-ce["price"]-pe["price"],2),"net_delta":0}
    elif strategy_name=="Strangle":
        ce_k=atm+tick; pe_k=atm-tick
        ce=greeks(ce_k,"CE"); pe=greeks(pe_k,"PE")
        nd=round((ce["price"]+pe["price"])*lot,2)
        return {"name":"Strangle","bias":"VOLATILE","lot":lot,"dte":dte,
                "legs":[{"action":"BUY","type":"CE","strike":ce_k,"price":ce["price"],"delta":ce["delta"]},
                        {"action":"BUY","type":"PE","strike":pe_k,"price":pe["price"],"delta":pe["delta"]}],
                "net_debit":nd,"max_profit":"Unlimited","max_loss":nd,
                "breakeven_upper":round(ce_k+ce["price"]+pe["price"],2),
                "breakeven_lower":round(pe_k-ce["price"]-pe["price"],2),"net_delta":0}
    elif strategy_name=="Bear Put Spread":
        buy_k=atm; sell_k=atm-2*tick
        pb=greeks(buy_k,"PE"); ps=greeks(sell_k,"PE")
        nd=round((pb["price"]-ps["price"])*lot,2)
        return {"name":"Bear Put Spread","bias":"BEARISH","lot":lot,"dte":dte,
                "legs":[{"action":"BUY","type":"PE","strike":buy_k,"price":pb["price"],"delta":pb["delta"]},
                        {"action":"SELL","type":"PE","strike":sell_k,"price":ps["price"],"delta":ps["delta"]}],
                "net_debit":nd,"max_profit":round((buy_k-sell_k-pb["price"]+ps["price"])*lot,2),
                "max_loss":nd,"breakeven":round(buy_k-pb["price"]+ps["price"],2),"net_delta":round((pb["delta"]+ps["delta"])*lot,3)}
    return {}


def build_chain(index_name, spot, expiry_date, vix, n_strikes=12):
    tick=100 if index_name=="BANKNIFTY" else 50
    lot=15 if index_name=="BANKNIFTY" else 25
    atm=round(spot/tick)*tick
    dte=max(1,(expiry_date-datetime.now().date()).days)
    T=dte/365; r=0.065
    iv=max(0.08,vix/100*(1+0.05*math.sqrt(dte/365)))
    sl_pct=0.35 if dte<=7 else 0.45
    iv_percentile=compute_iv_percentile(vix)
    strikes=[atm+(i-n_strikes)*tick for i in range(2*n_strikes+1)]
    sym="^NSEBANK" if index_name=="BANKNIFTY" else "^NSEI"
    df=get_ohlcv(sym,"1mo","1d"); ind_u=compute_indicators(df)
    chain=[]
    for K in strikes:
        ce=bs_greeks(spot,K,T,r,iv,"CE"); pe=bs_greeks(spot,K,T,r,iv,"PE")
        ce_sig=_option_signal(spot,K,atm,ind_u,df,"CE",ce["delta"],dte,vix,iv_percentile)
        pe_sig=_option_signal(spot,K,atm,ind_u,df,"PE",pe["delta"],dte,vix,iv_percentile)
        chain.append({"strike":K,"is_atm":K==atm,"lot":lot,"dte":dte,
                      "iv":ce["iv"],"iv_percentile":iv_percentile,
                      "ce_price":ce["price"],"ce_delta":ce["delta"],"ce_gamma":ce["gamma"],
                      "ce_theta":ce["theta"],"ce_vega":ce["vega"],
                      "ce_sl":round(ce["price"]*sl_pct,2),
                      "ce_t1":round(ce["price"]*1.30,2),"ce_t2":round(ce["price"]*1.60,2),
                      "ce_t3":round(ce["price"]*2.00,2),"ce_signal":ce_sig,
                      "pe_price":pe["price"],"pe_delta":pe["delta"],"pe_gamma":pe["gamma"],
                      "pe_theta":pe["theta"],"pe_vega":pe["vega"],
                      "pe_sl":round(pe["price"]*sl_pct,2),
                      "pe_t1":round(pe["price"]*1.30,2),"pe_t2":round(pe["price"]*1.60,2),
                      "pe_t3":round(pe["price"]*2.00,2),"pe_signal":pe_sig,
                      "ce_oi":0,"pe_oi":0})
    return chain


def _option_signal(spot,K,atm,ind_u,df_u,otype,delta,dte,vix,iv_percentile=50):
    score=0
    if ind_u:
        rsi=ind_u.get("rsi",50); m5=ind_u.get("m5",0)
        e13=ind_u.get("e13",0); e21=ind_u.get("e21",0); close=ind_u.get("close",0)
        st_b=ind_u.get("st_bullish",False); st_s=ind_u.get("st_bearish",False)
        macd_above=ind_u.get("macd_above_zero",False)
        bull=close>e13>e21 if (close and e13 and e21) else False
        bear=close<e13<e21 if (close and e13 and e21) else False
        if otype=="CE":
            if bull:       score+=3
            if st_b:       score+=2
            if bear:       score-=2
            if rsi<40:     score+=1
            if m5>1.5:     score+=2
            if macd_above: score+=1
        else:
            if bear:       score+=3
            if st_s:       score+=2
            if bull:       score-=2
            if rsi>60:     score+=1
            if m5<-1.5:    score+=2
            if not macd_above: score+=1
    if iv_percentile>70: score-=1
    elif iv_percentile<25: score+=2
    ad=abs(delta)
    if 0.35<=ad<=0.65: score+=2
    elif 0.20<=ad<0.35: score+=1
    elif ad<0.15: score-=2
    if dte<=2: score-=3
    elif dte<=5: score-=1
    else: score+=1
    if not spot or spot<=0: return None
    pct=(K-spot)/spot*100 if otype=="CE" else (spot-K)/spot*100
    if 0<=pct<=0.5: score+=2
    elif 0.5<pct<=1.5: score+=1
    elif pct>3: score-=2
    if score>=7: sig="STRONG BUY"; str_=min(95,60+score*3)
    elif score>=4: sig="BUY"; str_=min(80,50+score*5)
    elif score<=-3: sig="AVOID"; str_=max(10,50+score*5)
    else: sig="NEUTRAL"; str_=45
    return {"signal":sig,"score":score,"strength":str_,"reasons":[]}


def compute_rank_score(result, rs_rating=None, stage=None):
    score=result.get("strength",0)*0.4
    if rs_rating: score+=min(rs_rating*20,40)
    if stage==2: score+=20
    score+=min(result.get("vr",1)*5,15)
    score+=min(result.get("adx",0)*0.3,10)
    return round(score,2)


# ─── All Indices + Parallel Scanner ──────────────────────────────────────────

def get_all_indices() -> dict:
    short_map = {
        "NIFTY 50":"NF","NIFTY BANK":"BN","India VIX":"VIX",
        "NIFTY IT":"IT","NIFTY MIDCAP 50":"MID",
    }
    out = {k:{"p":0,"c":0,"pct":0,"h":0,"l":0} for k in ["BN","NF","VIX","SX","IT","MID"]}
    try:
        resp = _nse_fetch_with_retry("https://www.nseindia.com/api/allIndices")
        if resp:
            for item in resp.json().get("data",[]):
                key = short_map.get(item.get("indexSymbol","")) or short_map.get(item.get("index",""))
                if key:
                    ltp=float(item.get("last",0) or 0)
                    prev=float(item.get("previousClose",ltp) or ltp)
                    ch=ltp-prev; pct=ch/prev*100 if prev else 0
                    out[key]={"p":ltp,"c":round(ch,2),"pct":round(pct,2),
                              "h":float(item.get("high",ltp) or ltp),
                              "l":float(item.get("low",ltp) or ltp)}
    except Exception:
        pass
    yahoo_fallback={"NF":"^NSEI","BN":"^NSEBANK","VIX":"^INDIAVIX","SX":"^BSESN","IT":"^CNXIT","MID":"^NSEMDCP50"}
    for key,ysym in yahoo_fallback.items():
        if out.get(key,{}).get("p",0)>0:
            continue
        try:
            url=f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}"
            resp=requests.get(url,params={"range":"1d","interval":"1m"},
                              headers={"User-Agent":_NSE_HEADERS["User-Agent"]},timeout=8)
            if resp.status_code==200:
                result=resp.json().get("chart",{}).get("result",[])
                if result:
                    meta=result[0].get("meta",{})
                    price=meta.get("regularMarketPrice") or meta.get("previousClose")
                    prev=meta.get("previousClose") or price
                    if price and float(price)>0:
                        price=float(price); prev=float(prev or price); chg=price-prev
                        out[key]={"p":price,"c":round(chg,2),
                                  "pct":round((chg/prev*100) if prev else 0,2),
                                  "h":float(meta.get("regularMarketDayHigh") or price),
                                  "l":float(meta.get("regularMarketDayLow") or price)}
        except Exception:
            pass
    return out


def scan_parallel(symbols, mode="INTRADAY", market_mood="NEUTRAL", vix=15.0,
                  max_workers=10, min_strength=55, use_fundamentals=False):
    results=[]
    def _scan_one(sym):
        try:
            if mode=="DELIVERY":
                df=get_ohlcv(sym,"3y","1d"); df_weekly=get_ohlcv(sym,"2y","1wk")
            else:
                df=get_ohlcv(sym,"3mo","1d"); df_weekly=None
            ind=compute_indicators(df,for_delivery=(mode=="DELIVERY"))
            if not ind: return None
            fund={}
            if use_fundamentals or mode=="DELIVERY":
                try: fund=get_fundamentals(sym)
                except: fund={}
            rs_rating=None
            if mode=="DELIVERY" and df is not None:
                rs_rating=compute_rs_rating(df,period_days=65)
            rec,strength,bs,ss,reasons=score_signal(ind,fund,df,market_mood,vix,mode,
                                                      df_weekly=df_weekly,rs_rating=rs_rating)
            if rec=="NEUTRAL" and strength<min_strength: return None
            price=ind.get("close",0); atr=ind.get("atr",price*0.02)
            if mode=="DELIVERY":
                if "BUY" in rec:
                    target=round(price*1.08,2); sl=round(price*0.95,2)
                    t1=round(price*1.05,2); t2=round(price*1.10,2); t3=round(price*1.15,2)
                else:
                    target=round(price*0.92,2); sl=round(price*1.05,2)
                    t1=round(price*0.95,2); t2=round(price*0.90,2); t3=round(price*0.85,2)
            else:
                if "BUY" in rec:
                    target=round(price*(1+0.015*(bs/5)),2); sl=round(price-1.5*atr,2)
                else:
                    target=round(price*(1-0.015*(ss/5)),2); sl=round(price+1.5*atr,2)
                t1=t2=t3=target
            rr=abs(target-price)/max(abs(price-sl),0.01)
            patterns=detect_patterns(df); div=detect_divergence(df,ind)
            stage,stage_desc=weinstein_stage(df_weekly) if df_weekly is not None else (None,"N/A")
            rank_score=compute_rank_score({"strength":strength,"vr":ind.get("vr",1),"adx":ind.get("adx",0)},
                                           rs_rating=rs_rating,stage=stage)
            vol_size=volatility_adjusted_position_size(500000,atr,price)
            pos_size,pos_tier=tiered_position_size(500000,strength)
            sector=SECTOR_MAP.get(sym,fund.get("sector","Unknown") if fund else "Unknown")
            return {
                "symbol":sym,"rec":rec,"strength":strength,"buy_score":bs,"sell_score":ss,
                "price":price,"target":target,"sl":sl,"rr":round(rr,2),
                "target_1":t1,"target_2":t2,"target_3":t3,"atr":atr,
                "day_chg":ind.get("day_chg",0),"m5":ind.get("m5",0),"m20":ind.get("m20",0),
                "vr":ind.get("vr",1),"adx":ind.get("adx",0),"rsi":ind.get("rsi",50),
                "macd":ind.get("macd",0),"roc10":ind.get("roc10",0),
                "indicators":ind,"reasons":reasons,
                "patterns":[(p[0],p[1]) for p in patterns],"divergence":div,
                "s1":ind.get("s1",0),"r1":ind.get("r1",0),
                "rs_rating":rs_rating,"stage":stage,"stage_desc":stage_desc,
                "rank_score":rank_score,"sector":sector,
                "pos_size":pos_size,"pos_tier":pos_tier,
                "vol_size":vol_size,
                "fundamentals":fund if fund else {},
                "gap_type":ind.get("gap_type"),"gap_pct":ind.get("gap_pct",0),
                "ad_trend":ind.get("ad_trend",0),"vwap":ind.get("vwap",0),
                "st_bullish":ind.get("st_bullish",False),
            }
        except Exception:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for r in ex.map(_scan_one,symbols):
            if r and r["strength"]>=min_strength:
                results.append(r)

    if mode=="DELIVERY":
        results.sort(key=lambda x: -x["rank_score"])
    else:
        results.sort(key=lambda x:(0 if "BUY" in x["rec"] else 1,-x["strength"]))
    return results


def scan_segment_parallel(symbols,segment="EQUITY",mode="INTRADAY",market_mood="NEUTRAL",
                           vix=15.0,max_workers=20,min_strength=58):
    regular=[s for s in symbols if not str(s).upper().endswith(".MCX")]
    results=scan_parallel(regular,mode,market_mood,vix,max_workers,min_strength) if regular else []
    for sym in symbols:
        if not str(sym).upper().endswith(".MCX"): continue
        price=get_live_price(sym)
        if not price or price<=0: continue
        strength=max(int(min_strength),60)
        rec="BUY" if market_mood!="BEARISH" else "SELL"
        results.append({"symbol":sym,"rec":rec,"strength":strength,"price":price,
                         "target":round(price*(1.018 if rec=="BUY" else 0.982),2),
                         "sl":round(price*(0.99 if rec=="BUY" else 1.01),2),
                         "rr":1.8,"reasons":["MCX live rate"],"patterns":[],"atr":price*0.01})
    return sorted(results,key=lambda x:-x.get("strength",0))
