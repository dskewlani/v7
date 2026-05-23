"""
app.py — ProTrader Terminal v6
Next-Level Professional Trading Terminal: Equity · Options · Futures · Auto Trading
Enhancements:
  ✅ 12-second live price refresh (hard-locked, not 5s)
  ✅ Daily P&L goal tracker with progress ring
  ✅ Enhanced entry gate: min R/R, daily loss circuit-breaker, VIX guard
  ✅ Two-phase trailing stop (break-even → 1x ATR tight trail)
  ✅ Bloomberg-dark UI with live position pulse cards
  ✅ Daily stats banner on every tab
  ✅ Improved signal accuracy (ADX≥20, VR≥1.3, R/R≥1.3 minimum filters)
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import time
import math

import storage as db

import engine as eng
from ui import (
    TERMINAL_CSS, sig_badge, strength_bar, pnl_fmt, pnl_fmt_large,
    ticker_item, metric_card, metric_card_trend, level_box,
    profit_book_row, greek_box, live_position_card, daily_pnl_banner,
)

# ─── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ProTrader Terminal v6",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(TERMINAL_CSS, unsafe_allow_html=True)

# ─── Persistent State Bootstrap ───────────────────────────────────────────────
def load_persistent():
    if "loaded" not in st.session_state:
        for key, default in [
            ("eq_portfolio",  []),
            ("eq_history",    []),
            ("opt_portfolio", []),
            ("opt_history",   []),
            ("fut_portfolio", []),
            ("fut_history",   []),
            ("etf_portfolio", []),
            ("etf_history",   []),
            ("mcx_portfolio", []),
            ("mcx_history",   []),
            ("journal",       []),
            ("kelly_wr",      0.55),
            ("scan_eq",       []),
            ("scan_opt",      []),
            ("scan_fut",      []),
            ("auto_eq",       False),
            ("auto_opt",      False),
            ("auto_fut",      False),
            ("auto_etf",      False),
            ("auto_mcx",      False),
            ("auto_eq_end",   None),
            ("auto_opt_end",  None),
            ("auto_fut_end",  None),
            ("auto_etf_end",  None),
            ("auto_mcx_end",  None),
            ("daily_goal",    5000),
            ("daily_loss_limit", -3000),
            ("last_price_refresh", 0.0),
        ]:
            st.session_state[key] = db.load(key, default)
        st.session_state["loaded"] = True

load_persistent()

def save_all():
    for key in ["eq_portfolio","eq_history","opt_portfolio","opt_history",
                "fut_portfolio","fut_history","etf_portfolio","etf_history",
                "mcx_portfolio","mcx_history","journal","kelly_wr",
                "daily_goal","daily_loss_limit"]:
        db.save(key, st.session_state[key])

# ─── Safe number_input helper ─────────────────────────────────────────────────
def safe_num_input(label, hardcoded_min, dynamic_max, step=1, key=None, **kwargs):
    """
    Prevents StreamlitValueBelowMinError when dynamic_max < hardcoded_min.
    Computes a safe min, max, and default value automatically.
    """
    safe_max = max(int(hardcoded_min), int(dynamic_max))
    safe_min = min(int(hardcoded_min), int(dynamic_max))
    safe_min = max(1, safe_min)
    safe_max = max(safe_min, safe_max)
    safe_val = safe_max  # default to scanning everything
    safe_step = max(1, int(step))
    return st.number_input(label, safe_min, safe_max, safe_val, safe_step, key=key, **kwargs)

# ─── Live Index Data ──────────────────────────────────────────────────────────
@st.cache_data(ttl=5)
def get_indices():
    """Live index data from NSE India public API (no login, no API key)."""
    try:
        return eng.get_all_indices()
    except Exception:
        return {k: {"p": 0, "c": 0, "pct": 0, "h": 0, "l": 0}
                for k in ["BN", "NF", "VIX", "SX", "IT", "MID"]}

@st.cache_data(ttl=3600)
def get_expiries(n=5):
    dates = []
    d = datetime.now().date()

    for _ in range(n * 3):
        d += timedelta(days=1)
        if d.weekday() == 3:
            dates.append(d)
        if len(dates) == n:
            break

    return dates

def get_live_option_cmp(index: str, strike: int, opt_type: str,
                        expiry_str: str, vix: float) -> float | None:
    """
    Thin wrapper around eng.get_live_option_price — uses Black-Scholes with
    the freshest live spot price fetched directly from NSE (12s cache in engine).
    This is called every auto-trading cycle so CMP always reflects market moves.
    """
    return eng.get_live_option_price(index, int(strike), opt_type, expiry_str, vix)

def refresh_open_position_prices(vix: float = 15.0) -> None:
    """Refresh CMP/P&L for every open segment on each render."""
    for pos in st.session_state.get("eq_portfolio", []):
        lp = eng.get_live_price(pos["symbol"]) or pos.get("cmp", pos["entry"])
        pos["cmp"] = lp
        qty = pos.get("qty", 0)
        gross = (lp - pos["entry"]) * qty if pos.get("type") == "BUY" else (pos["entry"] - lp) * qty
        pos["pnl"] = round(gross - pos.get("brokerage", 0), 2)

    if st.session_state.get("opt_portfolio"):
        try:
            eng.force_refresh_index_spots()
        except Exception:
            pass
    for pos in st.session_state.get("opt_portfolio", []):
        ep = pos["entry"]
        try:
            eng._opt_price_cache.pop(
                f"opt_{pos['index']}_{pos['strike']}_{pos['type']}_{pos.get('expiry', '')}",
                None,
            )
        except Exception:
            pass
        live = eng.get_live_option_price(
            pos["index"], int(pos["strike"]), pos["type"], pos.get("expiry", ""), vix
        )
        cmp_val = live if live and live > 0 else pos.get("cmp", ep)
        pos["cmp"] = cmp_val
        pos["pnl"] = round((cmp_val - ep) * pos.get("lots", 1) * pos.get("lot_size", 1) - pos.get("brokerage", 0), 2)

    for pos in st.session_state.get("fut_portfolio", []):
        lp = eng.get_live_price(pos["symbol"]) or pos.get("cmp", pos["entry"])
        pos["cmp"] = lp
        qty = pos.get("lots", 1) * pos.get("lot_size", 1)
        gross = (lp - pos["entry"]) * qty if pos.get("type") == "LONG" else (pos["entry"] - lp) * qty
        pos["pnl"] = round(gross - pos.get("brokerage", 0), 2)

    for key in ["etf_portfolio", "mcx_portfolio"]:
        for pos in st.session_state.get(key, []):
            lp = eng.get_live_price(pos["symbol"]) or pos.get("cmp", pos["entry"])
            pos["cmp"] = lp
            qty = pos.get("qty", 0)
            gross = (lp - pos["entry"]) * qty if pos.get("type") == "BUY" else (pos["entry"] - lp) * qty
            pos["pnl"] = round(gross - pos.get("brokerage", 0), 2)

def update_kelly():
    j = st.session_state.journal
    if j:
        wins = sum(1 for x in j if x.get("win", False))
        st.session_state.kelly_wr = wins / len(j)
        db.save("kelly_wr", st.session_state.kelly_wr)

# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="font-family:'Space Grotesk';font-size:0.9rem;color:var(--accent2);
    letter-spacing:3px;padding:8px 0;border-bottom:1px solid var(--border);
    margin-bottom:12px;">⚙ SETTINGS</div>
    """, unsafe_allow_html=True)

    capital    = st.number_input("Total Capital (₹)",   50000, 10000000, 500000, 50000)
    trade_cap  = st.number_input("Capital/Trade (₹)", 100000, 5000000, 100000, 10000)
    use_kelly  = st.checkbox("Kelly Criterion Sizing",  value=True)
    use_trail  = st.checkbox("Trailing Stop Loss",       value=True)
    use_time_x = st.checkbox("Time-Based Exit",          value=True)
    use_mktf   = st.checkbox("Market Mood Filter",       value=True)
    use_fundm  = st.checkbox("Fundamental Filter",       value=False)
    min_str    = st.slider("Min Signal Strength", 45, 90, 62, 2)
    n_strikes  = st.slider("Option Chain Strikes (each side ATM)", 5, 15, 10, 1)

    st.markdown("---")
    st.markdown("""<div style="font-family:'Space Grotesk';font-size:0.75rem;color:var(--accent2);
    letter-spacing:2px;margin-bottom:8px;">🎯 DAILY P&L GOALS</div>""", unsafe_allow_html=True)
    st.session_state.daily_goal = st.number_input(
        "Daily Profit Goal (₹)", 1000, 100000, int(st.session_state.daily_goal), 500, key="dg_input"
    )
    st.session_state.daily_loss_limit = st.number_input(
        "Daily Loss Limit (₹)", -50000, -500, int(st.session_state.daily_loss_limit), 500,
        key="dll_input"
    )

    st.markdown("---")
    st.markdown("""
    <div style="font-family:'Space Grotesk';font-size:0.75rem;color:var(--accent2);
    letter-spacing:2px;">📅 EXPIRY</div>
    """, unsafe_allow_html=True)
    expiries   = get_expiries(5)
    exp_labels = [e.strftime("%d %b %Y") for e in expiries]
    exp_bn_lbl = st.selectbox("BankNifty Expiry", exp_labels)
    exp_nf_lbl = st.selectbox("Nifty50 Expiry",   exp_labels)
    exp_bn     = expiries[exp_labels.index(exp_bn_lbl)]
    exp_nf     = expiries[exp_labels.index(exp_nf_lbl)]

    st.markdown("---")
    st.markdown("""
    <div style="font-family:'Space Grotesk';font-size:0.75rem;color:var(--accent2);
    letter-spacing:2px;">📊 SESSION P&L</div>
    """, unsafe_allow_html=True)
    ep = sum(x.get("pnl", 0) for x in st.session_state.eq_portfolio)
    op = sum(x.get("pnl", 0) for x in st.session_state.opt_portfolio)
    fp = sum(x.get("pnl", 0) for x in st.session_state.fut_portfolio)
    xp = sum(x.get("pnl", 0) for x in st.session_state.etf_portfolio)
    mp = sum(x.get("pnl", 0) for x in st.session_state.mcx_portfolio)
    eh = sum(x.get("pnl", 0) for x in st.session_state.eq_history)
    oh = sum(x.get("pnl", 0) for x in st.session_state.opt_history)
    fh = sum(x.get("pnl", 0) for x in st.session_state.fut_history)
    xh = sum(x.get("pnl", 0) for x in st.session_state.etf_history)
    mh = sum(x.get("pnl", 0) for x in st.session_state.mcx_history)
    total     = ep + op + fp + xp + mp + eh + oh + fh + xh + mh
    open_pnl  = ep + op + fp + xp + mp
    real_pnl  = eh + oh + fh + xh + mh
    pnl_color = "var(--green3)" if total >= 0 else "var(--red3)"
    goal      = st.session_state.daily_goal
    goal_pct  = min(100, max(0, total / goal * 100)) if goal > 0 else 0
    st.markdown(f"""
    <div style="font-family:'JetBrains Mono';font-size:0.7rem;color:var(--tx2);">
        Open P&amp;L:  <span style="color:{'var(--green3)' if open_pnl>=0 else 'var(--red3)'}">₹{open_pnl:+,.0f}</span><br>
        Realized:  <span style="color:{'var(--green3)' if real_pnl>=0 else 'var(--red3)'}">₹{real_pnl:+,.0f}</span><br>
        <span style="font-size:1rem;color:{pnl_color};font-weight:700;">TOTAL: ₹{total:+,.0f}</span><br>
        <span style="font-size:0.62rem;color:var(--muted);">Goal: ₹{goal:,.0f} ({goal_pct:.0f}%)</span>
    </div>
    <div style="background:var(--bg3);border-radius:4px;height:5px;overflow:hidden;margin-top:5px;">
      <div style="height:5px;border-radius:4px;width:{goal_pct:.1f}%;background:{'var(--green3)' if total>=0 else 'var(--red3)'};transition:width .5s;"></div>
    </div>""", unsafe_allow_html=True)

    st.markdown("---")
    kelly_wr = st.session_state.kelly_wr
    st.markdown(f"""
    <div class="info-b" style="font-size:0.72rem;">
    🧮 Kelly WR: <b>{kelly_wr*100:.1f}%</b><br>
    Trades: {len(st.session_state.journal)}</div>
    """, unsafe_allow_html=True)

    if st.button("🗑️ Clear ALL Data", use_container_width=True):
        for key in ["eq_portfolio","eq_history","opt_portfolio","opt_history",
                    "fut_portfolio","fut_history","etf_portfolio","etf_history","mcx_portfolio","mcx_history","journal"]:
            st.session_state[key] = []
            db.delete(key)
        st.success("All data cleared.")
        st.rerun()

# ─── Header ───────────────────────────────────────────────────────────────────
idx = get_indices()
bn  = idx.get("BN",  {})
nf  = idx.get("NF",  {})
vx  = idx.get("VIX", {})
sx  = idx.get("SX",  {})
it  = idx.get("IT",  {})
mid = idx.get("MID", {})
vix_val = vx.get("p", 15.0)
refresh_open_position_prices(vix_val)

st.markdown(f"""
<div class="terminal-header">
    <div class="terminal-title">
        <span class="terminal-dot"></span>
        📡 <span>ProTrader</span> Terminal <span style="color:var(--p4);">v6</span>
    </div>
    <div class="terminal-sub">
        NSE · BSE · Options · Futures · Auto AI Trading ·
        Live 12s Refresh · {datetime.now().strftime('%d %b %Y %H:%M:%S')}
    </div>
</div>""", unsafe_allow_html=True)

# Ticker tape
items = [
    ticker_item("BANKNIFTY", bn.get("p",  0), bn.get("pct",  0)),
    ticker_item("NIFTY50",   nf.get("p",  0), nf.get("pct",  0)),
    ticker_item("SENSEX",    sx.get("p",  0), sx.get("pct",  0)),
    ticker_item("VIX",       vx.get("p",  0), vx.get("pct",  0)),
    ticker_item("NIFTYIT",   it.get("p",  0), it.get("pct",  0)),
    ticker_item("NIFTYMID",  mid.get("p", 0), mid.get("pct", 0)),
]
tape = " ◆ ".join(items)
st.markdown(
    f'<div class="ticker-outer"><div class="ticker-inner">{tape+" ◆ "+tape}</div></div>',
    unsafe_allow_html=True,
)

# Index cards
ic = st.columns(6)
def icard(col, label, d, css):
    c = "up" if d.get("pct", 0) >= 0 else "dn"
    a = "▲"  if d.get("pct", 0) >= 0 else "▼"
    col.markdown(f"""
    <div class="idx-card {css}">
        <div class="idx-label">{label}</div>
        <div class="idx-price {c}">{d.get('p',0):,.2f}</div>
        <div class="idx-chg {c}">{a} {d.get('c',0):+,.2f} ({d.get('pct',0):+.2f}%)</div>
    </div>""", unsafe_allow_html=True)

icard(ic[0], "BANKNIFTY", bn,  "bn")
icard(ic[1], "NIFTY 50",  nf,  "nf")
icard(ic[2], "SENSEX",    sx,  "sx")
icard(ic[3], "VIX",       vx,  "vx")
icard(ic[4], "NIFTY IT",  it,  "it")
icard(ic[5], "NIFTY MID", mid, "nf")

if vix_val > 22:
    st.markdown(
        f'<div class="warn-b">⚠️ HIGH VIX {vix_val:.1f} — Options expensive. '
        f'Prefer spreads. Widen stops. Only STRONG signals (≥75%) in auto-trade.</div>',
        unsafe_allow_html=True,
    )
elif vix_val < 13:
    st.markdown(
        f'<div class="info-b">🟢 LOW VIX {vix_val:.1f} — Options cheap. '
        f'Good time to buy directional CE/PE on breakouts.</div>',
        unsafe_allow_html=True,
    )

# ─── Daily P&L Banner ──────────────────────────────────────────────────────────
_d_stats = eng.compute_daily_pnl_stats(
    st.session_state.eq_history,  st.session_state.opt_history,
    st.session_state.fut_history,  st.session_state.etf_history,
    st.session_state.mcx_history,
    st.session_state.eq_portfolio, st.session_state.opt_portfolio,
    st.session_state.fut_portfolio, st.session_state.etf_portfolio,
    st.session_state.mcx_portfolio,
    daily_goal=float(st.session_state.daily_goal),
)
st.markdown(
    daily_pnl_banner(
        _d_stats["realized"], _d_stats["unrealized"],
        _d_stats["trades_today"], _d_stats["win_rate"],
        daily_goal=float(st.session_state.daily_goal),
        trades_closed=_d_stats["trades_today"],
    ),
    unsafe_allow_html=True,
)

# ─── Daily loss circuit breaker warning ───────────────────────────────────────
_dll = float(st.session_state.daily_loss_limit)
if _d_stats["total"] <= _dll:
    st.markdown(
        f'<div class="danger-b">🛑 <b>DAILY LOSS LIMIT REACHED</b> — '
        f'Auto-trading is halted. Total P&L: ₹{_d_stats["total"]:+,.0f} | '
        f'Limit: ₹{_dll:,.0f}. Reset tomorrow or adjust limit in sidebar.</div>',
        unsafe_allow_html=True,
    )

@st.cache_data(ttl=300)
def market_mood_data():
    """Market mood from NSE Nifty50 historical data (no yfinance)."""
    try:
        df = eng.get_ohlcv("^NSEI", "1mo", "1d")
        if df is None or len(df) < 2:
            return "NEUTRAL"
        c   = df["Close"].astype(float)
        e5  = c.ewm(span=5).mean()
        chg = float((c.iloc[-1] - c.iloc[-2]) / c.iloc[-2] * 100)
        if c.iloc[-1] > e5.iloc[-1] and chg > 0.3:
            return "BULLISH"
        elif c.iloc[-1] < e5.iloc[-1] and chg < -0.3:
            return "BEARISH"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"

mood        = market_mood_data() if use_mktf else "NEUTRAL"
mood_filter = mood if use_mktf else "NEUTRAL"

st.markdown("<br>", unsafe_allow_html=True)

# ─── MAIN TABS ────────────────────────────────────────────────────────────────
page_tabs = st.tabs([
    "📈 EQUITY", "⚡ OPTIONS", "🔮 FUTURES", "ETF", "MCX",
    "💼 PORTFOLIO", "📜 HISTORY", "📓 JOURNAL", "📊 ANALYTICS"
])

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — EQUITY
# ══════════════════════════════════════════════════════════════════════════════
with page_tabs[0]:
    st.markdown('<div class="sec-ttl">📈 EQUITY TRADING — NSE + BSE FULL UNIVERSE</div>',
                unsafe_allow_html=True)

    eq_tabs = st.tabs(["🔍 Scanner", "⚡ Auto Trading", "💼 Open Positions", "📜 Trade History"])

    # ── EQ Scanner ────────────────────────────────────────────────────────────
    with eq_tabs[0]:
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
        with c1:
            eq_mode   = st.radio("Mode", ["INTRADAY", "DELIVERY"], horizontal=True)
        with c2:
            eq_exch   = st.multiselect("Exchange", ["NSE", "BSE"], default=["NSE"])
        with c3:
            eq_filter = st.selectbox("Show", ["All Signals", "BUY Only", "SELL Only", "STRONG Only"])
        with c4:
            _eq_universe_total = len(eng.NSE_SYMBOLS) + len(eng.BSE_SYMBOLS)
            _eq_universe_total = max(50, _eq_universe_total)
            eq_max_scan = st.number_input(
                "Max stocks to scan", 50, _eq_universe_total, min(200, _eq_universe_total), 50
            )

        scan_universe = []
        if "NSE" in eq_exch:
            scan_universe += eng.NSE_SYMBOLS
        if "BSE" in eq_exch:
            scan_universe += eng.BSE_SYMBOLS
        scan_universe = scan_universe[:int(eq_max_scan)]

        col_btn, col_sym = st.columns([1, 3])
        with col_btn:
            do_scan = st.button("🔭 SCAN ALL STOCKS", use_container_width=True)
        with col_sym:
            _qs_list = [""] + eng.NSE_SYMBOLS[:100]
            quick_sym = st.selectbox("Quick Analyse", _qs_list)

        if do_scan or (quick_sym and quick_sym != ""):
            syms = [quick_sym] if quick_sym else scan_universe
            prog = st.progress(0)
            with st.spinner(f"Scanning {len(syms)} stocks…"):
                results = eng.scan_parallel(
                    syms, mode=eq_mode,
                    market_mood=mood_filter, vix=vix_val,
                    max_workers=40, min_strength=min_str,
                )
            prog.progress(1.0)
            prog.empty()
            st.session_state["scan_eq"] = results

        results = st.session_state.get("scan_eq", [])
        if eq_filter == "BUY Only":
            results = [r for r in results if "BUY"    in r["rec"]]
        elif eq_filter == "SELL Only":
            results = [r for r in results if "SELL"   in r["rec"]]
        elif eq_filter == "STRONG Only":
            results = [r for r in results if "STRONG" in r["rec"]]

        if results:
            buys  = [r for r in results if "BUY"  in r["rec"]]
            sells = [r for r in results if "SELL" in r["rec"]]
            mc    = st.columns(5)
            mc[0].markdown(metric_card(len(results), "Total Signals",  "var(--accent2)"), unsafe_allow_html=True)
            mc[1].markdown(metric_card(len(buys),    "BUY Signals",    "var(--green3)"),  unsafe_allow_html=True)
            mc[2].markdown(metric_card(len(sells),   "SELL Signals",   "var(--red3)"),    unsafe_allow_html=True)
            avg_s = int(np.mean([r["strength"] for r in results])) if results else 0
            mc[3].markdown(metric_card(f"{avg_s}%",  "Avg Strength",   "var(--gold3)"),   unsafe_allow_html=True)
            sq_ct = len([r for r in results if "STRONG" in r["rec"]])
            mc[4].markdown(metric_card(sq_ct,        "Strong Signals",  "var(--teal)"),  unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            tbl = []
            for r in results:
                pats = ", ".join([p[0] for p in r.get("patterns", [])]) or "—"
                div  = "✓ " + r["divergence"][0] if r.get("divergence") else "—"
                tbl.append({
                    "Symbol":     r["symbol"].replace(".NS", "").replace(".BO", ""),
                    "CMP(₹)":    f"₹{r['price']:,.2f}",
                    "Signal":     r["rec"],
                    "Strength":   r["strength"],
                    "Target":    f"₹{r['target']:,.2f}",
                    "SL":        f"₹{r['sl']:,.2f}",
                    "R/R":       f"{r['rr']:.2f}",
                    "Day%":      f"{r.get('day_chg',0):+.2f}%",
                    "5D%":       f"{r.get('m5',0):+.1f}%",
                    "RSI":       f"{r.get('rsi',0):.0f}",
                    "ADX":       f"{r.get('adx',0):.0f}",
                    "Vol Ratio": f"{r.get('vr',1):.1f}x",
                    "Pattern":    pats,
                    "Divergence": div,
                })
            st.dataframe(pd.DataFrame(tbl), use_container_width=True, hide_index=True)
            st.markdown("<br>", unsafe_allow_html=True)

            # Deduplicate by symbol to prevent duplicate Streamlit button keys
            seen_syms = set()
            deduped_results = []
            for r in results:
                if r["symbol"] not in seen_syms:
                    seen_syms.add(r["symbol"])
                    deduped_results.append(r)

            st.markdown("#### 🔎 Detailed Cards")
            for card_idx, r in enumerate(deduped_results[:40]):
                icon = "🟢" if "BUY" in r["rec"] else ("🔴" if "SELL" in r["rec"] else "🟡")
                with st.expander(
                    f"{icon} {r['symbol'].replace('.NS','').replace('.BO','')} | "
                    f"₹{r['price']:,.2f} | {r['rec']} | Str:{r['strength']}% | "
                    f"ADX:{r.get('adx',0):.0f} | RSI:{r.get('rsi',0):.0f}"
                ):
                    d1,d2,d3,d4,d5,d6 = st.columns(6)
                    d1.metric("CMP",       f"₹{r['price']:,.2f}")
                    d2.metric("Target",    f"₹{r['target']:,.2f}")
                    d3.metric("SL",        f"₹{r['sl']:,.2f}")
                    d4.metric("R/R",       f"{r['rr']:.2f}")
                    d5.metric("5D Mov",    f"{r.get('m5',0):+.1f}%")
                    d6.metric("Vol Ratio", f"{r.get('vr',1):.1f}x")

                    ind = r.get("indicators", {})
                    if ind:
                        st.markdown("**Indicators**")
                        i1,i2,i3,i4,i5,i6,i7 = st.columns(7)
                        i1.metric("RSI",   f"{ind.get('rsi',0):.1f}")
                        i2.metric("MACD",  f"{ind.get('macd',0):.3f}")
                        i3.metric("ADX",   f"{ind.get('adx',0):.0f}")
                        i4.metric("BB%",   f"{ind.get('bb_pct',0):.2f}")
                        i5.metric("Stoch", f"{ind.get('sk',0):.0f}")
                        i6.metric("CCI",   f"{ind.get('cci',0):.0f}")
                        i7.metric("WR%",   f"{ind.get('wr',0):.0f}")

                    if ind.get("s1") and ind.get("r1"):
                        st.markdown("**Support & Resistance**")
                        sr1,sr2,sr3,sr4 = st.columns(4)
                        sr1.markdown(level_box("S2", ind.get("s2",0), "lvl-s"), unsafe_allow_html=True)
                        sr2.markdown(level_box("S1", ind.get("s1",0), "lvl-s"), unsafe_allow_html=True)
                        sr3.markdown(level_box("R1", ind.get("r1",0), "lvl-r"), unsafe_allow_html=True)
                        sr4.markdown(level_box("R2", ind.get("r2",0), "lvl-r"), unsafe_allow_html=True)

                    if r.get("patterns"):
                        phtml = " ".join([
                            f'<span style="background:rgba(245,166,35,0.12);border:1px solid '
                            f'rgba(245,166,35,0.4);color:var(--gold);border-radius:4px;'
                            f'padding:2px 8px;font-size:0.72rem;font-family:JetBrains Mono;">'
                            f'{p[0]}</span>' for p in r["patterns"]
                        ])
                        st.markdown(f"**Candlestick:** {phtml}", unsafe_allow_html=True)

                    if r.get("divergence"):
                        st.markdown(
                            f'<div class="success-b">📐 {r["divergence"][2]}</div>',
                            unsafe_allow_html=True,
                        )
                    if ind.get("squeeze"):
                        st.markdown(
                            '<div class="warn-b">⚡ TTM SQUEEZE FIRING — Big move imminent!</div>',
                            unsafe_allow_html=True,
                        )

                    st.markdown("**Signal Reasoning**")
                    for rn in r["reasons"][:8]:
                        st.markdown(
                            f"<div style='font-size:0.78rem;color:var(--tx3);padding:1px 0;'>• {rn}</div>",
                            unsafe_allow_html=True,
                        )

                    bar_c = "#00e676" if "BUY" in r["rec"] else "#ff1744"
                    st.markdown(strength_bar(r["strength"], bar_c), unsafe_allow_html=True)

                    # Fixed allocation: ₹1 lakh per trade
                    qty = max(1, int(100000 / r["price"])) if r["price"] > 0 else 1
                    cost = eng.equity_cost(r["price"], qty, "BUY", eq_mode == "DELIVERY")
                    st.markdown(
                        f'<div class="info-b">🧮 Fixed Allocation: ₹1,00,000 | '
                        f'Qty: {qty} shares | Est. Charges: ₹{cost:.2f}</div>',
                        unsafe_allow_html=True,
                    )

                    bc1, bc2 = st.columns(2)
                    with bc1:
                        if r["rec"] not in ("NEUTRAL",) and st.button(
                            f"🚀 EXECUTE {r['rec']}", key=f"eq_exec_{r['symbol']}_{card_idx}"
                        ):
                            qty2  = max(1, int(100000 / r["price"])) if r["price"] > 0 else 1
                            trade = {
                                "id":         f"{r['symbol']}_{int(time.time()*1000)}",
                                "symbol":     r["symbol"],
                                "type":       "BUY" if "BUY" in r["rec"] else "SELL",
                                "mode":       eq_mode,
                                "entry":      r["price"],
                                "cmp":        eng.get_live_price(r["symbol"]) or r["price"],
                                "qty":        qty2,
                                "invested":   round(r["price"] * qty2, 2),
                                "brokerage":  eng.equity_cost(r["price"], qty2, "BUY", eq_mode == "DELIVERY"),
                                "target":     r["target"],
                                "sl":         r["sl"],
                                "trailing_sl": None,
                                "pnl":        0.0,
                                "rec":        r["rec"],
                                "strength":   r["strength"],
                                "rr":         r["rr"],
                                "reasons":    r["reasons"][:5],
                                "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "entry_dt":   datetime.now().isoformat(),
                                "patterns":   [p[0] for p in r.get("patterns", [])],
                            }
                            st.session_state.eq_portfolio.append(trade)
                            db.save("eq_portfolio", st.session_state.eq_portfolio)
                            st.success(f"✅ {r['rec']} executed: {r['symbol']} @ ₹{r['price']:.2f}")

    # ── EQ Auto Trading ───────────────────────────────────────────────────────
    with eq_tabs[1]:
        st.markdown('<div class="sec-ttl">⚡ EQUITY AUTO TRADING ENGINE</div>', unsafe_allow_html=True)

        if not st.session_state.auto_eq:
            st.markdown(f"""
            <div style="background:var(--bg2);border:1px solid var(--accent);
            border-radius:14px;padding:24px;text-align:center;margin-bottom:16px;
            background:linear-gradient(135deg,var(--bg2) 0%,rgba(59,130,246,0.04) 100%);">
                <div style="font-family:'Space Grotesk';font-size:1.35rem;color:var(--accent2);
                font-weight:800;letter-spacing:1px;">⚡ AI EQUITY AUTO TRADER</div>
                <div style="color:var(--tx3);font-size:0.8rem;margin-top:8px;line-height:1.6;">
                    Scans ALL NSE+BSE stocks · Momentum + Technical + Pattern ·
                    Kelly Sizing · Two-Phase Trailing SL · Min R/R 1.3 · Daily Loss Guard
                </div>
            </div>""", unsafe_allow_html=True)

            _, ac2, _ = st.columns([1, 2, 1])
            with ac2:
                a_dur  = st.number_input("Duration (minutes)", 1, 390, 30, 5, key="eq_dur")
                a_mode = st.radio("Trading Mode", ["INTRADAY", "DELIVERY"], horizontal=True, key="eq_at_mode")
                a_max  = st.number_input("Max simultaneous positions", 1, 20, 5, 1, key="eq_max")
                _eq_scan_max = max(50, len(eng.NSE_SYMBOLS))
                a_scan = st.number_input(
                    "Stocks to scan per cycle", 50, _eq_scan_max,
                    min(200, _eq_scan_max), 50, key="eq_scan_n"
                )
                st.markdown(
                    f'<div class="info-b">Market: <b>{mood}</b> | VIX: {vix_val:.1f} | '
                    f'Kelly WR: {st.session_state.kelly_wr*100:.1f}% | '
                    f'Daily P&L: ₹{_d_stats["total"]:+,.0f}</div>',
                    unsafe_allow_html=True,
                )
                _dll_ok = _d_stats["total"] > float(st.session_state.daily_loss_limit)
                if not _dll_ok:
                    st.error(f"🛑 Daily loss limit ₹{st.session_state.daily_loss_limit:,.0f} reached. Trading blocked.")
                else:
                    if st.button("🚀 START EQUITY AUTO TRADING", use_container_width=True, key="eq_auto_start"):
                        st.session_state.auto_eq       = True
                        st.session_state.auto_eq_end   = (
                            datetime.now() + timedelta(minutes=int(a_dur))
                        ).isoformat()
                        st.session_state["eq_at_mode2"]  = a_mode
                        st.session_state["eq_at_max"]    = int(a_max)
                        st.session_state["eq_at_scan"]   = int(a_scan)
                        st.session_state["eq_total_s"]   = float(a_dur) * 60.0
                        st.session_state["eq_start_ts"]  = time.time()
                        db.save("auto_eq",     True)
                        db.save("auto_eq_end", st.session_state.auto_eq_end)
                        st.rerun()
        else:
            end_dt   = datetime.fromisoformat(st.session_state.auto_eq_end)
            rem      = max(0.0, (end_dt - datetime.now()).total_seconds())
            _eq_total_s  = float(st.session_state.get("eq_total_s", 1800))
            _eq_start_ts = float(st.session_state.get("eq_start_ts", time.time()))
            elapsed_eq   = time.time() - _eq_start_ts
            prog_pct     = max(0.0, min(1.0, elapsed_eq / max(_eq_total_s, 1)))

            opnl = sum(p.get("pnl", 0) for p in st.session_state.eq_portfolio)
            rpnl = sum(p.get("pnl", 0) for p in st.session_state.eq_history)
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("⏱ Time Left",   f"{int(rem//60)}m {int(rem%60)}s")
            c2.metric("📂 Open Pos",    len(st.session_state.eq_portfolio))
            c3.metric("📈 Live P&L",    f"₹{opnl:+,.0f}",
                      delta=f"{'▲' if opnl>=0 else '▼'}", delta_color="normal" if opnl>=0 else "inverse")
            c4.metric("✅ Realized",    f"₹{rpnl:+,.0f}")
            c5.metric("🎯 Daily Goal",  f"{_d_stats['goal_pct']:.0f}%")
            st.progress(prog_pct)

            if rem <= 0:
                st.warning("⏰ Session ended — squaring off all equity positions!")
                for pos in st.session_state.eq_portfolio:
                    ep2  = pos["entry"]; cmp2 = pos.get("cmp", ep2); qty2 = pos["qty"]
                    gross = (cmp2 - ep2) * qty2 if pos["type"] == "BUY" else (ep2 - cmp2) * qty2
                    net   = gross - pos.get("brokerage", 0)
                    closed = {
                        **pos,
                        "exit":      cmp2,
                        "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "pnl":       round(net, 2),
                        "status":    "CLOSED",
                    }
                    st.session_state.eq_history.append(closed)
                    st.session_state.journal.append({
                        "cat":      "EQUITY",
                        "symbol":   pos["symbol"],
                        "pnl":      round(net, 2),
                        "win":      net >= 0,
                        "strength": pos.get("strength", 0),
                        "date":     datetime.now().strftime("%Y-%m-%d"),
                        "rec":      pos.get("rec", ""),
                    })
                st.session_state.eq_portfolio = []
                st.session_state.auto_eq      = False
                db.save("auto_eq", False)
                db.save("eq_portfolio", [])
                db.save("eq_history",   st.session_state.eq_history)
                db.save("journal",      st.session_state.journal)
                update_kelly()
                st.rerun()
            else:
                _max  = st.session_state.get("eq_at_max",  5)
                _mode = st.session_state.get("eq_at_mode2", "INTRADAY")
                _sc   = st.session_state.get("eq_at_scan",  200)

                if len(st.session_state.eq_portfolio) < _max:
                    # v6: check daily loss circuit-breaker first
                    _curr_daily = _d_stats["total"]
                    _dll2 = float(st.session_state.daily_loss_limit)
                    if _curr_daily <= _dll2:
                        st.error(f"🛑 Daily loss limit ₹{_dll2:,.0f} hit. New entries blocked.")
                    else:
                        with st.spinner("Scanning for signals…"):
                            scan_syms = eng.NSE_SYMBOLS[:_sc]
                            new_sigs  = eng.scan_parallel(scan_syms, _mode, mood_filter, vix_val, 40, min_str)
                        existing = {p["symbol"] + p["type"] for p in st.session_state.eq_portfolio}
                        for sig in new_sigs:
                            if len(st.session_state.eq_portfolio) >= _max:
                                break
                            if sig["rec"] == "NEUTRAL":
                                continue
                            k = sig["symbol"] + ("BUY" if "BUY" in sig["rec"] else "SELL")
                            if k in existing:
                                continue
                            # v6: Enhanced entry gate
                            ok, reason = eng.should_enter_trade(
                                sig, _mode, mood_filter, vix_val,
                                daily_pnl=_curr_daily,
                                daily_goal=float(st.session_state.daily_goal),
                                daily_loss_limit=_dll2,
                            )
                            if not ok:
                                continue
                            p = sig["price"]
                            if p <= 0:
                                continue
                            kc2  = eng.kelly_size(float(trade_cap), st.session_state.kelly_wr, sig["rr"], sig["strength"]) if use_kelly else float(trade_cap)
                            qty3 = max(eng.min_cash_qty(p), int(kc2 / p))
                            cost = eng.equity_cost(p, qty3, "BUY", _mode == "DELIVERY")
                            trade = {
                                "id":         f"{sig['symbol']}_{int(time.time()*1000)}",
                                "symbol":     sig["symbol"],
                                "type":       "BUY" if "BUY" in sig["rec"] else "SELL",
                                "mode":       _mode,
                                "entry":      p, "cmp": eng.get_live_price(sig["symbol"]) or p,
                                "qty":        qty3,
                                "invested":   round(p * qty3, 2),
                                "brokerage":  cost,
                                "target":     sig["target"],
                                "sl":         sig["sl"],
                                "atr":        sig.get("atr", p * 0.015),
                                "trailing_sl": None,
                                "pnl":        0.0,
                                "rec":        sig["rec"],
                                "strength":   sig["strength"],
                                "rr":         sig["rr"],
                                "reasons":    sig["reasons"][:5],
                                "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "entry_dt":   datetime.now().isoformat(),
                                "patterns":   [p2[0] for p2 in sig.get("patterns", [])],
                            }
                            st.session_state.eq_portfolio.append(trade)
                            existing.add(k)

                still = []
                for pos in st.session_state.eq_portfolio:
                    # FIX: fall back to last known CMP (not entry price) so that
                    # the display never freezes at the buying price when the API
                    # is briefly unavailable between refresh cycles.
                    _fetched = eng.get_live_price(pos["symbol"])
                    lp = _fetched if (_fetched and _fetched > 0) else pos.get("cmp", pos["entry"])
                    pos["cmp"] = lp
                    ep2  = pos["entry"]; qty2 = pos["qty"]; cost = pos.get("brokerage", 0)
                    gross = (lp - ep2) * qty2 if pos["type"] == "BUY" else (ep2 - lp) * qty2
                    pos["pnl"] = round(gross - cost, 2)

                    # v6: Enhanced two-phase trailing stop
                    if use_trail:
                        pos = eng.update_trailing_stop(pos, lp, use_trail=True)

                    eff_sl = pos.get("trailing_sl") or pos.get("sl", 0)
                    hit = (
                        (pos["type"] == "BUY"  and (lp >= pos.get("target", lp + 1) or lp <= eff_sl)) or
                        (pos["type"] == "SELL" and (lp <= pos.get("target", 0)       or lp >= eff_sl))
                    )
                    if use_time_x:
                        try:
                            ed = datetime.fromisoformat(pos.get("entry_dt", datetime.now().isoformat()))
                            if (datetime.now() - ed).total_seconds() > 1800 and abs(lp - ep2) / ep2 < 0.005:
                                hit = True
                        except Exception:
                            pass

                    if hit:
                        cost2  = eng.equity_cost(lp, qty2, pos["type"], _mode == "DELIVERY")
                        gross2 = (lp - ep2) * qty2 if pos["type"] == "BUY" else (ep2 - lp) * qty2
                        net    = gross2 - cost - cost2
                        st.session_state.eq_history.append({
                            **pos,
                            "exit":      lp,
                            "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "pnl":       round(net, 2),
                            "status":    "CLOSED",
                        })
                        st.session_state.journal.append({
                            "cat":      "EQUITY",
                            "symbol":   pos["symbol"],
                            "pnl":      round(net, 2),
                            "win":      net >= 0,
                            "strength": pos.get("strength", 0),
                            "date":     datetime.now().strftime("%Y-%m-%d"),
                            "rec":      pos.get("rec", ""),
                        })
                    else:
                        still.append(pos)

                st.session_state.eq_portfolio = still
                db.save("eq_portfolio", still)
                db.save("eq_history",   st.session_state.eq_history)
                db.save("journal",      st.session_state.journal)
                update_kelly()

                st.caption(f"🔄 Live 12s refresh | {datetime.now().strftime('%H:%M:%S')} | Positions: {len(st.session_state.eq_portfolio)}")
                st.markdown("### Live Equity Positions")
                if st.session_state.eq_portfolio:
                    for at_idx, pos in enumerate(st.session_state.eq_portfolio):
                        # v6: Enhanced live position card with progress bar
                        st.markdown(live_position_card(pos), unsafe_allow_html=True)
                        # Per-line square off button
                        if st.button(f"✅ Square Off {pos['symbol'].replace('.NS','')} {pos['type']}",
                                     key=f"at_eq_sq_{pos['id']}_{at_idx}", use_container_width=False):
                            lp_sq  = eng.get_live_price(pos["symbol"]) or pos["entry"]
                            ep_sq  = pos["entry"]; qty_sq = pos["qty"]; cost_sq = pos.get("brokerage", 0)
                            gross_sq = (lp_sq - ep_sq) * qty_sq if pos["type"] == "BUY" else (ep_sq - lp_sq) * qty_sq
                            cost_sq2 = eng.equity_cost(lp_sq, qty_sq, pos["type"], _mode == "DELIVERY")
                            net_sq   = gross_sq - cost_sq - cost_sq2
                            st.session_state.eq_history.append({
                                **pos, "exit": lp_sq, "pnl": round(net_sq, 2),
                                "status": "CLOSED",
                                "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            })
                            st.session_state.journal.append({
                                "cat": "EQUITY", "symbol": pos["symbol"],
                                "pnl": round(net_sq, 2), "win": net_sq >= 0,
                                "strength": pos.get("strength", 0),
                                "date": datetime.now().strftime("%Y-%m-%d"),
                                "rec": pos.get("rec", ""),
                            })
                            st.session_state.eq_portfolio = [
                                p for p in st.session_state.eq_portfolio if p["id"] != pos["id"]
                            ]
                            db.save("eq_portfolio", st.session_state.eq_portfolio)
                            db.save("eq_history",   st.session_state.eq_history)
                            db.save("journal",      st.session_state.journal)
                            update_kelly()
                            st.success(f"✅ Squared off {pos['symbol']} @ ₹{lp_sq:.2f} | P&L: ₹{net_sq:+,.0f}")
                            st.rerun()

                stp, _ = st.columns([1, 3])
                with stp:
                    if st.button("🛑 STOP & SQUARE OFF", key="eq_stop", use_container_width=True):
                        for pos in st.session_state.eq_portfolio:
                            lp2  = eng.get_live_price(pos["symbol"]) or pos["entry"]
                            ep3  = pos["entry"]; qty3 = pos["qty"]; cost3 = pos.get("brokerage", 0)
                            gross3 = (lp2 - ep3) * qty3 if pos["type"] == "BUY" else (ep3 - lp2) * qty3
                            cost4  = eng.equity_cost(lp2, qty3, pos["type"], False)
                            net2   = gross3 - cost3 - cost4
                            st.session_state.eq_history.append({
                                **pos,
                                "exit":      lp2,
                                "pnl":       round(net2, 2),
                                "status":    "CLOSED",
                                "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            })
                            st.session_state.journal.append({
                                "cat":      "EQUITY",
                                "symbol":   pos["symbol"],
                                "pnl":      round(net2, 2),
                                "win":      net2 >= 0,
                                "strength": pos.get("strength", 0),
                                "date":     datetime.now().strftime("%Y-%m-%d"),
                                "rec":      pos.get("rec", ""),
                            })
                        st.session_state.eq_portfolio = []
                        st.session_state.auto_eq      = False
                        db.save("auto_eq", False)
                        db.save("eq_portfolio", [])
                        db.save("eq_history",   st.session_state.eq_history)
                        db.save("journal",      st.session_state.journal)
                        update_kelly()
                        st.rerun()

                time.sleep(12)   # v6: 12-second live price refresh cycle
                st.rerun()

    # ── EQ Open Positions ─────────────────────────────────────────────────────
    with eq_tabs[2]:
        st.markdown('<div class="sec-ttl">💼 EQUITY OPEN POSITIONS</div>', unsafe_allow_html=True)
        if not st.session_state.eq_portfolio:
            st.info("No open equity positions.")
        else:
            tot_inv = sum(p.get("invested",   0) for p in st.session_state.eq_portfolio)
            tot_pnl = sum(p.get("pnl",        0) for p in st.session_state.eq_portfolio)
            tot_brk = sum(p.get("brokerage",  0) for p in st.session_state.eq_portfolio)
            pc      = st.columns(4)
            pc[0].markdown(metric_card(f"₹{tot_inv:,.0f}", "Invested",        "var(--accent2)"), unsafe_allow_html=True)
            pc[1].markdown(metric_card(f"₹{tot_pnl:+,.0f}", "Unrealised P&L", "var(--green3)" if tot_pnl >= 0 else "var(--red3)"), unsafe_allow_html=True)
            pc[2].markdown(metric_card(f"{tot_pnl/tot_inv*100:+.1f}%" if tot_inv > 0 else "0%", "Return %", "var(--teal)"), unsafe_allow_html=True)
            pc[3].markdown(metric_card(f"₹{tot_brk:,.0f}", "Charges",         "var(--gold3)"),   unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            for pos in st.session_state.eq_portfolio:
                # FIX: use last known CMP as fallback so price never reverts to entry
                _fetched_eq = eng.get_live_price(pos["symbol"])
                lp = _fetched_eq if (_fetched_eq and _fetched_eq > 0) else pos.get("cmp", pos["entry"])
                pos["cmp"] = lp
                ep2  = pos["entry"]; qty2 = pos["qty"]; cost = pos.get("brokerage", 0)
                gross = (lp - ep2) * qty2 if pos["type"] == "BUY" else (ep2 - lp) * qty2
                pos["pnl"] = round(gross - cost, 2)
                pnl   = pos["pnl"]
                trail = f" | Trail: ₹{pos['trailing_sl']:.2f}" if pos.get("trailing_sl") else ""
                with st.expander(
                    f"{'🟢' if pos['type']=='BUY' else '🔴'} "
                    f"{pos['symbol'].replace('.NS','')} | Entry ₹{ep2:.2f} | "
                    f"CMP ₹{lp:.2f} | {pnl_fmt(pnl)}{trail}"
                ):
                    d1,d2,d3,d4,d5 = st.columns(5)
                    d1.metric("Entry",   f"₹{ep2:.2f}")
                    d2.metric("CMP",     f"₹{lp:.2f}")
                    d3.metric("Target",  f"₹{pos.get('target',0):.2f}")
                    d4.metric("SL",      f"₹{pos.get('sl',0):.2f}")
                    d5.metric("Net P&L", f"₹{pnl:+,.2f}")

                    if pos.get("patterns"):
                        st.markdown(
                            " ".join([
                                f'<span style="background:rgba(245,166,35,0.12);border:1px solid '
                                f'rgba(245,166,35,0.3);color:var(--gold);border-radius:3px;'
                                f'padding:1px 6px;font-size:0.7rem;">{p}</span>'
                                for p in pos["patterns"]
                            ]),
                            unsafe_allow_html=True,
                        )

                    if st.button("✅ Square Off", key=f"eq_sq_{pos['id']}"):
                        cost2  = eng.equity_cost(lp, qty2, pos["type"], pos.get("mode", "INTRADAY") == "DELIVERY")
                        net    = gross - cost - cost2
                        st.session_state.eq_history.append({
                            **pos,
                            "exit":      lp,
                            "pnl":       round(net, 2),
                            "status":    "CLOSED",
                            "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        })
                        st.session_state.journal.append({
                            "cat":      "EQUITY",
                            "symbol":   pos["symbol"],
                            "pnl":      round(net, 2),
                            "win":      net >= 0,
                            "strength": pos.get("strength", 0),
                            "date":     datetime.now().strftime("%Y-%m-%d"),
                            "rec":      pos.get("rec", ""),
                        })
                        st.session_state.eq_portfolio = [
                            p2 for p2 in st.session_state.eq_portfolio if p2["id"] != pos["id"]
                        ]
                        db.save("eq_portfolio", st.session_state.eq_portfolio)
                        db.save("eq_history",   st.session_state.eq_history)
                        db.save("journal",      st.session_state.journal)
                        update_kelly()
                        st.success(f"Squared off ₹{net:+,.2f}")
                        st.rerun()
            db.save("eq_portfolio", st.session_state.eq_portfolio)

    # ── EQ History ────────────────────────────────────────────────────────────
    with eq_tabs[3]:
        st.markdown('<div class="sec-ttl">📜 EQUITY TRADE HISTORY</div>', unsafe_allow_html=True)
        h = st.session_state.eq_history
        if not h:
            st.info("No closed equity trades yet.")
        else:
            wins = len([x for x in h if x.get("pnl", 0) >= 0])
            net  = sum(x.get("pnl", 0) for x in h)
            wr   = wins / len(h) * 100 if h else 0
            hc   = st.columns(5)
            hc[0].metric("Total",    len(h))
            hc[1].metric("Wins",     wins)
            hc[2].metric("Losses",   len(h) - wins)
            hc[3].metric("Win Rate", f"{wr:.1f}%")
            hc[4].metric("Net P&L",  f"₹{net:+,.0f}")
            df_h = pd.DataFrame(h)
            disp = [c for c in ["symbol","type","mode","entry","exit","qty","invested","brokerage","pnl","entry_time","exit_time"] if c in df_h.columns]
            st.dataframe(df_h[disp].rename(columns={"entry":"Entry(₹)","exit":"Exit(₹)","pnl":"Net P&L(₹)"}),
                         use_container_width=True, hide_index=True)
            if len(h) >= 2:
                df_h2 = pd.DataFrame(h)
                df_h2["cum"] = df_h2["pnl"].cumsum()
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    y=df_h2["cum"], mode="lines+markers",
                    line=dict(color="#00e676", width=2),
                    fill="tozeroy", fillcolor="rgba(0,230,118,0.08)",
                    marker=dict(color=["#00e676" if p >= 0 else "#ff1744" for p in df_h2["pnl"]], size=7),
                ))
                fig.update_layout(
                    title="Equity Cumulative P&L",
                    paper_bgcolor="#050A14", plot_bgcolor="#050A14",
                    font=dict(color="#94A3B8"), height=250,
                    margin=dict(l=40, r=20, t=30, b=20),
                )
                st.plotly_chart(fig, use_container_width=True)
            st.download_button(
                "📥 Download CSV",
                data=df_h.to_csv(index=False),
                file_name=f"equity_history_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )
            if st.button("🗑️ Clear Equity History"):
                st.session_state.eq_history = []
                db.save("eq_history", [])
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — OPTIONS
# ══════════════════════════════════════════════════════════════════════════════
with page_tabs[1]:
    st.markdown('<div class="sec-ttl">⚡ OPTIONS TRADING — BANKNIFTY · NIFTY50 · ALL EQUITY CE/PE</div>',
                unsafe_allow_html=True)

    opt_tabs = st.tabs(["📊 Option Chain", "🔍 Scanner", "⚡ Auto Trading", "💼 Open Positions", "📜 History"])

    # ── Option Chain ──────────────────────────────────────────────────────────
    with opt_tabs[0]:
        oc1, oc2, oc3 = st.columns([1, 1, 2])
        with oc1:
            chain_idx = st.radio("Index", ["BANKNIFTY", "NIFTY50"], horizontal=True, key="chain_idx")
        with oc2:
            chain_otype = st.radio("View", ["Full Chain","CE Only","PE Only"], horizontal=True, key="chain_otype")
        with oc3:
            exp_date = exp_bn if chain_idx == "BANKNIFTY" else exp_nf
            spot_val = bn.get("p", 50000) if chain_idx == "BANKNIFTY" else nf.get("p", 22000)
            tick     = 100  if chain_idx == "BANKNIFTY" else 50
            lot_size = 15   if chain_idx == "BANKNIFTY" else 25
            atm      = round(spot_val / tick) * tick
            dte_val  = max(1, (exp_date - datetime.now().date()).days)
            st.markdown(f"""
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding-top:8px;">
                <span class="atm-chip">ATM {atm:,}</span>
                <span class="ce-chip">LOT {lot_size}</span>
                <span class="pe-chip">EXP {exp_date.strftime('%d %b')}</span>
                <span style="font-family:'JetBrains Mono';font-size:0.72rem;color:var(--muted);">
                SPOT ₹{spot_val:,.2f} | {dte_val}D</span>
            </div>""", unsafe_allow_html=True)

        sym_u  = "^NSEBANK" if chain_idx == "BANKNIFTY" else "^NSEI"
        df_u   = eng.get_ohlcv(sym_u, "1mo", "1d")
        ind_u  = eng.compute_indicators(df_u)
        u_rec, u_str, u_bs, u_ss, u_rsn = eng.score_signal(ind_u, {}, df_u, mood_filter, vix_val, "INTRADAY")
        uc = "var(--green3)" if "BUY" in u_rec else ("var(--red3)" if "SELL" in u_rec else "var(--yellow)")
        st.markdown(f"""
        <div style="background:var(--bg2);border:1px solid {uc};border-radius:8px;
        padding:12px 16px;margin:10px 0;display:flex;gap:16px;align-items:center;flex-wrap:wrap;">
            <div>
                <div style="font-family:'Space Grotesk';font-size:1rem;color:{uc};">{chain_idx}: {u_rec}</div>
                <div style="font-size:0.75rem;color:var(--muted);">Strength {u_str}% |
                {'Prefer CE (BUY CALLS)' if 'BUY' in u_rec else
                 ('Prefer PE (BUY PUTS)' if 'SELL' in u_rec else 'Sideways — Straddle/Strangle')}</div>
            </div>
            {''.join([
                f'<span style="background:var(--bg2);border:1px solid var(--border);'
                f'border-radius:4px;padding:2px 8px;font-size:0.65rem;color:var(--tx3);">'
                f'{r[:55]}</span>' for r in u_rsn[:3]
            ])}
        </div>""", unsafe_allow_html=True)

        if ind_u:
            ui1,ui2,ui3,ui4,ui5,ui6 = st.columns(6)
            ui1.metric("RSI",      f"{ind_u.get('rsi',0):.1f}")
            ui2.metric("MACD",     f"{ind_u.get('macd',0):.2f}")
            ui3.metric("ADX",      f"{ind_u.get('adx',0):.1f}")
            ui4.metric("BB%",      f"{ind_u.get('bb_pct',0):.2f}")
            ui5.metric("5D Mom",   f"{ind_u.get('m5',0):+.2f}%")
            ui6.metric("Vol Ratio",f"{ind_u.get('vr',1):.2f}x")

        if st.button(f"🔄 Load {chain_idx} Chain ({dte_val}D to Expiry)", key="load_chain"):
            with st.spinner("Building option chain with Black-Scholes pricing…"):
                chain_data = eng.build_chain(chain_idx, spot_val, exp_date, vix_val, n_strikes)
            st.session_state[f"chain_{chain_idx}"]    = chain_data
            st.session_state[f"chain_ts_{chain_idx}"] = datetime.now().strftime("%H:%M:%S")

        chain_data = st.session_state.get(f"chain_{chain_idx}")
        chain_ts   = st.session_state.get(f"chain_ts_{chain_idx}")

        if chain_data:
            st.markdown(
                f'<div class="info-b" style="font-size:0.72rem;">Chain loaded at {chain_ts} · '
                f'{len(chain_data)} strikes · Black-Scholes + Live VIX ({vix_val:.1f})</div>',
                unsafe_allow_html=True,
            )

            def oc_sig_html(sig):
                s = sig["signal"]
                if "STRONG BUY" in s: return f'<span class="sig sig-sbuy">{s}</span>'
                elif "BUY"      in s: return f'<span class="sig sig-buy">{s}</span>'
                elif "AVOID"    in s: return f'<span class="sig sig-ssell">{s}</span>'
                return                        f'<span class="sig sig-neut">{s}</span>'

            st.markdown("""
            <div class="oc-hdr">
                <div style="color:var(--accent)">CE Signal</div>
                <div style="color:var(--accent2);text-align:center">CE ₹</div>
                <div style="color:var(--accent2);text-align:center">Δ</div>
                <div style="color:var(--accent2);text-align:center">θ</div>
                <div style="color:var(--accent2);text-align:center">CE T1/T2</div>
                <div style="color:var(--gold);text-align:center;font-family:'Space Grotesk';
                font-size:0.75rem;letter-spacing:1px">STRIKE</div>
                <div style="color:var(--red);text-align:center">PE T1/T2</div>
                <div style="color:var(--red);text-align:center">θ</div>
                <div style="color:var(--red);text-align:center">Δ</div>
                <div style="color:var(--red);text-align:center">PE ₹</div>
                <div style="color:var(--red)">PE Signal</div>
            </div>""", unsafe_allow_html=True)

            for row in chain_data:
                atm_cls = "oc-atm" if row["is_atm"] else ("itm-ce" if row["strike"] < atm else "itm-pe")
                atm_tag = " 🎯" if row["is_atm"] else ""
                ce_p    = row["ce_price"]; pe_p = row["pe_price"]
                skip_ce = (chain_otype == "PE Only")
                skip_pe = (chain_otype == "CE Only")

                ce_price_str = f"₹{ce_p:.2f}"                               if not skip_ce else "—"
                pe_price_str = f"₹{pe_p:.2f}"                               if not skip_pe else "—"
                ce_delta_str = f"{row['ce_delta']:.3f}"                      if not skip_ce else "—"
                pe_delta_str = f"{row['pe_delta']:.3f}"                      if not skip_pe else "—"
                ce_theta_str = f"{row['ce_theta']:.2f}"                      if not skip_ce else "—"
                pe_theta_str = f"{row['pe_theta']:.2f}"                      if not skip_pe else "—"
                ce_t12_str   = f"₹{row['ce_t1']:.0f}/₹{row['ce_t2']:.0f}"  if not skip_ce else "—"
                pe_t12_str   = f"₹{row['pe_t1']:.0f}/₹{row['pe_t2']:.0f}"  if not skip_pe else "—"
                ce_sig_str   = oc_sig_html(row['ce_signal'])                 if not skip_ce else "—"
                pe_sig_str   = oc_sig_html(row['pe_signal'])                 if not skip_pe else "—"

                st.markdown(f"""
                <div class="oc-row {atm_cls}">
                    <div>{ce_sig_str}</div>
                    <div style="text-align:center;font-family:'JetBrains Mono';color:var(--accent2);
                    font-weight:700;font-size:0.85rem;">{ce_price_str}</div>
                    <div style="text-align:center;font-family:'JetBrains Mono';font-size:0.72rem;">
                    {ce_delta_str}</div>
                    <div style="text-align:center;font-size:0.72rem;color:var(--red);">{ce_theta_str}</div>
                    <div style="text-align:center;font-size:0.7rem;color:var(--teal);">{ce_t12_str}</div>
                    <div style="text-align:center;font-family:'Space Grotesk';font-size:1rem;
                    color:var(--gold);font-weight:700;">{row['strike']:,}{atm_tag}</div>
                    <div style="text-align:center;font-size:0.7rem;color:var(--teal);">{pe_t12_str}</div>
                    <div style="text-align:center;font-size:0.72rem;color:var(--red);">{pe_theta_str}</div>
                    <div style="text-align:center;font-family:'JetBrains Mono';font-size:0.72rem;">
                    {pe_delta_str}</div>
                    <div style="text-align:center;font-family:'JetBrains Mono';color:var(--red);
                    font-weight:700;font-size:0.85rem;">{pe_price_str}</div>
                    <div>{pe_sig_str}</div>
                </div>""", unsafe_allow_html=True)

            st.markdown("""
            <div style="border:1px solid var(--border);border-top:none;border-radius:0 0 8px 8px;
            padding:6px 12px;background:var(--bg2);font-size:0.65rem;color:var(--muted);">
            Black-Scholes pricing · IV from India VIX · Click strike for deep analysis</div>
            """, unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown('<div class="sec-ttl">🔍 STRIKE DEEP DIVE & EXECUTE</div>', unsafe_allow_html=True)
            strike_list = [
                f"{r['strike']:,} {'🎯ATM' if r['is_atm'] else r['type']}" for r in chain_data
            ]
            sel_lbl = st.selectbox("Select Strike", strike_list, key="dd_strike")
            sel_row = chain_data[[i for i, l in enumerate(strike_list) if l == sel_lbl][0]]

            dd1, dd2 = st.columns(2)
            for col, otype, color in [(dd1,"CE","var(--accent2)"), (dd2,"PE","var(--red3)")]:
                with col:
                    ot  = otype.lower()
                    pr  = sel_row[f"{ot}_price"]
                    sl  = sel_row[f"{ot}_sl"]
                    t1  = sel_row[f"{ot}_t1"]
                    t2  = sel_row[f"{ot}_t2"]
                    t3  = sel_row[f"{ot}_t3"]
                    dlt = sel_row[f"{ot}_delta"]
                    gam = sel_row[f"{ot}_gamma"]
                    tht = sel_row[f"{ot}_theta"]
                    veg = sel_row[f"{ot}_vega"]
                    sig = sel_row[f"{ot}_signal"]

                    st.markdown(f"""
                    <div style="background:var(--card);border:1px solid {color};border-radius:10px;padding:14px;">
                    <div style="font-family:'Space Grotesk';font-size:1.1rem;color:{color};
                    letter-spacing:2px;margin-bottom:8px;">
                        {sel_row['strike']:,} {otype} — ₹{pr:.2f}
                    </div>""", unsafe_allow_html=True)

                    gc = st.columns(4)
                    gc[0].markdown(greek_box(f"{dlt:.4f}", "DELTA",  color),          unsafe_allow_html=True)
                    gc[1].markdown(greek_box(f"{gam:.5f}", "GAMMA",  "var(--p4)"), unsafe_allow_html=True)
                    gc[2].markdown(greek_box(f"{tht:.2f}", "THETA",  "var(--red3)"),    unsafe_allow_html=True)
                    gc[3].markdown(greek_box(f"{veg:.2f}", "VEGA",   "var(--teal)"),   unsafe_allow_html=True)

                    st.markdown("<br>", unsafe_allow_html=True)
                    lc1, lc2 = st.columns(2)
                    lc1.markdown(level_box("ENTRY",  pr, "lvl-e"), unsafe_allow_html=True)
                    lc2.markdown(level_box("SL 50%", sl, "lvl-r"), unsafe_allow_html=True)

                    st.markdown("<br><div style='font-size:0.72rem;color:var(--muted);margin-bottom:4px;'>PROFIT BOOKING PLAN</div>", unsafe_allow_html=True)
                    st.markdown(profit_book_row(30,  t1, "Book 1/3",      (t1 - pr) * lot_size), unsafe_allow_html=True)
                    st.markdown(profit_book_row(60,  t2, "Book 1/3 more", (t2 - pr) * lot_size), unsafe_allow_html=True)
                    st.markdown(profit_book_row(100, t3, "Full exit",      (t3 - pr) * lot_size), unsafe_allow_html=True)

                    st.markdown(
                        f"<div style='margin-top:8px;'>{oc_sig_html(sig)} "
                        f"<span style='font-size:0.72rem;color:var(--muted);'>"
                        f"Strength: {sig['strength']}%</span></div>",
                        unsafe_allow_html=True,
                    )
                    for r in sig["reasons"][:4]:
                        st.markdown(
                            f"<div style='font-size:0.7rem;color:var(--tx3);padding:1px 0;'>• {r}</div>",
                            unsafe_allow_html=True,
                        )
                    st.markdown("</div>", unsafe_allow_html=True)

                    if pr > 0 and "BUY" in sig["signal"]:
                        lots = max(1, int(eng.kelly_size(float(trade_cap), st.session_state.kelly_wr, 1.5, sig["strength"]) / (pr * lot_size))) if use_kelly else 1
                        cost = eng.options_cost(pr, lots, lot_size, "BUY")
                        st.markdown(
                            f'<div class="info-b" style="font-size:0.72rem;">Kelly: {lots} lot(s) | '
                            f'Invested: ₹{pr*lots*lot_size:,.0f} | Charges: ₹{cost:.2f}</div>',
                            unsafe_allow_html=True,
                        )
                        if st.button(f"🚀 BUY {sel_row['strike']} {otype}", key=f"dd_buy_{otype}_{sel_row['strike']}"):
                            trade = {
                                "id":         f"{chain_idx}{sel_row['strike']}{otype}_{int(time.time()*1000)}",
                                "index":      chain_idx,
                                "strike":     sel_row["strike"],
                                "type":       otype,
                                "expiry":     str(exp_date),
                                "entry":      pr, "cmp": pr,
                                "lots":       lots,
                                "lot_size":   lot_size,
                                "invested":   round(pr * lots * lot_size, 2),
                                "brokerage":  cost,
                                "sl":         sl, "t1": t1, "t2": t2, "t3": t3,
                                "trailing_sl": None,
                                "pnl":        0.0, "status": "OPEN",
                                "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "entry_dt":   datetime.now().isoformat(),
                                "signal":     sig["signal"],
                                "strength":   sig["strength"],
                                "delta":      dlt, "theta": tht,
                                "dte":        sel_row["dte"],
                            }
                            st.session_state.opt_portfolio.append(trade)
                            db.save("opt_portfolio", st.session_state.opt_portfolio)
                            st.success(f"✅ {lots} lot(s) {chain_idx} {sel_row['strike']} {otype} @ ₹{pr:.2f}")
        else:
            st.info(f"👆 Click 'Load {chain_idx} Chain' to see full CE/PE chain with signals.")

    # ── Options Scanner ───────────────────────────────────────────────────────
    with opt_tabs[1]:
        st.markdown('<div class="sec-ttl">🔍 OPTIONS SIGNAL SCANNER — ALL STRIKES</div>', unsafe_allow_html=True)

        osc1, osc2, osc3 = st.columns([1, 1, 1])
        with osc1:
            scan_indices = st.multiselect("Scan", ["BANKNIFTY","NIFTY50"],
                                          default=["BANKNIFTY","NIFTY50"], key="opt_scan_idx")
        with osc2:
            opt_type_filter = st.selectbox("Type", ["All","CE Only","PE Only","STRONG Only"], key="opt_type_f")
        with osc3:
            opt_min_str2 = st.slider("Min Strength", 40, 90, 55, 5, key="opt_min_str2")

        if st.button("🔭 SCAN OPTIONS UNIVERSE", use_container_width=True, key="opt_scan_btn"):
            all_sigs = []
            with st.spinner("Scanning BankNifty & Nifty50 option chains…"):
                for idx_name in scan_indices:
                    sp    = bn.get("p", 50000) if idx_name == "BANKNIFTY" else nf.get("p", 22000)
                    exp   = exp_bn if idx_name == "BANKNIFTY" else exp_nf
                    chain_s = eng.build_chain(idx_name, sp, exp, vix_val, n_strikes)
                    for row in chain_s:
                        for ot in ["CE", "PE"]:
                            sig_d  = row[f"{ot.lower()}_signal"]
                            pr_val = row[f"{ot.lower()}_price"]
                            if "BUY" in sig_d["signal"] and sig_d["strength"] >= opt_min_str2 and pr_val > 0:
                                lot = row["lot"]
                                all_sigs.append({
                                    "index":    idx_name,
                                    "strike":   row["strike"],
                                    "type":     ot,
                                    "expiry":   str(exp),
                                    "price":    pr_val,
                                    "sl":       row[f"{ot.lower()}_sl"],
                                    "t1":       row[f"{ot.lower()}_t1"],
                                    "t2":       row[f"{ot.lower()}_t2"],
                                    "t3":       row[f"{ot.lower()}_t3"],
                                    "delta":    row[f"{ot.lower()}_delta"],
                                    "gamma":    row[f"{ot.lower()}_gamma"],
                                    "theta":    row[f"{ot.lower()}_theta"],
                                    "vega":     row[f"{ot.lower()}_vega"],
                                    "iv":       row["iv"],
                                    "lot":      lot,
                                    "dte":      row["dte"],
                                    "signal":   sig_d["signal"],
                                    "strength": sig_d["strength"],
                                    "score":    sig_d["score"],
                                    "reasons":  sig_d["reasons"],
                                    "is_atm":   row["is_atm"],
                                })
            all_sigs.sort(key=lambda x: -x["strength"])
            st.session_state["scan_opt"] = all_sigs
            db.save("scan_opt", all_sigs)

        scan_opt = st.session_state.get("scan_opt", [])
        if opt_type_filter == "CE Only":
            scan_opt = [s for s in scan_opt if s["type"] == "CE"]
        elif opt_type_filter == "PE Only":
            scan_opt = [s for s in scan_opt if s["type"] == "PE"]
        elif opt_type_filter == "STRONG Only":
            scan_opt = [s for s in scan_opt if "STRONG" in s["signal"]]

        if scan_opt:
            sm   = st.columns(5)
            ce_c = [s for s in scan_opt if s["type"] == "CE"]
            pe_c = [s for s in scan_opt if s["type"] == "PE"]
            sm[0].markdown(metric_card(len(scan_opt), "Total",        "var(--accent2)"), unsafe_allow_html=True)
            sm[1].markdown(metric_card(len(ce_c),     "CE Signals",   "var(--accent2)"), unsafe_allow_html=True)
            sm[2].markdown(metric_card(len(pe_c),     "PE Signals",   "var(--red3)"),    unsafe_allow_html=True)
            sm[3].markdown(metric_card(len([s for s in scan_opt if "STRONG" in s["signal"]]), "Strong Buys", "var(--green3)"), unsafe_allow_html=True)
            avg_ss = int(np.mean([s["strength"] for s in scan_opt])) if scan_opt else 0
            sm[4].markdown(metric_card(f"{avg_ss}%",  "Avg Strength", "var(--gold3)"),   unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            tbl_opt = []
            for s in scan_opt:
                tbl_opt.append({
                    "Index":  s["index"], "Strike": s["strike"], "Type": s["type"],
                    "Signal": s["signal"], "Str%":  s["strength"],
                    "Premium": f"₹{s['price']:.2f}", "SL": f"₹{s['sl']:.2f}",
                    "T1": f"₹{s['t1']:.2f}", "T2": f"₹{s['t2']:.2f}",
                    "Delta": f"{s['delta']:.3f}", "Theta": f"{s['theta']:.2f}",
                    "IV%": f"{s['iv']}%", "DTE": s["dte"],
                    "ATM": "🎯" if s.get("is_atm") else "",
                })
            st.dataframe(pd.DataFrame(tbl_opt), use_container_width=True, hide_index=True)

            st.markdown("#### 🎯 Signal Cards")
            for sig in scan_opt[:30]:
                oc = "var(--accent)" if sig["type"] == "CE" else "var(--red)"
                atm_tag = " 🎯ATM" if sig.get("is_atm") else ""
                lot = sig["lot"]
                with st.expander(
                    f"{'🔵' if sig['type']=='CE' else '🔴'} "
                    f"{sig['index']} {sig['strike']:,} {sig['type']}{atm_tag} | "
                    f"₹{sig['price']:.2f} | {sig['signal']} {sig['strength']}% | Δ={sig['delta']:.3f}"
                ):
                    sc1,sc2,sc3,sc4 = st.columns(4)
                    sc1.metric("Premium", f"₹{sig['price']:.2f}")
                    sc2.metric("SL",      f"₹{sig['sl']:.2f}")
                    sc3.metric("T1",      f"₹{sig['t1']:.2f}")
                    sc4.metric("T2",      f"₹{sig['t2']:.2f}")

                    gc = st.columns(4)
                    gc[0].markdown(greek_box(f"{sig['delta']:.4f}", "DELTA",  oc),             unsafe_allow_html=True)
                    gc[1].markdown(greek_box(f"{sig['gamma']:.5f}", "GAMMA",  "var(--p4)"), unsafe_allow_html=True)
                    gc[2].markdown(greek_box(f"{sig['theta']:.2f}", "THETA",  "var(--red3)"),    unsafe_allow_html=True)
                    gc[3].markdown(greek_box(f"{sig['vega']:.2f}",  "VEGA",   "var(--teal)"),   unsafe_allow_html=True)

                    st.markdown("**Profit Booking Plan**")
                    st.markdown(profit_book_row(30,  sig["t1"], "Book 1/3",      (sig["t1"] - sig["price"]) * lot), unsafe_allow_html=True)
                    st.markdown(profit_book_row(60,  sig["t2"], "Book 1/3 more", (sig["t2"] - sig["price"]) * lot), unsafe_allow_html=True)
                    st.markdown(profit_book_row(100, sig["t3"], "Full exit",      (sig["t3"] - sig["price"]) * lot), unsafe_allow_html=True)

                    st.markdown("**Reasoning**")
                    for r in sig["reasons"][:5]:
                        st.markdown(f"<div style='font-size:0.75rem;color:var(--tx3);'>• {r}</div>", unsafe_allow_html=True)

                    lots2 = max(1, int(eng.kelly_size(float(trade_cap), st.session_state.kelly_wr, 1.5, sig["strength"]) / (sig["price"] * lot))) if use_kelly else 1
                    cost2 = eng.options_cost(sig["price"], lots2, lot, "BUY")
                    st.markdown(
                        f'<div class="info-b" style="font-size:0.72rem;">Kelly: {lots2} lot(s) | '
                        f'₹{sig["price"]*lots2*lot:,.0f} invested | Charges: ₹{cost2:.2f}</div>',
                        unsafe_allow_html=True,
                    )
                    if st.button(f"🚀 BUY {sig['index']} {sig['strike']} {sig['type']}",
                                 key=f"scan_opt_buy_{sig['index']}_{sig['strike']}_{sig['type']}"):
                        trade = {
                            "id":         f"{sig['index']}{sig['strike']}{sig['type']}_{int(time.time()*1000)}",
                            "index":      sig["index"], "strike": sig["strike"], "type": sig["type"],
                            "expiry":     sig["expiry"], "entry": sig["price"], "cmp": sig["price"],
                            "lots":       lots2, "lot_size": lot,
                            "invested":   round(sig["price"] * lots2 * lot, 2),
                            "brokerage":  cost2,
                            "sl":         sig["sl"], "t1": sig["t1"], "t2": sig["t2"], "t3": sig["t3"],
                            "trailing_sl": None, "pnl": 0.0, "status": "OPEN",
                            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "entry_dt":   datetime.now().isoformat(),
                            "signal":     sig["signal"], "strength": sig["strength"],
                            "delta":      sig["delta"], "theta": sig["theta"], "dte": sig["dte"],
                        }
                        st.session_state.opt_portfolio.append(trade)
                        db.save("opt_portfolio", st.session_state.opt_portfolio)
                        st.success(f"✅ Bought {lots2} lot(s) @ ₹{sig['price']:.2f}")
        else:
            st.info("👆 Click Scan to find best CE/PE signals.")

    # ── Options Auto Trading ──────────────────────────────────────────────────
    with opt_tabs[2]:
        st.markdown('<div class="sec-ttl">⚡ OPTIONS AUTO TRADING ENGINE</div>', unsafe_allow_html=True)

        if vix_val > 28:
            st.markdown(
                '<div class="warn-b">🚨 VIX > 28 — Options auto trading BLOCKED. Too dangerous for option buying.</div>',
                unsafe_allow_html=True,
            )

        if not st.session_state.auto_opt:
            st.markdown("""
            <div style="background:var(--bg2);border:1px solid var(--accent);
            border-radius:10px;padding:18px;text-align:center;margin-bottom:14px;">
                <div style="font-family:'Space Grotesk';font-size:1.2rem;color:var(--accent2);
                letter-spacing:3px;">AI OPTIONS AUTO TRADER</div>
                <div style="color:var(--tx3);font-size:0.8rem;margin-top:6px;">
                    Scans BankNifty + Nifty50 · All Strikes · Delta-filtered ·
                    Staged Profit Booking · Auto Trailing SL
                </div>
            </div>""", unsafe_allow_html=True)

            _, oa2, _ = st.columns([1, 2, 1])
            with oa2:
                oa_dur = st.number_input("Duration (minutes)", 1, 390, 30, 5, key="oa_dur")
                oa_max = st.number_input("Max simultaneous positions", 1, 10, 3, 1, key="oa_max")
                oa_idx = st.multiselect("Trade Indices", ["BANKNIFTY","NIFTY50"],
                                        default=["BANKNIFTY","NIFTY50"], key="oa_idx")
                oa_str = st.number_input("Min signal strength", 50, 95, 60, 5, key="oa_str")
                bias_map = {"BULLISH": "CE", "BEARISH": "PE", "NEUTRAL": "Both"}
                st.markdown(
                    f'<div class="info-b">Market Bias: <b>{mood}</b> → Prefer '
                    f'<b>{bias_map.get(mood,"Both")}</b> | VIX: {vix_val:.1f}</div>',
                    unsafe_allow_html=True,
                )
                if vix_val <= 28:
                    if st.button("🚀 START OPTIONS AUTO TRADING", use_container_width=True, key="oa_start"):
                        st.session_state.auto_opt     = True
                        st.session_state.auto_opt_end = (
                            datetime.now() + timedelta(minutes=int(oa_dur))
                        ).isoformat()
                        st.session_state["oa_max2"]    = int(oa_max)
                        st.session_state["oa_idx2"]    = oa_idx
                        st.session_state["oa_str2"]    = int(oa_str)
                        st.session_state["oa_total_s"] = float(oa_dur) * 60.0
                        st.session_state["oa_start_ts"] = time.time()
                        db.save("auto_opt",     True)
                        db.save("auto_opt_end", st.session_state.auto_opt_end)
                        st.rerun()
                else:
                    st.error("🚫 VIX too high — blocked.")
        else:
            end_dt  = datetime.fromisoformat(st.session_state.auto_opt_end)
            rem      = max(0.0, (end_dt - datetime.now()).total_seconds())
            oa_dur_s = float(st.session_state.get("oa_dur", 30)) * 60.0
            tot_s    = max(oa_dur_s, 1.0)
            prog     = max(0.0, min(1.0, 1.0 - rem / tot_s))

            oc1, oc2, oc3, oc4 = st.columns(4)
            oc1.metric("Time Left", f"{int(rem//60)}m {int(rem%60)}s")
            oc2.metric("Open Pos",  len(st.session_state.opt_portfolio))
            op_pnl = sum(p.get("pnl", 0) for p in st.session_state.opt_portfolio)
            oc3.metric("Live P&L",  f"₹{op_pnl:+,.0f}")
            oc4.metric("Realized",  f"₹{sum(p.get('pnl',0) for p in st.session_state.opt_history):+,.0f}")
            # Use stored duration (in seconds) so progress survives tab switches
            _oa_total_s = float(st.session_state.get("oa_total_s", 1800))
            _oa_start_ts = float(st.session_state.get("oa_start_ts", time.time()))
            elapsed = time.time() - _oa_start_ts
            prog = max(0.0, min(1.0, elapsed / max(_oa_total_s, 1)))
            st.progress(prog)

            if rem <= 0:
                for pos in st.session_state.opt_portfolio:
                    ep2  = pos["entry"]
                    lots = pos["lots"];  ls   = pos["lot_size"]
                    # Fetch fresh price even at session end
                    live_end = get_live_option_cmp(
                        pos["index"], pos["strike"], pos["type"],
                        pos.get("expiry", str(exp_bn)), vix_val
                    )
                    cmp2 = live_end if (live_end is not None and live_end > 0) else pos.get("cmp", ep2)
                    gross = (cmp2 - ep2) * lots * ls
                    net   = gross - pos.get("brokerage", 0)
                    st.session_state.opt_history.append({
                        **pos,
                        "exit":      cmp2,
                        "pnl":       round(net, 2),
                        "status":    "CLOSED",
                        "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    st.session_state.journal.append({
                        "cat":      "OPTIONS",
                        "symbol":   f"{pos['index']}{pos['strike']}{pos['type']}",
                        "pnl":      round(net, 2),
                        "win":      net >= 0,
                        "strength": pos.get("strength", 0),
                        "date":     datetime.now().strftime("%Y-%m-%d"),
                        "rec":      pos.get("signal", ""),
                    })
                st.session_state.opt_portfolio = []
                st.session_state.auto_opt      = False
                db.save("auto_opt",    False)
                db.save("opt_portfolio", [])
                db.save("opt_history",  st.session_state.opt_history)
                db.save("journal",      st.session_state.journal)
                update_kelly()
                st.rerun()
            else:
                _max  = st.session_state.get("oa_max2", 3)
                _idxs = st.session_state.get("oa_idx2", ["BANKNIFTY"])
                _str  = st.session_state.get("oa_str2", 60)

                if len(st.session_state.opt_portfolio) < _max and vix_val <= 28:
                    with st.spinner("Scanning options chains…"):
                        new_sigs = []
                        for iname in _idxs:
                            sp2 = bn.get("p", 50000) if iname == "BANKNIFTY" else nf.get("p", 22000)
                            ex  = exp_bn if iname == "BANKNIFTY" else exp_nf
                            ch  = eng.build_chain(iname, sp2, ex, vix_val, 8)
                            for row in ch:
                                for ot in ["CE", "PE"]:
                                    if mood == "BULLISH" and ot == "PE": continue
                                    if mood == "BEARISH" and ot == "CE": continue
                                    sg   = row[f"{ot.lower()}_signal"]
                                    pr2  = row[f"{ot.lower()}_price"]
                                    if "BUY" in sg["signal"] and sg["strength"] >= _str and pr2 > 0:
                                        new_sigs.append({
                                            "index":    iname, "strike": row["strike"], "type": ot,
                                            "expiry":   str(ex), "price": pr2,
                                            "sl":       row[f"{ot.lower()}_sl"],
                                            "t1":       row[f"{ot.lower()}_t1"],
                                            "t2":       row[f"{ot.lower()}_t2"],
                                            "t3":       row[f"{ot.lower()}_t3"],
                                            "delta":    row[f"{ot.lower()}_delta"],
                                            "theta":    row[f"{ot.lower()}_theta"],
                                            "lot":      row["lot"], "dte": row["dte"],
                                            "signal":   sg["signal"], "strength": sg["strength"],
                                        })
                    new_sigs.sort(key=lambda x: -x["strength"])
                    existing = {f"{p['index']}{p['strike']}{p['type']}" for p in st.session_state.opt_portfolio}
                    for sig in new_sigs:
                        if len(st.session_state.opt_portfolio) >= _max: break
                        k = f"{sig['index']}{sig['strike']}{sig['type']}"
                        if k in existing: continue
                        lot  = sig["lot"]; pr2 = sig["price"]
                        # Minimum ₹1 lakh per trade: lots = ceil(100000 / (pr2 * lot))
                        min_lots = max(1, math.ceil(100000 / max(pr2 * lot, 1)))
                        kelly_lots = max(1, int(eng.kelly_size(float(trade_cap), st.session_state.kelly_wr, 1.5, sig["strength"]) / max(pr2 * lot, 1))) if use_kelly else 1
                        lots2 = max(min_lots, kelly_lots)
                        cost2 = eng.options_cost(pr2, lots2, lot, "BUY")
                        trade = {
                            "id":         f"{sig['index']}{sig['strike']}{sig['type']}_{int(time.time()*1000)}",
                            "index":      sig["index"], "strike": sig["strike"], "type": sig["type"],
                            "expiry":     sig["expiry"], "entry": pr2, "cmp": pr2,
                            "lots":       lots2, "lot_size": lot,
                            "invested":   round(pr2 * lots2 * lot, 2),
                            "brokerage":  cost2,
                            "sl":         sig["sl"], "t1": sig["t1"], "t2": sig["t2"], "t3": sig["t3"],
                            "trailing_sl": None, "pnl": 0.0, "status": "OPEN",
                            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "entry_dt":   datetime.now().isoformat(),
                            "signal":     sig["signal"], "strength": sig["strength"],
                            "delta":      sig["delta"], "theta": sig["theta"], "dte": sig["dte"],
                        }
                        st.session_state.opt_portfolio.append(trade)
                        existing.add(k)
                        st.success(f"✅ AUTO: Bought {lots2}L {sig['index']} {sig['strike']} {sig['type']} @ ₹{pr2:.2f} | Invested: ₹{pr2*lots2*lot:,.0f}")
                    # Persist new entries immediately
                    db.save("opt_portfolio", st.session_state.opt_portfolio)

                # ── Force-evict spot & option caches before CMP refresh ──────
                # This ensures _get_fresh_index_spot() fires a real HTTP call
                # every cycle instead of returning the same cached spot that
                # was used when the position was opened (which would produce
                # an identical BS price and make CMP look frozen at entry).
                eng.force_refresh_index_spots()

                still = []
                for pos in st.session_state.opt_portfolio:
                    ep2  = pos["entry"]; lots = pos["lots"]; ls = pos["lot_size"]
                    # ── LIVE CMP: evict per-position option cache then refetch ─
                    eng._opt_price_cache.pop(
                        f"opt_{pos['index']}_{pos['strike']}_{pos['type']}_{pos.get('expiry', str(exp_bn))}",
                        None
                    )
                    live_p = get_live_option_cmp(
                        pos["index"], pos["strike"], pos["type"],
                        pos.get("expiry", str(exp_bn)), vix_val
                    )
                    cmp2 = live_p if (live_p is not None and live_p > 0) else pos.get("cmp", ep2)
                    pos["cmp"] = cmp2                      # persist for display
                    # ────────────────────────────────────────────────────────
                    gross = (cmp2 - ep2) * lots * ls
                    pos["pnl"] = round(gross - pos.get("brokerage", 0), 2)
                    pnl_pct = (cmp2 - ep2) / ep2 * 100 if ep2 > 0 else 0
                    if use_trail and pnl_pct >= 40:
                        if pos.get("trailing_sl") is None: pos["trailing_sl"] = ep2
                        else:
                            new_t = cmp2 * 0.92
                            if new_t > pos["trailing_sl"]: pos["trailing_sl"] = round(new_t, 2)
                    eff_sl = pos.get("trailing_sl") or pos.get("sl", ep2 * 0.5)
                    hit    = (cmp2 <= eff_sl or cmp2 >= pos.get("t3", ep2 * 3))
                    if use_time_x:
                        try:
                            ed2 = datetime.fromisoformat(pos.get("entry_dt", datetime.now().isoformat()))
                            if (datetime.now() - ed2).total_seconds() > 1800 and abs(pnl_pct) < 10:
                                hit = True
                        except Exception:
                            pass
                    if hit:
                        cost3  = eng.options_cost(cmp2, lots, ls, "SELL")
                        gross2 = (cmp2 - ep2) * lots * ls
                        net    = gross2 - pos.get("brokerage", 0) - cost3
                        st.session_state.opt_history.append({
                            **pos,
                            "exit":      cmp2,
                            "pnl":       round(net, 2),
                            "status":    "CLOSED",
                            "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        })
                        st.session_state.journal.append({
                            "cat":      "OPTIONS",
                            "symbol":   f"{pos['index']}{pos['strike']}{pos['type']}",
                            "pnl":      round(net, 2),
                            "win":      net >= 0,
                            "strength": pos.get("strength", 0),
                            "date":     datetime.now().strftime("%Y-%m-%d"),
                            "rec":      pos.get("signal", ""),
                        })
                    else:
                        still.append(pos)
                st.session_state.opt_portfolio = still
                db.save("opt_portfolio", still)
                db.save("opt_history",   st.session_state.opt_history)
                db.save("journal",       st.session_state.journal)
                update_kelly()

                # Auto-refresh every 12s while trading is active
                st.caption(f"🔄 Live 12s refresh | {datetime.now().strftime('%H:%M:%S')} | Positions: {len(st.session_state.opt_portfolio)}")
                st.markdown("### Live Options Positions")
                if st.session_state.opt_portfolio:
                    for at_oi, pos in enumerate(st.session_state.opt_portfolio):
                        # v6: enhanced live option card
                        opt_entry  = pos.get("entry", 0)
                        opt_cmp    = pos.get("cmp", opt_entry)
                        opt_pnl    = pos.get("pnl", 0)
                        opt_t3     = pos.get("t3", opt_entry * 2)
                        opt_sl     = pos.get("trailing_sl") or pos.get("sl", opt_entry * 0.5)
                        opt_range  = abs(opt_t3 - opt_entry)
                        opt_done   = max(0, opt_cmp - opt_entry)
                        opt_prog   = min(100, (opt_done / opt_range * 100)) if opt_range > 0 else 0
                        opt_card_cls = "profit" if opt_pnl >= 0 else "loss"
                        opt_col    = "var(--accent2)" if pos["type"] == "CE" else "var(--red3)"
                        trail2     = f" | Trail SL: ₹{pos['trailing_sl']:.2f}" if pos.get("trailing_sl") else ""
                        pct_move   = (opt_cmp - opt_entry) / opt_entry * 100 if opt_entry > 0 else 0
                        st.markdown(f"""
                        <div class="live-pos-card {opt_card_cls}">
                          <div class="live-badge"><div class="live-dot"></div>LIVE</div>
                          <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;align-items:flex-start;">
                            <div>
                              <div class="tc-head" style="color:{opt_col};">{pos['index']} {pos['strike']:,} {pos['type']}</div>
                              <div class="tc-meta">Entry ₹{opt_entry:.2f} → CMP ₹{opt_cmp:.2f} | {pos['lots']}L × {pos['lot_size']} | Δ {pos.get('delta',0):.3f}{trail2}</div>
                              <div class="tc-meta" style="margin-top:3px;">Move: <span style="color:{'var(--green3)' if pct_move>=0 else 'var(--red3)'};">{pct_move:+.1f}%</span>
                              | DTE: {pos.get('dte',0)}</div>
                            </div>
                            <div style="text-align:right;">
                              <div class="{'pnl-pos' if opt_pnl>=0 else 'pnl-neg'}" style="font-size:1.1rem;">{'▲' if opt_pnl>=0 else '▼'} ₹{abs(opt_pnl):,.2f}</div>
                              <div style="font-size:.62rem;color:var(--muted);margin-top:2px;">Net P&amp;L</div>
                            </div>
                          </div>
                          <div class="tc-progress-wrap" style="margin-top:10px;">
                            <div class="tc-progress-fill {'tc-progress-green' if opt_pnl>=0 else 'tc-progress-red'}" style="width:{opt_prog:.1f}%;"></div>
                          </div>
                          <div style="display:flex;justify-content:space-between;font-size:.58rem;color:var(--muted);margin-top:3px;">
                            <span>Entry ₹{opt_entry:.0f}</span>
                            <span style="color:{'var(--green3)' if opt_prog>50 else 'var(--gold3)'};">{opt_prog:.0f}% to T3</span>
                            <span>T3 ₹{opt_t3:.0f}</span>
                          </div>
                        </div>""", unsafe_allow_html=True)
                        # Per-line square off button
                        if st.button(f"✅ Square Off {pos['index']} {pos['strike']:,} {pos['type']}",
                                     key=f"at_opt_sq_{pos['id']}_{at_oi}"):
                            ep_osq  = pos["entry"]; lots_osq = pos["lots"]; ls_osq = pos["lot_size"]
                            live_osq = get_live_option_cmp(
                                pos["index"], pos["strike"], pos["type"],
                                pos.get("expiry", str(exp_bn)), vix_val
                            )
                            cmp_osq = live_osq if (live_osq is not None and live_osq > 0) else pos.get("cmp", ep_osq)
                            gross_osq = (cmp_osq - ep_osq) * lots_osq * ls_osq
                            net_osq   = gross_osq - pos.get("brokerage", 0) - eng.options_cost(cmp_osq, lots_osq, ls_osq, "SELL")
                            st.session_state.opt_history.append({
                                **pos, "exit": cmp_osq, "pnl": round(net_osq, 2),
                                "status": "CLOSED",
                                "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            })
                            st.session_state.journal.append({
                                "cat": "OPTIONS",
                                "symbol": f"{pos['index']}{pos['strike']}{pos['type']}",
                                "pnl": round(net_osq, 2), "win": net_osq >= 0,
                                "strength": pos.get("strength", 0),
                                "date": datetime.now().strftime("%Y-%m-%d"),
                                "rec": pos.get("signal", ""),
                            })
                            st.session_state.opt_portfolio = [
                                p for p in st.session_state.opt_portfolio if p["id"] != pos["id"]
                            ]
                            db.save("opt_portfolio", st.session_state.opt_portfolio)
                            db.save("opt_history",   st.session_state.opt_history)
                            db.save("journal",       st.session_state.journal)
                            update_kelly()
                            st.success(f"✅ Squared off {pos['index']} {pos['strike']} {pos['type']} @ ₹{cmp_osq:.2f} | P&L: ₹{net_osq:+,.0f}")
                            st.rerun()
                else:
                    st.info("No open options positions. Scanning next cycle…")

                stp2, _ = st.columns([1, 3])
                with stp2:
                    if st.button("🛑 STOP OPTIONS AUTO TRADING", key="opt_stop", use_container_width=True):
                        for pos in st.session_state.opt_portfolio:
                            ep2  = pos["entry"]
                            lots = pos["lots"];  ls   = pos["lot_size"]
                            live_stop = get_live_option_cmp(
                                pos["index"], pos["strike"], pos["type"],
                                pos.get("expiry", str(exp_bn)), vix_val
                            )
                            cmp2 = live_stop if (live_stop is not None and live_stop > 0) else pos.get("cmp", ep2)
                            gross = (cmp2 - ep2) * lots * ls
                            net   = gross - pos.get("brokerage", 0) - eng.options_cost(cmp2, lots, ls, "SELL")
                            st.session_state.opt_history.append({
                                **pos,
                                "exit":      cmp2,
                                "pnl":       round(net, 2),
                                "status":    "CLOSED",
                                "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            })
                            st.session_state.journal.append({
                                "cat":      "OPTIONS",
                                "symbol":   f"{pos['index']}{pos['strike']}{pos['type']}",
                                "pnl":      round(net, 2),
                                "win":      net >= 0,
                                "strength": pos.get("strength", 0),
                                "date":     datetime.now().strftime("%Y-%m-%d"),
                                "rec":      pos.get("signal", ""),
                            })
                        st.session_state.opt_portfolio = []
                        st.session_state.auto_opt      = False
                        db.save("auto_opt",    False)
                        db.save("opt_portfolio", [])
                        db.save("opt_history",  st.session_state.opt_history)
                        db.save("journal",      st.session_state.journal)
                        update_kelly()
                        st.rerun()
                time.sleep(12)   # v6: 12-second live refresh
                st.rerun()

    # ── Options Open Positions ────────────────────────────────────────────────
    with opt_tabs[3]:
        st.markdown('<div class="sec-ttl">💼 OPTIONS OPEN POSITIONS</div>', unsafe_allow_html=True)
        if not st.session_state.opt_portfolio:
            st.info("No open options positions.")
        else:
            tot_inv2 = sum(p.get("invested", 0) for p in st.session_state.opt_portfolio)
            tot_pnl2 = sum(p.get("pnl",      0) for p in st.session_state.opt_portfolio)
            pc2      = st.columns(3)
            pc2[0].markdown(metric_card(f"₹{tot_inv2:,.0f}",   "Invested",       "var(--accent2)"), unsafe_allow_html=True)
            pc2[1].markdown(metric_card(f"₹{tot_pnl2:+,.0f}",  "Unrealised P&L", "var(--green3)" if tot_pnl2 >= 0 else "var(--red3)"), unsafe_allow_html=True)
            pc2[2].markdown(metric_card(f"{tot_pnl2/tot_inv2*100:+.1f}%" if tot_inv2 > 0 else "0%", "Return%", "var(--teal)"), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            # ── Force fresh spot fetch for positions display ─────────────────
            eng.force_refresh_index_spots()

            for pos in st.session_state.opt_portfolio:
                oc3   = "var(--accent)" if pos["type"] == "CE" else "var(--red)"
                ep2   = pos["entry"]; lots = pos["lots"]; ls = pos["lot_size"]
                # ── LIVE CMP: evict stale option cache then refresh ───────────
                eng._opt_price_cache.pop(
                    f"opt_{pos['index']}_{pos['strike']}_{pos['type']}_{pos.get('expiry', str(exp_bn))}",
                    None
                )
                live_p2 = get_live_option_cmp(
                    pos["index"], pos["strike"], pos["type"],
                    pos.get("expiry", str(exp_bn)), vix_val
                )
                if live_p2 is not None and live_p2 > 0:
                    pos["cmp"] = live_p2
                    pos["pnl"] = round((live_p2 - ep2) * lots * ls - pos.get("brokerage", 0), 2)
                pn = pos.get("pnl", 0)
                trail3 = f" | Trail SL: ₹{pos['trailing_sl']:.2f}" if pos.get("trailing_sl") else ""
                with st.expander(
                    f"{'🔵' if pos['type']=='CE' else '🔴'} "
                    f"{pos['index']} {pos['strike']:,} {pos['type']} | "
                    f"Entry ₹{ep2:.2f} | CMP ₹{pos.get('cmp',ep2):.2f} | {pos['lots']}L | {pnl_fmt(pn)}{trail3}"
                ):
                    p1,p2,p3,p4,p5 = st.columns(5)
                    p1.metric("Entry",   f"₹{ep2:.2f}")
                    p2.metric("CMP",     f"₹{pos.get('cmp',ep2):.2f}")
                    p3.metric("SL",      f"₹{pos.get('sl',0):.2f}")
                    p4.metric("T1",      f"₹{pos.get('t1',0):.2f}")
                    p5.metric("Net P&L", f"₹{pn:+,.0f}")

                    st.markdown("**Profit Booking Targets**")
                    st.markdown(profit_book_row(30,  pos.get("t1",0), "Book 1/3",      (pos.get("t1",0)-ep2)*ls*lots), unsafe_allow_html=True)
                    st.markdown(profit_book_row(60,  pos.get("t2",0), "Book 1/3 more", (pos.get("t2",0)-ep2)*ls*lots), unsafe_allow_html=True)
                    st.markdown(profit_book_row(100, pos.get("t3",0), "Full exit",      (pos.get("t3",0)-ep2)*ls*lots), unsafe_allow_html=True)

                    if st.button("✅ Square Off", key=f"opt_sq_{pos['id']}"):
                        cmp3  = pos.get("cmp", ep2)
                        gross3 = (cmp3 - ep2) * lots * ls
                        cost4  = eng.options_cost(cmp3, lots, ls, "SELL")
                        net2   = gross3 - pos.get("brokerage", 0) - cost4
                        st.session_state.opt_history.append({
                            **pos,
                            "exit":      cmp3,
                            "pnl":       round(net2, 2),
                            "status":    "CLOSED",
                            "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        })
                        st.session_state.journal.append({
                            "cat":      "OPTIONS",
                            "symbol":   f"{pos['index']}{pos['strike']}{pos['type']}",
                            "pnl":      round(net2, 2),
                            "win":      net2 >= 0,
                            "strength": pos.get("strength", 0),
                            "date":     datetime.now().strftime("%Y-%m-%d"),
                            "rec":      pos.get("signal", ""),
                        })
                        st.session_state.opt_portfolio = [
                            p2b for p2b in st.session_state.opt_portfolio if p2b["id"] != pos["id"]
                        ]
                        db.save("opt_portfolio", st.session_state.opt_portfolio)
                        db.save("opt_history",   st.session_state.opt_history)
                        db.save("journal",       st.session_state.journal)
                        update_kelly()
                        st.success(f"Squared off ₹{net2:+,.0f}")
                        st.rerun()

    # ── Options History ───────────────────────────────────────────────────────
    with opt_tabs[4]:
        st.markdown('<div class="sec-ttl">📜 OPTIONS TRADE HISTORY</div>', unsafe_allow_html=True)
        oh = st.session_state.opt_history
        if not oh:
            st.info("No closed options trades yet.")
        else:
            ow  = len([x for x in oh if x.get("pnl", 0) >= 0])
            on  = sum(x.get("pnl", 0) for x in oh)
            owr = ow / len(oh) * 100
            hc2 = st.columns(4)
            hc2[0].metric("Total",    len(oh))
            hc2[1].metric("Wins",     ow)
            hc2[2].metric("Win Rate", f"{owr:.1f}%")
            hc2[3].metric("Net P&L",  f"₹{on:+,.0f}")
            df_oh  = pd.DataFrame(oh)
            dcols  = [c for c in ["index","strike","type","entry","exit","lots","invested","brokerage","pnl","signal","entry_time","exit_time"] if c in df_oh.columns]
            st.dataframe(df_oh[dcols].rename(columns={"entry":"Entry(₹)","exit":"Exit(₹)","pnl":"Net P&L(₹)"}),
                         use_container_width=True, hide_index=True)
            if len(oh) >= 2:
                df_oh2       = pd.DataFrame(oh)
                df_oh2["cum"] = df_oh2["pnl"].cumsum()
                fig2 = go.Figure()
                fig2.add_trace(go.Scatter(
                    y=df_oh2["cum"], mode="lines+markers",
                    line=dict(color="#00e5ff", width=2),
                    fill="tozeroy", fillcolor="rgba(0,229,255,0.06)",
                    marker=dict(color=["#00e676" if p >= 0 else "#ff1744" for p in df_oh2["pnl"]], size=7),
                ))
                fig2.update_layout(
                    title="Options Cumulative P&L",
                    paper_bgcolor="#050A14", plot_bgcolor="#050A14",
                    font=dict(color="#94A3B8"), height=250,
                    margin=dict(l=40, r=20, t=30, b=20),
                )
                st.plotly_chart(fig2, use_container_width=True)
            st.download_button(
                "📥 Download Options CSV",
                data=df_oh.to_csv(index=False),
                file_name=f"options_history_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )
            if st.button("🗑️ Clear Options History", key="clr_opt_hist"):
                st.session_state.opt_history = []
                db.save("opt_history", [])
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — FUTURES
# ══════════════════════════════════════════════════════════════════════════════
with page_tabs[2]:
    st.markdown('<div class="sec-ttl">🔮 FUTURES TRADING — INDEX + EQUITY FUTURES</div>', unsafe_allow_html=True)

    fut_tabs = st.tabs(["🔍 Scanner","⚡ Auto Trading","💼 Open Positions","📜 History"])

    # ── Futures Scanner ───────────────────────────────────────────────────────
    with fut_tabs[0]:
        fc1, fc2, fc3 = st.columns([1, 1, 1])
        with fc1:
            _ft = max(1, len(eng.FUTURES_SYMBOLS))
            _fm = min(30, _ft)
            _fd = _ft
            _fs = max(1, _ft // 10)
            fut_scan_n = st.number_input(
                "Stocks to scan", _fm, _ft, _fd, _fs, key="fut_scan_n"
            )
        with fc2:
            fut_filter = st.selectbox("Show", ["All","LONG Only","SHORT Only","STRONG Only"], key="fut_filter")
        with fc3:
            fut_min_str = st.slider("Min Strength", 45, 90, 58, 2, key="fut_min_str")

        fut_syms = [s for s in eng.FUTURES_SYMBOLS if not s.endswith("_FUT")][:int(fut_scan_n)]

        if st.button("🔭 SCAN FUTURES UNIVERSE", use_container_width=True, key="fut_scan_btn"):
            with st.spinner(f"Scanning {len(fut_syms)} futures instruments…"):
                fut_results = eng.scan_parallel(fut_syms, "INTRADAY", mood_filter, vix_val, 40, fut_min_str)
            st.session_state["scan_fut"] = fut_results
            db.save("scan_fut", fut_results)

        fut_results = st.session_state.get("scan_fut", [])
        if fut_filter == "LONG Only":
            fut_results = [r for r in fut_results if "BUY"    in r["rec"]]
        elif fut_filter == "SHORT Only":
            fut_results = [r for r in fut_results if "SELL"   in r["rec"]]
        elif fut_filter == "STRONG Only":
            fut_results = [r for r in fut_results if "STRONG" in r["rec"]]

        if fut_results:
            fl = [r for r in fut_results if "BUY"  in r["rec"]]
            fs = [r for r in fut_results if "SELL" in r["rec"]]
            fm = st.columns(4)
            fm[0].markdown(metric_card(len(fut_results), "Total", "var(--p4)"), unsafe_allow_html=True)
            fm[1].markdown(metric_card(len(fl), "LONG",  "var(--green3)"),           unsafe_allow_html=True)
            fm[2].markdown(metric_card(len(fs), "SHORT", "var(--red3)"),             unsafe_allow_html=True)
            fm[3].markdown(metric_card(
                int(np.mean([r["strength"] for r in fut_results])), "Avg Str%", "var(--gold)"
            ), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            ftbl = []
            for r in fut_results:
                lot_sz = 25 if "NIFTY" in r["symbol"] else (15 if "BANK" in r["symbol"] else 1)
                margin = round(r["price"] * lot_sz * 0.12, 0)
                ftbl.append({
                    "Symbol":  r["symbol"].replace(".NS",""),
                    "Signal":  r["rec"],
                    "Str%":    r["strength"],
                    "CMP":     f"₹{r['price']:,.2f}",
                    "Target":  f"₹{r['target']:,.2f}",
                    "SL":      f"₹{r['sl']:,.2f}",
                    "R/R":     f"{r['rr']:.2f}",
                    "5D%":     f"{r.get('m5',0):+.1f}%",
                    "RSI":     f"{r.get('rsi',0):.0f}",
                    "ADX":     f"{r.get('adx',0):.0f}",
                    "Vol":     f"{r.get('vr',1):.1f}x",
                    "Margin≈": f"₹{margin:,.0f}",
                })
            st.dataframe(pd.DataFrame(ftbl), use_container_width=True, hide_index=True)
            st.markdown("<br>", unsafe_allow_html=True)

            for r in fut_results[:20]:
                icon   = "🟢" if "BUY" in r["rec"] else "🔴"
                lot_sz2 = 25 if "NIFTY" in r["symbol"] else (15 if "BANK" in r["symbol"] else 1)
                with st.expander(
                    f"{icon} {r['symbol'].replace('.NS','')} | ₹{r['price']:,.2f} | "
                    f"{r['rec']} {r['strength']}% | ADX:{r.get('adx',0):.0f}"
                ):
                    d1,d2,d3,d4,d5 = st.columns(5)
                    d1.metric("CMP",    f"₹{r['price']:,.2f}")
                    d2.metric("Target", f"₹{r['target']:,.2f}")
                    d3.metric("SL",     f"₹{r['sl']:,.2f}")
                    d4.metric("R/R",    f"{r['rr']:.2f}")
                    d5.metric("5D Mov", f"{r.get('m5',0):+.1f}%")

                    ind_f = r.get("indicators", {})
                    if ind_f:
                        fi1,fi2,fi3,fi4 = st.columns(4)
                        fi1.metric("RSI",  f"{ind_f.get('rsi',0):.1f}")
                        fi2.metric("ADX",  f"{ind_f.get('adx',0):.1f}")
                        fi3.metric("MACD", f"{ind_f.get('macd',0):.3f}")
                        fi4.metric("BB%",  f"{ind_f.get('bb_pct',0):.2f}")

                    if r.get("patterns"):
                        phtml2 = " ".join([
                            f'<span style="background:rgba(213,0,249,0.1);border:1px solid '
                            f'rgba(213,0,249,0.3);color:var(--p4);border-radius:3px;'
                            f'padding:1px 6px;font-size:0.7rem;">{p[0]}</span>'
                            for p in r["patterns"]
                        ])
                        st.markdown(f"**Patterns:** {phtml2}", unsafe_allow_html=True)

                    if r.get("divergence"):
                        st.markdown(
                            f'<div class="success-b">📐 {r["divergence"][2]}</div>',
                            unsafe_allow_html=True,
                        )

                    st.markdown("**Signal Reasoning**")
                    for rn in r["reasons"][:6]:
                        st.markdown(f"<div style='font-size:0.75rem;color:var(--tx3);'>• {rn}</div>", unsafe_allow_html=True)

                    margin2 = round(r["price"] * lot_sz2 * 0.12, 0)
                    kc_f    = eng.kelly_size(float(trade_cap), st.session_state.kelly_wr, r["rr"], r["strength"]) if use_kelly else float(trade_cap)
                    lots_f  = max(eng.min_lots_for_value(r["price"], lot_sz2), int(kc_f / max(margin2, 1)))
                    cost_f  = eng.futures_cost(r["price"], lots_f, lot_sz2, "BUY")
                    st.markdown(
                        f'<div class="info-b" style="font-size:0.72rem;">Margin/lot: ₹{margin2:,.0f} | '
                        f'Kelly lots: {lots_f} | Charges: ₹{cost_f:.2f}</div>',
                        unsafe_allow_html=True,
                    )

                    if r["rec"] not in ("NEUTRAL",):
                        if st.button(
                            f"🚀 {'LONG' if 'BUY' in r['rec'] else 'SHORT'} {r['symbol'].replace('.NS','')} FUTURES",
                            key=f"fut_exec_{r['symbol']}",
                        ):
                            trade_f = {
                                "id":         f"{r['symbol']}_FUT_{int(time.time()*1000)}",
                                "symbol":     r["symbol"],
                                "type":       "LONG" if "BUY" in r["rec"] else "SHORT",
                                "entry":      r["price"], "cmp": eng.get_live_price(r["symbol"]) or r["price"],
                                "lots":       lots_f, "lot_size": lot_sz2,
                                "margin":     round(margin2 * lots_f, 2),
                                "brokerage":  cost_f,
                                "target":     r["target"], "sl": r["sl"],
                                "trailing_sl": None, "pnl": 0.0,
                                "rec":        r["rec"], "strength": r["strength"], "rr": r["rr"],
                                "reasons":    r["reasons"][:5],
                                "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "entry_dt":   datetime.now().isoformat(),
                                "patterns":   [p[0] for p in r.get("patterns", [])],
                            }
                            st.session_state.fut_portfolio.append(trade_f)
                            db.save("fut_portfolio", st.session_state.fut_portfolio)
                            st.success(
                                f"✅ {'LONG' if 'BUY' in r['rec'] else 'SHORT'} "
                                f"{r['symbol']} Futures @ ₹{r['price']:.2f}"
                            )
        else:
            st.info("👆 Click 'Scan Futures Universe' to find LONG/SHORT setups.")

    # ── Futures Auto Trading ──────────────────────────────────────────────────
    with fut_tabs[1]:
        st.markdown('<div class="sec-ttl">⚡ FUTURES AUTO TRADING ENGINE</div>', unsafe_allow_html=True)

        if not st.session_state.auto_fut:
            st.markdown("""
            <div style="background:var(--bg2);border:1px solid var(--p-border);
            border-radius:10px;padding:18px;text-align:center;margin-bottom:14px;">
                <div style="font-family:'Space Grotesk';font-size:1.2rem;color:var(--p4);
                letter-spacing:3px;">AI FUTURES AUTO TRADER</div>
                <div style="color:var(--tx3);font-size:0.8rem;margin-top:6px;">
                    Scans All Equity + Index Futures · Momentum + Breakout + Trend ·
                    Kelly Margin Sizing · Auto SL
                </div>
            </div>""", unsafe_allow_html=True)

            _, fa2, _ = st.columns([1, 2, 1])
            with fa2:
                fa_dur = st.number_input("Duration (minutes)", 1, 390, 30, 5, key="fa_dur")
                fa_max = st.number_input("Max simultaneous positions", 1, 10, 3, 1, key="fa_max")
                fa_str = st.number_input("Min signal strength", 50, 95, 60, 5, key="fa_str")
                _fa_total = max(1, len(eng.FUTURES_SYMBOLS))
                _fa_min   = min(10, _fa_total)
                _fa_step  = max(1, _fa_total // 10)
                fa_scan   = st.number_input(
                    "Stocks to scan", _fa_min, _fa_total, _fa_total, _fa_step, key="fa_scan"
                )
                st.markdown(
                    f'<div class="info-b">Market Bias: <b>{mood}</b> | VIX: {vix_val:.1f}</div>',
                    unsafe_allow_html=True,
                )
                if st.button("🚀 START FUTURES AUTO TRADING", use_container_width=True, key="fa_start"):
                    st.session_state.auto_fut      = True
                    st.session_state.auto_fut_end  = (
                        datetime.now() + timedelta(minutes=int(fa_dur))
                    ).isoformat()
                    st.session_state["fa_max2"]    = int(fa_max)
                    st.session_state["fa_str2"]    = int(fa_str)
                    st.session_state["fa_scan2"]   = int(fa_scan)
                    st.session_state["fa_total_s"] = float(fa_dur) * 60.0
                    st.session_state["fa_start_ts"] = time.time()
                    db.save("auto_fut",     True)
                    db.save("auto_fut_end", st.session_state.auto_fut_end)
                    st.rerun()
        else:
            end_ft  = datetime.fromisoformat(st.session_state.auto_fut_end)
            rem_f   = max(0.0, (end_ft - datetime.now()).total_seconds())
            tot_f   = max(1.0, rem_f)
            prog_f  = 1.0 - rem_f / tot_f
            fc1b,fc2b,fc3b,fc4b = st.columns(4)
            fc1b.metric("Time Left", f"{int(rem_f//60)}m {int(rem_f%60)}s")
            fc2b.metric("Open Pos",  len(st.session_state.fut_portfolio))
            fp_pnl = sum(p.get("pnl", 0) for p in st.session_state.fut_portfolio)
            fc3b.metric("Live P&L",  f"₹{fp_pnl:+,.0f}")
            fc4b.metric("Realized",  f"₹{sum(p.get('pnl',0) for p in st.session_state.fut_history):+,.0f}")
            _fa_total_s  = float(st.session_state.get("fa_total_s", 1800))
            _fa_start_ts = float(st.session_state.get("fa_start_ts", time.time()))
            elapsed_f    = time.time() - _fa_start_ts
            prog_f       = max(0.0, min(1.0, elapsed_f / max(_fa_total_s, 1)))
            st.progress(prog_f)

            if rem_f <= 0:
                for pos in st.session_state.fut_portfolio:
                    ep2  = pos["entry"]; cmp2 = pos.get("cmp", ep2)
                    lots = pos["lots"];  ls   = pos["lot_size"]
                    gross = (cmp2 - ep2) * lots * ls if pos["type"] == "LONG" else (ep2 - cmp2) * lots * ls
                    net   = gross - pos.get("brokerage", 0)
                    st.session_state.fut_history.append({
                        **pos,
                        "exit":      cmp2,
                        "pnl":       round(net, 2),
                        "status":    "CLOSED",
                        "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    st.session_state.journal.append({
                        "cat":      "FUTURES",
                        "symbol":   pos["symbol"],
                        "pnl":      round(net, 2),
                        "win":      net >= 0,
                        "strength": pos.get("strength", 0),
                        "date":     datetime.now().strftime("%Y-%m-%d"),
                        "rec":      pos.get("rec", ""),
                    })
                st.session_state.fut_portfolio = []
                st.session_state.auto_fut      = False
                db.save("auto_fut",    False)
                db.save("fut_portfolio", [])
                db.save("fut_history", st.session_state.fut_history)
                db.save("journal",     st.session_state.journal)
                update_kelly()
                st.rerun()
            else:
                _fmax  = st.session_state.get("fa_max2", 3)
                _fstr  = st.session_state.get("fa_str2", 60)
                _fscan = st.session_state.get("fa_scan2", max(1, len(eng.FUTURES_SYMBOLS)))

                if len(st.session_state.fut_portfolio) < _fmax:
                    with st.spinner("Scanning futures…"):
                        fsyms = [s for s in eng.FUTURES_SYMBOLS if not s.endswith("_FUT")][:_fscan]
                        fnew  = eng.scan_parallel(fsyms, "INTRADAY", mood_filter, vix_val, 40, _fstr)
                    fexist = {p["symbol"] + p["type"] for p in st.session_state.fut_portfolio}
                    for sig in fnew:
                        if len(st.session_state.fut_portfolio) >= _fmax: break
                        if sig["rec"] == "NEUTRAL": continue
                        if mood_filter == "BEARISH" and "BUY"  in sig["rec"]: continue
                        if mood_filter == "BULLISH" and "SELL" in sig["rec"]: continue
                        fk = sig["symbol"] + sig["rec"]
                        if fk in fexist: continue
                        p3       = sig["price"]
                        lot_sz3  = 25 if "NIFTY" in sig["symbol"] else (15 if "BANK" in sig["symbol"] else 1)
                        margin3  = round(p3 * lot_sz3 * 0.12, 0)
                        # Minimum ₹1 lakh per trade: lots = ceil(100000 / margin_per_lot)
                        min_lots_f = eng.min_lots_for_value(p3, lot_sz3)
                        kc_f2      = eng.kelly_size(float(trade_cap), st.session_state.kelly_wr, sig["rr"], sig["strength"]) if use_kelly else float(trade_cap)
                        kelly_lots_f = max(1, int(kc_f2 / max(margin3, 1)))
                        lots_f2    = max(min_lots_f, kelly_lots_f)
                        cost_f2    = eng.futures_cost(p3, lots_f2, lot_sz3, "BUY")
                        trade_f2   = {
                            "id":         f"{sig['symbol']}_FUT_{int(time.time()*1000)}",
                            "symbol":     sig["symbol"],
                            "type":       "LONG" if "BUY" in sig["rec"] else "SHORT",
                            "entry":      p3, "cmp": eng.get_live_price(sig["symbol"]) or p3,
                            "lots":       lots_f2, "lot_size": lot_sz3,
                            "margin":     round(margin3 * lots_f2, 2),
                            "brokerage":  cost_f2,
                            "target":     sig["target"], "sl": sig["sl"],
                            "trailing_sl": None, "pnl": 0.0,
                            "rec":        sig["rec"], "strength": sig["strength"], "rr": sig["rr"],
                            "reasons":    sig["reasons"][:5],
                            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "entry_dt":   datetime.now().isoformat(),
                            "patterns":   [p4[0] for p4 in sig.get("patterns", [])],
                        }
                        st.session_state.fut_portfolio.append(trade_f2)
                        fexist.add(fk)
                        st.success(f"✅ AUTO: {trade_f2['type']} {sig['symbol'].replace('.NS','')} FUT {lots_f2}L @ ₹{p3:.2f} | Margin: ₹{margin3*lots_f2:,.0f}")
                    # Persist new entries immediately
                    db.save("fut_portfolio", st.session_state.fut_portfolio)

                fstill = []
                for pos in st.session_state.fut_portfolio:
                    # FIX: fall back to last known CMP (not entry) so futures
                    # price never reverts to the buying price on API hiccups.
                    _fetched_fut = eng.get_live_price(pos["symbol"])
                    lp2 = _fetched_fut if (_fetched_fut and _fetched_fut > 0) else pos.get("cmp", pos["entry"])
                    pos["cmp"] = lp2
                    ep2  = pos["entry"]; lots2 = pos["lots"]; ls2 = pos["lot_size"]; cost5 = pos.get("brokerage", 0)
                    gross2 = (lp2 - ep2) * lots2 * ls2 if pos["type"] == "LONG" else (ep2 - lp2) * lots2 * ls2
                    pos["pnl"] = round(gross2 - cost5, 2)
                    pnl_pct2 = (lp2 - ep2) / ep2 * 100 if ep2 > 0 else 0
                    if use_trail and abs(pnl_pct2) >= 1.5:
                        if pos.get("trailing_sl") is None:
                            pos["trailing_sl"] = ep2
                        else:
                            atr2  = pos.get("atr", ep2 * 0.015)
                            if pos["type"] == "LONG":
                                new_t2 = lp2 - 1.5 * atr2
                                pos["trailing_sl"] = max(pos["trailing_sl"], round(new_t2, 2))
                            else:
                                new_t2 = lp2 + 1.5 * atr2
                                pos["trailing_sl"] = min(pos["trailing_sl"], round(new_t2, 2))
                    eff_sl2 = pos.get("trailing_sl") or pos.get("sl", 0)
                    hit2 = (
                        (pos["type"] == "LONG"  and (lp2 >= pos.get("target", lp2+1) or lp2 <= eff_sl2)) or
                        (pos["type"] == "SHORT" and (lp2 <= pos.get("target", 0)      or lp2 >= eff_sl2))
                    )
                    if use_time_x:
                        try:
                            ed3 = datetime.fromisoformat(pos.get("entry_dt", datetime.now().isoformat()))
                            if (datetime.now() - ed3).total_seconds() > 1800 and abs(pnl_pct2) < 0.5:
                                hit2 = True
                        except Exception:
                            pass
                    if hit2:
                        cost6 = eng.futures_cost(lp2, lots2, ls2, pos["type"])
                        net3  = gross2 - cost5 - cost6
                        st.session_state.fut_history.append({
                            **pos,
                            "exit":      lp2,
                            "pnl":       round(net3, 2),
                            "status":    "CLOSED",
                            "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        })
                        st.session_state.journal.append({
                            "cat":      "FUTURES",
                            "symbol":   pos["symbol"],
                            "pnl":      round(net3, 2),
                            "win":      net3 >= 0,
                            "strength": pos.get("strength", 0),
                            "date":     datetime.now().strftime("%Y-%m-%d"),
                            "rec":      pos.get("rec", ""),
                        })
                    else:
                        fstill.append(pos)
                st.session_state.fut_portfolio = fstill
                db.save("fut_portfolio", fstill)
                db.save("fut_history",   st.session_state.fut_history)
                db.save("journal",       st.session_state.journal)
                update_kelly()

                # Auto-refresh every 12s while trading is active
                st.caption(f"🔄 Live 12s refresh | {datetime.now().strftime('%H:%M:%S')} | Positions: {len(st.session_state.fut_portfolio)}")
                st.markdown("### Live Futures Positions")
                if st.session_state.fut_portfolio:
                    for at_fi, pos in enumerate(st.session_state.fut_portfolio):
                        # v6: use enhanced live card
                        fut_pos = dict(pos)
                        fut_pos["type"] = pos["type"]   # LONG/SHORT
                        pct_move2 = (pos.get("cmp", pos["entry"]) - pos["entry"]) / pos["entry"] * 100 if pos["entry"] > 0 else 0
                        target_f  = pos.get("target", pos["entry"])
                        sl_f      = pos.get("trailing_sl") or pos.get("sl", pos["entry"])
                        prog_range_f = abs(target_f - pos["entry"])
                        prog_done_f  = max(0, pos.get("cmp", pos["entry"]) - pos["entry"]) if pos["type"] == "LONG" else max(0, pos["entry"] - pos.get("cmp", pos["entry"]))
                        prog_f2      = min(100, (prog_done_f / prog_range_f * 100)) if prog_range_f > 0 else 0
                        pn2   = pos.get("pnl", 0)
                        card_c2 = "profit" if pn2 >= 0 else "loss"
                        col_f2  = "var(--green3)" if pos["type"] == "LONG" else "var(--red3)"
                        trail4  = f" | Trail: ₹{pos['trailing_sl']:.2f}" if pos.get("trailing_sl") else ""
                        st.markdown(f"""
                        <div class="live-pos-card {card_c2}">
                          <div class="live-badge"><div class="live-dot"></div>LIVE</div>
                          <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;align-items:flex-start;">
                            <div>
                              <div class="tc-head" style="color:{col_f2};">{pos['type']} {pos['symbol'].replace('.NS','')} FUT</div>
                              <div class="tc-meta">Entry ₹{pos['entry']:.2f} → CMP ₹{pos.get('cmp',pos['entry']):.2f} | {pos['lots']}L × {pos['lot_size']}{trail4}</div>
                              <div class="tc-meta" style="margin-top:3px;">Move: <span style="color:{'var(--green3)' if pct_move2>=0 else 'var(--red3)'};">{pct_move2:+.2f}%</span></div>
                            </div>
                            <div style="text-align:right;">
                              <div class="{'pnl-pos' if pn2>=0 else 'pnl-neg'}" style="font-size:1.1rem;">{'▲' if pn2>=0 else '▼'} ₹{abs(pn2):,.2f}</div>
                              <div style="font-size:.62rem;color:var(--muted);margin-top:2px;">Net P&amp;L</div>
                            </div>
                          </div>
                          <div class="tc-progress-wrap" style="margin-top:10px;">
                            <div class="tc-progress-fill {'tc-progress-green' if pn2>=0 else 'tc-progress-red'}" style="width:{prog_f2:.1f}%;"></div>
                          </div>
                        </div>""", unsafe_allow_html=True)
                        # Per-line square off button
                        if st.button(f"✅ Square Off {pos['symbol'].replace('.NS','')} {pos['type']}",
                                     key=f"at_fut_sq_{pos['id']}_{at_fi}"):
                            lp_fsq  = eng.get_live_price(pos["symbol"]) or pos["entry"]
                            ep_fsq  = pos["entry"]; lots_fsq = pos["lots"]; ls_fsq = pos["lot_size"]
                            gross_fsq = (lp_fsq - ep_fsq) * lots_fsq * ls_fsq if pos["type"] == "LONG" else (ep_fsq - lp_fsq) * lots_fsq * ls_fsq
                            cost_fsq  = eng.futures_cost(lp_fsq, lots_fsq, ls_fsq, pos["type"])
                            net_fsq   = gross_fsq - pos.get("brokerage", 0) - cost_fsq
                            st.session_state.fut_history.append({
                                **pos, "exit": lp_fsq, "pnl": round(net_fsq, 2),
                                "status": "CLOSED",
                                "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            })
                            st.session_state.journal.append({
                                "cat": "FUTURES", "symbol": pos["symbol"],
                                "pnl": round(net_fsq, 2), "win": net_fsq >= 0,
                                "strength": pos.get("strength", 0),
                                "date": datetime.now().strftime("%Y-%m-%d"),
                                "rec": pos.get("rec", ""),
                            })
                            st.session_state.fut_portfolio = [
                                p for p in st.session_state.fut_portfolio if p["id"] != pos["id"]
                            ]
                            db.save("fut_portfolio", st.session_state.fut_portfolio)
                            db.save("fut_history",   st.session_state.fut_history)
                            db.save("journal",       st.session_state.journal)
                            update_kelly()
                            st.success(f"✅ Squared off {pos['symbol']} {pos['type']} @ ₹{lp_fsq:.2f} | P&L: ₹{net_fsq:+,.0f}")
                            st.rerun()
                else:
                    st.info("No open futures positions. Scanning next cycle…")

                fs2, _ = st.columns([1, 3])
                with fs2:
                    if st.button("🛑 STOP FUTURES AUTO TRADING", key="fut_stop", use_container_width=True):
                        for pos in st.session_state.fut_portfolio:
                            lp3  = eng.get_live_price(pos["symbol"]) or pos["entry"]
                            ep3  = pos["entry"]; lots3 = pos["lots"]; ls3 = pos["lot_size"]
                            gross3 = (lp3 - ep3) * lots3 * ls3 if pos["type"] == "LONG" else (ep3 - lp3) * lots3 * ls3
                            cost7  = eng.futures_cost(lp3, lots3, ls3, pos["type"])
                            net4   = gross3 - pos.get("brokerage", 0) - cost7
                            st.session_state.fut_history.append({
                                **pos,
                                "exit":      lp3,
                                "pnl":       round(net4, 2),
                                "status":    "CLOSED",
                                "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            })
                            st.session_state.journal.append({
                                "cat":      "FUTURES",
                                "symbol":   pos["symbol"],
                                "pnl":      round(net4, 2),
                                "win":      net4 >= 0,
                                "strength": pos.get("strength", 0),
                                "date":     datetime.now().strftime("%Y-%m-%d"),
                                "rec":      pos.get("rec", ""),
                            })
                        st.session_state.fut_portfolio = []
                        st.session_state.auto_fut      = False
                        db.save("auto_fut",    False)
                        db.save("fut_portfolio", [])
                        db.save("fut_history", st.session_state.fut_history)
                        db.save("journal",     st.session_state.journal)
                        update_kelly()
                        st.rerun()
                time.sleep(12)   # v6: 12-second live refresh
                st.rerun()

    # ── Futures Open Positions ────────────────────────────────────────────────
    with fut_tabs[2]:
        st.markdown('<div class="sec-ttl">💼 FUTURES OPEN POSITIONS</div>', unsafe_allow_html=True)
        if not st.session_state.fut_portfolio:
            st.info("No open futures positions.")
        else:
            ftot_inv = sum(p.get("margin", 0) for p in st.session_state.fut_portfolio)
            ftot_pnl = sum(p.get("pnl",    0) for p in st.session_state.fut_portfolio)
            fp2      = st.columns(3)
            fp2[0].markdown(metric_card(f"₹{ftot_inv:,.0f}",  "Margin Deployed", "var(--p4)"), unsafe_allow_html=True)
            fp2[1].markdown(metric_card(f"₹{ftot_pnl:+,.0f}", "Unrealised P&L",  "var(--green3)" if ftot_pnl >= 0 else "var(--red3)"), unsafe_allow_html=True)
            fp2[2].markdown(metric_card(len(st.session_state.fut_portfolio), "Open Positions", "var(--gold3)"), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            for pos in st.session_state.fut_portfolio:
                # FIX: use last known CMP as fallback so price never reverts to entry
                _fetched_fp = eng.get_live_price(pos["symbol"])
                lp4 = _fetched_fp if (_fetched_fp and _fetched_fp > 0) else pos.get("cmp", pos["entry"])
                pos["cmp"] = lp4
                ep4  = pos["entry"]; lots4 = pos["lots"]; ls4 = pos["lot_size"]; cost8 = pos.get("brokerage", 0)
                gross4 = (lp4 - ep4) * lots4 * ls4 if pos["type"] == "LONG" else (ep4 - lp4) * lots4 * ls4
                pos["pnl"] = round(gross4 - cost8, 2)
                pn3   = pos["pnl"]
                trail5 = f" | Trail: ₹{pos['trailing_sl']:.2f}" if pos.get("trailing_sl") else ""
                with st.expander(
                    f"{'🟢' if pos['type']=='LONG' else '🔴'} {pos['type']} "
                    f"{pos['symbol'].replace('.NS','')} FUT | "
                    f"Entry ₹{ep4:.2f} | CMP ₹{lp4:.2f} | {pnl_fmt(pn3)}{trail5}"
                ):
                    fp3_c = st.columns(5)
                    fp3_c[0].metric("Entry",  f"₹{ep4:.2f}")
                    fp3_c[1].metric("CMP",    f"₹{lp4:.2f}")
                    fp3_c[2].metric("Target", f"₹{pos.get('target',0):.2f}")
                    fp3_c[3].metric("SL",     f"₹{pos.get('sl',0):.2f}")
                    fp3_c[4].metric("P&L",    f"₹{pn3:+,.0f}")
                    if st.button("✅ Square Off", key=f"fut_sq_{pos['id']}"):
                        cost9  = eng.futures_cost(lp4, lots4, ls4, pos["type"])
                        net5   = gross4 - cost8 - cost9
                        st.session_state.fut_history.append({
                            **pos,
                            "exit":      lp4,
                            "pnl":       round(net5, 2),
                            "status":    "CLOSED",
                            "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        })
                        st.session_state.journal.append({
                            "cat":      "FUTURES",
                            "symbol":   pos["symbol"],
                            "pnl":      round(net5, 2),
                            "win":      net5 >= 0,
                            "strength": pos.get("strength", 0),
                            "date":     datetime.now().strftime("%Y-%m-%d"),
                            "rec":      pos.get("rec", ""),
                        })
                        st.session_state.fut_portfolio = [
                            p5 for p5 in st.session_state.fut_portfolio if p5["id"] != pos["id"]
                        ]
                        db.save("fut_portfolio", st.session_state.fut_portfolio)
                        db.save("fut_history",   st.session_state.fut_history)
                        db.save("journal",       st.session_state.journal)
                        update_kelly()
                        st.success(f"Squared off ₹{net5:+,.0f}")
                        st.rerun()
            db.save("fut_portfolio", st.session_state.fut_portfolio)

    # ── Futures History ───────────────────────────────────────────────────────
    with fut_tabs[3]:
        st.markdown('<div class="sec-ttl">📜 FUTURES TRADE HISTORY</div>', unsafe_allow_html=True)
        fh2 = st.session_state.fut_history
        if not fh2:
            st.info("No closed futures trades yet.")
        else:
            fw  = len([x for x in fh2 if x.get("pnl", 0) >= 0])
            fn  = sum(x.get("pnl", 0) for x in fh2)
            fwr = fw / len(fh2) * 100
            fhc = st.columns(4)
            fhc[0].metric("Total",    len(fh2))
            fhc[1].metric("Wins",     fw)
            fhc[2].metric("Win Rate", f"{fwr:.1f}%")
            fhc[3].metric("Net P&L",  f"₹{fn:+,.0f}")
            df_fh  = pd.DataFrame(fh2)
            fdcols = [c for c in ["symbol","type","entry","exit","lots","margin","brokerage","pnl","rec","entry_time","exit_time"] if c in df_fh.columns]
            st.dataframe(df_fh[fdcols].rename(columns={"entry":"Entry(₹)","exit":"Exit(₹)","pnl":"Net P&L(₹)"}),
                         use_container_width=True, hide_index=True)
            if len(fh2) >= 2:
                df_fh2        = pd.DataFrame(fh2)
                df_fh2["cum"] = df_fh2["pnl"].cumsum()
                fig3 = go.Figure()
                fig3.add_trace(go.Scatter(
                    y=df_fh2["cum"], mode="lines+markers",
                    line=dict(color="#d500f9", width=2),
                    fill="tozeroy", fillcolor="rgba(213,0,249,0.05)",
                    marker=dict(color=["#00e676" if p >= 0 else "#ff1744" for p in df_fh2["pnl"]], size=7),
                ))
                fig3.update_layout(
                    title="Futures Cumulative P&L",
                    paper_bgcolor="#050A14", plot_bgcolor="#050A14",
                    font=dict(color="#94A3B8"), height=250,
                    margin=dict(l=40, r=20, t=30, b=20),
                )
                st.plotly_chart(fig3, use_container_width=True)
            st.download_button(
                "📥 Download Futures CSV",
                data=df_fh.to_csv(index=False),
                file_name=f"futures_history_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )
            if st.button("🗑️ Clear Futures History", key="clr_fut_hist"):
                st.session_state.fut_history = []
                db.save("fut_history", [])
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — COMBINED PORTFOLIO
# ══════════════════════════════════════════════════════════════════════════════

def _close_cash_position(pos, cat, history_key, journal_rec=""):
    lp = eng.get_live_price(pos["symbol"]) or pos.get("cmp", pos["entry"])
    ep = pos["entry"]
    qty = pos["qty"]
    side = pos.get("type", "BUY")
    gross = (lp - ep) * qty if side in {"BUY", "LONG"} else (ep - lp) * qty
    exit_cost = eng.segment_cost(lp, qty, side, pos.get("mode") == "DELIVERY", pos.get("leverage", 1))
    net = gross - pos.get("brokerage", 0) - exit_cost
    st.session_state[history_key].append({
        **pos, "exit": lp, "pnl": round(net, 2), "status": "CLOSED",
        "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    st.session_state.journal.append({
        "cat": cat, "symbol": pos["symbol"], "pnl": round(net, 2), "win": net >= 0,
        "strength": pos.get("strength", 0), "date": datetime.now().strftime("%Y-%m-%d"),
        "rec": journal_rec or pos.get("rec", ""),
    })
    return lp, net

def _render_cash_segment(tab, title, cat, symbols, portfolio_key, history_key, auto_key, end_key, scan_key):
    with tab:
        st.markdown(f'<div class="sec-ttl">{title}</div>', unsafe_allow_html=True)
        seg_tabs = st.tabs(["Scanner", "Auto Trading", "Open Positions", "Trade History"])

        with seg_tabs[0]:
            c1, c2, c3, c4 = st.columns(4)
            mode = c1.radio("Mode", ["INTRADAY", "DELIVERY"], horizontal=True, key=f"{cat}_mode")
            lev = c2.selectbox("Leverage", ["1x", "5x", "10x"], key=f"{cat}_lev")
            if mode == "DELIVERY":
                lev = "1x"
            scan_n = c3.number_input("Symbols to scan", 1, max(1, len(symbols)), min(20, max(1, len(symbols))), 1, key=f"{cat}_scan_n")
            quick = c4.selectbox("Quick Analyse", [""] + symbols, key=f"{cat}_quick")
            if st.button(f"Scan {cat}", use_container_width=True, key=f"{cat}_scan_btn") or quick:
                syms = [quick] if quick else symbols[:int(scan_n)]
                with st.spinner(f"Scanning {cat}..."):
                    st.session_state[scan_key] = eng.scan_segment_parallel(syms, cat, mode, mood_filter, vix_val, 20, min_str)
            results = st.session_state.get(scan_key, [])
            if not results:
                st.info("No active signals yet. Add Angel One credentials for live MCX prices and scan again.")
            else:
                st.dataframe(pd.DataFrame([{
                    "Symbol": r["symbol"].replace(".NS", "").replace(".MCX", ""),
                    "Signal": r["rec"], "Strength": f"{r['strength']}%",
                    "CMP": f"₹{r['price']:,.2f}", "Target": f"₹{r['target']:,.2f}",
                    "SL": f"₹{r['sl']:,.2f}", "RR": r.get("rr", 0),
                } for r in results]), use_container_width=True, hide_index=True)
                for i, r in enumerate(results[:10]):
                    qty = eng.min_cash_qty(r["price"])
                    notional = qty * r["price"]
                    leverage = int(str(lev).replace("x", ""))
                    margin = notional / max(leverage, 1)
                    with st.expander(f"{r['rec']} {r['symbol']} | ₹{notional:,.0f} notional"):
                        st.write(" | ".join(r.get("reasons", [])[:4]))
                        if st.button(f"Execute {cat} {r['symbol']}", key=f"{cat}_manual_{i}"):
                            side = "BUY" if "BUY" in r["rec"] else "SELL"
                            cost = eng.segment_cost(r["price"], qty, side, mode == "DELIVERY", leverage)
                            st.session_state[portfolio_key].append({
                                "id": f"{cat}_{r['symbol']}_{int(time.time()*1000)}",
                                "symbol": r["symbol"], "type": side, "mode": mode, "leverage": leverage,
                                "entry": r["price"], "cmp": eng.get_live_price(r["symbol"]) or r["price"],
                                "qty": qty, "invested": round(notional, 2), "margin": round(margin, 2),
                                "brokerage": cost, "target": r["target"], "sl": r["sl"], "trailing_sl": None,
                                "pnl": 0.0, "rec": r["rec"], "strength": r["strength"], "rr": r.get("rr", 0),
                                "reasons": r.get("reasons", [])[:5],
                                "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "entry_dt": datetime.now().isoformat(),
                            })
                            db.save(portfolio_key, st.session_state[portfolio_key])
                            st.success(f"Executed {cat} {side} {r['symbol']} qty {qty} @ ₹{r['price']:.2f}")
                            st.rerun()

        with seg_tabs[1]:
            if not st.session_state[auto_key]:
                _, mid, _ = st.columns([1, 2, 1])
                with mid:
                    dur = st.number_input("Duration (minutes)", 1, 390, 30, 5, key=f"{cat}_dur")
                    max_pos = st.number_input("Max simultaneous positions", 1, 20, 3, 1, key=f"{cat}_max")
                    lev_auto = st.selectbox("Intraday leverage", ["1x", "5x", "10x"], key=f"{cat}_auto_lev")
                    mode_auto = st.radio("Trading Mode", ["INTRADAY", "DELIVERY"], horizontal=True, key=f"{cat}_auto_mode")
                    if mode_auto == "DELIVERY":
                        lev_auto = "1x"
                    scan_auto = st.number_input("Symbols per cycle", 1, max(1, len(symbols)), min(20, max(1, len(symbols))), 1, key=f"{cat}_auto_scan")
                    st.markdown(f'<div class="info-b">Minimum per line: ₹{eng.MIN_AUTO_TRADE_VALUE:,.0f} | CMP refresh: <b>12 seconds</b> | Angel One first, fallback to NSE public APIs.</div>', unsafe_allow_html=True)
                    if st.button(f"START {cat} AUTO TRADING", use_container_width=True, key=f"{cat}_auto_start"):
                        st.session_state[auto_key] = True
                        st.session_state[end_key] = (datetime.now() + timedelta(minutes=int(dur))).isoformat()
                        st.session_state[f"{cat}_auto_total_s"] = float(dur) * 60.0
                        st.session_state[f"{cat}_auto_start_ts"] = time.time()
                        st.session_state[f"{cat}_auto_max2"] = int(max_pos)
                        st.session_state[f"{cat}_auto_scan2"] = int(scan_auto)
                        st.session_state[f"{cat}_auto_mode2"] = mode_auto
                        st.session_state[f"{cat}_auto_lev2"] = int(str(lev_auto).replace("x", ""))
                        db.save(auto_key, True); db.save(end_key, st.session_state[end_key])
                        st.rerun()
            else:
                end_dt = datetime.fromisoformat(st.session_state[end_key])
                rem = max(0.0, (end_dt - datetime.now()).total_seconds())
                elapsed = time.time() - float(st.session_state.get(f"{cat}_auto_start_ts", time.time()))
                total_s = max(float(st.session_state.get(f"{cat}_auto_total_s", 1800)), 1)
                st.progress(max(0.0, min(1.0, elapsed / total_s)))
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Time Left", f"{int(rem//60)}m {int(rem%60)}s")
                m2.metric("Open Pos", len(st.session_state[portfolio_key]))
                m3.metric("Live P&L", f"₹{sum(p.get('pnl',0) for p in st.session_state[portfolio_key]):+,.0f}")
                m4.metric("Realized", f"₹{sum(p.get('pnl',0) for p in st.session_state[history_key]):+,.0f}")

                if rem <= 0:
                    for pos in list(st.session_state[portfolio_key]):
                        _close_cash_position(pos, cat, history_key)
                    st.session_state[portfolio_key] = []
                    st.session_state[auto_key] = False
                    db.save(auto_key, False); db.save(portfolio_key, []); db.save(history_key, st.session_state[history_key]); db.save("journal", st.session_state.journal)
                    update_kelly(); st.rerun()

                max_pos = st.session_state.get(f"{cat}_auto_max2", 3)
                mode_auto = st.session_state.get(f"{cat}_auto_mode2", "INTRADAY")
                leverage = int(st.session_state.get(f"{cat}_auto_lev2", 1))
                scan_auto = st.session_state.get(f"{cat}_auto_scan2", min(20, len(symbols)))
                if len(st.session_state[portfolio_key]) < max_pos:
                    new_sigs = eng.scan_segment_parallel(symbols[:int(scan_auto)], cat, mode_auto, mood_filter, vix_val, 20, min_str)
                    existing = {p["symbol"] + p["type"] for p in st.session_state[portfolio_key]}
                    for sig in new_sigs:
                        if len(st.session_state[portfolio_key]) >= max_pos:
                            break
                        if sig.get("rec") == "NEUTRAL" or sig.get("price", 0) <= 0:
                            continue
                        side = "BUY" if "BUY" in sig["rec"] else "SELL"
                        key = sig["symbol"] + side
                        if key in existing:
                            continue
                        p = sig["price"]
                        kc = eng.kelly_size(float(trade_cap), st.session_state.kelly_wr, sig.get("rr", 1.5), sig["strength"]) if use_kelly else float(trade_cap)
                        qty = max(eng.min_cash_qty(p), int(kc / max(p, 0.01)))
                        notional = p * qty
                        margin = notional / max(leverage, 1)
                        cost = eng.segment_cost(p, qty, side, mode_auto == "DELIVERY", leverage)
                        st.session_state[portfolio_key].append({
                            "id": f"{cat}_{sig['symbol']}_{int(time.time()*1000)}", "symbol": sig["symbol"],
                            "type": side, "mode": mode_auto, "leverage": leverage,
                            "entry": p, "cmp": eng.get_live_price(sig["symbol"]) or p,
                            "qty": qty, "invested": round(notional, 2), "margin": round(margin, 2),
                            "brokerage": cost, "target": sig["target"], "sl": sig["sl"], "trailing_sl": None,
                            "pnl": 0.0, "rec": sig["rec"], "strength": sig["strength"], "rr": sig.get("rr", 0),
                            "reasons": sig.get("reasons", [])[:5],
                            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "entry_dt": datetime.now().isoformat(),
                        })
                        existing.add(key)
                    db.save(portfolio_key, st.session_state[portfolio_key])

                still = []
                for pos in st.session_state[portfolio_key]:
                    lp = eng.get_live_price(pos["symbol"]) or pos.get("cmp", pos["entry"])
                    pos["cmp"] = lp
                    ep = pos["entry"]; qty = pos["qty"]
                    gross = (lp - ep) * qty if pos["type"] == "BUY" else (ep - lp) * qty
                    pos["pnl"] = round(gross - pos.get("brokerage", 0), 2)
                    pnl_pct = (lp - ep) / ep * 100 if ep > 0 else 0
                    # v6: Enhanced two-phase trailing stop
                    if use_trail:
                        pos = eng.update_trailing_stop(pos, lp, use_trail=True)
                    eff_sl = pos.get("trailing_sl") or pos.get("sl", 0)
                    hit = ((pos["type"] == "BUY" and (lp >= pos.get("target", lp+1) or lp <= eff_sl)) or
                           (pos["type"] == "SELL" and (lp <= pos.get("target", 0) or lp >= eff_sl)))
                    if hit:
                        _close_cash_position(pos, cat, history_key)
                    else:
                        still.append(pos)
                st.session_state[portfolio_key] = still
                db.save(portfolio_key, still); db.save(history_key, st.session_state[history_key]); db.save("journal", st.session_state.journal)
                update_kelly()

                st.caption(f"🔄 Live 12s refresh | {datetime.now().strftime('%H:%M:%S')} | Positions: {len(st.session_state[portfolio_key])}")
                for i, pos in enumerate(st.session_state[portfolio_key]):
                    st.markdown(live_position_card(pos), unsafe_allow_html=True)
                    if st.button(f"Square Off {cat} {pos['symbol']}", key=f"{cat}_sq_{pos['id']}_{i}"):
                        lp, net = _close_cash_position(pos, cat, history_key)
                        st.session_state[portfolio_key] = [p for p in st.session_state[portfolio_key] if p["id"] != pos["id"]]
                        db.save(portfolio_key, st.session_state[portfolio_key]); db.save(history_key, st.session_state[history_key]); db.save("journal", st.session_state.journal)
                        update_kelly()
                        st.success(f"Squared off {pos['symbol']} @ ₹{lp:.2f} | P&L ₹{net:+,.0f}")
                        st.rerun()
                if st.button(f"STOP {cat} AUTO TRADING", use_container_width=True, key=f"{cat}_stop"):
                    for pos in list(st.session_state[portfolio_key]):
                        _close_cash_position(pos, cat, history_key)
                    st.session_state[portfolio_key] = []
                    st.session_state[auto_key] = False
                    db.save(auto_key, False); db.save(portfolio_key, []); db.save(history_key, st.session_state[history_key]); db.save("journal", st.session_state.journal)
                    update_kelly(); st.rerun()
                time.sleep(12)   # v6: 12-second live refresh
                st.rerun()

        with seg_tabs[2]:
            positions = st.session_state[portfolio_key]
            if not positions:
                st.info(f"No open {cat} positions.")
            else:
                for i, pos in enumerate(positions):
                    lp = eng.get_live_price(pos["symbol"]) or pos.get("cmp", pos["entry"])
                    pos["cmp"] = lp
                    ep = pos["entry"]; qty = pos["qty"]
                    gross = (lp - ep) * qty if pos["type"] == "BUY" else (ep - lp) * qty
                    pos["pnl"] = round(gross - pos.get("brokerage", 0), 2)
                    st.markdown(f"<div class='tc {'win' if pos['pnl'] >= 0 else 'loss'}'><b>{pos['type']} {pos['symbol']}</b> | Entry ₹{ep:.2f} | CMP ₹{lp:.2f} | Qty {qty} | {pnl_fmt(pos['pnl'])}</div>", unsafe_allow_html=True)
                    if st.button(f"Square Off {pos['symbol']}", key=f"{cat}_open_sq_{pos['id']}_{i}"):
                        _close_cash_position(pos, cat, history_key)
                        st.session_state[portfolio_key] = [p for p in st.session_state[portfolio_key] if p["id"] != pos["id"]]
                        db.save(portfolio_key, st.session_state[portfolio_key]); db.save(history_key, st.session_state[history_key]); db.save("journal", st.session_state.journal)
                        st.rerun()
                db.save(portfolio_key, st.session_state[portfolio_key])

        with seg_tabs[3]:
            hist = st.session_state[history_key]
            if not hist:
                st.info(f"No closed {cat} trades yet.")
            else:
                df = pd.DataFrame(hist)
                cols = [c for c in ["symbol", "type", "mode", "leverage", "entry", "exit", "qty", "invested", "margin", "brokerage", "pnl", "entry_time", "exit_time"] if c in df.columns]
                st.dataframe(df[cols], use_container_width=True, hide_index=True)
                st.download_button(f"Download {cat} history", data=df.to_csv(index=False), file_name=f"{cat.lower()}_history_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv")

_render_cash_segment(page_tabs[3], "ETF TRADING - GOLD, SILVER, INDEX AND LIQUID ETFs", "ETF", eng.ETF_SYMBOLS, "etf_portfolio", "etf_history", "auto_etf", "auto_etf_end", "scan_etf")
_render_cash_segment(page_tabs[4], "MCX + COMMODITIES TRADING - GOLD, SILVER, ENERGY AND METALS", "MCX", eng.COMMODITY_SYMBOLS, "mcx_portfolio", "mcx_history", "auto_mcx", "auto_mcx_end", "scan_mcx")

with page_tabs[5]:
    st.markdown('<div class="sec-ttl">💼 COMBINED PORTFOLIO — ALL SEGMENTS</div>', unsafe_allow_html=True)

    all_open = (
        [(p, "EQUITY")  for p in st.session_state.eq_portfolio]  +
        [(p, "OPTIONS") for p in st.session_state.opt_portfolio] +
        [(p, "FUTURES") for p in st.session_state.fut_portfolio] +
        [(p, "ETF")     for p in st.session_state.etf_portfolio] +
        [(p, "MCX")     for p in st.session_state.mcx_portfolio]
    )

    if not all_open:
        st.info("No open positions across any segment.")
        # Still show daily stats even with no open positions
        st.markdown(
            daily_pnl_banner(
                _d_stats["realized"], 0,
                _d_stats["trades_today"], _d_stats["win_rate"],
                daily_goal=float(st.session_state.daily_goal),
                trades_closed=_d_stats["trades_today"],
            ),
            unsafe_allow_html=True,
        )
    else:
        total_eq_pnl  = sum(p.get("pnl", 0) for p in st.session_state.eq_portfolio)
        total_opt_pnl = sum(p.get("pnl", 0) for p in st.session_state.opt_portfolio)
        total_fut_pnl = sum(p.get("pnl", 0) for p in st.session_state.fut_portfolio)
        total_etf_pnl = sum(p.get("pnl", 0) for p in st.session_state.etf_portfolio)
        total_mcx_pnl = sum(p.get("pnl", 0) for p in st.session_state.mcx_portfolio)
        total_all_pnl = total_eq_pnl + total_opt_pnl + total_fut_pnl + total_etf_pnl + total_mcx_pnl
        total_inv_all = (
            sum(p.get("invested", 0) for p in st.session_state.eq_portfolio)  +
            sum(p.get("invested", 0) for p in st.session_state.opt_portfolio) +
            sum(p.get("margin",   0) for p in st.session_state.fut_portfolio) +
            sum(p.get("margin", p.get("invested", 0)) for p in st.session_state.etf_portfolio) +
            sum(p.get("margin", p.get("invested", 0)) for p in st.session_state.mcx_portfolio)
        )
        total_realized = sum(p.get("pnl", 0) for p in
            st.session_state.eq_history + st.session_state.opt_history +
            st.session_state.fut_history + st.session_state.etf_history +
            st.session_state.mcx_history)

        # v6: Combined portfolio metrics row
        ret_pct = (total_all_pnl / total_inv_all * 100) if total_inv_all > 0 else 0
        pc_all = st.columns(6)
        pc_all[0].markdown(metric_card(f"₹{total_inv_all:,.0f}",   "Capital Deployed",  "var(--accent2)"), unsafe_allow_html=True)
        pc_all[1].markdown(metric_card(f"₹{total_all_pnl:+,.0f}",  "Unrealised P&L",    "var(--green3)" if total_all_pnl >= 0 else "var(--red3)"), unsafe_allow_html=True)
        pc_all[2].markdown(metric_card(f"₹{total_realized:+,.0f}", "Realized Today",    "var(--green3)" if total_realized >= 0 else "var(--red3)"), unsafe_allow_html=True)
        pc_all[3].markdown(metric_card(f"{ret_pct:+.2f}%",          "Open Return",       "var(--teal)"), unsafe_allow_html=True)
        pc_all[4].markdown(metric_card(len(all_open),                "Open Positions",    "var(--gold3)"), unsafe_allow_html=True)
        pc_all[5].markdown(metric_card(f"₹{total_eq_pnl + total_opt_pnl + total_fut_pnl + total_realized:+,.0f}", "Total Day P&L", "var(--green3)" if (total_all_pnl + total_realized) >= 0 else "var(--red3)"), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        if total_inv_all > 0:
            seg_labels = []; seg_vals = []; seg_colors = []
            if st.session_state.eq_portfolio:
                seg_labels.append("Equity")
                seg_vals.append(sum(p.get("invested", 0) for p in st.session_state.eq_portfolio))
                seg_colors.append("#00e676")
            if st.session_state.opt_portfolio:
                seg_labels.append("Options")
                seg_vals.append(sum(p.get("invested", 0) for p in st.session_state.opt_portfolio))
                seg_colors.append("#00e5ff")
            if st.session_state.fut_portfolio:
                seg_labels.append("Futures")
                seg_vals.append(sum(p.get("margin", 0) for p in st.session_state.fut_portfolio))
                seg_colors.append("#d500f9")
            if seg_vals:
                fig_pie = go.Figure(data=[go.Pie(
                    labels=seg_labels, values=seg_vals,
                    marker=dict(colors=seg_colors), hole=0.5,
                    textfont=dict(color="#94A3B8"),
                )])
                fig_pie.update_layout(
                    paper_bgcolor="#050A14", plot_bgcolor="#050A14",
                    font=dict(color="#94A3B8"), height=220,
                    margin=dict(l=10, r=10, t=20, b=10),
                    showlegend=True, legend=dict(font=dict(color="#94A3B8")),
                )
                st.plotly_chart(fig_pie, use_container_width=True)

        port_tbl = []
        for pos, seg in all_open:
            if seg == "EQUITY":
                name = pos.get("symbol", "").replace(".NS", "")
                desc = f"{pos.get('type','')} {pos.get('mode','')}"
                inv  = pos.get("invested", 0)
            elif seg == "OPTIONS":
                name = f"{pos.get('index','')} {pos.get('strike','')} {pos.get('type','')}"
                desc = f"Exp: {pos.get('expiry','')} | {pos.get('lots',1)}L"
                inv  = pos.get("invested", 0)
            else:
                name = pos.get("symbol", "").replace(".NS", "") + " FUT"
                desc = f"{pos.get('type','')} | {pos.get('lots',1)}L"
                inv  = pos.get("margin", 0)
            pn = pos.get("pnl", 0)
            port_tbl.append({
                "Segment": seg, "Name": name, "Detail": desc,
                "Invested": f"₹{inv:,.0f}", "P&L": f"₹{pn:+,.0f}",
                "Entry":    f"₹{pos.get('entry',0):.2f}",
                "CMP":      f"₹{pos.get('cmp', pos.get('entry',0)):.2f}",
                "Target":   f"₹{pos.get('target',0):.2f}",
                "SL":       f"₹{pos.get('sl',0):.2f}",
                "Strength": f"{pos.get('strength',0)}%",
                "Trail SL": f"₹{pos.get('trailing_sl',0):.2f}" if pos.get("trailing_sl") else "—",
            })
        st.dataframe(pd.DataFrame(port_tbl), use_container_width=True, hide_index=True)

        if st.button("🛑 SQUARE OFF ALL POSITIONS", use_container_width=True, key="sq_all"):
            for pos in st.session_state.eq_portfolio:
                _f = eng.get_live_price(pos["symbol"])
                lp  = _f if (_f and _f > 0) else pos.get("cmp", pos["entry"])
                ep2 = pos["entry"]; qty2 = pos["qty"]
                gross = (lp - ep2) * qty2 if pos["type"] == "BUY" else (ep2 - lp) * qty2
                net   = gross - pos.get("brokerage", 0) - eng.equity_cost(lp, qty2, pos["type"], False)
                st.session_state.eq_history.append({
                    **pos, "exit": lp, "pnl": round(net, 2), "status": "CLOSED",
                    "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                st.session_state.journal.append({
                    "cat": "EQUITY", "symbol": pos["symbol"], "pnl": round(net, 2),
                    "win": net >= 0, "strength": pos.get("strength", 0),
                    "date": datetime.now().strftime("%Y-%m-%d"), "rec": pos.get("rec", ""),
                })
            for pos in st.session_state.opt_portfolio:
                ep2  = pos["entry"]; lots = pos["lots"]; ls = pos["lot_size"]
                live_port = get_live_option_cmp(
                    pos["index"], pos["strike"], pos["type"],
                    pos.get("expiry", str(exp_bn)), vix_val
                )
                cmp2 = live_port if (live_port is not None and live_port > 0) else pos.get("cmp", ep2)
                gross = (cmp2 - ep2) * lots * ls
                net   = gross - pos.get("brokerage", 0) - eng.options_cost(cmp2, lots, ls, "SELL")
                st.session_state.opt_history.append({
                    **pos, "exit": cmp2, "pnl": round(net, 2), "status": "CLOSED",
                    "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                st.session_state.journal.append({
                    "cat": "OPTIONS", "symbol": f"{pos.get('index','')}{pos.get('strike','')}{pos.get('type','')}",
                    "pnl": round(net, 2), "win": net >= 0, "strength": pos.get("strength", 0),
                    "date": datetime.now().strftime("%Y-%m-%d"), "rec": pos.get("signal", ""),
                })
            for pos in st.session_state.fut_portfolio:
                _ff = eng.get_live_price(pos["symbol"])
                lp  = _ff if (_ff and _ff > 0) else pos.get("cmp", pos["entry"])
                ep2 = pos["entry"]; lots = pos["lots"]; ls = pos["lot_size"]
                gross = (lp - ep2) * lots * ls if pos["type"] == "LONG" else (ep2 - lp) * lots * ls
                net   = gross - pos.get("brokerage", 0) - eng.futures_cost(lp, lots, ls, pos["type"])
                st.session_state.fut_history.append({
                    **pos, "exit": lp, "pnl": round(net, 2), "status": "CLOSED",
                    "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                st.session_state.journal.append({
                    "cat": "FUTURES", "symbol": pos["symbol"], "pnl": round(net, 2),
                    "win": net >= 0, "strength": pos.get("strength", 0),
                    "date": datetime.now().strftime("%Y-%m-%d"), "rec": pos.get("rec", ""),
                })
            for pos in st.session_state.etf_portfolio:
                _close_cash_position(pos, "ETF", "etf_history")
            for pos in st.session_state.mcx_portfolio:
                _close_cash_position(pos, "MCX", "mcx_history")
            st.session_state.eq_portfolio  = []
            st.session_state.opt_portfolio = []
            st.session_state.fut_portfolio = []
            st.session_state.etf_portfolio = []
            st.session_state.mcx_portfolio = []
            for key in ["eq_portfolio","opt_portfolio","fut_portfolio","etf_portfolio","mcx_portfolio","eq_history","opt_history","fut_history","etf_history","mcx_history","journal"]:
                db.save(key, st.session_state[key])
            update_kelly()
            st.success("All positions squared off!")
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 5 — CONSOLIDATED HISTORY
# ══════════════════════════════════════════════════════════════════════════════
with page_tabs[6]:
    st.markdown('<div class="sec-ttl">📜 CONSOLIDATED TRADE HISTORY — ALL SEGMENTS</div>', unsafe_allow_html=True)

    all_hist = (
        [(h, "EQUITY")  for h in st.session_state.eq_history]  +
        [(h, "OPTIONS") for h in st.session_state.opt_history] +
        [(h, "FUTURES") for h in st.session_state.fut_history] +
        [(h, "ETF")     for h in st.session_state.etf_history] +
        [(h, "MCX")     for h in st.session_state.mcx_history]
    )

    if not all_hist:
        st.info("No closed trades yet.")
    else:
        total_real = sum(h.get("pnl", 0) for h, _ in all_hist)
        wins_all   = sum(1 for h, _ in all_hist if h.get("pnl", 0) >= 0)
        wr_all     = wins_all / len(all_hist) * 100
        total_brk  = sum(h.get("brokerage", 0) for h, _ in all_hist)
        avg_win    = np.mean([h.get("pnl",0) for h,_ in all_hist if h.get("pnl",0)>0]) if wins_all > 0 else 0
        avg_loss   = np.mean([h.get("pnl",0) for h,_ in all_hist if h.get("pnl",0)<=0]) if (len(all_hist)-wins_all) > 0 else 0

        hmc = st.columns(6)
        hmc[0].markdown(metric_card(len(all_hist),              "Total Trades",  "var(--accent2)"), unsafe_allow_html=True)
        hmc[1].markdown(metric_card(wins_all,                   "Winners",       "var(--green3)"),  unsafe_allow_html=True)
        hmc[2].markdown(metric_card(len(all_hist)-wins_all,     "Losers",        "var(--red3)"),    unsafe_allow_html=True)
        hmc[3].markdown(metric_card(f"{wr_all:.1f}%",           "Win Rate",      "var(--teal)"),    unsafe_allow_html=True)
        hmc[4].markdown(metric_card(f"₹{total_real:+,.0f}",     "Realized P&L",  "var(--green3)" if total_real >= 0 else "var(--red3)"), unsafe_allow_html=True)
        hmc[5].markdown(metric_card(f"₹{total_brk:,.0f}",       "Total Charges", "var(--gold3)"),   unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        # v6: Daily P&L breakdown bar chart
        if len(all_hist) >= 2:
            try:
                daily_map: dict = {}
                for h, seg in all_hist:
                    day = h.get("exit_time", h.get("date", ""))[:10]
                    if day:
                        daily_map[day] = daily_map.get(day, 0) + h.get("pnl", 0)
                if daily_map:
                    days = sorted(daily_map.keys())
                    dpnls = [daily_map[d] for d in days]
                    fig_daily = go.Figure(data=[go.Bar(
                        x=days, y=dpnls,
                        marker_color=["#10B981" if v >= 0 else "#EF4444" for v in dpnls],
                        text=[f"₹{v:+,.0f}" for v in dpnls], textposition="outside",
                        textfont=dict(color="#94A3B8", size=10),
                    )])
                    goal_line = float(st.session_state.daily_goal)
                    fig_daily.add_hline(y=goal_line, line_dash="dot",
                                        line_color="rgba(245,158,11,0.6)",
                                        annotation_text=f"Goal ₹{goal_line:,.0f}")
                    fig_daily.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.15)")
                    fig_daily.update_layout(
                        title="Daily P&L (All Segments)",
                        paper_bgcolor="#050A14", plot_bgcolor="#050A14",
                        font=dict(color="#94A3B8"), height=280,
                        margin=dict(l=40, r=20, t=40, b=20),
                        xaxis=dict(gridcolor="#1a2d45"), yaxis=dict(gridcolor="#1a2d45"),
                    )
                    st.plotly_chart(fig_daily, use_container_width=True)
            except Exception:
                pass

        hist_seg_filter = st.radio("Segment", ["All","EQUITY","OPTIONS","FUTURES","ETF","MCX"], horizontal=True, key="hist_seg")
        filtered_hist   = [(h, s) for h, s in all_hist if hist_seg_filter == "All" or s == hist_seg_filter]

        hist_rows = []
        for h, seg in filtered_hist:
            if seg == "OPTIONS":
                sym = f"{h.get('index','')}{h.get('strike','')}{h.get('type','')}"
            else:
                sym = h.get("symbol", "").replace(".NS", "").replace(".BO", "")
            hist_rows.append({
                "Segment":    seg,
                "Symbol":     sym,
                "Type":       h.get("type") or h.get("signal", ""),
                "Entry(₹)":  f"₹{h.get('entry',0):.2f}",
                "Exit(₹)":   f"₹{h.get('exit',0):.2f}",
                "Net P&L":   f"₹{h.get('pnl',0):+,.0f}",
                "Charges":   f"₹{h.get('brokerage',0):.0f}",
                "Strength":  f"{h.get('strength',0)}%",
                "Entry Time": h.get("entry_time", ""),
                "Exit Time":  h.get("exit_time",  ""),
                "Result":    "✅ WIN" if h.get("pnl", 0) >= 0 else "❌ LOSS",
            })
        st.dataframe(pd.DataFrame(hist_rows), use_container_width=True, hide_index=True)

        if len(filtered_hist) >= 2:
            pnls    = [h.get("pnl", 0) for h, _ in filtered_hist]
            cum_pnl = np.cumsum(pnls)
            fig_cum = go.Figure()
            fig_cum.add_trace(go.Scatter(
                y=cum_pnl, mode="lines+markers",
                line=dict(color="#f5a623", width=2),
                fill="tozeroy", fillcolor="rgba(245,166,35,0.07)",
                marker=dict(color=["#00e676" if p >= 0 else "#ff1744" for p in pnls], size=6),
            ))
            fig_cum.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.2)")
            fig_cum.update_layout(
                title="Cumulative Realized P&L",
                paper_bgcolor="#050A14", plot_bgcolor="#050A14",
                font=dict(color="#94A3B8"), height=280,
                margin=dict(l=40, r=20, t=30, b=20),
                xaxis=dict(gridcolor="#1a2d45"), yaxis=dict(gridcolor="#1a2d45"),
            )
            st.plotly_chart(fig_cum, use_container_width=True)

        if len(all_hist) >= 2:
            seg_pnl = {"EQUITY": 0, "OPTIONS": 0, "FUTURES": 0, "ETF": 0, "MCX": 0}
            for h, s in all_hist:
                seg_pnl[s] = seg_pnl.get(s, 0) + h.get("pnl", 0)
            fig_seg = go.Figure(data=[go.Bar(
                x=list(seg_pnl.keys()), y=list(seg_pnl.values()),
                marker_color=["#00e676" if v >= 0 else "#ff1744" for v in seg_pnl.values()],
                text=[f"₹{v:+,.0f}" for v in seg_pnl.values()], textposition="auto",
            )])
            fig_seg.update_layout(
                title="P&L by Segment",
                paper_bgcolor="#050A14", plot_bgcolor="#050A14",
                font=dict(color="#94A3B8"), height=240,
                margin=dict(l=40, r=20, t=30, b=20),
                xaxis=dict(gridcolor="#1a2d45"), yaxis=dict(gridcolor="#1a2d45"),
            )
            st.plotly_chart(fig_seg, use_container_width=True)

        all_df = pd.DataFrame(hist_rows)
        st.download_button(
            "📥 Download Full History CSV",
            data=all_df.to_csv(index=False),
            file_name=f"full_history_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 6 — TRADE JOURNAL
# ══════════════════════════════════════════════════════════════════════════════
with page_tabs[7]:
    st.markdown('<div class="sec-ttl">📓 TRADE JOURNAL — PERSISTENT ACROSS SESSIONS</div>', unsafe_allow_html=True)

    jrnl = st.session_state.journal
    if not jrnl:
        st.info("No journal entries yet. Close some trades to populate the journal.")
    else:
        jdf = pd.DataFrame(jrnl)
        st.markdown(
            f'<div class="success-b">📒 {len(jrnl)} journal entries loaded from persistent storage. '
            f'This data survives page reloads and session restarts.</div>',
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)

        j_wins  = sum(1 for j in jrnl if j.get("win", False))
        j_net   = sum(j.get("pnl", 0) for j in jrnl)
        j_wr    = j_wins / len(jrnl) * 100
        avg_win = np.mean([j["pnl"] for j in jrnl if j.get("win", False)])     if j_wins > 0                else 0
        avg_los = np.mean([j["pnl"] for j in jrnl if not j.get("win", False)]) if len(jrnl) - j_wins > 0   else 0
        exp_r   = avg_win / (abs(avg_los) + 0.01)

        jm = st.columns(6)
        jm[0].markdown(metric_card(len(jrnl),            "Total Trades", "var(--accent2)"), unsafe_allow_html=True)
        jm[1].markdown(metric_card(j_wins,               "Winners",      "var(--green3)"),  unsafe_allow_html=True)
        jm[2].markdown(metric_card(len(jrnl) - j_wins,   "Losers",       "var(--red3)"),    unsafe_allow_html=True)
        jm[3].markdown(metric_card(f"{j_wr:.1f}%",       "Win Rate",     "var(--teal)"),   unsafe_allow_html=True)
        jm[4].markdown(metric_card(f"₹{j_net:+,.0f}",    "Net P&L",      "var(--green3)" if j_net >= 0 else "var(--red3)"), unsafe_allow_html=True)
        jm[5].markdown(metric_card(f"{exp_r:.2f}x",      "Profit Factor","var(--gold3)"),   unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        if "cat" in jdf.columns:
            st.markdown("#### By Segment")
            cat_agg = jdf.groupby("cat").agg(
                trades=("pnl","count"), net_pnl=("pnl","sum"), win_rate=("win","mean")
            ).reset_index()
            cat_agg["win_rate"] = (cat_agg["win_rate"] * 100).round(1)
            st.dataframe(
                cat_agg.rename(columns={"cat":"Segment","trades":"Trades","net_pnl":"Net P&L(₹)","win_rate":"Win Rate%"}),
                use_container_width=True, hide_index=True,
            )

        if "strength" in jdf.columns:
            st.markdown("#### P&L by Signal Strength")
            jdf2 = jdf.copy()
            jdf2["bucket"] = pd.cut(jdf2["strength"], bins=[0,50,60,70,80,100], labels=["<50","50-60","60-70","70-80","80+"])
            bagg = jdf2.groupby("bucket", observed=True).agg(
                trades=("pnl","count"), net_pnl=("pnl","sum"), win_rate=("win","mean")
            ).reset_index()
            bagg["win_rate"] = (bagg["win_rate"] * 100).round(1)
            fig_b = px.bar(
                bagg, x="bucket", y="net_pnl", color="win_rate",
                color_continuous_scale=["#ff1744","#ffd600","#00e676"],
                title="P&L by Signal Strength Bucket",
                labels={"bucket":"Signal Strength","net_pnl":"Net P&L (₹)"},
            )
            fig_b.update_layout(
                paper_bgcolor="#050A14", plot_bgcolor="#050A14",
                font=dict(color="#94A3B8"), height=260,
                margin=dict(l=40, r=20, t=30, b=20),
            )
            st.plotly_chart(fig_b, use_container_width=True)

        if "date" in jdf.columns:
            st.markdown("#### Daily P&L vs Goal")
            daily = jdf.groupby("date")["pnl"].sum().reset_index().sort_values("date")
            goal_line = float(st.session_state.daily_goal)
            fig_d = go.Figure(data=[go.Bar(
                x=daily["date"], y=daily["pnl"],
                marker_color=["#10B981" if v >= goal_line else ("#F59E0B" if v >= 0 else "#EF4444") for v in daily["pnl"]],
                text=[f"₹{v:+,.0f}" for v in daily["pnl"]], textposition="outside",
                textfont=dict(color="#94A3B8", size=10),
            )])
            fig_d.add_hline(y=goal_line, line_dash="dot",
                            line_color="rgba(245,158,11,0.7)",
                            annotation_text=f"Daily Goal ₹{goal_line:,.0f}",
                            annotation_font_color="#F59E0B")
            fig_d.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.15)")
            fig_d.update_layout(
                title="Daily P&L vs Goal",
                paper_bgcolor="#050A14", plot_bgcolor="#050A14",
                font=dict(color="#94A3B8"), height=280,
                margin=dict(l=40, r=20, t=40, b=20),
                xaxis=dict(gridcolor="#1a2d45"), yaxis=dict(gridcolor="#1a2d45"),
            )
            st.plotly_chart(fig_d, use_container_width=True)

        st.markdown("#### All Journal Entries")
        jrnl_tbl = []
        for j in reversed(jrnl):
            jrnl_tbl.append({
                "Date":     j.get("date", ""),
                "Segment":  j.get("cat",  ""),
                "Symbol":   j.get("symbol",""),
                "Signal":   j.get("rec",  ""),
                "P&L":     f"₹{j.get('pnl',0):+,.0f}",
                "Result":  "✅ WIN" if j.get("win", False) else "❌ LOSS",
                "Strength":f"{j.get('strength',0)}%",
            })
        st.dataframe(pd.DataFrame(jrnl_tbl), use_container_width=True, hide_index=True)

        st.download_button(
            "📥 Download Journal CSV",
            data=pd.DataFrame(jrnl).to_csv(index=False),
            file_name=f"trade_journal_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )
        if st.button("🗑️ Clear Journal (PERMANENT)", key="clr_jrnl"):
            st.session_state.journal  = []
            st.session_state.kelly_wr = 0.55
            db.save("journal",   [])
            db.save("kelly_wr",  0.55)
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE 7 — ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════
with page_tabs[8]:
    st.markdown('<div class="sec-ttl">📊 ANALYTICS DASHBOARD — PERFORMANCE INSIGHTS</div>', unsafe_allow_html=True)

    all_closed = (
        st.session_state.eq_history  +
        st.session_state.opt_history +
        st.session_state.fut_history +
        st.session_state.etf_history +
        st.session_state.mcx_history
    )

    if len(all_closed) < 2:
        st.info("Trade at least 2 positions to see analytics.")
    else:
        adf = pd.DataFrame(all_closed)

        st.markdown("### 🏆 Key Performance Metrics")
        total_pnl_a = sum(t.get("pnl", 0) for t in all_closed)
        wins_a      = [t for t in all_closed if t.get("pnl", 0) > 0]
        losses_a    = [t for t in all_closed if t.get("pnl", 0) < 0]
        wr_a        = len(wins_a) / len(all_closed) * 100
        avg_win_a   = np.mean([t["pnl"] for t in wins_a])   if wins_a   else 0
        avg_los_a   = np.mean([t["pnl"] for t in losses_a]) if losses_a else 0
        pf_a        = abs(avg_win_a / (avg_los_a + 0.01))
        max_win     = max((t["pnl"] for t in all_closed), default=0)
        max_loss    = min((t["pnl"] for t in all_closed), default=0)
        gross_profit = sum(t["pnl"] for t in wins_a)
        gross_loss   = abs(sum(t["pnl"] for t in losses_a))
        total_charges = sum(t.get("brokerage", 0) for t in all_closed)

        pnl_series  = np.cumsum([t.get("pnl", 0) for t in all_closed])
        rolling_max = np.maximum.accumulate(pnl_series)
        drawdown    = pnl_series - rolling_max
        max_dd      = float(np.min(drawdown))

        am = st.columns(4)
        am[0].markdown(metric_card(f"₹{total_pnl_a:+,.0f}", "Total Net P&L",  "var(--green3)" if total_pnl_a >= 0 else "var(--red3)"), unsafe_allow_html=True)
        am[1].markdown(metric_card(f"{wr_a:.1f}%",           "Win Rate",        "var(--teal)"),   unsafe_allow_html=True)
        am[2].markdown(metric_card(f"{pf_a:.2f}x",           "Profit Factor",   "var(--gold3)"),   unsafe_allow_html=True)
        am[3].markdown(metric_card(f"₹{max_dd:,.0f}",        "Max Drawdown",    "var(--red3)"),    unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        am2 = st.columns(4)
        am2[0].markdown(metric_card(f"₹{avg_win_a:,.0f}",    "Avg Win",         "var(--green3)"),  unsafe_allow_html=True)
        am2[1].markdown(metric_card(f"₹{avg_los_a:,.0f}",    "Avg Loss",        "var(--red3)"),    unsafe_allow_html=True)
        am2[2].markdown(metric_card(f"₹{max_win:,.0f}",      "Best Trade",      "var(--teal)"),   unsafe_allow_html=True)
        am2[3].markdown(metric_card(f"₹{total_charges:,.0f}","Total Charges",   "var(--muted)"),  unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        st.markdown("### 📈 Equity Curve")
        fig_eq = go.Figure()
        fig_eq.add_trace(go.Scatter(
            y=pnl_series, mode="lines", name="Equity",
            line=dict(color="#f5a623", width=2.5),
            fill="tozeroy", fillcolor="rgba(245,166,35,0.06)",
        ))
        fig_eq.add_trace(go.Scatter(
            y=rolling_max, mode="lines", name="Peak",
            line=dict(color="#00e5ff", width=1, dash="dash"),
        ))
        fig_eq.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.15)")
        fig_eq.update_layout(
            title="Equity Curve (Cumulative P&L)",
            paper_bgcolor="#050A14", plot_bgcolor="#050A14",
            font=dict(color="#94A3B8"), height=300,
            margin=dict(l=40, r=20, t=30, b=20),
            xaxis=dict(gridcolor="#1a2d45"), yaxis=dict(gridcolor="#1a2d45"),
            legend=dict(font=dict(color="#94A3B8")),
        )
        st.plotly_chart(fig_eq, use_container_width=True)

        fig_dd = go.Figure()
        fig_dd.add_trace(go.Scatter(
            y=drawdown, mode="lines", fill="tozeroy",
            fillcolor="rgba(255,23,68,0.1)", line=dict(color="#ff1744", width=1.5),
        ))
        fig_dd.update_layout(
            title="Drawdown",
            paper_bgcolor="#050A14", plot_bgcolor="#050A14",
            font=dict(color="#94A3B8"), height=200,
            margin=dict(l=40, r=20, t=30, b=20),
            xaxis=dict(gridcolor="#1a2d45"), yaxis=dict(gridcolor="#1a2d45"),
        )
        st.plotly_chart(fig_dd, use_container_width=True)

        st.markdown("### 📊 P&L Distribution")
        pnl_vals = [t["pnl"] for t in all_closed]
        fig_dist = go.Figure()
        fig_dist.add_trace(go.Histogram(
            x=pnl_vals, nbinsx=30,
            marker_color="#00e5ff",
            opacity=0.75, name="P&L Distribution",
        ))
        # FIX: add_vline takes x= (vertical line), not y=
        fig_dist.add_vline(x=0, line_dash="dash", line_color="rgba(255,255,255,0.3)")
        fig_dist.update_layout(
            title="P&L Distribution Histogram",
            paper_bgcolor="#050A14", plot_bgcolor="#050A14",
            font=dict(color="#94A3B8"), height=260,
            margin=dict(l=40, r=20, t=30, b=20),
            xaxis=dict(gridcolor="#1a2d45"), yaxis=dict(gridcolor="#1a2d45"),
        )
        st.plotly_chart(fig_dist, use_container_width=True)

        st.markdown("### 🎯 Signal Strength vs P&L")
        str_vals = [t.get("strength", 50) for t in all_closed]
        pnl_col  = ["#00e676" if p >= 0 else "#ff1744" for p in pnl_vals]
        fig_sc   = go.Figure()
        fig_sc.add_trace(go.Scatter(
            x=str_vals, y=pnl_vals, mode="markers",
            marker=dict(color=pnl_col, size=9, opacity=0.8),
            text=[t.get("symbol", "") for t in all_closed],
            hovertemplate="<b>%{text}</b><br>Str: %{x}%<br>P&L: ₹%{y:,.0f}",
        ))
        fig_sc.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.2)")
        fig_sc.update_layout(
            title="Signal Strength vs P&L",
            paper_bgcolor="#050A14", plot_bgcolor="#050A14",
            font=dict(color="#94A3B8"), height=280,
            margin=dict(l=40, r=20, t=30, b=20),
            xaxis=dict(title="Signal Strength %", gridcolor="#1a2d45"),
            yaxis=dict(title="Net P&L (₹)",       gridcolor="#1a2d45"),
        )
        st.plotly_chart(fig_sc, use_container_width=True)

        col_w, col_l = st.columns(2)
        with col_w:
            st.markdown("#### 🏆 Top 10 Winners")
            for t in sorted(all_closed, key=lambda x: -x.get("pnl",0))[:10]:
                sym = t.get("symbol","") or f"{t.get('index','')}{t.get('strike','')}{t.get('type','')}"
                st.markdown(
                    f'<div class="jrnl-row"><span style="font-family:JetBrains Mono;font-size:0.8rem;">'
                    f'{sym.replace(".NS","")}</span>'
                    f'<span class="pnl-pos">₹{t.get("pnl",0):+,.0f}</span></div>',
                    unsafe_allow_html=True,
                )
        with col_l:
            st.markdown("#### ❌ Top 10 Losers")
            for t in sorted(all_closed, key=lambda x: x.get("pnl",0))[:10]:
                sym = t.get("symbol","") or f"{t.get('index','')}{t.get('strike','')}{t.get('type','')}"
                st.markdown(
                    f'<div class="jrnl-row"><span style="font-family:JetBrains Mono;font-size:0.8rem;">'
                    f'{sym.replace(".NS","")}</span>'
                    f'<span class="pnl-neg">₹{t.get("pnl",0):+,.0f}</span></div>',
                    unsafe_allow_html=True,
                )

        st.markdown("### 🔥 Streak Analysis")
        wins_seq = [t.get("pnl", 0) > 0 for t in all_closed]
        max_w = max_l = cur_w = cur_l = 0
        for w in wins_seq:
            if w:
                cur_w += 1; cur_l = 0
            else:
                cur_l += 1; cur_w = 0
            max_w = max(max_w, cur_w)
            max_l = max(max_l, cur_l)
        sc = st.columns(4)
        sc[0].markdown(metric_card(max_w,                  "Max Win Streak",  "var(--green3)"),   unsafe_allow_html=True)
        sc[1].markdown(metric_card(max_l,                  "Max Loss Streak", "var(--red3)"),     unsafe_allow_html=True)
        sc[2].markdown(metric_card(f"₹{gross_profit:,.0f}","Gross Profit",    "var(--teal)"),    unsafe_allow_html=True)
        sc[3].markdown(metric_card(f"₹{gross_loss:,.0f}",  "Gross Loss",      "var(--orange)"),  unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            f'<div class="info-b">🧮 <b>Kelly Criterion Win Rate: '
            f'{st.session_state.kelly_wr*100:.1f}%</b> (from {len(all_closed)} trades) — '
            f'Used for dynamic position sizing across all segments.<br>'
            f'Avg Win/Loss Ratio: {pf_a:.2f}x | Expected Value per trade: '
            f'₹{total_pnl_a/max(len(all_closed),1):,.0f}</div>',
            unsafe_allow_html=True,
        )

        # ── Daily P&L Goal Tracker ─────────────────────────────────────────────
        st.markdown("### 🎯 Daily P&L Goal Tracker")
        if "date" in adf.columns and len(adf) > 0:
            daily_g = adf.groupby("date")["pnl"].agg(["sum","count","mean"]).reset_index()
            daily_g.columns = ["date","total_pnl","trades","avg_pnl"]
            daily_g["goal"] = float(st.session_state.daily_goal)
            daily_g["goal_pct"] = (daily_g["total_pnl"] / daily_g["goal"] * 100).clip(-200, 300)
            daily_g["beat_goal"] = daily_g["total_pnl"] >= daily_g["goal"]

            dg_cols = st.columns(4)
            days_with_data = len(daily_g)
            days_beat_goal = len(daily_g[daily_g["beat_goal"]])
            best_day  = daily_g["total_pnl"].max() if len(daily_g) > 0 else 0
            worst_day = daily_g["total_pnl"].min() if len(daily_g) > 0 else 0
            dg_cols[0].markdown(metric_card(days_with_data,          "Trading Days",    "var(--accent2)"), unsafe_allow_html=True)
            dg_cols[1].markdown(metric_card(days_beat_goal,          "Days Beat Goal",  "var(--green3)"),  unsafe_allow_html=True)
            dg_cols[2].markdown(metric_card(f"₹{best_day:+,.0f}",   "Best Day",        "var(--teal)"),    unsafe_allow_html=True)
            dg_cols[3].markdown(metric_card(f"₹{worst_day:+,.0f}",  "Worst Day",       "var(--red3)"),    unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            fig_dg = go.Figure()
            bar_colors = [
                "#10B981" if v >= float(st.session_state.daily_goal)
                else ("#F59E0B" if v >= 0 else "#EF4444")
                for v in daily_g["total_pnl"]
            ]
            fig_dg.add_trace(go.Bar(
                x=daily_g["date"], y=daily_g["total_pnl"],
                marker_color=bar_colors, name="Daily P&L",
                text=[f"₹{v:+,.0f}" for v in daily_g["total_pnl"]],
                textposition="outside", textfont=dict(size=9),
            ))
            fig_dg.add_hline(
                y=float(st.session_state.daily_goal),
                line_dash="dot", line_color="#F59E0B",
                annotation_text=f"Goal ₹{float(st.session_state.daily_goal):,.0f}",
                annotation_font_color="#F59E0B",
            )
            fig_dg.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.15)")
            fig_dg.update_layout(
                title="Daily P&L vs Goal",
                paper_bgcolor="#050A14", plot_bgcolor="#050A14",
                font=dict(color="#94A3B8"), height=300,
                margin=dict(l=40, r=20, t=40, b=20),
                xaxis=dict(gridcolor="#1a2d45"),
                yaxis=dict(gridcolor="#1a2d45", title="Net P&L (₹)"),
                showlegend=False,
            )
            st.plotly_chart(fig_dg, use_container_width=True)

        # ── Segment-wise P&L breakdown ─────────────────────────────────────────
        st.markdown("### 📦 Segment P&L Breakdown")
        seg_data = {
            "Equity":  sum(t.get("pnl",0) for t in st.session_state.eq_history),
            "Options": sum(t.get("pnl",0) for t in st.session_state.opt_history),
            "Futures": sum(t.get("pnl",0) for t in st.session_state.fut_history),
            "ETF":     sum(t.get("pnl",0) for t in st.session_state.etf_history),
            "MCX":     sum(t.get("pnl",0) for t in st.session_state.mcx_history),
        }
        seg_data = {k: v for k, v in seg_data.items() if v != 0}
        if seg_data:
            seg_colors = ["#10B981" if v >= 0 else "#EF4444" for v in seg_data.values()]
            fig_seg = go.Figure(data=[go.Bar(
                x=list(seg_data.keys()), y=list(seg_data.values()),
                marker_color=seg_colors,
                text=[f"₹{v:+,.0f}" for v in seg_data.values()],
                textposition="outside",
            )])
            fig_seg.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.15)")
            fig_seg.update_layout(
                title="Realized P&L by Segment",
                paper_bgcolor="#050A14", plot_bgcolor="#050A14",
                font=dict(color="#94A3B8"), height=240,
                margin=dict(l=40, r=20, t=35, b=20),
                xaxis=dict(gridcolor="#1a2d45"),
                yaxis=dict(gridcolor="#1a2d45", title="Net P&L (₹)"),
            )
            st.plotly_chart(fig_seg, use_container_width=True)

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
st.markdown(f"""
<div style="background:var(--bg2);border-top:1px solid var(--border);padding:12px 20px;
text-align:center;font-family:'JetBrains Mono';font-size:0.62rem;color:var(--muted);">
    ProTrader Terminal v6 · NSE · BSE · Options · Futures · Auto AI Trading ·
    12s Live Price Refresh · Data via Neon/PostgreSQL ·
    Refreshed {datetime.now().strftime('%H:%M:%S')} ·
    <span style="color:var(--red3);">⚠ Educational simulator — not investment advice</span>
</div>""", unsafe_allow_html=True)

# ── Auto-save on every render ──────────────────────────────────────────────────
save_all()

if any([
    st.session_state.get("auto_eq"), st.session_state.get("auto_opt"), st.session_state.get("auto_fut"),
    st.session_state.get("auto_etf"), st.session_state.get("auto_mcx"),
    st.session_state.get("eq_portfolio"), st.session_state.get("opt_portfolio"),
    st.session_state.get("fut_portfolio"), st.session_state.get("etf_portfolio"),
    st.session_state.get("mcx_portfolio"),
]):
    time.sleep(12)   # v6: 12-second live price refresh for all open positions
    st.rerun()
