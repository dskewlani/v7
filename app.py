"""
app.py — ProTrader Terminal v7 — Main Application
==================================================
Integrates all 14 Enhancement Blocks:
  • Tabs: Equity | Options | Futures | ETF | MCX | Auto-Trade | Analytics | Journal | Settings
  • 12-second live refresh loop
  • Multi-user login (Block 7d)
  • Bloomberg dark/light theme (Block 4a/5a)
  • Command palette Ctrl+K (Block 4c)
  • Toast notifications (Block 4d)
  • Candlestick charts (Block 4e)
  • Options chain table (Block 4f)
  • Monte Carlo (Block 6a)
  • AI Journal Review (Block 6c)
  • Daily P&L heatmap (Block 6d)
  • Portfolio treemap (Block 6e)
  • VaR display (Block 6f)
  • Price alerts (Block 8a-8d)
  • Max pain / PCR (Block 9a/9b)
  • Options strategy builder (Block 9d)
  • Theta decay calendar (Block 9e)
  • Backtester (Block 10a/10b/10c)
  • Break-even stop manager (Block 11b)
  • FII/DII dashboard (Block 12a)
  • Economic calendar (Block 12c)
  • Global market panel (Block 12d)
  • Screenshot/notes (Block 13a)
  • Journal patterns (Block 13b)
  • Weekly report generator (Block 13c)
  • Behavioral bias detector (Block 13d)
  • Log dashboard (Block 14c)
  • Data export/import (Block 14d)
"""

import time
import math
import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

import engine as eng
import storage as db
import ui

# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ProTrader Terminal v7",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def ss(key, default=None):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def _load_user_state(uid: str) -> None:
    """Load all portfolios + settings for logged-in user."""
    db.set_current_user(uid)
    for key, default in [
        ("eq_portfolio",  []),("opt_portfolio",[]),("fut_portfolio",[]),
        ("etf_portfolio", []),("mcx_portfolio",[]),
        ("eq_history",    []),("opt_history",  []),("fut_history",   []),
        ("etf_history",   []),("mcx_history",  []),
        ("watchlists",    {}),("active_watchlist",""),
        ("daily_goal",    5000),("daily_loss_limit",-3000),
        ("auto_trading",  False),("universe","Nifty 50"),
        ("theme", db.load("ui_theme", "dark")),
        ("density", db.load("ui_density", "comfortable")),
        ("palette", db.load("ui_palette", "Blue")),
    ]:
        if key not in st.session_state:
            st.session_state[key] = db.load(key, default)


def _save_portfolio(key: str) -> None:
    db.save(key, st.session_state.get(key, []))


def _today_trades(history_keys: list) -> list:
    today_str = date.today().isoformat()
    trades = []
    for k in history_keys:
        for t in st.session_state.get(k, []):
            if str(t.get("exit_time", t.get("date", "")))[:10] == today_str:
                trades.append(t)
    return trades


def _all_history() -> list:
    return sum([
        st.session_state.get("eq_history",  []),
        st.session_state.get("opt_history", []),
        st.session_state.get("fut_history", []),
        st.session_state.get("etf_history", []),
        st.session_state.get("mcx_history", []),
    ], [])


def _all_open() -> list:
    return sum([
        st.session_state.get("eq_portfolio",  []),
        st.session_state.get("opt_portfolio", []),
        st.session_state.get("fut_portfolio", []),
        st.session_state.get("etf_portfolio", []),
        st.session_state.get("mcx_portfolio", []),
    ], [])


def _get_indices() -> dict:
    cache = st.session_state.get("_indices_cache")
    if cache and time.time() - cache["ts"] < 15:
        return cache["data"]
    data = eng.get_all_indices()
    st.session_state["_indices_cache"] = {"data": data, "ts": time.time()}
    return data


def _get_vix() -> float:
    return _get_indices().get("VIX", {}).get("p", 15.0) or 15.0


def _market_mood() -> str:
    idx = _get_indices()
    nf_pct = idx.get("NF", {}).get("pct", 0)
    vix    = _get_vix()
    if vix > 22 or nf_pct < -1.0:  return "BEARISH"
    if nf_pct > 0.5:                 return "BULLISH"
    return "NEUTRAL"


def _update_open_positions(portfolio_key: str, segment: str = "equity") -> None:
    """Update CMP, P&L, trailing stop for all open positions."""
    port = st.session_state.get(portfolio_key, [])
    changed = False
    for pos in port:
        lp = eng.get_live_price(pos["symbol"])
        if lp and lp > 0:
            pos["cmp"] = lp
            entry = pos.get("entry", lp)
            qty   = pos.get("qty", pos.get("lots", 1))
            lot_size = pos.get("lot_size", 1)
            pnl_qty  = qty * lot_size
            if pos.get("type") in ("BUY", "CE", "LONG"):
                pos["pnl"] = round((lp - entry) * pnl_qty - pos.get("brokerage", 0), 2)
            else:
                pos["pnl"] = round((entry - lp) * pnl_qty - pos.get("brokerage", 0), 2)
            pos = eng.update_trailing_stop(pos, lp)
            changed = True
    if changed:
        _save_portfolio(portfolio_key)


def _check_alerts(prices: dict) -> None:
    """Block 8a: Check price alerts and fire toasts."""
    alerts = db.get_active_alerts()
    for alert in alerts:
        sym   = alert["symbol"]
        atype = alert["alert_type"]
        tgt   = alert["target_price"]
        p     = (prices.get(sym) or {}).get("p") or eng.get_live_price(sym)
        if not p:
            continue
        triggered = False
        if atype == "ABOVE" and p >= tgt:     triggered = True
        elif atype == "BELOW" and p <= tgt:   triggered = True
        elif atype == "PCT_UP":
            prev = (prices.get(sym) or {}).get("prev", p)
            if prev and ((p - prev) / prev * 100) >= tgt: triggered = True
        if triggered:
            db.mark_alert_triggered(alert["id"])
            ui.show_toast_js(f"🔔 {sym} alert: {atype} ₹{tgt:,.2f}", "warn")


def _check_milestone_alerts(stats: dict) -> None:
    """Block 8c: Daily goal milestone toasts (25/50/75/100/125%)."""
    pct = stats.get("goal_pct", 0)
    key = "milestone_fired"
    fired: set = st.session_state.get(key, set())
    milestones = {25: "🎯 25% of daily goal reached!", 50: "💪 Half-way to daily goal!",
                  75: "🔥 75% — almost there!", 100: "🏆 Daily goal achieved!",
                  125: "⭐ 125% — outstanding day!"}
    for pct_thresh, msg in milestones.items():
        if pct >= pct_thresh and pct_thresh not in fired:
            ui.show_toast_js(msg, "buy")
            fired.add(pct_thresh)
    st.session_state[key] = fired


def _drawdown_warning(daily_pnl: float, limit: float) -> None:
    """Block 8d: Warn at 60% of daily loss limit."""
    if limit < 0 and daily_pnl < limit * 0.6 and not st.session_state.get("dd_warn_fired"):
        ui.show_toast_js("⚠️ 60% of daily loss limit reached — consider reducing size", "warn")
        st.session_state["dd_warn_fired"] = True


# ─── Login Screen (Block 7d) ──────────────────────────────────────────────────

def render_login() -> bool:
    """Render login/register screen. Returns True if authenticated."""
    if st.session_state.get("logged_in"):
        return True

    st.markdown("""
    <div style="max-width:400px;margin:80px auto;text-align:center">
      <div style="font-size:36px;margin-bottom:8px">📈</div>
      <h1 style="font-family:var(--f-head);font-size:28px;color:var(--tx);margin-bottom:4px">
        ProTrader Terminal
      </h1>
      <p style="color:var(--tx3);font-size:14px">v7 · All 14 Blocks</p>
    </div>""", unsafe_allow_html=True)

    tab_login, tab_reg = st.tabs(["Login", "Register"])

    with tab_login:
        username = st.text_input("Username", key="login_user", placeholder="your username")
        pin      = st.text_input("4-digit PIN", type="password", key="login_pin", placeholder="••••")
        if st.button("Login →", key="btn_login"):
            uid = db.verify_user(username, pin)
            if uid:
                st.session_state["logged_in"]   = True
                st.session_state["current_user"] = uid
                _load_user_state(uid)
                st.rerun()
            else:
                st.error("Invalid username or PIN")

    with tab_reg:
        r_user = st.text_input("Choose username", key="reg_user")
        r_pin  = st.text_input("Choose 4-digit PIN", type="password", key="reg_pin")
        if st.button("Create Account →", key="btn_reg"):
            if len(r_pin) < 4:
                st.error("PIN must be at least 4 digits")
            elif db.create_user(r_user, r_pin):
                st.success("Account created! Please login.")
            else:
                st.error("Username already taken")
    return False


# ─── Header ───────────────────────────────────────────────────────────────────

def render_header() -> None:
    """Render top header with ticker bar and controls."""
    indices = _get_indices()
    st.markdown(ui.ticker_bar(indices), unsafe_allow_html=True)

    col1, col2, col3, col4 = st.columns([4, 1, 1, 1])
    with col1:
        vix  = _get_vix()
        mood = _market_mood()
        mood_col = {"BULLISH": "var(--green)", "BEARISH": "var(--red)", "NEUTRAL": "var(--tx3)"}[mood]
        regime_info = eng.classify_regime(
            adx=20, bb_width=0.03, vix=vix
        )
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:12px;padding:6px 0">'
            f'<span class="pt-h1" style="margin:0">ProTrader v7</span>'
            f'<span class="badge badge-gold"><span class="blink">●</span> LIVE</span>'
            f'<span style="color:{mood_col};font-size:13px;font-weight:600">{mood}</span>'
            f'{ui.regime_badge(regime_info["regime"])}'
            f'<span style="font-size:12px;color:var(--tx3)">VIX {vix:.1f}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with col2:
        ui.theme_toggle_button()
    with col3:
        ui.density_toggle()
    with col4:
        user = st.session_state.get("current_user", "default")
        if st.button(f"👤 {user}", key="logout_btn"):
            st.session_state["logged_in"] = False
            st.rerun()


# ─── Candlestick Chart (Block 4e) ─────────────────────────────────────────────

def render_candlestick(symbol: str, period: str = "3mo", entry: float = None,
                       sl: float = None, target: float = None) -> None:
    """Block 4e: Full candlestick chart with EMA/VWAP/BB/SuperTrend overlays."""
    df = eng.get_ohlcv(symbol, period, "1d")
    if df is None or len(df) < 10:
        st.warning("Insufficient OHLCV data")
        return
    ind = eng.compute_indicators(df)
    ct  = ui.get_chart_theme()

    fig = go.Figure()

    # Candlestick
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        name=symbol, increasing_line_color="var(--green, #22C55E)",
        decreasing_line_color="var(--red, #EF4444)",
    ))

    # Toggleable overlays
    c = df["Close"]
    for span, col, name in [(9,"#3B82F6","EMA9"),(21,"#F59E0B","EMA21"),(50,"#A855F7","EMA50")]:
        ema = c.ewm(span=span, adjust=False).mean()
        fig.add_trace(go.Scatter(x=df.index, y=ema, name=name,
                                  line=dict(color=col, width=1.5), visible="legendonly"))

    # Bollinger Bands
    s20  = c.rolling(20).mean()
    sd20 = c.rolling(20).std()
    for band, bname, col in [(s20+2*sd20,"BB Upper","#22C55E40"),
                              (s20,"BB Mid","#FFFFFF40"),
                              (s20-2*sd20,"BB Lower","#22C55E40")]:
        fig.add_trace(go.Scatter(x=df.index, y=band, name=bname,
                                  line=dict(color=col, width=1, dash="dot"), visible="legendonly"))

    # VWAP
    vwap_data = eng.compute_vwap_bands(df)
    if vwap_data.get("vwap"):
        vwap_line = [vwap_data["vwap"]] * len(df)
        fig.add_trace(go.Scatter(x=df.index, y=vwap_line, name="VWAP",
                                  line=dict(color="#14B8A6", width=1.5, dash="dash"), visible="legendonly"))

    # Entry / SL / Target horizontal lines
    for price, color, label in [(entry, "#3B82F6", "Entry"), (sl, "#EF4444", "SL"), (target, "#22C55E", "Target")]:
        if price and price > 0:
            fig.add_hline(y=price, line_color=color, line_dash="dash", line_width=1.5,
                          annotation_text=f"  {label} ₹{price:,.2f}",
                          annotation_font=dict(color=color, size=11))

    fig.update_layout(
        paper_bgcolor=ct["paper_bgcolor"], plot_bgcolor=ct["plot_bgcolor"],
        font_color=ct["font_color"],
        height=400, margin=dict(t=30, b=30, l=10, r=10),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(gridcolor=ct["grid_color"], showgrid=True),
        yaxis=dict(gridcolor=ct["grid_color"], showgrid=True, side="right"),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ─── Options Chain Table (Block 4f) ──────────────────────────────────────────

def render_options_chain(index: str = "NIFTY", expiry_date: date = None) -> None:
    """Block 4f / 9a / 9b: NSE-style options chain with max pain + PCR."""
    vix = _get_vix()
    exp = expiry_date or (date.today() + timedelta(days=7 - date.today().weekday()))
    indices = _get_indices()
    spot = indices.get("BN" if index == "BANKNIFTY" else "NF", {}).get("p", 0)
    if not spot or spot <= 0:
        st.warning("Spot price unavailable")
        return

    chain = eng.build_chain(index, spot, exp, vix, n_strikes=10)
    tick  = 100 if index == "BANKNIFTY" else 50
    atm   = round(spot / tick) * tick

    # PCR calculation from chain
    pcr_data  = eng.compute_pcr(chain)
    pain_data = eng.compute_max_pain(chain, spot, index)
    iv_pct    = eng.compute_iv_percentile(vix)

    col1, col2, col3, col4 = st.columns(4)
    with col1: st.metric("Spot", f"₹{spot:,.2f}")
    with col2:
        pcr_color = "normal" if pcr_data["pcr"] > 0.9 else "inverse"
        st.metric("PCR", f"{pcr_data['pcr']:.3f}", pcr_data["sentiment"])
    with col3: st.metric("Max Pain", f"₹{pain_data['max_pain']:,.0f}", f"{pain_data['vs_spot_pct']:+.2f}% vs spot")
    with col4: st.metric("IV Rank", f"{iv_pct:.0f}%", "Expensive" if iv_pct > 60 else "Cheap" if iv_pct < 30 else "Fair")

    rows = []
    for row in chain:
        K = row["strike"]
        is_atm = K == atm
        rows.append({
            "CE Signal":  (row["ce_signal"] or {}).get("signal", ""),
            "CE Price":   f"₹{row['ce_price']:.2f}",
            "CE Delta":   f"{row['ce_delta']:.2f}",
            "CE θ/day":   f"₹{row['ce_theta']:.2f}",
            "Strike":     f"{'→ ' if is_atm else ''}{K:,}{'  ATM' if is_atm else ''}",
            "PE θ/day":   f"₹{row['pe_theta']:.2f}",
            "PE Delta":   f"{row['pe_delta']:.2f}",
            "PE Price":   f"₹{row['pe_price']:.2f}",
            "PE Signal":  (row["pe_signal"] or {}).get("signal", ""),
            "_atm":       is_atm,
        })

    # Render as styled HTML table
    header = ["CE Signal","CE Price","CE Delta","CE θ/day","Strike","PE θ/day","PE Delta","PE Price","PE Signal"]
    html = '<table class="chain-table"><thead><tr>'
    html += "".join(f"<th>{h}</th>" for h in header)
    html += "</tr></thead><tbody>"
    for r in rows:
        cls = ' class="atm"' if r["_atm"] else ""
        html += f"<tr{cls}>"
        for h in header:
            v = r[h]
            if h in ("CE Signal","PE Signal") and v:
                c = "var(--green)" if "BUY" in v else ("var(--red)" if "AVOID" in v else "var(--tx3)")
                v = f'<span style="color:{c};font-size:11px;font-weight:600">{v}</span>'
            html += f"<td>{v}</td>"
        html += "</tr>"
    html += "</tbody></table>"
    st.markdown(html, unsafe_allow_html=True)


# ─── Analytics Tab Helpers ────────────────────────────────────────────────────

def render_monte_carlo(history: list) -> None:
    """Block 6a: Monte Carlo portfolio simulator."""
    if len(history) < 10:
        st.info("Need ≥10 closed trades for Monte Carlo simulation")
        return
    wins  = [t for t in history if t.get("pnl", 0) > 0]
    loss  = [t for t in history if t.get("pnl", 0) <= 0]
    wr    = len(wins) / len(history)
    aw    = sum(t["pnl"] for t in wins) / max(len(wins), 1)
    al    = abs(sum(t["pnl"] for t in loss) / max(len(loss), 1))
    cap   = st.session_state.get("capital", 500000)

    with st.spinner("Running 1000 Monte Carlo simulations..."):
        mc = eng.run_monte_carlo(wr, aw, al, cap, n_days=30, n_sims=1000)

    ct = ui.get_chart_theme()
    fig = go.Figure()
    for pct, color, name, dash in [(mc["p90"],"#22C55E","P90 Bull","dash"),
                                    (mc["p50"],"#3B82F6","P50 Base","solid"),
                                    (mc["p10"],"#EF4444","P10 Bear","dash")]:
        if pct:
            fig.add_trace(go.Scatter(x=mc["days"], y=pct, name=name,
                                      line=dict(color=color, dash=dash, width=2)))
    fig.update_layout(paper_bgcolor=ct["paper_bgcolor"], plot_bgcolor=ct["plot_bgcolor"],
                      font_color=ct["font_color"], height=320,
                      margin=dict(t=20,b=30,l=10,r=10),
                      yaxis=dict(title="Portfolio Value (₹)", gridcolor=ct["grid_color"]),
                      xaxis=dict(title="Trading Days", gridcolor=ct["grid_color"]))
    st.plotly_chart(fig, use_container_width=True)

    col1, col2, col3, col4 = st.columns(4)
    with col1: st.metric("Prob Profit", f"{mc['prob_profit']:.0f}%")
    with col2: st.metric("P50 Final", f"₹{mc['final_p50']:,.0f}")
    with col3: st.metric("P90 Bull",  f"₹{mc['final_p90']:,.0f}")
    with col4: st.metric("P10 Bear",  f"₹{mc['final_p10']:,.0f}")


def render_risk_metrics(history: list) -> None:
    """Block 6b: Sharpe / Sortino / Calmar metrics."""
    if len(history) < 5:
        st.info("Need ≥5 trades for risk metrics")
        return
    rm = eng.compute_risk_metrics(history)
    col1, col2, col3, col4, col5 = st.columns(5)
    metrics = [
        ("Sharpe",   rm.get("sharpe",   0), ">1.0 good"),
        ("Sortino",  rm.get("sortino",  0), ">1.5 good"),
        ("Calmar",   rm.get("calmar",   0), ">0.5 good"),
        ("Max DD",   rm.get("max_drawdown", 0), "₹"),
        ("Win Rate", rm.get("win_rate", 0), "%"),
    ]
    for col, (label, val, hint) in zip([col1,col2,col3,col4,col5], metrics):
        with col:
            if hint == "₹":
                st.metric(label, f"₹{val:,.0f}")
            elif hint == "%":
                st.metric(label, f"{val:.1f}%")
            else:
                color = "normal" if val >= float(hint.split(">")[1].split(" ")[0]) else "inverse"
                st.metric(label, f"{val:.3f}", hint)


def render_pnl_heatmap(uid: str) -> None:
    """Block 6d: GitHub-style P&L heatmap calendar."""
    daily = db.get_daily_pnl_history(uid, days=90)
    if not daily:
        st.info("No daily P&L history yet")
        return
    df = pd.DataFrame(daily)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    # Build a weeks × days matrix
    min_dt = df.index.min(); max_dt = df.index.max()
    all_days = pd.date_range(min_dt, max_dt)
    z_val = [df.loc[d, "pnl"] if d in df.index else 0 for d in all_days]

    fig = go.Figure(go.Heatmap(
        x=[d.strftime("%b %d") for d in all_days],
        y=["P&L"] * len(all_days),
        z=[z_val],
        colorscale=[[0,"#7F1D1D"],[0.5,"#1E293B"],[1,"#14532D"]],
        zmid=0, showscale=True,
        hovertemplate="%{x}: ₹%{z:,.0f}<extra></extra>",
    ))
    ct = ui.get_chart_theme()
    fig.update_layout(paper_bgcolor=ct["paper_bgcolor"], plot_bgcolor=ct["plot_bgcolor"],
                      font_color=ct["font_color"], height=120,
                      margin=dict(t=10,b=30,l=10,r=10),
                      yaxis=dict(showticklabels=False))
    st.plotly_chart(fig, use_container_width=True)


def render_portfolio_treemap() -> None:
    """Block 6e: Portfolio heat treemap."""
    all_pos = _all_open()
    if not all_pos:
        st.info("No open positions")
        return
    labels, parents, values, colors, text = [], [], [], [], []
    sectors = {}
    for p in all_pos:
        sym = p.get("symbol","")
        sector = eng.SECTOR_MAP.get(sym, "Other")
        pnl    = p.get("pnl", 0)
        val    = abs(p.get("entry",0) * p.get("qty",1))
        sectors.setdefault(sector, {"val":0,"pnl":0})
        sectors[sector]["val"] += val
        sectors[sector]["pnl"] += pnl
        labels.append(sym); parents.append(sector)
        values.append(val); colors.append(pnl)
        text.append(f"{sym}<br>₹{pnl:+,.0f}")
    for sector, d in sectors.items():
        labels.append(sector); parents.append("")
        values.append(d["val"]); colors.append(d["pnl"])
        text.append(f"{sector}<br>₹{d['pnl']:+,.0f}")

    fig = go.Figure(go.Treemap(
        labels=labels, parents=parents, values=values,
        text=text, textinfo="text",
        marker=dict(
            colors=colors,
            colorscale=[[0,"#7F1D1D"],[0.5,"#1E2840"],[1,"#14532D"]],
            cmid=0, showscale=True,
        ),
    ))
    ct = ui.get_chart_theme()
    fig.update_layout(paper_bgcolor=ct["paper_bgcolor"], font_color=ct["font_color"],
                      height=360, margin=dict(t=10,b=10,l=10,r=10))
    st.plotly_chart(fig, use_container_width=True)


def render_ai_journal_review(history: list) -> None:
    """Block 6c: AI trade journal review via Claude API."""
    if len(history) < 5:
        st.info("Need ≥5 trades for AI review")
        return
    if st.button("🤖 Generate AI Trade Review", key="ai_review_btn"):
        recent = sorted(history, key=lambda t: str(t.get("exit_time",""))[:10], reverse=True)[:50]
        summary = [{"sym": t.get("symbol",""), "pnl": t.get("pnl",0),
                    "rec": t.get("rec",""), "str": t.get("strength",0),
                    "date": str(t.get("exit_time",""))[:10]} for t in recent]
        prompt = f"""Analyze these {len(summary)} recent trades from a ProTrader Terminal user.
Trades (most recent first): {json.dumps(summary, indent=2)}

Provide:
1. Top 3 strengths in their trading
2. Top 3 weaknesses or biases detected
3. Specific actionable improvements
4. Which signal strengths perform best for them
5. Best and worst days/patterns

Be specific and direct. Use ₹ for values. Keep total under 400 words."""

        try:
            import anthropic
            client = anthropic.Anthropic()
            with st.spinner("AI is analyzing your trades..."):
                msg = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=600,
                    messages=[{"role":"user","content":prompt}],
                )
            st.markdown(
                f'<div class="pt-card">{msg.content[0].text}</div>',
                unsafe_allow_html=True,
            )
        except Exception as exc:
            st.error(f"AI review failed: {exc}")


# ─── Backtester (Block 10a/10b/10c) ──────────────────────────────────────────

def render_backtester() -> None:
    """Block 10a/10b/10c: Strategy backtest + walk-forward optimization."""
    st.markdown(ui.section_header("Backtester", "Replay score_signal() on historical data", "⏪"), unsafe_allow_html=True)

    col1, col2, col3, col4 = st.columns(4)
    with col1: symbol  = st.text_input("Symbol", "RELIANCE.NS", key="bt_sym")
    with col2: period  = st.selectbox("Period", ["6mo","1y","2y","3y"], index=1, key="bt_period")
    with col3: mode    = st.selectbox("Mode", ["INTRADAY","DELIVERY"], key="bt_mode")
    with col4: min_str = st.slider("Min Strength", 50, 85, 62, key="bt_minstr")

    col_a, col_b = st.columns(2)
    with col_a: sl_mult  = st.slider("SL ATR Mult", 0.5, 3.0, 1.5, 0.5, key="bt_sl")
    with col_b: tgt_mult = st.slider("Target ATR Mult", 1.0, 4.0, 2.5, 0.5, key="bt_tgt")

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("▶ Run Backtest", key="run_bt"):
            with st.spinner(f"Backtesting {symbol} ({period})..."):
                result = eng.run_backtest(symbol, mode, period, sl_mult, tgt_mult, min_str)
            st.session_state["bt_result"] = result

    with c2:
        if st.button("🔬 Walk-Forward Optimize", key="run_wfo"):
            with st.spinner("Running walk-forward optimization (this may take ~30s)..."):
                wfo = eng.walk_forward_optimize(symbol, period, mode)
            st.session_state["wfo_result"] = wfo

    with c3:
        pass  # Strategy comparison placeholder

    result = st.session_state.get("bt_result")
    if result:
        if "error" in result:
            st.error(f"Backtest error: {result['error']}")
        else:
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            with col1: st.metric("Total Trades", result["total_trades"])
            with col2: st.metric("Win Rate", f"{result['win_rate']:.1f}%")
            with col3: st.metric("Total P&L", f"₹{result['total_pnl']:,.0f}")
            with col4: st.metric("Profit Factor", f"{result['profit_factor']:.2f}")
            with col5: st.metric("Max Drawdown", f"{result['max_drawdown']:.1f}%")
            with col6: st.metric("Sharpe", f"{result['sharpe']:.3f}")

            # Equity curve
            if result.get("equity_curve"):
                ct = ui.get_chart_theme()
                fig = go.Figure(go.Scatter(
                    y=result["equity_curve"], mode="lines",
                    line=dict(color="#3B82F6", width=2),
                    fill="tozeroy", fillcolor="rgba(59,130,246,0.08)",
                ))
                fig.update_layout(
                    paper_bgcolor=ct["paper_bgcolor"], plot_bgcolor=ct["plot_bgcolor"],
                    font_color=ct["font_color"], height=280,
                    margin=dict(t=10,b=30,l=10,r=10),
                    yaxis=dict(title="Portfolio ₹", gridcolor=ct["grid_color"]),
                    xaxis=dict(title="Days", gridcolor=ct["grid_color"]),
                )
                st.plotly_chart(fig, use_container_width=True)

    wfo = st.session_state.get("wfo_result")
    if wfo and wfo.get("best_params"):
        st.markdown(ui.section_header("Walk-Forward Results", icon="🔬"), unsafe_allow_html=True)
        st.success(f"Best params: {wfo['best_params']} | Metric: {wfo['best_metric']:.3f}")
        if wfo.get("all_results"):
            df_wfo = pd.DataFrame(wfo["all_results"])
            st.dataframe(df_wfo.style.background_gradient(subset=["metric"], cmap="RdYlGn"),
                         use_container_width=True)


# ─── Options Strategy Builder (Block 9d) ─────────────────────────────────────

def render_strategy_builder() -> None:
    """Block 9d: Options strategy builder with payoff diagram."""
    indices = _get_indices()
    vix = _get_vix()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        index_name = st.selectbox("Index", ["NIFTY","BANKNIFTY"], key="sb_idx")
    with col2:
        strategy = st.selectbox("Strategy",
                                ["Bull Call Spread","Bear Put Spread",
                                 "Iron Condor","Straddle","Strangle"],
                                key="sb_strat")
    with col3:
        days_out = st.slider("Days to Expiry", 1, 30, 7, key="sb_dte")
    with col4:
        lots = st.number_input("Lots", 1, 50, 1, key="sb_lots")

    spot_key = "BN" if index_name == "BANKNIFTY" else "NF"
    spot = indices.get(spot_key, {}).get("p", 0)
    if not spot:
        st.warning("Spot price unavailable"); return

    exp_date  = date.today() + timedelta(days=days_out)
    strat_def = eng.build_strategy(strategy, spot, vix, exp_date, index_name)
    if not strat_def:
        st.warning("Strategy build failed"); return

    col1, col2, col3 = st.columns(3)
    with col1:
        debit  = strat_def.get("net_debit", strat_def.get("net_credit", 0))
        label  = "Net Debit" if "net_debit" in strat_def else "Net Credit"
        st.metric(label, f"₹{debit:,.0f}")
    with col2:
        mp = strat_def.get("max_profit","")
        st.metric("Max Profit", f"₹{mp:,.0f}" if isinstance(mp, (int,float)) else str(mp))
    with col3:
        ml = strat_def.get("max_loss", 0)
        st.metric("Max Loss", f"₹{ml:,.0f}" if isinstance(ml, (int,float)) else str(ml))

    # Payoff diagram
    tick = 100 if index_name == "BANKNIFTY" else 50
    atm  = round(spot / tick) * tick
    x_range = np.linspace(atm * 0.92, atm * 1.08, 200)
    payoffs = []
    for s in x_range:
        pnl_total = 0
        for leg in strat_def.get("legs", []):
            K    = leg["strike"]; ltype = leg["type"]; action = leg["action"]; price = leg["price"]
            if ltype == "CE": intrinsic = max(0, s - K)
            else:             intrinsic = max(0, K - s)
            if action == "BUY":  pnl_total += (intrinsic - price) * strat_def["lot"] * lots
            else:                pnl_total -= (intrinsic - price) * strat_def["lot"] * lots
        payoffs.append(pnl_total)

    ct = ui.get_chart_theme()
    fig = go.Figure()
    fig.add_hline(y=0, line_color=ct["grid_color"], line_dash="dot")
    fig.add_vline(x=spot, line_color="#F59E0B", line_dash="dash",
                  annotation_text=f"  Spot {spot:,.0f}", annotation_font_color="#F59E0B")
    fig.add_trace(go.Scatter(
        x=x_range, y=payoffs, mode="lines",
        line=dict(color="#3B82F6", width=2.5),
        fill="tozeroy",
        fillcolor="rgba(34,197,94,0.1)" if max(payoffs) > 0 else "rgba(239,68,68,0.1)",
        name="Payoff at Expiry",
    ))
    fig.update_layout(
        paper_bgcolor=ct["paper_bgcolor"], plot_bgcolor=ct["plot_bgcolor"],
        font_color=ct["font_color"], height=300,
        margin=dict(t=20,b=30,l=10,r=10),
        xaxis=dict(title="Spot at Expiry", gridcolor=ct["grid_color"]),
        yaxis=dict(title="P&L (₹)", gridcolor=ct["grid_color"]),
    )
    st.plotly_chart(fig, use_container_width=True)


# ─── Theta Decay Calendar (Block 9e) ─────────────────────────────────────────

def render_theta_decay(opt_portfolio: list, vix: float) -> None:
    """Block 9e: Theta decay calendar per open options position."""
    if not opt_portfolio:
        st.info("No open options positions")
        return
    ct = ui.get_chart_theme()
    fig = go.Figure()
    for pos in opt_portfolio[:5]:
        sym   = pos.get("symbol","")
        entry = pos.get("entry", 0)
        dte   = pos.get("dte", 7)
        K     = pos.get("strike", 0)
        iv    = max(0.08, vix/100)
        days  = list(range(dte, 0, -1))
        prices = []
        for d in days:
            T = d / 365
            g = eng.bs_greeks(pos.get("spot", entry*1.01), K, T, 0.065, iv, pos.get("opt_type","CE"))
            prices.append(g.get("price", 0))
        if days and prices:
            fig.add_trace(go.Scatter(x=days, y=prices, mode="lines+markers",
                                      name=sym, line=dict(width=2)))

    fig.update_layout(
        paper_bgcolor=ct["paper_bgcolor"], plot_bgcolor=ct["plot_bgcolor"],
        font_color=ct["font_color"], height=280,
        margin=dict(t=20,b=30,l=10,r=10),
        xaxis=dict(title="Days to Expiry", gridcolor=ct["grid_color"], autorange="reversed"),
        yaxis=dict(title="Option Price (₹)", gridcolor=ct["grid_color"]),
    )
    st.plotly_chart(fig, use_container_width=True)


# ─── FII/DII Dashboard (Block 12a) ───────────────────────────────────────────

def render_fii_dii() -> None:
    """Block 12a: FII/DII net buy/sell 20-day chart."""
    # Fetch from NSE
    try:
        resp = eng._nse_fetch_with_retry(
            "https://www.nseindia.com/api/fiidiiTradeReact"
        )
        if resp:
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("data", [])
            if rows:
                df = pd.DataFrame(rows[:20])
                if "date" in df.columns and "fii_net" in df.columns:
                    ct = ui.get_chart_theme()
                    fig = go.Figure()
                    fii_colors = ["#22C55E" if v >= 0 else "#EF4444" for v in df["fii_net"]]
                    dii_colors = ["#3B82F6" if v >= 0 else "#F59E0B" for v in df.get("dii_net", [0]*len(df))]
                    fig.add_trace(go.Bar(x=df["date"], y=df["fii_net"], name="FII Net", marker_color=fii_colors))
                    if "dii_net" in df.columns:
                        fig.add_trace(go.Bar(x=df["date"], y=df["dii_net"], name="DII Net", marker_color=dii_colors))
                    fig.update_layout(
                        paper_bgcolor=ct["paper_bgcolor"], plot_bgcolor=ct["plot_bgcolor"],
                        font_color=ct["font_color"], barmode="group", height=280,
                        margin=dict(t=10,b=30,l=10,r=10),
                        yaxis=dict(title="Net (₹ Cr)", gridcolor=ct["grid_color"]),
                    )
                    st.plotly_chart(fig, use_container_width=True)
                    return
    except Exception:
        pass
    st.info("FII/DII data temporarily unavailable")


# ─── Global Markets Panel (Block 12d) ────────────────────────────────────────

def render_global_markets() -> None:
    """Block 12d: Global market correlation strip — SGX Nifty, Dow, S&P, DXY, Brent."""
    symbols = [("^GSPC","S&P 500"),("^DJI","Dow 30"),("GC=F","Gold"),("CL=F","Brent"),("DX-Y.NYB","DXY")]
    cols = st.columns(len(symbols))
    for col, (sym, label) in zip(cols, symbols):
        with col:
            try:
                price = eng.get_live_price(sym) or 0
                st.metric(label, f"{price:,.2f}")
            except Exception:
                st.metric(label, "N/A")


# ─── Journal Tab ─────────────────────────────────────────────────────────────

def render_journal() -> None:
    """Block 13a-13d: Trade journal with notes, patterns, reports, bias detector."""
    history = _all_history()
    st.markdown(ui.section_header("Trade Journal", f"{len(history)} trades", "📓"), unsafe_allow_html=True)

    j_tab1, j_tab2, j_tab3, j_tab4, j_tab5 = st.tabs(
        ["📋 History", "🔍 Patterns", "🧠 Bias Detector", "🤖 AI Review", "📄 Report"]
    )

    with j_tab1:
        if not history:
            st.info("No closed trades yet")
        else:
            # Search / filter
            search = st.text_input("Filter by symbol", key="j_search")
            filtered = [t for t in history if search.upper() in str(t.get("symbol","")).upper()] if search else history
            df_h = pd.DataFrame(filtered[-100:]).sort_values("exit_time", ascending=False, errors="ignore")
            if "pnl" in df_h.columns:
                df_h["pnl"] = df_h["pnl"].apply(lambda x: f"₹{float(x):+,.0f}")
            st.dataframe(df_h, use_container_width=True, height=400)

            # Note attachment (Block 13a)
            st.markdown(ui.section_header("Attach Note", icon="📝"), unsafe_allow_html=True)
            trade_id = st.text_input("Trade ID", key="j_note_id")
            note_txt = st.text_area("Note", key="j_note_txt")
            if st.button("Save Note", key="j_note_save") and trade_id:
                db.save_trade_note(trade_id, note_txt)
                st.success("Note saved")

    with j_tab2:
        # Block 13b
        patterns = eng.analyze_journal_patterns(history)
        if patterns.get("insights"):
            for insight in patterns["insights"]:
                st.markdown(f'<div class="pt-card" style="margin-bottom:6px">{insight}</div>',
                            unsafe_allow_html=True)
        else:
            st.info("Need ≥5 trades for pattern analysis")

    with j_tab3:
        # Block 13d
        biases = eng.detect_behavioral_biases(history)
        if not biases:
            st.success("✅ No significant behavioral biases detected")
        else:
            for b in biases:
                color = "var(--red)" if b["severity"]=="HIGH" else "var(--gold)"
                st.markdown(
                    f'<div class="pt-card pt-card-red" style="margin-bottom:8px">'
                    f'<div style="font-weight:700;color:{color}">{b["bias"]} — {b["severity"]}</div>'
                    f'<div style="color:var(--tx2);margin:4px 0">{b["evidence"]}</div>'
                    f'<div style="color:var(--teal);font-size:12px">💡 {b["fix"]}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    with j_tab4:
        render_ai_journal_review(history)

    with j_tab5:
        # Block 13c: Weekly/Monthly Report
        period_r = st.selectbox("Report Period", ["This Week","This Month","Last 30 Days"], key="rep_period")
        if st.button("📄 Generate Report", key="gen_report"):
            days = {"This Week": 7, "This Month": 30, "Last 30 Days": 30}[period_r]
            cutoff = date.today() - timedelta(days=days)
            period_trades = [t for t in history
                             if str(t.get("exit_time",""))[:10] >= cutoff.isoformat()]
            if not period_trades:
                st.warning("No trades in selected period")
            else:
                wins   = [t for t in period_trades if t.get("pnl",0) > 0]
                total  = sum(t.get("pnl",0) for t in period_trades)
                wr     = len(wins)/len(period_trades)*100 if period_trades else 0
                best   = max(period_trades, key=lambda t: t.get("pnl",0))
                worst  = min(period_trades, key=lambda t: t.get("pnl",0))
                rm     = eng.compute_risk_metrics(period_trades)
                report_html = f"""
                <div class="pt-card">
                  <div class="pt-h2">📊 {period_r} Report — {date.today().isoformat()}</div>
                  <hr class="pt-divider"/>
                  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:12px 0">
                    <div><div class="pt-label">Total P&L</div>
                         <div style="font-size:20px;font-weight:700;color:{ui.pnl_color(total)}">₹{total:+,.0f}</div></div>
                    <div><div class="pt-label">Trades / Win Rate</div>
                         <div style="font-size:20px;font-weight:700">{len(period_trades)} / {wr:.0f}%</div></div>
                    <div><div class="pt-label">Sharpe Ratio</div>
                         <div style="font-size:20px;font-weight:700">{rm.get('sharpe',0):.3f}</div></div>
                  </div>
                  <div><b>Best Trade:</b> {best.get('symbol','')} ₹{best.get('pnl',0):+,.0f} on {str(best.get('exit_time',''))[:10]}</div>
                  <div><b>Worst Trade:</b> {worst.get('symbol','')} ₹{worst.get('pnl',0):+,.0f} on {str(worst.get('exit_time',''))[:10]}</div>
                </div>"""
                st.markdown(report_html, unsafe_allow_html=True)


# ─── Settings Tab ─────────────────────────────────────────────────────────────

def render_settings() -> None:
    """Render settings: preferences, alerts, export/import, admin logs."""
    s_tab1, s_tab2, s_tab3, s_tab4 = st.tabs(["⚙️ Preferences", "🔔 Alerts", "📤 Export/Import", "🛠 Admin Logs"])

    with s_tab1:
        st.markdown(ui.section_header("Trading Parameters", icon="⚙️"), unsafe_allow_html=True)
        col1, col2 = st.columns(2)
        with col1:
            goal = st.number_input("Daily Goal (₹)", 1000, 100000,
                                    int(st.session_state.get("daily_goal", 5000)),
                                    step=500, key="set_goal")
            if goal != st.session_state.get("daily_goal"):
                st.session_state["daily_goal"] = goal
                db.save("daily_goal", goal)
        with col2:
            loss_limit = st.number_input("Daily Loss Limit (₹)", -50000, -500,
                                          int(st.session_state.get("daily_loss_limit", -3000)),
                                          step=500, key="set_loss")
            if loss_limit != st.session_state.get("daily_loss_limit"):
                st.session_state["daily_loss_limit"] = loss_limit
                db.save("daily_loss_limit", loss_limit)

        st.markdown(ui.section_header("Appearance", icon="🎨"), unsafe_allow_html=True)
        ui.accent_color_picker()

        st.markdown(ui.section_header("Universe", icon="🌐"), unsafe_allow_html=True)
        universe = st.selectbox("Default Universe",
                                 list(eng.UNIVERSE_PRESETS.keys()),
                                 index=list(eng.UNIVERSE_PRESETS.keys()).index(
                                     st.session_state.get("universe","Nifty 50")),
                                 key="set_universe")
        if universe != st.session_state.get("universe"):
            st.session_state["universe"] = universe
            db.save("universe", universe)

    with s_tab2:
        st.markdown(ui.section_header("Price Alerts", icon="🔔"), unsafe_allow_html=True)
        col1, col2, col3 = st.columns(3)
        with col1: a_sym  = st.text_input("Symbol", key="alert_sym")
        with col2: a_type = st.selectbox("Type", ["ABOVE","BELOW","PCT_UP"], key="alert_type")
        with col3: a_price = st.number_input("Price / %", 0.01, 1000000.0, 100.0, key="alert_price")
        a_note = st.text_input("Note (optional)", key="alert_note")
        if st.button("Add Alert", key="add_alert_btn") and a_sym:
            db.save_alert(a_sym, a_type, a_price, a_note)
            st.success(f"Alert set: {a_sym} {a_type} ₹{a_price:,.2f}")

        st.markdown("**Active Alerts:**")
        active = db.get_active_alerts()
        for alert in active:
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(
                    f'<div class="pt-card" style="padding:8px 12px;margin-bottom:4px">'
                    f'<b>{alert["symbol"]}</b> {alert["alert_type"]} ₹{alert["target_price"]:,.2f}'
                    f'{" — "+alert["note"] if alert.get("note") else ""}</div>',
                    unsafe_allow_html=True,
                )
            with col2:
                if st.button("✕", key=f"del_alert_{alert['id']}"):
                    db.delete_alert(alert["id"])
                    st.rerun()

    with s_tab3:
        st.markdown(ui.section_header("Export / Import", icon="📤"), unsafe_allow_html=True)
        history = _all_history()
        if history:
            csv_data = db.export_trades_csv(history)
            st.download_button("⬇ Download Trade History (CSV)", csv_data,
                               file_name=f"protrader_history_{date.today()}.csv",
                               mime="text/csv")

        uploaded = st.file_uploader("Import Trades from CSV", type=["csv"], key="csv_import")
        if uploaded:
            raw = uploaded.read().decode()
            imported = db.import_trades_csv(raw)
            if imported:
                st.success(f"Imported {len(imported)} trades")
                st.session_state["eq_history"] = (st.session_state.get("eq_history",[]) + imported)
                _save_portfolio("eq_history")

    with s_tab4:
        st.markdown(ui.section_header("Structured App Logs", icon="🛠"), unsafe_allow_html=True)
        logs = db.get_app_logs(50)
        for log in logs:
            level = log.get("level","INFO")
            color = {"ERROR":"var(--red)","WARN":"var(--gold)","INFO":"var(--tx3)"}.get(level,"var(--tx3)")
            st.markdown(
                f'<div style="font-family:var(--f-mono);font-size:11px;padding:2px 0;'
                f'color:{color}">[{log["ts"]}] {level} · {log["category"]} — {log["message"]}</div>',
                unsafe_allow_html=True,
            )


# ─── Scan & Signal ────────────────────────────────────────────────────────────

def render_scanner(mode: str = "INTRADAY", segment: str = "EQUITY",
                   symbols: list = None) -> list:
    """Run scanner and display results table."""
    if symbols is None:
        universe = st.session_state.get("universe", "Nifty 50")
        symbols = eng.get_dynamic_universe(universe)

    vix  = _get_vix()
    mood = _market_mood()

    min_str = st.slider("Min Strength", 50, 85, 58, key=f"scan_minstr_{segment}")
    if st.button(f"🔍 Scan {len(symbols)} symbols", key=f"scan_btn_{segment}"):
        with st.spinner(f"Scanning {len(symbols)} symbols for {mode} signals..."):
            results = eng.scan_parallel(symbols, mode, mood, vix,
                                         max_workers=15, min_strength=min_str)
            st.session_state[f"scan_{segment}"] = results
            db.app_log("INFO","scanner", f"{mode} scan: {len(results)} signals from {len(symbols)} symbols")

    results = st.session_state.get(f"scan_{segment}", [])
    if not results:
        st.info("Run a scan to see signals")
        return []

    # Regime check
    regime = eng.classify_regime(20, 0.03, vix)
    st.markdown(
        f'<div style="margin-bottom:8px">{ui.regime_badge(regime["regime"])} '
        f'<span style="font-size:12px;color:var(--tx3)">{regime["description"]}</span></div>',
        unsafe_allow_html=True,
    )

    for r in results[:30]:
        col1, col2, col3, col4, col5, col6, col7 = st.columns([2,2,1.5,1.5,1.5,1.5,1])
        with col1:
            st.markdown(
                f'<div style="font-weight:700;font-size:14px">{r["symbol"].replace(".NS","")}</div>'
                f'<div style="font-size:11px;color:var(--tx3)">{r.get("sector","")}</div>',
                unsafe_allow_html=True,
            )
        with col2:
            st.markdown(ui.sig_badge(r["rec"]) + ui.strength_bar(r["strength"], r["rec"]),
                        unsafe_allow_html=True)
        with col3: st.markdown(f'<div class="pt-mono">₹{r["price"]:,.2f}</div>', unsafe_allow_html=True)
        with col4: st.markdown(f'<div class="pt-mono" style="color:var(--green)">T: ₹{r["target"]:,.2f}</div>', unsafe_allow_html=True)
        with col5: st.markdown(f'<div class="pt-mono" style="color:var(--red)">SL: ₹{r["sl"]:,.2f}</div>', unsafe_allow_html=True)
        with col6: st.markdown(f'<div style="font-size:12px">RR: {r["rr"]:.1f}x · V: {r.get("vr",1):.1f}x</div>', unsafe_allow_html=True)
        with col7:
            if st.button("Add", key=f"add_{r['symbol']}_{segment}"):
                return [r]
        st.markdown('<hr style="border:none;border-top:1px solid var(--border2);margin:2px 0"/>', unsafe_allow_html=True)
    return []


# ─── Position Management ──────────────────────────────────────────────────────

def add_to_portfolio(result: dict, portfolio_key: str, segment: str = "equity",
                     qty: int = 1, mode: str = "INTRADAY") -> None:
    """Add scan result to portfolio with brokerage calculation."""
    price   = result.get("price", 0)
    brok    = eng.equity_cost(price, qty, result.get("type","BUY"), delivery=(mode=="DELIVERY"))
    new_pos = {
        "symbol":   result["symbol"],
        "type":     "BUY" if "BUY" in result.get("rec","") else "SELL",
        "entry":    price,
        "cmp":      price,
        "target":   result.get("target", price),
        "target_1": result.get("target_1", result.get("target", price)),
        "target_2": result.get("target_2", result.get("target", price)),
        "sl":       result.get("sl", price),
        "qty":      qty,
        "lot_size": 1,
        "pnl":      -brok,
        "brokerage":brok,
        "strength": result.get("strength", 0),
        "rec":      result.get("rec",""),
        "atr":      result.get("atr", price*0.02),
        "date":     date.today().isoformat(),
        "mode":     mode,
        "segment":  segment,
        "scale_phase": 0,
    }
    port = st.session_state.get(portfolio_key, [])
    port.append(new_pos)
    st.session_state[portfolio_key] = port
    _save_portfolio(portfolio_key)
    db.app_log("INFO","trade", f"New position: {result['symbol']} {new_pos['type']} ₹{price:,.2f} qty={qty}")
    ui.show_toast_js(f"✅ {result['symbol']} added at ₹{price:,.2f}", "buy" if "BUY" in new_pos["type"] else "sell")


def close_position(pos: dict, portfolio_key: str, history_key: str, cmp: float = None) -> None:
    """Close a position and move to history."""
    if cmp is None:
        cmp = eng.get_live_price(pos["symbol"]) or pos.get("entry", 0)

    port = [p for p in st.session_state.get(portfolio_key, []) if p is not pos]
    st.session_state[portfolio_key] = port
    _save_portfolio(portfolio_key)

    pos["exit_price"]  = cmp
    pos["exit_time"]   = datetime.now().isoformat()
    pos["category"]    = pos.get("segment", "equity")

    hist = st.session_state.get(history_key, [])
    hist.append(pos)
    st.session_state[history_key] = hist
    db.save(history_key, hist)

    db.log_trade(
        category=pos.get("segment","equity"),
        symbol=pos["symbol"],
        trade_type=pos.get("type","BUY"),
        entry_price=pos.get("entry",0),
        exit_price=cmp,
        qty=int(pos.get("qty",1)),
        pnl=pos.get("pnl",0),
        win=pos.get("pnl",0) > 0,
        strength=pos.get("strength",0),
        rec=pos.get("rec",""),
    )
    db.app_log("INFO","trade", f"Closed {pos['symbol']} P&L ₹{pos.get('pnl',0):+,.0f}")
    ui.show_toast_js(f"{'✅' if pos.get('pnl',0)>0 else '🔴'} {pos['symbol']} closed ₹{pos.get('pnl',0):+,.0f}",
                     "buy" if pos.get("pnl",0)>0 else "sell")


def render_portfolio(portfolio_key: str, history_key: str, segment: str = "equity") -> None:
    """Render open portfolio cards with close/scale-out controls."""
    port = st.session_state.get(portfolio_key, [])
    if not port:
        st.info(f"No open {segment} positions")
        return

    for i, pos in enumerate(port):
        lp = eng.get_live_price(pos["symbol"])
        if lp:
            pos["cmp"] = lp
            pnl_qty = pos.get("qty",1) * pos.get("lot_size",1)
            if pos.get("type") in ("BUY","CE","LONG"):
                pos["pnl"] = round((lp - pos["entry"]) * pnl_qty - pos.get("brokerage",0), 2)
            else:
                pos["pnl"] = round((pos["entry"] - lp) * pnl_qty - pos.get("brokerage",0), 2)
            pos = eng.update_trailing_stop(pos, lp)
            port[i] = pos

        col_card, col_ctrl = st.columns([7, 1])
        with col_card:
            st.markdown(ui.live_position_card(pos, lp), unsafe_allow_html=True)
        with col_ctrl:
            if st.button("Close", key=f"close_{portfolio_key}_{i}"):
                close_position(pos, portfolio_key, history_key, lp)
                st.rerun()
            # Block 3a: Scale out
            if pos.get("scale_phase",0) < 2:
                if st.button("Scale", key=f"scale_{portfolio_key}_{i}"):
                    eng.scale_out_position(pos, lp or pos["entry"])
                    _save_portfolio(portfolio_key)
                    st.rerun()

    st.session_state[portfolio_key] = port
    _save_portfolio(portfolio_key)


# ─── Main App ─────────────────────────────────────────────────────────────────

def main() -> None:
    # Inject CSS with current theme/density/palette
    theme   = st.session_state.get("theme",   "dark")
    density = st.session_state.get("density", "comfortable")
    palette = st.session_state.get("palette", "Blue")
    ui.inject_css(theme, density, palette)

    # Auth gate
    if not render_login():
        return

    uid  = st.session_state.get("current_user", "default")
    _load_user_state(uid)

    # Inject command palette + toasts
    symbols_for_palette = eng.get_dynamic_universe(st.session_state.get("universe","Nifty 50"))[:50]
    ui.inject_command_palette(symbols_for_palette)

    # Header
    render_header()

    # Sidebar watchlist (Block 4b)
    wl_syms = list(st.session_state.get("watchlists",{}).get(
        st.session_state.get("active_watchlist",""), []
    ))[:20]
    if wl_syms:
        prices_wl = {s: {"p": eng.get_live_price(s) or 0, "pct": 0} for s in wl_syms}
        ui.sidebar_watchlist(wl_syms, prices_wl)

    # Update open positions (live)
    for pk in ("eq_portfolio","opt_portfolio","fut_portfolio","etf_portfolio","mcx_portfolio"):
        _update_open_positions(pk)

    # Daily stats
    stats = eng.compute_daily_pnl_stats(
        st.session_state.get("eq_history",[]),  st.session_state.get("opt_history",[]),
        st.session_state.get("fut_history",[]), st.session_state.get("etf_history",[]),
        st.session_state.get("mcx_history",[]),
        st.session_state.get("eq_portfolio",[]), st.session_state.get("opt_portfolio",[]),
        st.session_state.get("fut_portfolio",[]),st.session_state.get("etf_portfolio",[]),
        st.session_state.get("mcx_portfolio",[]),
        daily_goal=st.session_state.get("daily_goal", 5000),
    )

    # VaR for banner
    history_all = _all_history()
    var_data = eng.compute_var(_all_open(), history_all)
    stats["var_95"] = var_data.get("var_95", 0)

    # Banner
    st.markdown(ui.daily_pnl_banner(stats), unsafe_allow_html=True)

    # Milestone + drawdown alerts
    _check_milestone_alerts(stats)
    _drawdown_warning(stats.get("total", 0), st.session_state.get("daily_loss_limit",-3000))

    # Watchlist price alerts
    _check_alerts({})

    # Time filter display (Block 3c)
    valid_time, time_msg = eng.is_valid_entry_time()
    st.markdown(
        f'<div style="margin:4px 0">{ui.time_filter_badge(valid_time, time_msg)}</div>',
        unsafe_allow_html=True,
    )

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tabs = st.tabs([
        "📊 Equity", "📉 Options", "🔮 Futures", "🏦 ETF", "🪙 MCX",
        "🤖 Auto-Trade", "📈 Analytics", "📓 Journal",
        "🌍 Market Intel", "⚙️ Settings",
    ])

    # ── Equity Tab ────────────────────────────────────────────────────────────
    with tabs[0]:
        col1, col2 = st.columns([2, 1])
        with col1:
            sym_input = st.text_input("Analyse Symbol", "RELIANCE.NS", key="eq_sym_input")
        with col2:
            mode = st.selectbox("Mode", ["INTRADAY","DELIVERY"], key="eq_mode")

        if sym_input:
            df = eng.get_ohlcv(sym_input, "3mo", "1d")
            if df is not None and len(df) >= 20:
                ind  = eng.compute_indicators(df)
                fund = eng.get_fundamentals(sym_input)
                vix  = _get_vix()
                rec, strength, bs, ss, reasons = eng.score_signal(
                    ind, fund, df, _market_mood(), vix, mode
                )
                col1, col2, col3, col4 = st.columns(4)
                with col1: st.markdown(ui.sig_badge(rec) + ui.strength_bar(strength, rec), unsafe_allow_html=True)
                with col2: st.metric("RSI", f"{ind.get('rsi',50):.1f}")
                with col3: st.metric("ADX", f"{ind.get('adx',0):.1f}")
                with col4: st.metric("ATR", f"₹{ind.get('atr',0):.2f}")

                # MTF Confirmation (Block 2a)
                with st.expander("🔍 Multi-Timeframe Confirmation"):
                    mtf = eng.confirm_mtf(sym_input, rec)
                    st.json(mtf)

                # Volume Profile (Block 2b)
                with st.expander("📊 Volume Profile & VWAP"):
                    vp = eng.compute_volume_profile(df)
                    vpb = eng.compute_vwap_bands(df)
                    col1, col2 = st.columns(2)
                    with col1:
                        st.metric("Point of Control", f"₹{vp.get('poc',0):,.2f}")
                        st.metric("VWAP", f"₹{vpb.get('vwap',0):,.2f}")
                    with col2:
                        st.metric("Value Area High", f"₹{vp.get('value_area_high',0):,.2f}")
                        st.metric("Value Area Low",  f"₹{vp.get('value_area_low',0):,.2f}")

                render_candlestick(sym_input)

                # Signal reasons
                with st.expander("📋 Signal Reasons"):
                    for r in reasons[:15]:
                        st.markdown(f'<div style="font-size:12px;color:var(--tx2);padding:1px 0">{r}</div>',
                                    unsafe_allow_html=True)

        st.markdown("---")
        st.markdown(ui.section_header("Equity Scanner", icon="🔍"), unsafe_allow_html=True)
        added = render_scanner("INTRADAY" if mode=="INTRADAY" else "DELIVERY", "EQUITY")
        if added:
            qty = st.number_input("Qty", 1, 10000, 10, key="eq_add_qty")
            add_to_portfolio(added[0], "eq_portfolio", "equity", qty, mode)

        st.markdown(ui.section_header("Open Positions", icon="💼"), unsafe_allow_html=True)
        render_portfolio("eq_portfolio", "eq_history", "equity")

    # ── Options Tab ───────────────────────────────────────────────────────────
    with tabs[1]:
        o_tab1, o_tab2, o_tab3 = st.tabs(["Chain", "Strategy Builder", "Theta Decay"])
        with o_tab1:
            idx = st.selectbox("Index", ["NIFTY","BANKNIFTY"], key="opt_idx")
            render_options_chain(idx)
        with o_tab2:
            render_strategy_builder()
        with o_tab3:
            render_theta_decay(st.session_state.get("opt_portfolio",[]), _get_vix())

        st.markdown(ui.section_header("Options Positions", icon="📉"), unsafe_allow_html=True)
        render_portfolio("opt_portfolio", "opt_history", "options")

    # ── Futures Tab ───────────────────────────────────────────────────────────
    with tabs[2]:
        st.markdown(ui.section_header("Futures Scanner", icon="🔮"), unsafe_allow_html=True)
        fut_syms = eng.FUTURES_SYMBOLS
        added_f  = render_scanner("INTRADAY", "FUTURES", fut_syms)
        if added_f:
            qty = st.number_input("Lots", 1, 100, 1, key="fut_add_qty")
            add_to_portfolio(added_f[0], "fut_portfolio", "futures", qty)
        render_portfolio("fut_portfolio", "fut_history", "futures")

    # ── ETF Tab ───────────────────────────────────────────────────────────────
    with tabs[3]:
        st.markdown(ui.section_header("ETF Scanner", icon="🏦"), unsafe_allow_html=True)
        added_e = render_scanner("DELIVERY", "ETF", eng.ETF_SYMBOLS)
        if added_e:
            qty = st.number_input("Qty", 1, 10000, 100, key="etf_add_qty")
            add_to_portfolio(added_e[0], "etf_portfolio", "etf", qty, "DELIVERY")
        render_portfolio("etf_portfolio", "etf_history", "etf")

    # ── MCX Tab ───────────────────────────────────────────────────────────────
    with tabs[4]:
        st.markdown(ui.section_header("MCX Commodities", icon="🪙"), unsafe_allow_html=True)
        added_m = render_scanner("INTRADAY", "MCX", eng.MCX_SYMBOLS)
        if added_m:
            lots = st.number_input("Lots", 1, 100, 1, key="mcx_add_qty")
            add_to_portfolio(added_m[0], "mcx_portfolio", "mcx", lots)
        render_portfolio("mcx_portfolio", "mcx_history", "mcx")

    # ── Auto-Trade Tab ────────────────────────────────────────────────────────
    with tabs[5]:
        st.markdown(ui.section_header("Auto-Trading Engine", icon="🤖"), unsafe_allow_html=True)
        col1, col2, col3 = st.columns(3)
        with col1:
            auto = st.toggle("Enable Auto-Trading",
                             value=st.session_state.get("auto_trading", False),
                             key="auto_toggle")
            st.session_state["auto_trading"] = auto
        with col2:
            max_trades = st.number_input("Max trades/day", 1, 20, 8, key="auto_max_trades")
        with col3:
            auto_mode = st.selectbox("Auto Mode", ["INTRADAY","DELIVERY"], key="auto_mode")

        if auto:
            trades_today = len(_today_trades(["eq_history","opt_history","fut_history","etf_history","mcx_history"]))
            open_pos     = _all_open()
            vix          = _get_vix()
            mood         = _market_mood()
            time_ok, time_msg = eng.is_valid_entry_time()

            st.markdown(
                f'<div class="pt-card">'
                f'<div>Trades today: <b>{trades_today}/{max_trades}</b></div>'
                f'<div>Open positions: <b>{len(open_pos)}</b></div>'
                f'<div>{ui.time_filter_badge(time_ok, time_msg)}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            if time_ok and trades_today < max_trades:
                universe = eng.get_dynamic_universe(st.session_state.get("universe","Nifty 50"))
                scan_res = eng.scan_parallel(universe, auto_mode, mood, vix, max_workers=20, min_strength=68)
                for res in scan_res[:3]:
                    sig = dict(res, symbol=res["symbol"])
                    ok, reason = eng.should_enter_trade(
                        sig, auto_mode, mood, vix,
                        daily_pnl=stats.get("total",0),
                        daily_goal=st.session_state.get("daily_goal",5000),
                        daily_loss_limit=st.session_state.get("daily_loss_limit",-3000),
                        trades_today=trades_today,
                        max_trades_per_day=max_trades,
                        open_positions=open_pos,
                    )
                    if ok:
                        vol_size = eng.volatility_adjusted_position_size(
                            st.session_state.get("capital",500000),
                            res.get("atr",0), res.get("price",0)
                        )
                        add_to_portfolio(res, "eq_portfolio", "equity",
                                         vol_size.get("qty",1), auto_mode)
                        trades_today += 1
                        db.app_log("INFO","auto_trade",
                                   f"Auto-entered {res['symbol']} {res['rec']} str={res['strength']}")
            else:
                st.info(f"Auto-trade paused: {time_msg}")

        # Re-entry monitor (Block 3b)
        st.markdown(ui.section_header("Re-Entry Monitor", icon="🔄"), unsafe_allow_html=True)
        for pos in _all_open():
            if pos.get("sl_hit"):
                lp   = eng.get_live_price(pos["symbol"])
                ind  = eng.compute_indicators(eng.get_ohlcv(pos["symbol"],"3mo","1d")) or {}
                ok, reason, ep = eng.check_reentry_signal(
                    pos.get("entry",0), pos.get("type","BUY"), lp or 0,
                    pos.get("atr", 0.01), ind,
                    already_reentered=pos.get("reentered",False),
                )
                if ok:
                    st.markdown(
                        f'<div class="pt-card pt-card-green"><b>{pos["symbol"]}</b> {reason}</div>',
                        unsafe_allow_html=True,
                    )

    # ── Analytics Tab ─────────────────────────────────────────────────────────
    with tabs[6]:
        a_tab1, a_tab2, a_tab3, a_tab4, a_tab5 = st.tabs(
            ["📊 Overview", "🎲 Monte Carlo", "📅 P&L Calendar", "🗺 Treemap", "⏪ Backtester"]
        )
        with a_tab1:
            render_risk_metrics(_all_history())
            st.markdown(ui.section_header("Options Greeks (Open)", icon="🔢"), unsafe_allow_html=True)
            opt_port = st.session_state.get("opt_portfolio", [])
            if opt_port:
                total_delta = sum(p.get("delta",0) * p.get("qty",1) for p in opt_port)
                total_theta = sum(p.get("theta",0) * p.get("qty",1) for p in opt_port)
                total_vega  = sum(p.get("vega",0) * p.get("qty",1) for p in opt_port)
                col1, col2, col3 = st.columns(3)
                with col1: st.metric("Portfolio Δ Delta", f"{total_delta:.3f}")
                with col2: st.metric("Portfolio Θ Theta/day", f"₹{total_theta:.0f}")
                with col3: st.metric("Portfolio ν Vega",  f"₹{total_vega:.0f}")

        with a_tab2:
            render_monte_carlo(_all_history())

        with a_tab3:
            render_pnl_heatmap(uid)

        with a_tab4:
            render_portfolio_treemap()

        with a_tab5:
            render_backtester()

    # ── Journal Tab ───────────────────────────────────────────────────────────
    with tabs[7]:
        render_journal()

    # ── Market Intel Tab ──────────────────────────────────────────────────────
    with tabs[8]:
        mi_tab1, mi_tab2, mi_tab3 = st.tabs(["FII/DII Activity", "Global Markets", "Economic Calendar"])
        with mi_tab1:
            render_fii_dii()
        with mi_tab2:
            render_global_markets()
        with mi_tab3:
            st.markdown(ui.section_header("Economic Calendar", icon="📅"), unsafe_allow_html=True)
            events = [
                {"date":"2025-06-06","event":"RBI MPC Decision","impact":"HIGH"},
                {"date":"2025-06-18","event":"US FOMC Meeting","impact":"MEDIUM"},
                {"date":"2025-07-01","event":"Q1 GDP Data","impact":"HIGH"},
            ]
            for ev in events:
                days_away = (date.fromisoformat(ev["date"]) - date.today()).days
                col_col   = "var(--red)" if ev["impact"]=="HIGH" else "var(--gold)"
                st.markdown(
                    f'<div class="pt-card" style="margin-bottom:6px;display:flex;gap:16px;align-items:center">'
                    f'<div style="min-width:90px;font-family:var(--f-mono);font-size:12px">{ev["date"]}</div>'
                    f'<div style="flex:1">{ev["event"]}</div>'
                    f'<span class="badge" style="color:{col_col};border-color:{col_col}">{ev["impact"]}</span>'
                    f'<span style="font-size:12px;color:var(--tx3)">{days_away}d away</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # ── Settings Tab ──────────────────────────────────────────────────────────
    with tabs[9]:
        render_settings()

    # ── Auto-refresh ──────────────────────────────────────────────────────────
    time.sleep(0.1)
    st.markdown(
        '<script>setTimeout(function(){window.location.reload();},12000);</script>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
