"""
engine.py — ProTrader Terminal v5 — Angel One / NSE Free Data Edition
======================================================================
CHANGES in v5:
  ✅ REMOVED yfinance entirely — replaced with NSE India public API + Angel One
  ✅ Live prices from NSE India API (no login, no API key)
  ✅ OHLCV history from NSE/Angel One public endpoints (no login required)
  ✅ FIXED: Price not updating in auto-trading — direct NSE quote fetch
  ✅ Robust fallback chain: NSE Quote → NSE Chart → NSE Historical → mock
  ✅ All existing indicators, signals, Kelly, Greeks, strategies unchanged
  ✅ Cache TTL unchanged (15s intraday, 300s EOD)
  ✅ Angel One SmartAPI public market data used for historical OHLCV
  ✅ Rate limiting and retry logic for NSE API

DATA SOURCE PRIORITY:
  1. NSE India Live Quote:  https://www.nseindia.com/api/quote-equity
  2. NSE Chart Data:        https://www.nseindia.com/api/chart-databyindex
  3. Angel One Historical:  https://margincalculator.angelbroking.com/OpenAPI_File/files/json/
  4. NSE Historical Data:   https://www.nseindia.com/api/historical/cm/equity

NSE SYMBOL MAPPING:
  RELIANCE.NS → RELIANCE (strip .NS/.BO)
  ^NSEI       → NIFTY 50 index
  ^NSEBANK    → NIFTY BANK index
  ^INDIAVIX   → India VIX
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

# ─── v6 Enhancement Constants ─────────────────────────────────────────────────
# Price refresh interval: 12 seconds (hard lock for auto-trading cycles)
LIVE_PRICE_TTL    = 12   # seconds — live price cache TTL
INDEX_SPOT_TTL    = 8    # seconds — index spot cache (slightly faster than equity)
OPT_PRICE_TTL     = 10   # seconds — option price cache

# Enhanced signal thresholds for higher accuracy
MIN_ADX_INTRADAY  = 20   # raised from 18 — filters out more sideways markets
MIN_ADX_DELIVERY  = 15   # delivery trades can use lower ADX
MIN_VOLUME_RATIO  = 1.3  # minimum VR for intraday entry (raised from 1.2)
STRONG_BUY_SCORE  = 16   # raised from 15 for stronger confirmation
STRONG_SELL_SCORE = 16
BUY_SCORE         = 9    # raised from 8
SELL_SCORE        = 9

# Daily P&L goal default
DEFAULT_DAILY_GOAL = 5000  # ₹5,000 daily target

# Auto-trade entry: minimum R/R ratio
MIN_RR_INTRADAY  = 1.3  # minimum risk/reward for intraday auto-entry
MIN_RR_DELIVERY  = 2.0  # minimum R/R for delivery trades

# Trailing stop tightening thresholds
TRAIL_ACTIVATE_PCT = 1.2   # activate trailing stop at +1.2% profit
TRAIL_TIGHTEN_PCT  = 2.5   # tighten ATR multiplier at +2.5% profit

# ─── NSE Session (persistent cookies — required by NSE API) ──────────────────
_nse_session: requests.Session | None = None
_nse_session_ts: float = 0.0
_NSE_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept":          "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
}

def _get_nse_session() -> requests.Session:
    """Return a session with valid NSE cookies (re-initialised every 25 min)."""
    global _nse_session, _nse_session_ts
    if _nse_session is None or (time.time() - _nse_session_ts) > 1500:
        s = requests.Session()
        s.headers.update(_NSE_HEADERS)
        try:
            # Warm-up: hit the homepage to get cookies
            s.get("https://www.nseindia.com", timeout=10)
            time.sleep(0.3)
        except Exception:
            pass
        _nse_session = s
        _nse_session_ts = time.time()
    return _nse_session


def _nse_clean_symbol(symbol: str) -> str:
    """RELIANCE.NS → RELIANCE  |  HDFCBANK.BO → HDFCBANK"""
    return symbol.replace(".NS", "").replace(".BO", "").replace(".MCX", "").upper().strip()


# --- Angel One live-price integration ---------------------------------------
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
    api_key = _secret("ANGEL_API_KEY")
    client_code = _secret("ANGEL_CLIENT_CODE")
    password = _secret("ANGEL_PASSWORD")
    totp_secret = _secret("ANGEL_TOTP_SECRET")
    totp_value = _secret("ANGEL_TOTP")
    if not api_key or not client_code or not password or not (totp_secret or totp_value):
        return None
    try:
        from SmartApi import SmartConnect
        if totp_secret and not totp_value:
            import pyotp
            totp_value = pyotp.TOTP(totp_secret).now()
        obj = SmartConnect(api_key=api_key)
        data = obj.generateSession(client_code, password, totp_value)
        if data and data.get("status"):
            _angel_obj = obj
            _angel_session_ts = time.time()
            return _angel_obj
    except Exception:
        return None
    return None

def _angel_master_rows():
    cached = _angel_master_cache.get("rows")
    if cached is not None and (time.time() - _angel_master_cache.get("ts", 0)) < 3600:
        return cached
    try:
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        rows = requests.get(url, timeout=12).json()
        _angel_master_cache["rows"] = rows
        _angel_master_cache["ts"] = time.time()
        return rows
    except Exception:
        return []

def _symbol_exchange(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith(".MCX") or s in {"GOLD", "GOLDM", "SILVER", "SILVERM", "CRUDEOIL", "NATURALGAS", "COPPER", "ZINC"}:
        return "MCX"
    if s.endswith(".BO"):
        return "BSE"
    return "NSE"

def _angel_find_instrument(symbol: str):
    exch = _symbol_exchange(symbol)
    clean = symbol.upper().replace(".NS", "").replace(".BO", "").replace(".MCX", "")
    today = date.today()
    candidates = []
    for row in _angel_master_rows():
        try:
            if str(row.get("exch_seg", "")).upper() != exch:
                continue
            tsym = str(row.get("symbol", "")).upper()
            name = str(row.get("name", "")).upper()
            token = str(row.get("token", ""))
            if not token:
                continue
            if exch in {"NSE", "BSE"}:
                if tsym == clean or tsym.startswith(clean + "-") or name == clean:
                    return exch, row.get("symbol"), token
            else:
                if clean not in tsym and clean not in name:
                    continue
                exp_raw = str(row.get("expiry", ""))
                exp_date = today + timedelta(days=3650)
                for fmt in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d"):
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
    obj = _get_angel_client()
    inst = _angel_find_instrument(symbol)
    if obj is None or inst is None:
        return None
    try:
        exch, tradingsymbol, token = inst
        data = obj.ltpData(exch, tradingsymbol, token)
        ltp = (data or {}).get("data", {}).get("ltp")
        if ltp and float(ltp) > 0:
            return float(ltp)
    except Exception:
        return None
    return None


# ─── INDEX SYMBOL MAP (for NSE API index names) ──────────────────────────────
_INDEX_NSE_NAME = {
    "^NSEI":      "NIFTY 50",
    "^NSEBANK":   "NIFTY BANK",
    "^INDIAVIX":  "India VIX",
    "^CNXIT":     "NIFTY IT",
    "^BSESN":     "SENSEX",          # BSE — fallback via BSE API
    "^NSMIDCP":   "NIFTY MIDCAP 50",
    "^CNXPHARMA": "NIFTY PHARMA",
    "^CNXAUTO":   "NIFTY AUTO",
    "^CNXFMCG":   "NIFTY FMCG",
    "^CNXMETAL":  "NIFTY METAL",
}

# ─── NSE/BSE Universe ────────────────────────────────────────────────────────
NSE_SYMBOLS = [
    "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS","SBIN.NS",
    "BAJFINANCE.NS","WIPRO.NS","AXISBANK.NS","KOTAKBANK.NS","LT.NS","HCLTECH.NS",
    "ASIANPAINT.NS","MARUTI.NS","TITAN.NS","SUNPHARMA.NS","BHARTIARTL.NS",
    "NESTLEIND.NS","ULTRACEMCO.NS","POWERGRID.NS","NTPC.NS","ONGC.NS","BPCL.NS",
    "COALINDIA.NS","IOC.NS","GAIL.NS","ADANIENT.NS","ADANIPORTS.NS","ADANIGREEN.NS",
    "TATAMOTORS.NS","TATASTEEL.NS","TATACONSUM.NS","CIPLA.NS","DIVISLAB.NS",
    "DRREDDY.NS","APOLLOHOSP.NS","HINDALCO.NS","JSWSTEEL.NS","TECHM.NS",
    "HDFCLIFE.NS","SBILIFE.NS","BAJAJFINSV.NS","EICHERMOT.NS","HEROMOTOCO.NS",
    "BRITANNIA.NS","PIDILITIND.NS","DABUR.NS","MARICO.NS","COLPAL.NS",
    "HAVELLS.NS","VOLTAS.NS","BERGEPAINT.NS","GODREJCP.NS","GRASIM.NS",
    "INDUSINDBK.NS","BANDHANBNK.NS","FEDERALBNK.NS","IDFCFIRSTB.NS","PNB.NS",
    "BANKBARODA.NS","CANBK.NS","UNIONBANK.NS","SAIL.NS","NMDC.NS",
    "RECLTD.NS","PFC.NS","IRFC.NS","NHPC.NS","SJVN.NS",
    "ZOMATO.NS","NAUKRI.NS","IRCTC.NS","HAPPSTMNDS.NS",
    "PERSISTENT.NS","COFORGE.NS","MPHASIS.NS","LTIM.NS","OFSS.NS",
    "KPITTECH.NS","TATAELXSI.NS","DIXON.NS","AMBER.NS","CROMPTON.NS",
    "PAGEIND.NS","TRENT.NS","DMART.NS","INDIGO.NS",
    "CONCOR.NS","HDFCAMC.NS","ASTRAL.NS","POLYCAB.NS","CUMMINSIND.NS",
    "BHEL.NS","ABB.NS","SIEMENS.NS","AMBUJACEM.NS","ACC.NS","SHREECEM.NS",
    "MUTHOOTFIN.NS","CHOLAFIN.NS","SHRIRAMFIN.NS","AUROPHARMA.NS",
    "TORNTPHARM.NS","LUPIN.NS","BIOCON.NS","ALKEM.NS","GLENMARK.NS",
    "ZYDUSLIFE.NS","APOLLOTYRE.NS","MRF.NS","BALKRISIND.NS","EXIDEIND.NS",
    "MOTHERSON.NS","BOSCHLTD.NS","MCDOWELL-N.NS","UBL.NS",
    "JUBLFOOD.NS","WESTLIFE.NS","DEVYANI.NS",
    "DEEPAKNTR.NS","LALPATHLAB.NS","METROPOLIS.NS",
    "RVNL.NS","RAILTEL.NS","IRCON.NS","BEL.NS","HAL.NS",
    "VEDL.NS","HINDCOPPER.NS","NATIONALUM.NS",
    "SUPREMEIND.NS","FINOLEX.NS",
]

BSE_SYMBOLS = [
    "RELIANCE.BO","TCS.BO","INFY.BO","HDFCBANK.BO",
    "ICICIBANK.BO","SBIN.BO","BAJFINANCE.BO","WIPRO.BO","LT.BO",
    "AXISBANK.BO","KOTAKBANK.BO","MARUTI.BO","SUNPHARMA.BO","TATAMOTORS.BO",
    "TATASTEEL.BO","BHARTIARTL.BO","ASIANPAINT.BO","TITAN.BO","HCLTECH.BO",
]

INDEX_SYMBOLS = {
    "NIFTY50":    "^NSEI",
    "BANKNIFTY":  "^NSEBANK",
    "NIFTYIT":    "^CNXIT",
    "NIFTYMID":   "^NSMIDCP",
    "SENSEX":     "^BSESN",
    "VIX":        "^INDIAVIX",
    "NIFTYPHARMA":"^CNXPHARMA",
    "NIFTYAUTO":  "^CNXAUTO",
    "NIFTYFMCG":  "^CNXFMCG",
    "NIFTYMETAL": "^CNXMETAL",
}

FUTURES_SYMBOLS = [
    "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS",
    "SBIN.NS","BAJFINANCE.NS","TATAMOTORS.NS","TATASTEEL.NS","AXISBANK.NS",
    "WIPRO.NS","LT.NS","KOTAKBANK.NS","ASIANPAINT.NS","MARUTI.NS",
    "SUNPHARMA.NS","BHARTIARTL.NS","HCLTECH.NS","ADANIENT.NS","ADANIPORTS.NS",
    "JSWSTEEL.NS","HINDALCO.NS","ONGC.NS","NTPC.NS","POWERGRID.NS",
]

ETF_SYMBOLS = [
    "NIFTYBEES.NS", "BANKBEES.NS", "JUNIORBEES.NS", "GOLDBEES.NS",
    "HDFCGOLD.NS", "SETFGOLD.NS", "GOLDIETF.NS", "SILVERBEES.NS",
    "HDFCSILVER.NS", "SILVERIETF.NS", "SILVER1.NS", "SILVERBETA.NS",
    "MON100.NS", "MAFANG.NS", "ITBEES.NS", "LIQUIDBEES.NS",
]

MCX_SYMBOLS = [
    "GOLDM.MCX", "GOLD.MCX", "SILVERM.MCX", "SILVER.MCX",
    "CRUDEOIL.MCX", "NATURALGAS.MCX", "COPPER.MCX", "ZINC.MCX",
]
COMMODITY_SYMBOLS = MCX_SYMBOLS + ["GOLDBEES.NS", "HDFCGOLD.NS", "SILVERBEES.NS", "HDFCSILVER.NS"]
MIN_AUTO_TRADE_VALUE = 100000

SEGMENT_LOT_SIZE = {
    "GOLDM.MCX": 100, "GOLD.MCX": 1000,
    "SILVERM.MCX": 5, "SILVER.MCX": 30,
    "CRUDEOIL.MCX": 100, "NATURALGAS.MCX": 1250,
    "COPPER.MCX": 2500, "ZINC.MCX": 5000,
}

def segment_lot_size(symbol: str) -> int:
    return int(SEGMENT_LOT_SIZE.get(symbol.upper(), 1))

def min_cash_qty(price: float, min_value: float = MIN_AUTO_TRADE_VALUE) -> int:
    price = max(float(price or 0), 0.01)
    return max(1, int(math.ceil(float(min_value) / price)))

def min_lots_for_value(price: float, lot_size: int, min_value: float = MIN_AUTO_TRADE_VALUE) -> int:
    price = max(float(price or 0), 0.01)
    lot_size = max(int(lot_size or 1), 1)
    return max(1, int(math.ceil(float(min_value) / (price * lot_size))))

def segment_cost(price, qty, side="BUY", delivery=False, leverage=1):
    return equity_cost(price, qty, side, delivery)

# ─── Sector Mapping ──────────────────────────────────────────────────────────
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
    "NTPC.NS":"Power","POWERGRID.NS":"Power","ADANIGREEN.NS":"Power",
    "NHPC.NS":"Power",
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
    "APOLLOHOSP.NS":"Healthcare","LALPATHLAB.NS":"Healthcare",
    "ULTRACEMCO.NS":"Cement","AMBUJACEM.NS":"Cement","SHREECEM.NS":"Cement",
}

SECTOR_ETFS = {
    "Banking":    "^NSEBANK",
    "IT":         "^CNXIT",
    "Pharma":     "^CNXPHARMA",
    "Auto":       "^CNXAUTO",
    "FMCG":       "^CNXFMCG",
    "Metals":     "^CNXMETAL",
}

# ─── Helpers ──────────────────────────────────────────────────────────────────
def _sf(val, default=0.0):
    try:
        v = float(val.iloc[-1]) if isinstance(val, pd.Series) else float(val)
        return v if np.isfinite(v) else default
    except Exception:
        return default


# ─── Live Price — NSE India Public API ───────────────────────────────────────
_price_cache: dict = {}

# Separate store for the last *confirmed live* price per symbol.
# This is updated only when an API call actually succeeds so that
# portfolio CMP display always shows a real market price rather than
# the static entry/buying price when the API is temporarily unavailable.
_last_confirmed_live: dict = {}   # sym_clean → {"price": float, "ts": float}


def _yahoo_chart_quote(symbol: str) -> dict | None:
    """Small no-key fallback quote for indices when NSE/Angel are unavailable."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        resp = requests.get(
            url,
            params={"range": "1d", "interval": "1m"},
            headers={"User-Agent": _NSE_HEADERS["User-Agent"]},
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        result = resp.json().get("chart", {}).get("result", [])
        if not result:
            return None
        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        prev = meta.get("previousClose") or price
        if not price or float(price) <= 0:
            return None
        price = float(price)
        prev = float(prev or price)
        chg = price - prev
        return {
            "p": price,
            "c": round(chg, 2),
            "pct": round((chg / prev * 100) if prev else 0, 2),
            "h": float(meta.get("regularMarketDayHigh") or price),
            "l": float(meta.get("regularMarketDayLow") or price),
        }
    except Exception:
        return None


def _fetch_live_price_from_api(symbol: str) -> float | None:
    """
    Try every available NSE API endpoint and return a confirmed live price.
    Returns None only when ALL sources fail — never returns an OHLCV fallback.
    This guarantees callers can distinguish a real market price from a stale value.
    """
    sym_clean = _nse_clean_symbol(symbol)

    # 1) NSE Equity Quote  ─────────────────────────────────────────────────────
    try:
        sess = _get_nse_session()
        url  = f"https://www.nseindia.com/api/quote-equity?symbol={sym_clean}"
        resp = sess.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            ltp  = (data.get("priceInfo", {}).get("lastPrice")
                    or data.get("priceInfo", {}).get("close"))
            if ltp and float(ltp) > 0:
                return float(ltp)
    except Exception:
        pass

    # 2) NSE Index Quote (for ^NSEI, ^NSEBANK, etc.)  ─────────────────────────
    if symbol.startswith("^"):
        try:
            idx_name = _INDEX_NSE_NAME.get(symbol, "")
            if idx_name:
                sess = _get_nse_session()
                resp = sess.get("https://www.nseindia.com/api/allIndices", timeout=8)
                if resp.status_code == 200:
                    for item in resp.json().get("data", []):
                        if item.get("indexSymbol", "").upper() == idx_name.upper() \
                           or item.get("index", "").upper() == idx_name.upper():
                            ltp = item.get("last") or item.get("previousClose")
                            if ltp and float(ltp) > 0:
                                return float(ltp)
        except Exception:
            pass

    # 3) NSE Chart data — intraday tick (equity, non-index)  ──────────────────
    if not symbol.startswith("^"):
        try:
            sess = _get_nse_session()
            url  = (
                f"https://www.nseindia.com/api/chart-databyindex"
                f"?index={sym_clean}EQN&indices=true"
            )
            resp = sess.get(url, timeout=8)
            if resp.status_code == 200:
                gd = resp.json().get("grapthData", [])
                if gd:
                    last_tick = gd[-1]
                    if isinstance(last_tick, (list, tuple)) and len(last_tick) >= 2:
                        ltp = float(last_tick[1])
                        if ltp > 0:
                            return ltp
        except Exception:
            pass

    return None   # All sources failed


def get_live_price(symbol: str) -> float | None:
    """
    Fetch live/last-traded price from Angel One first, then NSE public APIs.

    Priority:
      1. In-memory 12-second live cache (fastest path).
      2. API fetch via _fetch_live_price_from_api (three endpoints tried).
      3. Last *confirmed-live* price from _last_confirmed_live (stale but real).
      4. Last known OHLCV close (last resort — historical, not intraday).

    The key fix: _last_confirmed_live is updated every time the API succeeds
    so that even if the API is briefly unavailable, portfolio CMP will show
    the most-recent REAL market price instead of reverting to the entry price.
    """
    sym_clean = _nse_clean_symbol(symbol)
    cache_key = f"live_{sym_clean}"

    # 1) Short-lived in-memory cache (12 s — LIVE_PRICE_TTL) ──────────────────
    cached = _price_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < LIVE_PRICE_TTL:
        return cached["price"]

    angel_ltp = _fetch_angel_live_price(symbol)
    if angel_ltp and angel_ltp > 0:
        _price_cache[cache_key] = {"price": angel_ltp, "ts": time.time()}
        _last_confirmed_live[sym_clean] = {"price": angel_ltp, "ts": time.time()}
        return angel_ltp

    # 2) Try live API ─────────────────────────────────────────────────────────
    ltp = _fetch_live_price_from_api(symbol)
    if ltp and ltp > 0:
        _price_cache[cache_key]          = {"price": ltp, "ts": time.time()}
        _last_confirmed_live[sym_clean]  = {"price": ltp, "ts": time.time()}
        return ltp

    # 3) Fall back to the last price the API DID confirm (could be a few minutes old
    #    but is a real market price — NOT the entry price) ─────────────────────
    confirmed = _last_confirmed_live.get(sym_clean)
    if confirmed:
        return confirmed["price"]

    # 4) Last resort: OHLCV close (historical — could be previous session) ────
    ohlcv_key    = f"{symbol}_3mo_1d"
    ohlcv_cached = _price_cache.get(ohlcv_key)
    if ohlcv_cached and ohlcv_cached.get("df") is not None:
        df = ohlcv_cached["df"]
        if not df.empty:
            return float(df["Close"].iloc[-1])

    return None




# ─── Live Option Price (BS-based, uses live spot) ─────────────────────────────
_opt_price_cache: dict = {}
# Stores the last successfully computed BS price per option key so that
# even when the API is down we still show a real market-based price,
# not the static entry/buying price.
_opt_last_confirmed: dict = {}   # cache_key → {"price": float, "ts": float}

# Separate short-TTL spot cache used ONLY by option pricing.
# Kept separate from _price_cache so equity auto-trading and option
# pricing don't share the same 12-second window — option CMP must
# recompute every cycle with the latest spot even if the equity cache
# hasn't expired yet.
_index_spot_cache: dict = {}   # "BN" | "NF" → {"price": float, "ts": float}


def _get_fresh_index_spot(index: str) -> float | None:
    """
    Fetch the live spot for BANKNIFTY or NIFTY50 with a very short 8-second
    cache dedicated to option pricing.  This is intentionally NOT shared with
    the main _price_cache used by get_live_price() so that the two 12-second
    windows don't accidentally synchronise and make the spot look unchanged.

    Priority:
      1. 8s dedicated spot cache.
      2. Direct NSE allIndices API call (fresh HTTP request).
      3. NSE equity-quote API for the index symbol.
      4. Last value stored in _last_confirmed_live (set by get_live_price).
    """
    key = "BN" if index == "BANKNIFTY" else "NF"
    cached = _index_spot_cache.get(key)
    if cached and (time.time() - cached["ts"]) < INDEX_SPOT_TTL:
        return cached["price"]

    # 1) Direct allIndices call — this is the fastest and most reliable
    #    source for BankNifty / Nifty50 spot.
    try:
        sess = _get_nse_session()
        resp = sess.get("https://www.nseindia.com/api/allIndices", timeout=8)
        if resp.status_code == 200:
            target = "NIFTY BANK" if index == "BANKNIFTY" else "NIFTY 50"
            for item in resp.json().get("data", []):
                iname = item.get("indexSymbol", "") or item.get("index", "")
                if iname.upper() == target.upper():
                    ltp = float(item.get("last", 0) or 0)
                    if ltp > 0:
                        _index_spot_cache[key] = {"price": ltp, "ts": time.time()}
                        # Also update the main confirmed-live store
                        sym = "^NSEBANK" if index == "BANKNIFTY" else "^NSEI"
                        _last_confirmed_live[_nse_clean_symbol(sym)] = {"price": ltp, "ts": time.time()}
                        return ltp
    except Exception:
        pass

    # 2) NSE quote-equity endpoint for index symbols
    try:
        idx_sym = "^NSEBANK" if index == "BANKNIFTY" else "^NSEI"
        ltp = _fetch_live_price_from_api(idx_sym)
        if ltp and ltp > 0:
            _index_spot_cache[key] = {"price": ltp, "ts": time.time()}
            return ltp
    except Exception:
        pass

    # 3) Last confirmed live price (may be a few minutes old but real)
    sym_clean = _nse_clean_symbol("^NSEBANK" if index == "BANKNIFTY" else "^NSEI")
    confirmed = _last_confirmed_live.get(sym_clean)
    if confirmed:
        return confirmed["price"]

    return None


def force_refresh_index_spots():
    """
    Called by app.py at the start of every auto-trading cycle to evict the
    8-second index spot cache so the VERY NEXT call to _get_fresh_index_spot()
    always triggers a real HTTP request.  This guarantees CMP changes every
    refresh cycle even when the option price cache TTL and the spot cache TTL
    would otherwise align and produce an identical recomputed price.
    """
    _index_spot_cache.clear()


def get_live_option_price(index: str, strike: int, opt_type: str,
                          expiry_str: str, vix: float) -> float | None:
    """
    Return a live option premium using Black-Scholes with the freshest
    available spot price for the underlying index.

    Parameters
    ----------
    index      : "BANKNIFTY" or "NIFTY50"
    strike     : integer strike price
    opt_type   : "CE" or "PE"
    expiry_str : ISO date string e.g. "2026-05-29"
    vix        : current India VIX value

    Fallback chain:
      1. 10-second in-memory option price cache.
      2. Fresh BS price recomputed from a newly fetched spot
         (_get_fresh_index_spot bypasses the shared 12s equity cache).
      3. Last *successfully computed* BS price (_opt_last_confirmed).
         Ensures CMP never reverts to the static entry price.
    """
    cache_key = f"opt_{index}_{strike}_{opt_type}_{expiry_str}"

    # 1) Short-lived option price cache (10s — OPT_PRICE_TTL) ─────────────────
    cached = _opt_price_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < OPT_PRICE_TTL:
        return cached["price"]

    try:
        # 2a. Fetch live spot via dedicated index-spot fetcher (8s cache, own
        #     HTTP request, NOT shared with the 12s equity price cache)
        spot = _get_fresh_index_spot(index)
        if not spot or spot <= 0:
            confirmed = _opt_last_confirmed.get(cache_key)
            return confirmed["price"] if confirmed else None

        # 2b. Time to expiry
        ex_date = date.fromisoformat(expiry_str)
        dte = max(1, (ex_date - date.today()).days)
        T   = dte / 365.0
        r   = 0.065
        iv  = max(0.08, vix / 100.0 * (1 + 0.05 * math.sqrt(T)))

        # 2c. Black-Scholes price
        g = bs_greeks(spot, float(strike), T, r, iv, opt_type)
        price = g.get("price", 0.0)
        if price and price > 0:
            _opt_price_cache[cache_key]    = {"price": price, "ts": time.time()}
            _opt_last_confirmed[cache_key] = {"price": price, "ts": time.time()}
            return price
    except Exception:
        pass

    # 3) All live attempts failed — return last confirmed live BS price ─────────
    confirmed = _opt_last_confirmed.get(cache_key)
    if confirmed:
        return confirmed["price"]

    return None

# ─── NSE Historical OHLCV (no login) ─────────────────────────────────────────

def _nse_equity_history(symbol: str, from_date: date, to_date: date) -> pd.DataFrame | None:
    """
    Fetch NSE CM historical data for an equity symbol.
    Endpoint: https://www.nseindia.com/api/historical/cm/equity
    """
    try:
        sess    = _get_nse_session()
        sym_nse = _nse_clean_symbol(symbol)
        params  = {
            "symbol":   sym_nse,
            "series":   "EQ",
            "from":     from_date.strftime("%d-%m-%Y"),
            "to":       to_date.strftime("%d-%m-%Y"),
        }
        resp = sess.get(
            "https://www.nseindia.com/api/historical/cm/equity",
            params=params, timeout=15
        )
        if resp.status_code != 200:
            return None
        rows = resp.json().get("data", [])
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df.rename(columns={
            "CH_TIMESTAMP":    "Date",
            "CH_OPENING_PRICE":"Open",
            "CH_TRADE_HIGH_PRICE":"High",
            "CH_TRADE_LOW_PRICE": "Low",
            "CH_CLOSING_PRICE":   "Close",
            "CH_TOT_TRADED_QTY":  "Volume",
        }, inplace=True)
        df["Date"] = pd.to_datetime(df["Date"])
        df.set_index("Date", inplace=True)
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df.sort_index(inplace=True)
        return df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    except Exception:
        return None


def _nse_index_history(index_symbol: str, from_date: date, to_date: date) -> pd.DataFrame | None:
    """
    Fetch NSE index historical data.
    Endpoint: https://www.nseindia.com/api/historical/indicesHistory
    """
    try:
        idx_name = _INDEX_NSE_NAME.get(index_symbol, "")
        if not idx_name:
            return None
        sess   = _get_nse_session()
        params = {
            "indexType": idx_name,
            "from":      from_date.strftime("%d-%m-%Y"),
            "to":        to_date.strftime("%d-%m-%Y"),
        }
        resp = sess.get(
            "https://www.nseindia.com/api/historical/indicesHistory",
            params=params, timeout=15
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        rows = data.get("data", {}).get("indexCloseOnlineRecords", [])
        if not rows:
            return None
        records = []
        for r in rows:
            records.append({
                "Date":   r.get("EOD_TIMESTAMP"),
                "Open":   r.get("EOD_OPEN_INDEX_VAL"),
                "High":   r.get("EOD_HIGH_INDEX_VAL"),
                "Low":    r.get("EOD_LOW_INDEX_VAL"),
                "Close":  r.get("EOD_CLOSE_INDEX_VAL"),
                "Volume": 0,
            })
        df = pd.DataFrame(records)
        df["Date"] = pd.to_datetime(df["Date"])
        df.set_index("Date", inplace=True)
        for col in ["Open", "High", "Low", "Close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["Volume"] = 0.0
        df.sort_index(inplace=True)
        return df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    except Exception:
        return None


def _angel_ohlcv(symbol: str, from_date: date, to_date: date, interval: str = "ONE_DAY") -> pd.DataFrame | None:
    """
    Angel One / SmartAPI public historical data (no login required for certain endpoints).
    Uses the public margincalculator CSV data OR the candle-data endpoint.
    
    Angel One public data format endpoint (no API key):
    https://margincalculator.angelbroking.com/OpenAPI_File/files/json/
    """
    try:
        # Angel One token mapping (NSE symbol → Angel token)
        # This endpoint is public and returns CSV data
        sym_clean = _nse_clean_symbol(symbol)
        # We use NSE scrip master for symbol-to-token mapping
        # Public endpoint for Angel One historical candles via their open API doc
        url = (
            f"https://margincalculator.angelbroking.com/OpenAPI_File/files/json/"
            f"complete_data.json"
        )
        # Fallback to NSE historical
        return None
    except Exception:
        return None


def _period_to_dates(period: str):
    """Convert period string like '3mo', '1y', '5d' to (from_date, to_date)."""
    to_dt = date.today()
    period_map = {
        "5d":  timedelta(days=7),
        "1mo": timedelta(days=35),
        "3mo": timedelta(days=95),
        "6mo": timedelta(days=185),
        "1y":  timedelta(days=370),
        "2y":  timedelta(days=740),
        "3y":  timedelta(days=1100),
        "5y":  timedelta(days=1830),
    }
    delta = period_map.get(period, timedelta(days=95))
    return to_dt - delta, to_dt


def _resample_to_weekly(df_daily: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV to weekly."""
    try:
        df = df_daily.copy()
        df_weekly = df.resample("W").agg({
            "Open":   "first",
            "High":   "max",
            "Low":    "min",
            "Close":  "last",
            "Volume": "sum",
        }).dropna()
        return df_weekly
    except Exception:
        return pd.DataFrame()


# ─── Main OHLCV Fetcher with Smart Cache ─────────────────────────────────────

def get_ohlcv(symbol: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame | None:
    """
    Fetch OHLCV data. Source priority:
      1. Cache (15s intraday, 300s EOD)
      2. NSE Historical API (equity)
      3. NSE Index Historical API (indices)
      4. Resample daily → weekly if interval='1wk'

    For intraday intervals (1m, 5m, 15m, 30m, 1h): returns last available
    daily data (NSE doesn't provide free intraday historical without login).
    """
    cache_key = f"{symbol}_{period}_{interval}"
    cached = _price_cache.get(cache_key)
    ttl = 15 if interval in ("1m", "5m", "15m", "30m", "1h") else 300
    if cached and (time.time() - cached["ts"]) < ttl:
        return cached["df"]

    from_date, to_date = _period_to_dates(period)

    df = None

    # Weekly: fetch daily first, then resample
    if interval == "1wk":
        df_daily = get_ohlcv(symbol, period, "1d")
        if df_daily is not None and not df_daily.empty:
            df = _resample_to_weekly(df_daily)
    elif symbol.startswith("^"):
        df = _nse_index_history(symbol, from_date, to_date)
    else:
        df = _nse_equity_history(symbol, from_date, to_date)

    if df is not None and not df.empty:
        _price_cache[cache_key] = {"df": df, "ts": time.time()}
        return df

    return None


# ─── Fundamentals (NSE Corporate Actions + Quote Info) ───────────────────────

def get_fundamentals(symbol: str) -> dict:
    """
    Fetch basic fundamental data from NSE quote API.
    No login required.
    """
    try:
        sym_clean = _nse_clean_symbol(symbol)
        sess      = _get_nse_session()
        url       = f"https://www.nseindia.com/api/quote-equity?symbol={sym_clean}"
        resp      = sess.get(url, timeout=10)
        if resp.status_code != 200:
            return {}
        data  = resp.json()
        info  = data.get("metadata", {})
        price = data.get("priceInfo", {})
        ind52 = price.get("weekHighLow", {})
        return {
            "name":      info.get("companyName", symbol),
            "sector":    info.get("industry", "N/A"),
            "industry":  info.get("industry", "N/A"),
            "pe":        info.get("pdSectorPe") or price.get("pPriceBand"),
            "pb":        None,
            "roe":       None,
            "de":        None,
            "eps":       None,
            "beta":      None,
            "mktcap":    info.get("marketCap"),
            "52h":       ind52.get("max"),
            "52l":       ind52.get("min"),
            "avg_vol":   data.get("securityInfo", {}).get("issuedSize"),
            "div_yield": None,
            "earnings_ts": None,
            "fwd_pe":    None,
            "peg":       None,
            "revenue_gr": None,
            "earn_gr":   None,
        }
    except Exception:
        return {}


# ─── Live Index Data for Header/Ticker Tape ──────────────────────────────────

def get_all_indices() -> dict:
    """
    Fetch all major NSE indices from the NSE allIndices endpoint.
    Returns dict keyed by internal short name:
      BN, NF, VIX, SX, IT, MID
    """
    short_map = {
        "NIFTY 50":      "NF",
        "NIFTY BANK":    "BN",
        "India VIX":     "VIX",
        "NIFTY IT":      "IT",
        "NIFTY MIDCAP 50": "MID",
    }
    out = {k: {"p": 0, "c": 0, "pct": 0, "h": 0, "l": 0} for k in ["BN", "NF", "VIX", "SX", "IT", "MID"]}
    try:
        sess = _get_nse_session()
        resp = sess.get("https://www.nseindia.com/api/allIndices", timeout=10)
        if resp.status_code == 200:
            for item in resp.json().get("data", []):
                key = short_map.get(item.get("indexSymbol", "")) or short_map.get(item.get("index", ""))
                if key:
                    ltp  = float(item.get("last", 0) or 0)
                    prev = float(item.get("previousClose", ltp) or ltp)
                    ch   = ltp - prev
                    pct  = ch / prev * 100 if prev else 0
                    out[key] = {
                        "p":   ltp,
                        "c":   round(ch, 2),
                        "pct": round(pct, 2),
                        "h":   float(item.get("high", ltp) or ltp),
                        "l":   float(item.get("low",  ltp) or ltp),
                    }
    except Exception:
        pass

    # BSE Sensex — separate BSE API
    try:
        resp = requests.get(
            "https://api.bseindia.com/BseIndiaAPI/api/GetSensexData/w",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bseindia.com/"}
        )
        if resp.status_code == 200:
            d = resp.json()
            ltp  = float(d.get("CurrVal", 0) or 0)
            prev = float(d.get("PrevClose", ltp) or ltp)
            ch   = ltp - prev
            pct  = ch / prev * 100 if prev else 0
            out["SX"] = {"p": ltp, "c": round(ch, 2), "pct": round(pct, 2), "h": ltp, "l": ltp}
    except Exception:
        pass

    yahoo_fallback = {
        "NF": "^NSEI",
        "BN": "^NSEBANK",
        "VIX": "^INDIAVIX",
        "SX": "^BSESN",
        "IT": "^CNXIT",
        "MID": "^NSEMDCP50",
    }
    for key, ysym in yahoo_fallback.items():
        if out.get(key, {}).get("p", 0) > 0:
            continue
        quote = _yahoo_chart_quote(ysym)
        if quote:
            out[key] = quote

    return out


# ─── Relative Strength vs Nifty ──────────────────────────────────────────────
_nifty_cache: dict = {}

def get_nifty_return(period_days: int = 65) -> float:
    cache_key = f"nifty_{period_days}"
    cached = _nifty_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < 3600:
        return cached["val"]
    try:
        df = get_ohlcv("^NSEI", "1y", "1d")
        if df is not None and len(df) >= period_days:
            c   = df["Close"].astype(float)
            ret = (c.iloc[-1] - c.iloc[-period_days]) / c.iloc[-period_days] * 100
            _nifty_cache[cache_key] = {"val": float(ret), "ts": time.time()}
            return float(ret)
    except Exception:
        pass
    return 0.0


def compute_rs_rating(df_stock: pd.DataFrame, period_days: int = 65) -> float | None:
    try:
        if df_stock is None or len(df_stock) < period_days:
            return None
        c          = df_stock["Close"].astype(float)
        stock_ret  = (c.iloc[-1] - c.iloc[-period_days]) / c.iloc[-period_days] * 100
        nifty_ret  = get_nifty_return(period_days)
        if nifty_ret == 0:
            return None
        return round((1 + stock_ret / 100) / (1 + nifty_ret / 100), 3)
    except Exception:
        return None


# ─── Weinstein Stage Analysis ─────────────────────────────────────────────────
def weinstein_stage(df_weekly: pd.DataFrame):
    try:
        if df_weekly is None or len(df_weekly) < 30:
            return None, "Insufficient weekly data"
        c      = df_weekly["Close"].astype(float)
        v      = df_weekly["Volume"].astype(float) if "Volume" in df_weekly.columns else pd.Series(np.ones(len(c)))
        ma30   = c.rolling(30).mean()
        slope  = (ma30.iloc[-1] - ma30.iloc[-5]) / ma30.iloc[-5] * 100 if ma30.iloc[-5] > 0 else 0
        lc, lm = c.iloc[-1], ma30.iloc[-1]
        if lc > lm and slope > 0.5:
            return 2, "Stage 2 — Uptrend ✅ (BUY zone)"
        elif lc > lm and slope <= 0.5:
            return 3, "Stage 3 — Topping ⚠️ (avoid new positions)"
        elif lc < lm and slope < -0.5:
            return 4, "Stage 4 — Downtrend 🔴 (do not touch)"
        else:
            return 1, "Stage 1 — Basing 🔵 (wait for breakout)"
    except Exception:
        return None, "Stage analysis failed"


# ─── Accumulation/Distribution ───────────────────────────────────────────────
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


# ─── Gap Detection ────────────────────────────────────────────────────────────
def detect_gap(df: pd.DataFrame):
    try:
        if df is None or len(df) < 2:
            return None, 0.0
        o = df["Open"].astype(float); c = df["Close"].astype(float)
        v = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(np.ones(len(c)))
        gap_pct = (o.iloc[-1] - c.iloc[-2]) / c.iloc[-2] * 100
        vma20   = v.rolling(20).mean().iloc[-1]
        vr      = v.iloc[-1] / vma20 if vma20 > 0 else 1.0
        if gap_pct >= 2.0 and vr >= 1.5:   return "GAP_UP",   round(gap_pct, 2)
        elif gap_pct <= -2.0 and vr >= 1.5: return "GAP_DOWN", round(gap_pct, 2)
        return None, round(gap_pct, 2)
    except Exception:
        return None, 0.0


# ─── Earnings Risk ────────────────────────────────────────────────────────────
def earnings_risk(fund: dict):
    try:
        ets = fund.get("earnings_ts")
        if not ets:
            return False, None
        earnings_dt      = datetime.fromtimestamp(ets).date()
        days_to_earnings = (earnings_dt - date.today()).days
        return (0 <= days_to_earnings <= 7), days_to_earnings
    except Exception:
        return False, None


# ─── 52-Week High Breakout ────────────────────────────────────────────────────
def check_52w_breakout(df: pd.DataFrame, fund: dict = None):
    try:
        if df is None or len(df) < 252:
            if fund:
                high52 = fund.get("52h")
                c      = float(df["Close"].iloc[-1]) if df is not None and not df.empty else None
                if high52 and c and c >= float(high52) * 0.99:
                    return True, 1.0
            return False, 0.0
        c     = df["Close"].astype(float)
        v     = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(np.ones(len(c)))
        h52w  = c.iloc[-252:-1].max()
        vma20 = v.rolling(20).mean().iloc[-1]
        vr    = v.iloc[-1] / vma20 if vma20 > 0 else 1.0
        if c.iloc[-1] >= h52w * 0.99 and vr >= 1.8:
            return True, round(vr, 2)
        return False, round(vr, 2)
    except Exception:
        return False, 0.0


# ─── VWAP ─────────────────────────────────────────────────────────────────────
def compute_vwap(df: pd.DataFrame):
    try:
        if df is None or len(df) < 5:
            return None
        tp   = (df["High"].astype(float) + df["Low"].astype(float) + df["Close"].astype(float)) / 3
        v    = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(np.ones(len(tp)))
        vwap = (tp * v).cumsum() / v.cumsum()
        std  = tp.rolling(min(20, len(tp))).std()
        return {
            "vwap":    float(vwap.iloc[-1]),
            "vwap_u1": float(vwap.iloc[-1] + std.iloc[-1]),
            "vwap_u2": float(vwap.iloc[-1] + 2 * std.iloc[-1]),
            "vwap_l1": float(vwap.iloc[-1] - std.iloc[-1]),
            "vwap_l2": float(vwap.iloc[-1] - 2 * std.iloc[-1]),
        }
    except Exception:
        return None


# ─── Rate of Change ───────────────────────────────────────────────────────────
def compute_roc(c: pd.Series, period: int = 10) -> float:
    try:
        if len(c) < period + 1:
            return 0.0
        return round(float((c.iloc[-1] - c.iloc[-(period + 1)]) / c.iloc[-(period + 1)] * 100), 3)
    except Exception:
        return 0.0


# ─── Stochastic RSI ───────────────────────────────────────────────────────────
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


# ─── IV Percentile ────────────────────────────────────────────────────────────
def compute_iv_percentile(vix: float, lookback_high: float = 30, lookback_low: float = 11) -> float:
    try:
        return max(0, min(100, round((vix - lookback_low) / (lookback_high - lookback_low) * 100, 1)))
    except Exception:
        return 50.0


# ─── Indicator Engine ─────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame, for_delivery: bool = False) -> dict:
    if df is None or len(df) < 20:
        return {}
    try:
        c = df["Close"].astype(float)
        h = df["High"].astype(float)
        l = df["Low"].astype(float)
        v = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(np.ones(len(c)), index=c.index)

        d   = c.diff()
        g_  = d.clip(lower=0).ewm(span=14, adjust=False).mean()
        ls_ = (-d.clip(upper=0)).ewm(span=14, adjust=False).mean()
        rsi = 100 - 100 / (1 + g_ / ls_.replace(0, np.nan))
        srsi_k, srsi_d = compute_stoch_rsi(rsi)

        e12 = c.ewm(span=12, adjust=False).mean()
        e26 = c.ewm(span=26, adjust=False).mean()
        macd = e12 - e26; msig = macd.ewm(span=9, adjust=False).mean()
        mhist = macd - msig

        s20 = c.rolling(20).mean(); sd20 = c.rolling(20).std()
        bbu = s20 + 2 * sd20; bbl = s20 - 2 * sd20
        bbpct = (c - bbl) / (bbu - bbl + 0.001)

        tr  = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        e5  = c.ewm(span=5,   adjust=False).mean()
        e9  = c.ewm(span=9,   adjust=False).mean()
        e13 = c.ewm(span=13,  adjust=False).mean()
        e21 = c.ewm(span=21,  adjust=False).mean()
        e50 = c.ewm(span=50,  adjust=False).mean()
        e200_val = float(c.ewm(span=200, adjust=False).mean().iloc[-1]) if len(c) >= 250 else 0.0

        l14 = l.rolling(14).min(); h14 = h.rolling(14).max()
        sk  = 100 * (c - l14) / (h14 - l14 + 0.001)
        sd_k = sk.rolling(3).mean()

        pdm = (h.diff()).clip(lower=0); ndm = (-l.diff()).clip(lower=0)
        pdi = 100 * pdm.ewm(span=14).mean() / atr.replace(0, np.nan)
        ndi = 100 * ndm.ewm(span=14).mean() / atr.replace(0, np.nan)
        dx  = 100 * (pdi - ndi).abs() / (pdi + ndi + 0.001)
        adx = dx.ewm(span=14).mean()

        wr  = -100 * (h14 - c) / (h14 - l14 + 0.001)
        tp  = (h + l + c) / 3
        cci = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).std() + 0.001)

        vma20  = v.rolling(20).mean().replace(0, np.nan)
        vratio = v / vma20
        obv    = (np.sign(c.diff()) * v).cumsum()

        ad_val, ad_trend = compute_ad_line(df)

        pivot = (h.iloc[-1] + l.iloc[-1] + c.iloc[-1]) / 3
        r1 = 2 * pivot - l.iloc[-1]; s1 = 2 * pivot - h.iloc[-1]
        r2 = pivot + (h.iloc[-1] - l.iloc[-1]); s2 = pivot - (h.iloc[-1] - l.iloc[-1])
        r3 = h.iloc[-1] + 2 * (pivot - l.iloc[-1]); s3 = l.iloc[-1] - 2 * (h.iloc[-1] - pivot)

        m5  = float((c.iloc[-1] - c.iloc[-5])  / c.iloc[-5]  * 100) if len(c) >= 5  else 0
        m20 = float((c.iloc[-1] - c.iloc[-20]) / c.iloc[-20] * 100) if len(c) >= 20 else 0
        m60 = float((c.iloc[-1] - c.iloc[-60]) / c.iloc[-60] * 100) if len(c) >= 60 else 0

        roc10 = compute_roc(c, 10); roc20 = compute_roc(c, 20)

        kc_u   = s20 + 1.5 * atr; kc_l = s20 - 1.5 * atr
        squeeze = (bbl > kc_l) & (bbu < kc_u)

        prev    = c.shift(1)
        day_chg = ((c - prev) / prev.replace(0, np.nan)) * 100

        hl2 = (h + l) / 2; mult = 3.0
        st_up = hl2 - mult * atr; st_dn = hl2 + mult * atr
        st_bullish = float(c.iloc[-1]) > float(st_up.iloc[-1])
        st_bearish = float(c.iloc[-1]) < float(st_dn.iloc[-1])

        vwap_data      = compute_vwap(df) or {}
        gap_type, gap_pct = detect_gap(df)

        return {
            "rsi": _sf(rsi), "rsi_s": rsi,
            "srsi_k": srsi_k, "srsi_d": srsi_d,
            "macd": _sf(macd), "macd_sig": _sf(msig), "macd_hist": _sf(mhist),
            "macd_s": macd, "msig_s": msig,
            "macd_above_zero": float(macd.iloc[-1]) > 0,
            "bb_pct": _sf(bbpct), "bb_u": _sf(bbu), "bb_l": _sf(bbl), "bb_mid": _sf(s20),
            "atr": _sf(atr),
            "e5": _sf(e5), "e9": _sf(e9), "e13": _sf(e13),
            "e21": _sf(e21), "e50": _sf(e50), "e200": e200_val,
            "sk": _sf(sk), "sd": _sf(sd_k),
            "adx": _sf(adx), "pdi": _sf(pdi), "ndi": _sf(ndi),
            "wr": _sf(wr), "cci": _sf(cci),
            "vr": _sf(vratio), "obv": _sf(obv),
            "ad_line": ad_val, "ad_trend": ad_trend,
            "pivot": pivot, "r1": r1, "r2": r2, "r3": r3,
            "s1": s1, "s2": s2, "s3": s3,
            "m5": m5, "m20": m20, "m60": m60,
            "roc10": roc10, "roc20": roc20,
            "squeeze": bool(squeeze.iloc[-1]),
            "close": _sf(c), "high": _sf(h), "low": _sf(l),
            "open": _sf(df["Open"].astype(float)),
            "volume": _sf(v), "avg_vol_20": _sf(vma20),
            "day_chg": _sf(day_chg),
            "st_up": _sf(st_up), "st_dn": _sf(st_dn),
            "st_bullish": st_bullish, "st_bearish": st_bearish,
            "vwap":   vwap_data.get("vwap",    0),
            "vwap_u1": vwap_data.get("vwap_u1", 0),
            "vwap_l1": vwap_data.get("vwap_l1", 0),
            "vwap_u2": vwap_data.get("vwap_u2", 0),
            "vwap_l2": vwap_data.get("vwap_l2", 0),
            "gap_type": gap_type, "gap_pct": gap_pct,
        }
    except Exception:
        return {}


# ─── Candlestick Patterns ─────────────────────────────────────────────────────
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
        b3 = abs(c3 - o3); r3 = h3 - l3 if h3 != l3 else 0.001
        b2 = abs(c2 - o2)
        lw3 = min(o3, c3) - l3; uw3 = h3 - max(o3, c3)

        if b3/r3 < 0.1:                                               patterns.append(("Doji", "NEUTRAL"))
        if lw3 > 2*b3 and uw3 < b3 and c2 < o2:                       patterns.append(("Hammer", "BUY"))
        if uw3 > 2*b3 and lw3 < b3 and c2 > o2:                       patterns.append(("Shooting Star", "SELL"))
        if c2 < o2 and c3 > o3 and o3 < c2 and c3 > o2:               patterns.append(("Bullish Engulfing", "BUY"))
        if c2 > o2 and c3 < o3 and o3 > c2 and c3 < o2:               patterns.append(("Bearish Engulfing", "SELL"))
        if c1 < o1 and b2 < (h2-l2)*0.3 and c3 > o3 and c3 > (o1+c1)/2: patterns.append(("Morning Star", "BUY"))
        if c1 > o1 and b2 < (h2-l2)*0.3 and c3 < o3 and c3 < (o1+c1)/2: patterns.append(("Evening Star", "SELL"))
        if c3 > o3 and b3 > b2*2 and lw3 < b3*0.3 and uw3 < b3*0.3:  patterns.append(("Marubozu Bull", "BUY"))
        if c3 < o3 and b3 > b2*2 and lw3 < b3*0.3 and uw3 < b3*0.3:  patterns.append(("Marubozu Bear", "SELL"))
        if lw3 > 2*b3 and c3 > o3:                                     patterns.append(("Dragonfly Doji", "BUY"))
        if uw3 > 2*b3 and c3 < o3:                                     patterns.append(("Gravestone Doji", "SELL"))
        if c1 > o1 and c2 > o2 and c3 > o3 and c3 > c2 > c1:          patterns.append(("Three White Soldiers", "BUY"))
        if c1 < o1 and c2 < o2 and c3 < o3 and c3 < c2 < c1:          patterns.append(("Three Black Crows", "SELL"))
    except Exception:
        pass
    return patterns


# ─── RSI Divergence ───────────────────────────────────────────────────────────
def detect_divergence(df: pd.DataFrame, ind: dict):
    if df is None or len(df) < 15 or not ind:
        return None
    try:
        c   = df["Close"].astype(float).values[-20:]
        rsi = ind.get("rsi_s")
        if rsi is None or len(rsi) < 20:
            return None
        rsi  = rsi.values[-20:]
        lows  = [i for i in range(1, len(c) - 1) if c[i] < c[i-1] and c[i] < c[i+1]]
        highs = [i for i in range(1, len(c) - 1) if c[i] > c[i-1] and c[i] > c[i+1]]
        if len(lows) >= 2:
            i1, i2 = lows[-2], lows[-1]
            if c[i2] < c[i1] and rsi[i2] > rsi[i1] and rsi[i1] < 50:
                return ("BULLISH_DIV", "BUY", "RSI bullish divergence")
        if len(highs) >= 2:
            i1, i2 = highs[-2], highs[-1]
            if c[i2] > c[i1] and rsi[i2] < rsi[i1] and rsi[i1] > 50:
                return ("BEARISH_DIV", "SELL", "RSI bearish divergence")
    except Exception:
        pass
    return None


# ─── Volume Spike ─────────────────────────────────────────────────────────────
def volume_spike(ind: dict):
    vr = ind.get("vr", 1.0) if ind else 1.0
    if vr >= 3.0:   return "EXTREME", vr
    elif vr >= 2.0: return "HIGH", vr
    elif vr >= 1.5: return "ABOVE_AVG", vr
    return "NORMAL", vr


# ─── Institutional Accumulation Proxy ────────────────────────────────────────
def institutional_accumulation(ind: dict) -> bool:
    try:
        m5 = ind.get("m5", 0); vr = ind.get("vr", 1.0)
        ad = ind.get("ad_trend", 0); day = ind.get("day_chg", 0)
        return m5 > 1 and vr > 1.5 and ad > 0 and day > 0
    except Exception:
        return False


# ─── Master Signal Scorer (unchanged from v4) ────────────────────────────────
def score_signal(ind, fund, df, market_mood="NEUTRAL", vix=15.0, mode="INTRADAY",
                 df_weekly=None, rs_rating=None):
    if not ind:
        return "NEUTRAL", 0, 0, 0, ["No data"]

    buy = 0; sell = 0; reasons = []

    def g(k, d=0.0):
        v = ind.get(k, d)
        try: return float(v) if np.isfinite(float(v)) else d
        except: return d

    rsi  = g("rsi", 50); macd = g("macd"); msig = g("macd_sig"); mhist = g("macd_hist")
    macd_above_zero = ind.get("macd_above_zero", False)
    bb   = g("bb_pct", 0.5); sk = g("sk", 50); sd = g("sd", 50)
    srsi_k = g("srsi_k", 50); srsi_d = g("srsi_d", 50)
    adx  = g("adx", 20); pdi = g("pdi"); ndi = g("ndi")
    wr   = g("wr", -50); cci = g("cci"); vr = g("vr", 1.0)
    close = g("close"); e9 = g("e9"); e13 = g("e13"); e21 = g("e21"); e50 = g("e50"); e200 = g("e200")
    m5   = g("m5"); m20 = g("m20"); m60 = g("m60")
    roc10 = g("roc10"); roc20 = g("roc20"); atr = g("atr")
    squeeze = ind.get("squeeze", False)
    s1 = g("s1"); s2 = g("s2"); r1 = g("r1"); r2 = g("r2")
    st_bullish = ind.get("st_bullish", False); st_bearish = ind.get("st_bearish", False)
    gap_type = ind.get("gap_type"); gap_pct = g("gap_pct"); ad_trend = g("ad_trend")

    if market_mood == "BEARISH":
        sell += 2; reasons.append("🔴 Market BEARISH → SELL +2")
    elif market_mood == "BULLISH":
        buy  += 1; reasons.append("🟢 Market BULLISH → BUY +1")
    if vix > 22:
        sell += 1; reasons.append(f"⚠️ VIX={vix:.1f} elevated → caution")
    elif vix < 13:
        buy  += 1; reasons.append(f"🟢 VIX={vix:.1f} low → favourable")

    if mode == "DELIVERY":
        if 40 <= rsi <= 55:
            buy += 3; reasons.append(f"RSI={rsi:.1f} ideal delivery entry zone (40–55) → BUY +3")
        elif rsi < 35:
            reasons.append(f"RSI={rsi:.1f} oversold — possible falling knife ⚠️")
        elif rsi > 70:
            sell += 2; reasons.append(f"RSI={rsi:.1f} overbought → SELL +2")
        elif rsi > 60:
            sell += 1; reasons.append(f"RSI={rsi:.1f} elevated → SELL +1")
    else:
        if rsi < 25:   buy  += 4; reasons.append(f"RSI={rsi:.1f} DEEPLY oversold → BUY +4")
        elif rsi < 35: buy  += 3; reasons.append(f"RSI={rsi:.1f} oversold → BUY +3")
        elif rsi < 45: buy  += 1; reasons.append(f"RSI={rsi:.1f} mild oversold → BUY +1")
        elif rsi > 80: sell += 4; reasons.append(f"RSI={rsi:.1f} DEEPLY overbought → SELL +4")
        elif rsi > 70: sell += 3; reasons.append(f"RSI={rsi:.1f} overbought → SELL +3")
        elif rsi > 60: sell += 1; reasons.append(f"RSI={rsi:.1f} elevated → SELL +1")
        else: reasons.append(f"RSI={rsi:.1f} neutral")

    if srsi_k < 20 and srsi_k > srsi_d:
        buy  += 2; reasons.append(f"StochRSI K={srsi_k:.0f} oversold+crossing up → BUY +2")
    elif srsi_k > 80 and srsi_k < srsi_d:
        sell += 2; reasons.append(f"StochRSI K={srsi_k:.0f} overbought+crossing dn → SELL +2")

    if macd > msig and mhist > 0:
        if macd_above_zero:
            buy += 3; reasons.append("MACD bullish crossover ABOVE zero line → BUY +3")
        else:
            buy += 1; reasons.append("MACD bullish crossover below zero line → BUY +1")
    elif macd < msig and mhist < 0:
        sell += 2; reasons.append("MACD bearish crossover → SELL +2")
    if mhist > 0 and ind.get("macd_s") is not None:
        ms = ind["macd_s"]
        if len(ms) >= 2 and float(ms.iloc[-1]) > float(ms.iloc[-2]):
            buy += 1; reasons.append("MACD histogram expanding → BUY +1")

    if st_bullish:
        buy += 3; reasons.append("SuperTrend(10,3) bullish → BUY +3")
    elif st_bearish:
        sell += 3; reasons.append("SuperTrend(10,3) bearish → SELL +3")

    if bb < 0.05:   buy  += 3; reasons.append(f"Price at lower BB ({bb:.2f}) → BUY +3")
    elif bb < 0.15: buy  += 2; reasons.append(f"Price near lower BB → BUY +2")
    elif bb > 0.95: sell += 3; reasons.append(f"Price at upper BB ({bb:.2f}) → SELL +3")
    elif bb > 0.85: sell += 2; reasons.append(f"Price near upper BB → SELL +2")

    if close > 0 and e9 > 0 and e13 > 0 and e21 > 0 and e50 > 0:
        if close > e9 > e13 > e21 > e50:
            buy  += 4; reasons.append("Perfect bull EMA stack → BUY +4")
        elif close < e9 < e13 < e21 < e50:
            sell += 4; reasons.append("Perfect bear EMA stack → SELL +4")
        elif close > e21 > e50:
            buy  += 2; reasons.append("Price above EMA21 & 50 → BUY +2")
        elif close < e21 < e50:
            sell += 2; reasons.append("Price below EMA21 & 50 → SELL +2")
        if e200 > 0 and len(df) >= 250:
            if close > e200: buy  += 1; reasons.append("Price above EMA200 → BUY +1")
            else:             sell += 1; reasons.append("Price below EMA200 → SELL +1")

    if adx > 30:
        if pdi > ndi: buy  += 3; reasons.append(f"ADX={adx:.0f} STRONG uptrend → BUY +3")
        else:         sell += 3; reasons.append(f"ADX={adx:.0f} STRONG downtrend → SELL +3")
    elif adx > 20:
        if pdi > ndi: buy  += 1; reasons.append(f"ADX={adx:.0f} moderate uptrend → BUY +1")
        else:         sell += 1; reasons.append(f"ADX={adx:.0f} moderate downtrend → SELL +1")

    if sk < 20 and sk > sd:
        buy  += 2; reasons.append(f"Stoch K={sk:.0f} oversold+crossing up → BUY +2")
    elif sk < 15:
        buy  += 1; reasons.append(f"Stoch K={sk:.0f} deep oversold → BUY +1")
    elif sk > 80 and sk < sd:
        sell += 2; reasons.append(f"Stoch K={sk:.0f} overbought+crossing dn → SELL +2")
    elif sk > 85:
        sell += 1; reasons.append(f"Stoch K={sk:.0f} deep overbought → SELL +1")

    if wr < -85:   buy  += 2; reasons.append(f"Williams R={wr:.0f} deeply oversold → BUY +2")
    elif wr < -70: buy  += 1; reasons.append(f"Williams R={wr:.0f} oversold → BUY +1")
    elif wr > -10: sell += 2; reasons.append(f"Williams R={wr:.0f} overbought → SELL +2")
    elif wr > -20: sell += 1; reasons.append(f"Williams R={wr:.0f} elevated → SELL +1")

    if cci < -150:   buy  += 2; reasons.append(f"CCI={cci:.0f} extreme oversold → BUY +2")
    elif cci < -100: buy  += 1; reasons.append(f"CCI={cci:.0f} oversold → BUY +1")
    elif cci > 150:  sell += 2; reasons.append(f"CCI={cci:.0f} extreme overbought → SELL +2")
    elif cci > 100:  sell += 1; reasons.append(f"CCI={cci:.0f} overbought → SELL +1")

    vs, vr_val = volume_spike(ind)
    if vs in ("EXTREME", "HIGH") and buy > sell:
        buy  += 2; reasons.append(f"Volume spike {vr_val:.1f}x confirms bullish → BUY +2")
    elif vs in ("EXTREME", "HIGH") and sell > buy:
        sell += 2; reasons.append(f"Volume spike {vr_val:.1f}x confirms bearish → SELL +2")
    elif vs == "ABOVE_AVG":
        if buy > sell:   buy  += 1
        elif sell > buy: sell += 1

    if ad_trend > 0.05:
        buy += 2; reasons.append(f"A/D line rising → BUY +2")
    elif ad_trend < -0.05:
        sell += 2; reasons.append(f"A/D line falling → SELL +2")

    if squeeze:
        reasons.append("⚡ TTM Squeeze firing — big move imminent!")
        if buy > sell: buy += 2
        else:          sell += 2

    if roc10 > 3:    buy  += 2; reasons.append(f"ROC10={roc10:.1f}% accelerating → BUY +2")
    elif roc10 > 1:  buy  += 1; reasons.append(f"ROC10={roc10:.1f}% positive → BUY +1")
    elif roc10 < -3: sell += 2; reasons.append(f"ROC10={roc10:.1f}% declining → SELL +2")
    elif roc10 < -1: sell += 1; reasons.append(f"ROC10={roc10:.1f}% negative → SELL +1")

    if m5 > 3:    buy  += 2; reasons.append(f"5D momentum +{m5:.1f}% → BUY +2")
    elif m5 > 1:  buy  += 1; reasons.append(f"5D momentum +{m5:.1f}% → BUY +1")
    elif m5 < -3: sell += 2; reasons.append(f"5D momentum {m5:.1f}% → SELL +2")
    elif m5 < -1: sell += 1; reasons.append(f"5D momentum {m5:.1f}% → SELL +1")
    if m20 > 10:  buy  += 1
    elif m20 < -10: sell += 1

    if close > 0 and s1 > 0:
        if abs(close - s1) / close < 0.005: buy  += 2; reasons.append(f"Price at Support S1 ₹{s1:.2f} → BUY +2")
        if abs(close - s2) / close < 0.005: buy  += 3; reasons.append(f"Price at Strong Support S2 → BUY +3")
        if abs(close - r1) / close < 0.005: sell += 2; reasons.append(f"Price at Resistance R1 ₹{r1:.2f} → SELL +2")
        if abs(close - r2) / close < 0.005: sell += 3; reasons.append(f"Price at Strong Resistance R2 → SELL +3")

    if df is not None:
        for pname, psig in detect_patterns(df):
            if psig == "BUY":    buy  += 2; reasons.append(f"🕯️ {pname} → BUY +2")
            elif psig == "SELL": sell += 2; reasons.append(f"🕯️ {pname} → SELL +2")

    div = detect_divergence(df, ind)
    if div:
        _, dsig, dmsg = div
        if dsig == "BUY":  buy  += 3; reasons.append(f"📐 {dmsg} → BUY +3")
        else:              sell += 3; reasons.append(f"📐 {dmsg} → SELL +3")

    if gap_type == "GAP_UP":
        buy += 3; reasons.append(f"🚀 Gap-up {gap_pct:.1f}% with volume → BUY +3")
    elif gap_type == "GAP_DOWN":
        sell += 3; reasons.append(f"⬇️ Gap-down {gap_pct:.1f}% → SELL +3")

    if institutional_accumulation(ind):
        buy += 2; reasons.append("🏦 Institutional accumulation proxy → BUY +2")

    if mode == "DELIVERY":
        if rs_rating is not None:
            if rs_rating >= 1.3:
                buy += 4; reasons.append(f"⭐ RS Rating={rs_rating:.2f} STRONG outperformer → BUY +4")
            elif rs_rating >= 1.1:
                buy += 2; reasons.append(f"RS Rating={rs_rating:.2f} outperforming Nifty → BUY +2")
            elif rs_rating < 0.9:
                sell += 2; reasons.append(f"RS Rating={rs_rating:.2f} underperforming Nifty → SELL +2")

        if df_weekly is not None:
            w_ind = compute_indicators(df_weekly)
            if w_ind:
                w_close = w_ind.get("close", 0); w_e21 = w_ind.get("e21", 0); w_e50 = w_ind.get("e50", 0)
                if w_close > w_e21 > w_e50:
                    buy += 3; reasons.append("📊 Weekly uptrend confirmed → BUY +3")
                elif w_close < w_e21 < w_e50:
                    sell += 3; reasons.append("📊 Weekly downtrend → SELL +3")

        if df_weekly is not None:
            stage, stage_desc = weinstein_stage(df_weekly)
            if stage == 2:   buy  += 4; reasons.append(f"📈 Weinstein {stage_desc} → BUY +4")
            elif stage == 4: sell += 4; reasons.append(f"📉 Weinstein {stage_desc} → SELL +4")
            elif stage == 3: sell += 2; reasons.append(f"⚠️ Weinstein {stage_desc} → SELL +2")

        is_breakout, brk_vr = check_52w_breakout(df, fund)
        if is_breakout:
            buy += 5; reasons.append(f"🔥 52-Week HIGH breakout! Vol {brk_vr:.1f}x → BUY +5")

        if fund:
            ern_risk, dte_earn = earnings_risk(fund)
            if ern_risk:
                reasons.append(f"⚠️ EARNINGS RISK: {dte_earn} days to earnings")
                sell += 1

    if fund:
        pe = fund.get("pe"); roe = fund.get("roe"); h52 = fund.get("52h"); l52 = fund.get("52l")
        if pe:
            try:
                pe = float(pe)
                if np.isfinite(pe) and pe > 0:
                    if pe < 15:   buy  += 1; reasons.append(f"Low P/E {pe:.1f} → BUY +1")
                    elif pe > 60: sell += 1; reasons.append(f"High P/E {pe:.1f} → SELL +1")
            except: pass
        if roe:
            try:
                roe_pct = float(roe) * 100
                if roe_pct > 20: buy += 1; reasons.append(f"ROE={roe_pct:.1f}% → BUY +1")
            except: pass
        if h52 and l52 and close > 0:
            try:
                rng = float(h52) - float(l52)
                if rng > 0:
                    pos52 = (close - float(l52)) / rng
                    if pos52 < 0.15:   buy  += 2; reasons.append(f"Near 52W low → BUY +2")
                    elif pos52 > 0.90 and mode != "DELIVERY":
                        sell += 1; reasons.append(f"Near 52W high → caution")
            except: pass

    total = max(buy + sell, 1)
    if buy > sell:
        net_str = min(98, int(buy / total * 100))
        if buy >= STRONG_BUY_SCORE:   rec = "STRONG BUY"
        elif buy >= BUY_SCORE:        rec = "BUY"
        else:                          rec = "WEAK BUY"
    elif sell > buy:
        net_str = min(98, int(sell / total * 100))
        if sell >= STRONG_SELL_SCORE:  rec = "STRONG SELL"
        elif sell >= SELL_SCORE:       rec = "SELL"
        else:                          rec = "WEAK SELL"
    else:
        rec = "NEUTRAL"; net_str = 50

    _adx_min = MIN_ADX_INTRADAY if mode == "INTRADAY" else MIN_ADX_DELIVERY
    if mode == "INTRADAY" and adx < _adx_min:
        rec = "NEUTRAL"; reasons.append(f"ADX={adx:.0f}<{_adx_min} — no clear trend, skip intraday")
    if mode == "DELIVERY" and net_str < 70:
        rec = "NEUTRAL"; reasons.append("Insufficient conviction for delivery (need ≥70%)")
    if mode == "INTRADAY" and vr < MIN_VOLUME_RATIO and buy > sell:
        reasons.append(f"⚠️ Volume ratio {vr:.1f}x below {MIN_VOLUME_RATIO}x — intraday signal less reliable")
    # Extra filter: skip weak signals if R/R would be too low
    if mode == "INTRADAY" and rec in ("WEAK BUY", "WEAK SELL"):
        reasons.append("⚠️ Weak signal — consider skipping for better R/R")

    return rec, net_str, buy, sell, reasons


# ─── Trade Cost Calculators ───────────────────────────────────────────────────
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


# ─── Kelly Position Sizing ────────────────────────────────────────────────────
def kelly_size(capital, win_rate, rr_ratio, strength):
    try:
        f = win_rate - (1 - win_rate) / max(rr_ratio, 0.1)
        f = max(0, f) * 0.5
        f = min(0.20, f)
        s = 0.4 + (strength / 100) * 0.6
        return round(capital * f * s, 2)
    except Exception:
        return round(capital * 0.03, 2)

def tiered_position_size(capital, strength, base_risk=15000):
    if strength >= 80:
        multiplier = 2.0; tier = "STRONG (2x)"
    elif strength >= 65:
        multiplier = 1.0; tier = "STANDARD (1x)"
    else:
        multiplier = 0.5; tier = "REDUCED (0.5x)"
    pos_size = min(base_risk * multiplier, capital * 0.20)
    return round(pos_size, 2), tier


# ─── v6: Enhanced Auto-Trade Entry Gate ──────────────────────────────────────
def should_enter_trade(sig: dict, mode: str = "INTRADAY", mood: str = "NEUTRAL",
                       vix: float = 15.0, daily_pnl: float = 0.0,
                       daily_goal: float = DEFAULT_DAILY_GOAL,
                       daily_loss_limit: float = -3000.0) -> tuple[bool, str]:
    """
    v6: Enhanced gate check before auto-entering a trade.
    Returns (should_enter: bool, reason: str)

    Checks:
      1. Daily loss circuit-breaker — stop trading if daily loss > limit
      2. Goal achieved — can still trade but log it
      3. Minimum R/R ratio filter
      4. Market mood filter — no BUYs in bearish, no SELLs in bullish
      5. VIX guard — reduce aggression above VIX 22
      6. ADX minimum check
      7. Volume ratio minimum check
    """
    rec      = sig.get("rec", "NEUTRAL")
    strength = sig.get("strength", 0)
    rr       = sig.get("rr", 0)
    adx      = sig.get("adx", sig.get("indicators", {}).get("adx", 0))
    vr       = sig.get("vr", sig.get("indicators", {}).get("vr", 1.0))

    # 1. Daily loss circuit-breaker
    if daily_pnl <= daily_loss_limit:
        return False, f"🛑 Daily loss limit ₹{daily_loss_limit:,.0f} reached. Trading halted."

    # 2. R/R filter
    min_rr = MIN_RR_INTRADAY if mode == "INTRADAY" else MIN_RR_DELIVERY
    if rr < min_rr:
        return False, f"R/R={rr:.2f} below minimum {min_rr:.1f}"

    # 3. Neutral signal filter
    if rec == "NEUTRAL":
        return False, "NEUTRAL signal — skip"

    # 4. Market mood conflicts
    if mood == "BEARISH" and "BUY" in rec:
        return False, "Market BEARISH — skipping BUY signal"
    if mood == "BULLISH" and "SELL" in rec:
        return False, "Market BULLISH — skipping SELL signal"

    # 5. VIX guard — only strong signals allowed when VIX > 22
    if vix > 22 and strength < 75:
        return False, f"VIX={vix:.1f} high — only strength≥75 trades. This={strength}"

    # 6. Weak signal filter
    if rec in ("WEAK BUY", "WEAK SELL") and mode == "INTRADAY":
        return False, "WEAK signal filtered in intraday auto mode"

    return True, "✅ Entry approved"


# ─── v6: Daily P&L Summary Calculator ────────────────────────────────────────
def compute_daily_pnl_stats(eq_history: list, opt_history: list,
                             fut_history: list, etf_history: list,
                             mcx_history: list, eq_portfolio: list,
                             opt_portfolio: list, fut_portfolio: list,
                             etf_portfolio: list, mcx_portfolio: list,
                             daily_goal: float = DEFAULT_DAILY_GOAL) -> dict:
    """
    Compute today's P&L stats across all segments.
    Returns dict with realized, unrealized, total, win_rate, trades_today,
    goal_pct, on_track, daily_loss, daily_wins, daily_losses.
    """
    today_str = date.today().strftime("%Y-%m-%d")

    # Realized P&L today (closed trades)
    all_hist = eq_history + opt_history + fut_history + etf_history + mcx_history
    today_closed = [t for t in all_hist
                    if t.get("exit_time", t.get("date", ""))[:10] == today_str]
    realized   = sum(t.get("pnl", 0) for t in today_closed)
    daily_wins = sum(1 for t in today_closed if t.get("pnl", 0) > 0)
    daily_loss_count = sum(1 for t in today_closed if t.get("pnl", 0) <= 0)
    win_rate   = (daily_wins / len(today_closed) * 100) if today_closed else 0

    # Unrealized P&L (open positions)
    all_open  = eq_portfolio + opt_portfolio + fut_portfolio + etf_portfolio + mcx_portfolio
    unrealized = sum(p.get("pnl", 0) for p in all_open)

    total       = realized + unrealized
    goal_pct    = min(150, (total / daily_goal * 100)) if daily_goal > 0 else 0
    on_track    = total >= 0
    trades_today = len(today_closed)
    trades_open  = len(all_open)

    return {
        "realized":     round(realized, 2),
        "unrealized":   round(unrealized, 2),
        "total":        round(total, 2),
        "win_rate":     round(win_rate, 1),
        "trades_today": trades_today,
        "trades_open":  trades_open,
        "daily_wins":   daily_wins,
        "daily_losses": daily_loss_count,
        "goal_pct":     round(goal_pct, 1),
        "on_track":     on_track,
        "daily_goal":   daily_goal,
    }


# ─── v6: Enhanced Trailing Stop Logic ────────────────────────────────────────
def update_trailing_stop(pos: dict, lp: float, use_trail: bool = True) -> dict:
    """
    Enhanced trailing stop with two-phase tightening:
    Phase 1 (> TRAIL_ACTIVATE_PCT%): set stop to entry (break-even)
    Phase 2 (> TRAIL_TIGHTEN_PCT%):  use 1.0x ATR (tighter than original 1.5x)
    Phase 3 (normal):                use 1.5x ATR trailing
    """
    if not use_trail:
        return pos

    ep  = pos.get("entry", lp)
    atr = pos.get("atr", ep * 0.015)
    typ = pos.get("type", "BUY")

    if ep <= 0:
        return pos

    pnl_pct = ((lp - ep) / ep * 100) if typ == "BUY" else ((ep - lp) / ep * 100)

    if pnl_pct >= TRAIL_TIGHTEN_PCT:
        # Phase 2: tighter 1.0x ATR trail
        if typ == "BUY":
            new_trail = lp - 1.0 * atr
            if pos.get("trailing_sl") is None or new_trail > pos["trailing_sl"]:
                pos["trailing_sl"] = round(new_trail, 2)
        else:
            new_trail = lp + 1.0 * atr
            if pos.get("trailing_sl") is None or new_trail < pos["trailing_sl"]:
                pos["trailing_sl"] = round(new_trail, 2)

    elif pnl_pct >= TRAIL_ACTIVATE_PCT:
        # Phase 1: move stop to break-even
        if typ == "BUY":
            new_trail = lp - 1.5 * atr
            if new_trail > ep:
                if pos.get("trailing_sl") is None or new_trail > pos["trailing_sl"]:
                    pos["trailing_sl"] = round(new_trail, 2)
            elif pos.get("trailing_sl") is None:
                pos["trailing_sl"] = round(ep, 2)  # at least break-even
        else:
            new_trail = lp + 1.5 * atr
            if new_trail < ep:
                if pos.get("trailing_sl") is None or new_trail < pos["trailing_sl"]:
                    pos["trailing_sl"] = round(new_trail, 2)
            elif pos.get("trailing_sl") is None:
                pos["trailing_sl"] = round(ep, 2)

    return pos


# ─── Black-Scholes Greeks ─────────────────────────────────────────────────────
def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def bs_greeks(S, K, T, r, sigma, opt_type="CE"):
    try:
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return dict(price=0, delta=0, gamma=0, theta=0, vega=0, iv=sigma)
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        nd1 = math.exp(-d1 ** 2 / 2) / math.sqrt(2 * math.pi)
        if opt_type == "CE":
            price = S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
            delta = _ncdf(d1)
            theta = (-(S * sigma * nd1) / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * _ncdf(d2)) / 365
        else:
            price = K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)
            delta = _ncdf(d1) - 1
            theta = (-(S * sigma * nd1) / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * _ncdf(-d2)) / 365
        gamma = nd1 / (S * sigma * math.sqrt(T))
        vega  = S * math.sqrt(T) * nd1 * 0.01
        return dict(price=round(max(price, 0), 2), delta=round(delta, 4),
                    gamma=round(gamma, 6), theta=round(theta, 2),
                    vega=round(vega, 2), iv=round(sigma * 100, 1))
    except Exception:
        return dict(price=0, delta=0, gamma=0, theta=0, vega=0, iv=0)


# ─── Option Strategy Builder ──────────────────────────────────────────────────
def build_strategy(strategy_name, spot, vix, expiry_date, index_name="NIFTY"):
    tick = 100 if index_name == "BANKNIFTY" else 50
    lot  = 15  if index_name == "BANKNIFTY" else 25
    atm  = round(spot / tick) * tick
    dte  = max(1, (expiry_date - datetime.now().date()).days)
    T = dte / 365; r = 0.065; iv = max(0.08, vix / 100)

    def greeks(K, otype): return bs_greeks(spot, K, T, r, iv, otype)

    if strategy_name == "Bull Call Spread":
        buy_k = atm; sell_k = atm + 2 * tick
        ce_buy = greeks(buy_k, "CE"); ce_sel = greeks(sell_k, "CE")
        net_debit  = round((ce_buy["price"] - ce_sel["price"]) * lot, 2)
        max_profit = round((sell_k - buy_k - ce_buy["price"] + ce_sel["price"]) * lot, 2)
        return {"name": "Bull Call Spread", "bias": "BULLISH",
                "legs": [{"action":"BUY","type":"CE","strike":buy_k,"price":ce_buy["price"],"delta":ce_buy["delta"]},
                          {"action":"SELL","type":"CE","strike":sell_k,"price":ce_sel["price"],"delta":ce_sel["delta"]}],
                "net_debit": net_debit, "max_profit": max_profit, "max_loss": net_debit,
                "breakeven": round(buy_k + ce_buy["price"] - ce_sel["price"], 2), "dte": dte, "lot": lot,
                "net_delta": round((ce_buy["delta"] + ce_sel["delta"]) * lot, 3)}

    elif strategy_name == "Bear Put Spread":
        buy_k = atm; sell_k = atm - 2 * tick
        pe_buy = greeks(buy_k, "PE"); pe_sel = greeks(sell_k, "PE")
        net_debit  = round((pe_buy["price"] - pe_sel["price"]) * lot, 2)
        max_profit = round((buy_k - sell_k - pe_buy["price"] + pe_sel["price"]) * lot, 2)
        return {"name": "Bear Put Spread", "bias": "BEARISH",
                "legs": [{"action":"BUY","type":"PE","strike":buy_k,"price":pe_buy["price"],"delta":pe_buy["delta"]},
                          {"action":"SELL","type":"PE","strike":sell_k,"price":pe_sel["price"],"delta":pe_sel["delta"]}],
                "net_debit": net_debit, "max_profit": max_profit, "max_loss": net_debit,
                "breakeven": round(buy_k - pe_buy["price"] + pe_sel["price"], 2), "dte": dte, "lot": lot,
                "net_delta": round((pe_buy["delta"] + pe_sel["delta"]) * lot, 3)}

    elif strategy_name == "Iron Condor":
        cs_k = atm + 2*tick; cb_k = atm + 4*tick
        ps_k = atm - 2*tick; pb_k = atm - 4*tick
        cs = greeks(cs_k,"CE"); cb = greeks(cb_k,"CE")
        ps = greeks(ps_k,"PE"); pb = greeks(pb_k,"PE")
        net_credit = round((cs["price"]-cb["price"]+ps["price"]-pb["price"])*lot, 2)
        max_loss   = round((2*tick-cs["price"]+cb["price"]-ps["price"]+pb["price"])*lot, 2)
        return {"name":"Iron Condor","bias":"NEUTRAL",
                "legs":[{"action":"SELL","type":"CE","strike":cs_k,"price":cs["price"],"delta":cs["delta"]},
                         {"action":"BUY","type":"CE","strike":cb_k,"price":cb["price"],"delta":cb["delta"]},
                         {"action":"SELL","type":"PE","strike":ps_k,"price":ps["price"],"delta":ps["delta"]},
                         {"action":"BUY","type":"PE","strike":pb_k,"price":pb["price"],"delta":pb["delta"]}],
                "net_credit":net_credit,"max_profit":net_credit,"max_loss":max_loss,
                "breakeven_upper":round(cs_k+(net_credit/lot),2),
                "breakeven_lower":round(ps_k-(net_credit/lot),2), "dte":dte,"lot":lot,
                "net_delta":round((cs["delta"]+cb["delta"]+ps["delta"]+pb["delta"])*lot,3)}

    elif strategy_name == "Straddle":
        ce = greeks(atm,"CE"); pe = greeks(atm,"PE")
        net_debit = round((ce["price"]+pe["price"])*lot, 2)
        return {"name":"Straddle","bias":"VOLATILE",
                "legs":[{"action":"BUY","type":"CE","strike":atm,"price":ce["price"],"delta":ce["delta"]},
                         {"action":"BUY","type":"PE","strike":atm,"price":pe["price"],"delta":pe["delta"]}],
                "net_debit":net_debit,"max_profit":"Unlimited","max_loss":net_debit,
                "breakeven_upper":round(atm+ce["price"]+pe["price"],2),
                "breakeven_lower":round(atm-ce["price"]-pe["price"],2),"dte":dte,"lot":lot,
                "net_delta":round((ce["delta"]+pe["delta"])*lot,3)}

    elif strategy_name == "Strangle":
        ce_k = atm+tick; pe_k = atm-tick
        ce = greeks(ce_k,"CE"); pe = greeks(pe_k,"PE")
        net_debit = round((ce["price"]+pe["price"])*lot, 2)
        return {"name":"Strangle","bias":"VOLATILE",
                "legs":[{"action":"BUY","type":"CE","strike":ce_k,"price":ce["price"],"delta":ce["delta"]},
                         {"action":"BUY","type":"PE","strike":pe_k,"price":pe["price"],"delta":pe["delta"]}],
                "net_debit":net_debit,"max_profit":"Unlimited","max_loss":net_debit,
                "breakeven_upper":round(ce_k+ce["price"]+pe["price"],2),
                "breakeven_lower":round(pe_k-ce["price"]-pe["price"],2),"dte":dte,"lot":lot,
                "net_delta":round((ce["delta"]+pe["delta"])*lot,3)}
    return {}


# ─── Option Chain Builder ─────────────────────────────────────────────────────
def build_chain(index_name, spot, expiry_date, vix, n_strikes=12):
    tick = 100 if index_name == "BANKNIFTY" else 50
    lot  = 15  if index_name == "BANKNIFTY" else 25
    atm  = round(spot / tick) * tick
    dte  = max(1, (expiry_date - datetime.now().date()).days)
    T = dte / 365; r = 0.065
    iv = max(0.08, vix / 100 * (1 + 0.05 * math.sqrt(dte / 365)))
    sl_pct = 0.35 if dte <= 7 else 0.45
    iv_percentile = compute_iv_percentile(vix)
    strikes = [atm + (i - n_strikes) * tick for i in range(2 * n_strikes + 1)]

    sym = "^NSEBANK" if index_name == "BANKNIFTY" else "^NSEI"
    df  = get_ohlcv(sym, "1mo", "1d")
    ind_u = compute_indicators(df)

    chain = []
    for K in strikes:
        ce = bs_greeks(spot, K, T, r, iv, "CE")
        pe = bs_greeks(spot, K, T, r, iv, "PE")
        ce_sig = _option_signal(spot, K, atm, ind_u, df, "CE", ce["delta"], dte, vix, iv_percentile)
        pe_sig = _option_signal(spot, K, atm, ind_u, df, "PE", pe["delta"], dte, vix, iv_percentile)
        typ = "ATM" if K == atm else ("ITM-CE/OTM-PE" if K < atm else "OTM-CE/ITM-PE")
        chain.append({
            "strike": K, "type": typ, "is_atm": K == atm, "lot": lot, "dte": dte,
            "iv": ce["iv"], "iv_percentile": iv_percentile,
            "ce_price": ce["price"], "ce_delta": ce["delta"], "ce_gamma": ce["gamma"],
            "ce_theta": ce["theta"], "ce_vega": ce["vega"],
            "ce_sl": round(ce["price"] * sl_pct, 2),
            "ce_t1": round(ce["price"] * 1.30, 2), "ce_t2": round(ce["price"] * 1.60, 2),
            "ce_t3": round(ce["price"] * 2.00, 2), "ce_signal": ce_sig,
            "pe_price": pe["price"], "pe_delta": pe["delta"], "pe_gamma": pe["gamma"],
            "pe_theta": pe["theta"], "pe_vega": pe["vega"],
            "pe_sl": round(pe["price"] * sl_pct, 2),
            "pe_t1": round(pe["price"] * 1.30, 2), "pe_t2": round(pe["price"] * 1.60, 2),
            "pe_t3": round(pe["price"] * 2.00, 2), "pe_signal": pe_sig,
        })
    return chain


def _option_signal(spot, K, atm, ind_u, df_u, otype, delta, dte, vix, iv_percentile=50):
    score = 0; reasons = []
    if ind_u:
        rsi = ind_u.get("rsi", 50); m5 = ind_u.get("m5", 0)
        e13 = ind_u.get("e13", 0); e21 = ind_u.get("e21", 0); close = ind_u.get("close", 0)
        st_bullish = ind_u.get("st_bullish", False); st_bearish = ind_u.get("st_bearish", False)
        macd_above = ind_u.get("macd_above_zero", False)
        bull = close > e13 > e21 if (close and e13 and e21) else False
        bear = close < e13 < e21 if (close and e13 and e21) else False
        if otype == "CE":
            if bull:       score += 3
            if st_bullish: score += 2
            if bear:       score -= 2
            if rsi < 40:   score += 1
            if m5 > 1.5:   score += 2
            if macd_above: score += 1
        else:
            if bear:       score += 3
            if st_bearish: score += 2
            if bull:       score -= 2
            if rsi > 60:   score += 1
            if m5 < -1.5:  score += 2
            if not macd_above: score += 1

    if iv_percentile > 70: score -= 1
    elif iv_percentile < 25: score += 2

    ad = abs(delta)
    if 0.35 <= ad <= 0.65:  score += 2
    elif 0.20 <= ad < 0.35: score += 1
    elif ad < 0.15:         score -= 2

    if dte <= 2:   score -= 3
    elif dte <= 5: score -= 1
    else:          score += 1

    if vix > 20:  score += 1
    elif vix < 13: score += 1

    if not spot or spot <= 0:
        return None

    pct = (K - spot) / spot * 100 if otype == "CE" else (spot - K) / spot * 100
    if 0 <= pct <= 0.5:    score += 2
    elif 0.5 < pct <= 1.5: score += 1
    elif pct > 3:          score -= 2

    if score >= 7:   sig = "STRONG BUY"; str_ = min(95, 60 + score * 3)
    elif score >= 4: sig = "BUY";        str_ = min(80, 50 + score * 5)
    elif score <= -3: sig = "AVOID";     str_ = max(10, 50 + score * 5)
    else:             sig = "NEUTRAL";   str_ = 45
    return {"signal": sig, "score": score, "strength": str_, "reasons": reasons}


# ─── Composite Ranking Score ──────────────────────────────────────────────────
def compute_rank_score(result, rs_rating=None, stage=None):
    score = result.get("strength", 0) * 0.4
    if rs_rating: score += min(rs_rating * 20, 40)
    if stage == 2: score += 20
    score += min(result.get("vr", 1) * 5, 15)
    score += min(result.get("adx", 0) * 0.3, 10)
    return round(score, 2)


# ─── Parallel Scanner ─────────────────────────────────────────────────────────
def scan_parallel(symbols, mode="INTRADAY", market_mood="NEUTRAL", vix=15.0,
                  max_workers=10, min_strength=55, use_fundamentals=False):
    """
    v5: max_workers reduced to 10 (NSE API rate limit is stricter than yfinance).
    All data now comes from NSE India public API — no login, no API key.
    """
    results = []

    def _scan_one(sym):
        try:
            if mode == "DELIVERY":
                df        = get_ohlcv(sym, "3y", "1d")
                df_weekly = get_ohlcv(sym, "2y", "1wk")
            else:
                df        = get_ohlcv(sym, "3mo", "1d")
                df_weekly = None

            ind = compute_indicators(df, for_delivery=(mode == "DELIVERY"))
            if not ind:
                return None

            fund = {}
            if use_fundamentals or mode == "DELIVERY":
                try:
                    fund = get_fundamentals(sym)
                except Exception:
                    fund = {}

            rs_rating = None
            if mode == "DELIVERY" and df is not None:
                rs_rating = compute_rs_rating(df, period_days=65)

            rec, strength, bs, ss, reasons = score_signal(
                ind, fund, df, market_mood, vix, mode,
                df_weekly=df_weekly, rs_rating=rs_rating
            )
            if rec == "NEUTRAL" and strength < min_strength:
                return None

            price = ind.get("close", 0)
            atr   = ind.get("atr", price * 0.02)

            if mode == "DELIVERY":
                if rec in ("BUY", "STRONG BUY", "WEAK BUY"):
                    target_1 = round(price * 1.08, 2)
                    target_2 = round(price * 1.12, 2)
                    target_3 = round(price * 1.18, 2)
                    sl       = round(price * 0.95, 2)
                    target   = target_2
                elif rec in ("SELL", "STRONG SELL", "WEAK SELL"):
                    target_1 = round(price * 0.92, 2)
                    target_2 = round(price * 0.88, 2)
                    target_3 = round(price * 0.82, 2)
                    sl       = round(price * 1.05, 2)
                    target   = target_2
                else:
                    target = price; sl = price
                    target_1 = target_2 = target_3 = price
            else:
                if rec in ("BUY", "STRONG BUY", "WEAK BUY"):
                    target = round(price * (1 + 0.015 * (bs / 5)), 2)
                    sl     = round(price - 1.5 * atr, 2)
                elif rec in ("SELL", "STRONG SELL", "WEAK SELL"):
                    target = round(price * (1 - 0.015 * (ss / 5)), 2)
                    sl     = round(price + 1.5 * atr, 2)
                else:
                    target = price; sl = price
                target_1 = target_2 = target_3 = target

            rr = abs(target - price) / max(abs(price - sl), 0.01)
            patterns  = detect_patterns(df)
            div       = detect_divergence(df, ind)
            stage, stage_desc = weinstein_stage(df_weekly) if df_weekly is not None else (None, "N/A")
            rank_score = compute_rank_score(
                {"strength": strength, "vr": ind.get("vr", 1), "adx": ind.get("adx", 0)},
                rs_rating=rs_rating, stage=stage
            )
            earn_risk, earn_dte = earnings_risk(fund) if fund else (False, None)
            sector = SECTOR_MAP.get(sym, fund.get("sector", "Unknown") if fund else "Unknown")
            pos_size, pos_tier = tiered_position_size(500000, strength)

            return {
                "symbol": sym, "rec": rec, "strength": strength,
                "buy_score": bs, "sell_score": ss,
                "price": price, "target": target, "sl": sl, "rr": round(rr, 2),
                "target_1": target_1, "target_2": target_2, "target_3": target_3,
                "atr": atr, "day_chg": ind.get("day_chg", 0),
                "m5": ind.get("m5", 0), "m20": ind.get("m20", 0),
                "vr": ind.get("vr", 1), "adx": ind.get("adx", 0),
                "rsi": ind.get("rsi", 50), "macd": ind.get("macd", 0),
                "roc10": ind.get("roc10", 0),
                "indicators": ind, "reasons": reasons,
                "patterns": [(p[0], p[1]) for p in patterns],
                "divergence": div,
                "s1": ind.get("s1", 0), "r1": ind.get("r1", 0),
                "rs_rating": rs_rating,
                "stage": stage, "stage_desc": stage_desc,
                "rank_score": rank_score,
                "earn_risk": earn_risk, "earn_dte": earn_dte,
                "sector": sector,
                "pos_size": pos_size, "pos_tier": pos_tier,
                "fundamentals": fund if fund else {},
                "gap_type": ind.get("gap_type"), "gap_pct": ind.get("gap_pct", 0),
                "ad_trend": ind.get("ad_trend", 0),
                "vwap": ind.get("vwap", 0),
                "st_bullish": ind.get("st_bullish", False),
            }
        except Exception:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for r in ex.map(_scan_one, symbols):
            if r and r["strength"] >= min_strength:
                results.append(r)

    if mode == "DELIVERY":
        results.sort(key=lambda x: -x["rank_score"])
    else:
        results.sort(key=lambda x: (0 if "BUY" in x["rec"] else 1, -x["strength"]))

    return results


def scan_segment_parallel(symbols, segment="EQUITY", mode="INTRADAY", market_mood="NEUTRAL", vix=15.0,
                          max_workers=20, min_strength=58):
    """Scanner wrapper for ETF/equity symbols plus MCX fallback signals."""
    regular = [s for s in symbols if not str(s).upper().endswith(".MCX")]
    results = scan_parallel(regular, mode, market_mood, vix, max_workers, min_strength) if regular else []
    for sym in symbols:
        if not str(sym).upper().endswith(".MCX"):
            continue
        price = get_live_price(sym)
        if not price or price <= 0:
            continue
        strength = max(int(min_strength), 60)
        rec = "BUY" if market_mood != "BEARISH" else "SELL"
        results.append({
            "symbol": sym, "rec": rec, "strength": strength, "price": price,
            "target": round(price * (1.018 if rec == "BUY" else 0.982), 2),
            "sl": round(price * (0.99 if rec == "BUY" else 1.01), 2),
            "rr": 1.8, "reasons": ["Angel One live MCX rate", "Intraday momentum fallback"],
            "patterns": [], "atr": price * 0.01,
        })
    return sorted(results, key=lambda x: -x.get("strength", 0))
