"""
ui.py — ProTrader Terminal v6 UI
Next-level Bloomberg-grade dark professional terminal
Enhanced:
  ✅ Dark terminal theme (Bloomberg/Zerodha Kite dark style)
  ✅ Live price pulse animations for auto-trading positions
  ✅ Real-time P&L flash green/red on change
  ✅ Daily P&L dashboard cards with progress rings
  ✅ Enhanced candlestick-style position cards
  ✅ Profit target progress bars within trade cards
  ✅ Neon-glow signal badges
  ✅ Animated live indicators / market status dots
  ✅ Daily goal tracker widget
"""

TERMINAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@300;400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');

:root {
    /* ─── Bloomberg Dark Theme ─────────────────────────────── */
    --bg:        #050A14;
    --bg2:       #0A1220;
    --bg3:       #0F1A2E;
    --bg4:       #142038;
    --surface:   #0D1829;
    --card:      #0D1829;
    --glass:     rgba(13,24,41,0.85);

    /* ─── Borders ──────────────────────────────────────────── */
    --border:    rgba(255,255,255,0.07);
    --border2:   rgba(255,255,255,0.12);
    --border-hi: rgba(99,179,237,0.35);

    /* ─── Accent ───────────────────────────────────────────── */
    --accent:    #3B82F6;
    --accent2:   #60A5FA;
    --accent-glow: rgba(59,130,246,0.35);
    --p:         #7C3AED;
    --p2:        #6D28D9;
    --p3:        #8B5CF6;
    --p4:        #A78BFA;
    --p-light:   rgba(124,58,237,0.15);
    --p-border:  rgba(124,58,237,0.4);
    --p-glow:    rgba(124,58,237,0.3);
    --p-pale:    rgba(124,58,237,0.08);

    /* ─── Semantic ─────────────────────────────────────────── */
    --green:        #10B981;
    --green2:       #059669;
    --green3:       #34D399;
    --green-bg:     rgba(16,185,129,0.1);
    --green-border: rgba(16,185,129,0.35);
    --green-glow:   rgba(16,185,129,0.3);
    --red:          #EF4444;
    --red2:         #DC2626;
    --red3:         #F87171;
    --red-bg:       rgba(239,68,68,0.1);
    --red-border:   rgba(239,68,68,0.35);
    --red-glow:     rgba(239,68,68,0.3);
    --gold:         #F59E0B;
    --gold2:        #D97706;
    --gold3:        #FCD34D;
    --gold-bg:      rgba(245,158,11,0.1);
    --gold-border:  rgba(245,158,11,0.35);
    --teal:         #06B6D4;
    --teal-bg:      rgba(6,182,212,0.1);
    --orange:       #F97316;

    /* ─── Text ─────────────────────────────────────────────── */
    --tx:    #E2E8F0;
    --tx2:   #94A3B8;
    --tx3:   #64748B;
    --muted: #475569;
    --white: #FFFFFF;

    /* ─── Fonts ────────────────────────────────────────────── */
    --f-ui:   'Inter', sans-serif;
    --f-mono: 'JetBrains Mono', monospace;
    --f-head: 'Space Grotesk', sans-serif;

    /* ─── Shadows ──────────────────────────────────────────── */
    --sh-xs:  0 1px 3px rgba(0,0,0,0.4);
    --sh-sm:  0 2px 8px rgba(0,0,0,0.5);
    --sh-md:  0 4px 20px rgba(0,0,0,0.6);
    --sh-p:   0 4px 20px var(--p-glow);
    --sh-g:   0 4px 20px var(--green-glow);
    --sh-r:   0 4px 20px var(--red-glow);
    --sh-a:   0 4px 20px var(--accent-glow);
}

/* ══ BASE ══════════════════════════════════════════════════════════════════════ */
html, body, [class*="css"] {
    font-family: var(--f-ui) !important;
    background:  var(--bg)  !important;
    color:       var(--tx)  !important;
    font-size: 13.5px;
}
.stApp { background: var(--bg) !important; }
* { box-sizing: border-box; }

::-webkit-scrollbar       { width:5px; height:5px; }
::-webkit-scrollbar-track { background:var(--bg2); }
::-webkit-scrollbar-thumb { background:var(--border2); border-radius:4px; }
::-webkit-scrollbar-thumb:hover { background:var(--p3); }

/* ══ KEYFRAMES ══════════════════════════════════════════════════════════════════ */
@keyframes pdot {
    0%,100% { opacity:1; box-shadow:0 0 6px var(--green-glow); }
    50%      { opacity:0.3; box-shadow:none; }
}
@keyframes rdot {
    0%,100% { opacity:1; box-shadow:0 0 6px var(--red-glow); }
    50%      { opacity:0.3; box-shadow:none; }
}
@keyframes pulse-green {
    0%   { box-shadow: 0 0 0 0 rgba(16,185,129,0.5); }
    70%  { box-shadow: 0 0 0 8px rgba(16,185,129,0); }
    100% { box-shadow: 0 0 0 0 rgba(16,185,129,0); }
}
@keyframes pulse-red {
    0%   { box-shadow: 0 0 0 0 rgba(239,68,68,0.5); }
    70%  { box-shadow: 0 0 0 8px rgba(239,68,68,0); }
    100% { box-shadow: 0 0 0 0 rgba(239,68,68,0); }
}
@keyframes scrolll {
    0%   { transform:translateX(0); }
    100% { transform:translateX(-50%); }
}
@keyframes shimmer {
    0%   { background-position:-200% 0; }
    100% { background-position:200% 0; }
}
@keyframes fadein { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:translateY(0); } }
@keyframes blink { 0%,100% { opacity:1; } 50% { opacity:0.2; } }
@keyframes glow-pulse-g {
    0%,100% { text-shadow: 0 0 8px rgba(16,185,129,0.6); }
    50%      { text-shadow: 0 0 20px rgba(16,185,129,1); }
}
@keyframes glow-pulse-r {
    0%,100% { text-shadow: 0 0 8px rgba(239,68,68,0.6); }
    50%      { text-shadow: 0 0 20px rgba(239,68,68,1); }
}

/* ══ TERMINAL HEADER ══ */
.terminal-header {
    background: linear-gradient(135deg, #050A14 0%, #0A1220 60%, #0D1040 100%);
    border-bottom: 1px solid var(--border);
    padding: 18px 28px 14px;
    position: relative;
    overflow: hidden;
}
.terminal-header::before {
    content:'';
    position:absolute;
    top:0; left:0; right:0; bottom:0;
    background: radial-gradient(ellipse at 30% 50%, rgba(124,58,237,0.06) 0%, transparent 70%),
                radial-gradient(ellipse at 70% 50%, rgba(59,130,246,0.04) 0%, transparent 70%);
    pointer-events:none;
}
.terminal-header::after {
    content:'';
    position:absolute;
    bottom:0; left:0; right:0;
    height:2px;
    background: linear-gradient(90deg, transparent, var(--accent), var(--p3), var(--teal), transparent);
}
.terminal-title {
    font-family: var(--f-head);
    font-size: 1.55rem;
    font-weight: 800;
    color: var(--white);
    line-height: 1.2;
    letter-spacing: -0.5px;
}
.terminal-title span { color: var(--accent2); }
.terminal-sub {
    font-family: var(--f-mono);
    font-size: 0.58rem;
    color: var(--tx3);
    letter-spacing: 2.5px;
    text-transform: uppercase;
    margin-top: 4px;
}
.terminal-dot {
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--green);
    animation: pdot 2s ease-in-out infinite;
    margin-right: 6px;
    vertical-align: middle;
}
.terminal-dot.red { background: var(--red); animation: rdot 1.5s ease-in-out infinite; }

/* ══ TICKER TAPE ══ */
.ticker-outer {
    overflow: hidden;
    background: var(--bg2);
    border-bottom: 1px solid var(--border);
    padding: 7px 0;
}
.ticker-inner {
    display:flex; gap:56px;
    animation: scrolll 60s linear infinite;
    white-space:nowrap;
}
.t-item { font-family:var(--f-mono); font-size:0.67rem; color:var(--tx3); display:inline-flex; gap:8px; align-items:center; }
.t-name { color:var(--tx2); font-weight:600; letter-spacing:0.5px; }
.t-up   { color:var(--green3); font-weight:500; }
.t-dn   { color:var(--red3);   font-weight:500; }
.t-flat { color:var(--gold3);  font-weight:500; }
.t-sep  { color:var(--border2); }

/* ══ INDEX CARDS ══ */
.idx-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 16px 18px;
    position: relative;
    overflow: hidden;
    transition: box-shadow .2s, border-color .25s, transform .15s;
}
.idx-card:hover {
    border-color: var(--border2);
    transform: translateY(-1px);
    box-shadow: var(--sh-md);
}
.idx-card::before {
    content:''; position:absolute; top:0; left:0; right:0; height:3px;
    border-radius:14px 14px 0 0;
}
.idx-card.bn::before { background:linear-gradient(90deg,var(--p3),var(--accent)); }
.idx-card.nf::before { background:linear-gradient(90deg,var(--accent),var(--teal)); }
.idx-card.vx::before { background:linear-gradient(90deg,var(--red),var(--orange)); }
.idx-card.sx::before { background:linear-gradient(90deg,var(--gold),var(--orange)); }
.idx-card.it::before { background:linear-gradient(90deg,var(--teal),var(--accent)); }
.idx-card.mid::before { background:linear-gradient(90deg,var(--green),var(--teal)); }
.idx-label { font-size:.58rem; color:var(--muted); text-transform:uppercase; letter-spacing:2px; margin-bottom:7px; font-weight:700; font-family:var(--f-ui); }
.idx-price { font-family:var(--f-mono); font-size:1.35rem; font-weight:600; color:var(--white); line-height:1.1; letter-spacing:-0.5px; }
.idx-chg   { font-family:var(--f-mono); font-size:.68rem; margin-top:5px; }
.up   { color:var(--green3) !important; }
.dn   { color:var(--red3)   !important; }
.flat { color:var(--gold3)  !important; }

/* ══ METRIC CARDS ══ */
.m-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 18px 16px;
    text-align: center;
    transition: box-shadow .2s, border-color .2s, transform .15s;
    position: relative;
    overflow: hidden;
}
.m-card:hover { box-shadow:var(--sh-md); border-color:var(--border2); transform:translateY(-1px); }
.m-card::after {
    content:''; position:absolute; bottom:0; left:20%; right:20%; height:1px;
    background: linear-gradient(90deg, transparent, var(--border2), transparent);
}
.m-val { font-family:var(--f-mono); font-size:1.3rem; font-weight:700; color:var(--white); }
.m-lbl { font-size:.58rem; color:var(--muted); text-transform:uppercase; letter-spacing:1.8px; margin-top:5px; font-weight:700; }

/* ══ DAILY P&L GOAL CARD ══ */
.daily-pnl-card {
    background: linear-gradient(135deg, var(--bg2) 0%, var(--bg3) 100%);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 20px;
    position: relative;
    overflow: hidden;
}
.daily-pnl-card::before {
    content:''; position:absolute; top:0; left:0; right:0; height:3px;
    background: linear-gradient(90deg, var(--green), var(--teal));
}
.daily-goal-ring {
    width: 72px; height: 72px;
    border-radius: 50%;
    border: 4px solid var(--border2);
    display: flex; align-items: center; justify-content: center;
    position: relative;
    flex-shrink: 0;
}
.dpnl-row { display:flex; gap:16px; align-items:center; }
.dpnl-stats { flex:1; }
.dpnl-stat-row { display:flex; justify-content:space-between; align-items:center; padding:4px 0; border-bottom:1px solid var(--border); }
.dpnl-stat-row:last-child { border-bottom:none; }

/* ══ SIGNAL BADGES (neon glow) ══ */
.sig {
    display:inline-block; padding:4px 12px; border-radius:20px;
    font-family:var(--f-mono); font-size:.65rem; font-weight:700;
    letter-spacing:.5px; text-transform:uppercase;
    transition: box-shadow .2s;
}
.sig-sbuy  {
    background:rgba(16,185,129,0.12); color:#34D399;
    border:1px solid rgba(16,185,129,0.4);
    box-shadow: 0 0 10px rgba(16,185,129,0.25);
}
.sig-buy   { background:var(--green-bg); color:var(--green3); border:1px solid var(--green-border); }
.sig-wbuy  { background:var(--teal-bg);  color:var(--teal);   border:1px solid rgba(6,182,212,.35); }
.sig-ssell {
    background:rgba(239,68,68,0.12); color:#F87171;
    border:1px solid rgba(239,68,68,0.4);
    box-shadow: 0 0 10px rgba(239,68,68,0.25);
}
.sig-sell  { background:var(--red-bg);   color:var(--red3);   border:1px solid var(--red-border); }
.sig-wsell { background:rgba(249,115,22,.1); color:var(--orange); border:1px solid rgba(249,115,22,.3); }
.sig-neut  { background:var(--gold-bg);  color:var(--gold3);  border:1px solid var(--gold-border); }

/* ══ SECTION TITLE ══ */
.sec-ttl {
    font-family:var(--f-ui); font-size:.6rem; font-weight:800; letter-spacing:2.5px;
    color:var(--accent2); text-transform:uppercase;
    border-bottom:1px solid var(--border);
    padding-bottom:10px; margin-bottom:16px; margin-top:8px;
    display:flex; align-items:center; gap:8px;
}

/* ══ LIVE TRADE CARDS (enhanced) ══ */
.tc {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 16px 18px;
    margin-bottom: 8px;
    transition: box-shadow .2s, border-color .2s;
    position: relative;
    overflow: hidden;
    animation: fadein 0.3s ease;
}
.tc:hover { box-shadow: var(--sh-md); border-color: var(--border2); }
.tc.win  { border-left: 3px solid var(--green); }
.tc.loss { border-left: 3px solid var(--red); }
.tc.open { border-left: 3px solid var(--accent); }
.tc-live {
    position: absolute; top: 12px; right: 14px;
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--green);
    animation: pdot 2s ease-in-out infinite;
}
.tc-live.loss { background: var(--red); animation: rdot 1.5s ease-in-out infinite; }
.tc-head { font-family:var(--f-head); font-weight:700; font-size:.92rem; color:var(--white); }
.tc-meta { font-family:var(--f-mono); font-size:.64rem; color:var(--tx3); margin-top:3px; }

/* Target progress bar inside trade card */
.tc-progress-wrap { background:var(--bg3); border-radius:3px; height:4px; margin-top:8px; overflow:hidden; }
.tc-progress-fill { height:4px; border-radius:3px; transition:width .6s ease; }
.tc-progress-green { background: linear-gradient(90deg, var(--green2), var(--green3)); }
.tc-progress-red   { background: linear-gradient(90deg, var(--red2), var(--red3)); }
.tc-progress-warn  { background: linear-gradient(90deg, var(--gold2), var(--gold3)); }

/* ══ LIVE PRICE FLASH ══ */
.lp-flash-up {
    color: var(--green3) !important;
    animation: glow-pulse-g 0.8s ease;
}
.lp-flash-dn {
    color: var(--red3) !important;
    animation: glow-pulse-r 0.8s ease;
}

/* ══ POSITION TABLE ROWS ══ */
.pos-row {
    background:var(--bg2); border:1px solid var(--border); border-radius:10px;
    padding:12px 16px; margin:5px 0;
    display:grid; grid-template-columns:2fr 1fr 1fr 1fr 1fr 1fr 1.5fr 1fr;
    align-items:center; gap:6px;
    transition: background .15s, border-color .15s;
    font-family: var(--f-mono); font-size:.75rem;
}
.pos-row:hover { background:var(--bg3); border-color:var(--border2); }
.pos-row.profit { border-left:3px solid var(--green); }
.pos-row.loss   { border-left:3px solid var(--red); }

/* ══ PROFIT BOOK ══ */
.pb { background:var(--green-bg); border:1px solid var(--green-border); border-radius:10px; padding:10px 16px; margin:4px 0; display:flex; justify-content:space-between; align-items:center; }
.pb-pct { font-family:var(--f-mono); font-size:.78rem; color:var(--green3); font-weight:700; }
.pb-pr  { font-family:var(--f-mono); font-size:.82rem; color:var(--white); font-weight:600; }
.pb-lbl { font-size:.64rem; color:var(--tx3); }

/* ══ GREEK BOXES ══ */
.gk { background:var(--bg3); border:1px solid var(--border); border-radius:10px; padding:12px 14px; text-align:center; transition:border-color .2s; }
.gk:hover { border-color:var(--border-hi); }
.gk-v { font-family:var(--f-mono); font-size:.95rem; font-weight:600; color:var(--white); }
.gk-l { font-size:.56rem; color:var(--muted); text-transform:uppercase; letter-spacing:1.5px; margin-top:3px; font-weight:700; }

/* ══ S/R LEVELS ══ */
.lvl { border-radius:8px; padding:9px 14px; font-family:var(--f-mono); font-size:.82rem; text-align:center; font-weight:600; }
.lvl-r  { background:var(--red-bg);   border:1px solid var(--red-border);   color:var(--red3); }
.lvl-s  { background:var(--green-bg); border:1px solid var(--green-border); color:var(--green3); }
.lvl-e  { background:var(--p-pale);   border:1px solid var(--p-border);     color:var(--p4); }
.lvl-tg { background:var(--teal-bg);  border:1px solid rgba(6,182,212,.3);  color:var(--teal); }

/* ══ STRENGTH BAR ══ */
.sb-wrap { background:var(--bg3); border-radius:6px; height:6px; overflow:hidden; margin-top:5px; }
.sb-fill  { height:6px; border-radius:6px; transition:width .6s ease; }

/* ══ OPTION CHAIN ══ */
.oc-hdr {
    display:grid;
    grid-template-columns:1.5fr .8fr .7fr .6fr 1fr 1.2fr 1fr .6fr .7fr .8fr 1.5fr;
    padding:10px 14px; background:var(--bg3);
    border:1px solid var(--border); border-radius:12px 12px 0 0;
    font-size:.58rem; text-transform:uppercase; letter-spacing:1.5px;
    color:var(--muted); gap:4px; font-family:var(--f-ui); font-weight:700;
}
.oc-row {
    display:grid;
    grid-template-columns:1.5fr .8fr .7fr .6fr 1fr 1.2fr 1fr .6fr .7fr .8fr 1.5fr;
    padding:8px 14px; border:1px solid var(--border); border-top:none;
    font-size:.74rem; gap:4px; align-items:center; transition:background .12s;
    font-family:var(--f-mono); background:var(--bg2); color:var(--tx);
}
.oc-row:hover       { background:var(--bg3) !important; }
.oc-row:last-child  { border-radius:0 0 12px 12px; }
.oc-atm { background:rgba(245,158,11,0.07) !important; border-left:3px solid var(--gold) !important; border-right:3px solid var(--gold) !important; }

/* ══ JOURNAL ROW ══ */
.jrnl-row { background:var(--bg2); border:1px solid var(--border); border-radius:10px; padding:10px 16px; margin:4px 0; display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap; transition:box-shadow .15s, border-color .15s; }
.jrnl-row:hover { border-color:var(--border2); box-shadow:var(--sh-sm); }

/* ══ PILL TAG ══ */
.pill       { display:inline-block; background:var(--p-pale);    border:1px solid var(--p-border);        color:var(--p4);      border-radius:20px; padding:3px 11px; font-size:.65rem; font-family:var(--f-ui); font-weight:700; }
.pill-green { background:var(--green-bg);  border-color:var(--green-border); color:var(--green3); }
.pill-red   { background:var(--red-bg);    border-color:var(--red-border);   color:var(--red3); }
.pill-gold  { background:var(--gold-bg);   border-color:var(--gold-border);  color:var(--gold3); }
.pill-teal  { background:var(--teal-bg);   border-color:rgba(6,182,212,.35); color:var(--teal); }

/* ══ RANK BADGE ══ */
.rank-badge { display:inline-flex; align-items:center; justify-content:center; width:26px; height:26px; background:var(--p-pale); border:1px solid var(--p-border); border-radius:50%; font-family:var(--f-mono); font-size:.7rem; font-weight:700; color:var(--p4); }

/* ══ DIVIDERS ══ */
.divider        { height:1px; background:var(--border); margin:12px 0; }
.divider-accent { height:1px; background:linear-gradient(90deg,transparent,var(--accent-glow),transparent); margin:14px 0; }

/* ══ HIGHLIGHT BOX ══ */
.hl-box { background:var(--p-pale); border:1px solid var(--p-border); border-radius:12px; padding:16px 20px; text-align:center; }
.hl-val { font-family:var(--f-mono); font-size:1.45rem; font-weight:700; color:var(--p4); }
.hl-lbl { font-size:.58rem; color:var(--muted); text-transform:uppercase; letter-spacing:1.5px; margin-top:4px; font-weight:700; }

/* ══ SCROLLABLE TABLE ══ */
.scroll-table { max-height:420px; overflow-y:auto; border-radius:12px; border:1px solid var(--border); }
.scroll-table::-webkit-scrollbar       { width:4px; }
.scroll-table::-webkit-scrollbar-thumb { background:var(--border2); border-radius:4px; }

/* ══ ALERT BOXES ══ */
.info-b    { background:rgba(59,130,246,0.08); border:1px solid rgba(59,130,246,0.3); border-radius:10px; padding:12px 16px; font-size:.8rem; color:var(--accent2); margin:6px 0; }
.warn-b    { background:var(--gold-bg);  border:1px solid var(--gold-border);  border-radius:10px; padding:12px 16px; font-size:.8rem; color:var(--gold3);  margin:6px 0; }
.success-b { background:var(--green-bg); border:1px solid var(--green-border); border-radius:10px; padding:12px 16px; font-size:.8rem; color:var(--green3); margin:6px 0; }
.danger-b  { background:var(--red-bg);   border:1px solid var(--red-border);   border-radius:10px; padding:12px 16px; font-size:.8rem; color:var(--red3);   margin:6px 0; }

/* ══ CHIPS ══ */
.ce-chip  { background:rgba(59,130,246,0.12); border:1px solid rgba(59,130,246,.35); color:var(--accent2); border-radius:6px; padding:3px 10px; font-size:.66rem; font-family:var(--f-mono); font-weight:700; }
.pe-chip  { background:var(--red-bg);  border:1px solid var(--red-border);   color:var(--red3);   border-radius:6px; padding:3px 10px; font-size:.66rem; font-family:var(--f-mono); font-weight:700; }
.atm-chip { background:var(--gold-bg); border:1px solid var(--gold-border);  color:var(--gold3);  border-radius:6px; padding:3px 10px; font-size:.66rem; font-family:var(--f-mono); font-weight:700; }
.fut-chip { background:var(--p-pale);  border:1px solid var(--p-border);     color:var(--p4);     border-radius:6px; padding:3px 10px; font-size:.66rem; font-family:var(--f-mono); font-weight:700; }

/* ══ UTILITIES ══ */
.purple-color { color:var(--p4)     !important; }
.green-color  { color:var(--green3) !important; }
.red-color    { color:var(--red3)   !important; }
.gold-color   { color:var(--gold3)  !important; }
.muted-color  { color:var(--muted)  !important; }
.accent-color { color:var(--accent2)!important; }
.ce-color     { color:var(--accent2)!important; }
.pe-color     { color:var(--red3)   !important; }
.pnl-pos  { color:var(--green3); font-family:var(--f-mono); font-weight:700; }
.pnl-neg  { color:var(--red3);   font-family:var(--f-mono); font-weight:700; }
.pnl-zero { color:var(--gold3);  font-family:var(--f-mono); font-weight:700; }

/* ══ BUTTONS ══ */
.stButton > button {
    background: linear-gradient(135deg, var(--p), var(--p3)) !important;
    color: var(--white) !important;
    font-family: var(--f-ui) !important;
    font-weight: 700 !important;
    font-size: .83rem !important;
    letter-spacing: .2px !important;
    border: none !important;
    border-radius: 10px !important;
    padding: 10px 22px !important;
    transition: all .2s !important;
    box-shadow: 0 2px 10px var(--p-glow) !important;
}
.stButton > button:hover  { background: linear-gradient(135deg, var(--p2), var(--p)) !important; box-shadow: 0 4px 18px var(--p-glow) !important; transform:translateY(-1px); }
.stButton > button:active { transform:translateY(0); }

/* ══ TABS ══ */
.stTabs [data-baseweb="tab-list"] {
    background: var(--bg2) !important;
    border-bottom: 1px solid var(--border) !important;
    gap: 2px !important;
    padding: 0 4px !important;
}
.stTabs [data-baseweb="tab"] {
    font-family:var(--f-ui) !important; font-size:.8rem !important;
    font-weight:600 !important; color:var(--tx3) !important;
    padding:10px 18px !important; background:transparent !important;
    transition: color .15s !important;
}
.stTabs [aria-selected="true"] {
    color:var(--accent2) !important;
    border-bottom:2px solid var(--accent) !important;
    background:rgba(59,130,246,0.06) !important;
    border-radius:6px 6px 0 0 !important;
}

/* ══ DATAFRAME ══ */
.stDataFrame { border:1px solid var(--border) !important; border-radius:12px !important; overflow:hidden !important; }
.stDataFrame thead th { background:var(--bg3) !important; color:var(--tx3) !important; font-family:var(--f-ui) !important; font-size:.63rem !important; text-transform:uppercase !important; letter-spacing:1px !important; font-weight:700 !important; border-bottom:1px solid var(--border) !important; }
.stDataFrame tbody td { font-family:var(--f-mono) !important; font-size:.76rem !important; color:var(--tx) !important; border-bottom:1px solid var(--border) !important; background:var(--bg2) !important; }
.stDataFrame tbody tr:hover td { background:var(--bg3) !important; }

/* ══ SIDEBAR ══ */
[data-testid="stSidebar"] { background:var(--bg2) !important; border-right:1px solid var(--border) !important; }
[data-testid="stSidebar"] label { color:var(--tx2) !important; font-family:var(--f-ui) !important; font-size:.79rem !important; font-weight:600 !important; }
[data-testid="stSidebar"] .stSelectbox > div > div { background:var(--bg3) !important; border-color:var(--border) !important; color:var(--tx) !important; border-radius:8px !important; }

/* ══ INPUTS ══ */
.stSelectbox > div > div,
.stTextInput > div > div > input,
.stNumberInput > div > div > input {
    background:var(--bg3) !important; border:1px solid var(--border) !important;
    border-radius:8px !important; color:var(--tx) !important;
    font-family:var(--f-mono) !important; font-size:.83rem !important;
}
.stSelectbox > div > div:focus-within,
.stTextInput > div > div > input:focus,
.stNumberInput > div > div > input:focus {
    border-color:var(--accent) !important;
    box-shadow:0 0 0 3px rgba(59,130,246,0.12) !important; outline:none !important;
}
.stCheckbox label, .stRadio label { color:var(--tx2) !important; font-family:var(--f-ui) !important; font-size:.8rem !important; font-weight:500 !important; }
.stProgress > div > div { background: linear-gradient(90deg, var(--accent), var(--p3)) !important; border-radius:4px !important; }
.stSlider [data-baseweb="slider"] div[role="slider"] { background:var(--accent) !important; }

/* ══ EXPANDER ══ */
.streamlit-expanderHeader { background:var(--bg2) !important; border:1px solid var(--border) !important; border-radius:10px !important; font-family:var(--f-ui) !important; font-size:.8rem !important; color:var(--tx) !important; font-weight:600 !important; }
.streamlit-expanderHeader:hover { border-color:var(--border-hi) !important; }
.streamlit-expanderContent { background:var(--bg2) !important; border:1px solid var(--border) !important; border-top:none !important; border-radius:0 0 10px 10px !important; }

/* ══ INFO/WARN ══ */
.stAlert { border-radius:10px !important; border:none !important; }

/* ══ LIVE POSITION CARD (auto-trading) ══ */
.live-pos-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 16px 18px;
    margin-bottom: 8px;
    position: relative;
    overflow: hidden;
    animation: fadein 0.3s ease;
}
.live-pos-card.profit { border-left: 3px solid var(--green); background: linear-gradient(135deg, var(--bg2) 0%, rgba(16,185,129,0.04) 100%); }
.live-pos-card.loss   { border-left: 3px solid var(--red);   background: linear-gradient(135deg, var(--bg2) 0%, rgba(239,68,68,0.04) 100%); }
.live-pos-card.break  { border-left: 3px solid var(--accent); }
.live-badge { position:absolute; top:10px; right:12px; display:flex; align-items:center; gap:5px; font-size:.58rem; color:var(--muted); font-family:var(--f-mono); font-weight:600; text-transform:uppercase; letter-spacing:1px; }
.live-dot { width:6px; height:6px; border-radius:50%; background:var(--green); animation: pdot 1.5s ease-in-out infinite; }

/* ══ DAILY STATS BANNER ══ */
.daily-banner {
    background: linear-gradient(135deg, var(--bg2) 0%, var(--bg3) 100%);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 16px 20px;
    display: flex; gap: 24px; align-items: center; flex-wrap: wrap;
    margin-bottom: 12px;
}
.daily-stat { text-align: center; flex: 1; min-width: 80px; }
.daily-stat-val { font-family:var(--f-mono); font-size:1.1rem; font-weight:700; color:var(--white); }
.daily-stat-lbl { font-size:.56rem; color:var(--muted); text-transform:uppercase; letter-spacing:1.5px; margin-top:3px; font-weight:700; }
.daily-sep { width:1px; background:var(--border); align-self:stretch; }

/* ══ GOAL PROGRESS BAR ══ */
.goal-wrap { background:var(--bg3); border-radius:8px; height:8px; overflow:hidden; position:relative; }
.goal-fill { height:8px; border-radius:8px; transition:width .8s ease; }
.goal-fill.on-track  { background:linear-gradient(90deg, var(--green2), var(--green3)); }
.goal-fill.behind    { background:linear-gradient(90deg, var(--red2), var(--red3)); }
.goal-fill.exceeded  { background:linear-gradient(90deg, var(--gold2), var(--gold3)); }

/* ══ SPARKLINE CHART ══ */
.itm-ce { background:rgba(59,130,246,0.07); }
.itm-pe { background:rgba(239,68,68,0.07); }
</style>
"""


# ─── Helper Rendering Functions ───────────────────────────────────────────────

def sig_badge(rec):
    cls = {
        "STRONG BUY":  "sig-sbuy",
        "BUY":         "sig-buy",
        "WEAK BUY":    "sig-wbuy",
        "STRONG SELL": "sig-ssell",
        "SELL":        "sig-sell",
        "WEAK SELL":   "sig-wsell",
        "NEUTRAL":     "sig-neut",
        "AVOID":       "sig-ssell",
    }.get(rec, "sig-neut")
    return f'<span class="sig {cls}">{rec}</span>'


def strength_bar(pct, color=None):
    if color is None:
        if pct >= 80:   color = "var(--green3)"
        elif pct >= 65: color = "var(--accent2)"
        elif pct >= 50: color = "var(--p3)"
        else:           color = "var(--gold3)"
    return (f'<div class="sb-wrap">'
            f'<div class="sb-fill" style="width:{pct}%;background:{color};"></div>'
            f'</div>')


def pnl_fmt(val):
    if val > 0:   return f'<span class="pnl-pos">▲ ₹{val:,.2f}</span>'
    elif val < 0: return f'<span class="pnl-neg">▼ ₹{abs(val):,.2f}</span>'
    return f'<span class="pnl-zero">₹0.00</span>'


def pnl_fmt_large(val):
    """Bigger P&L display for summary cards."""
    if val > 0:
        return f'<span style="color:var(--green3);font-family:var(--f-mono);font-size:1.3rem;font-weight:700;">▲ ₹{val:,.0f}</span>'
    elif val < 0:
        return f'<span style="color:var(--red3);font-family:var(--f-mono);font-size:1.3rem;font-weight:700;">▼ ₹{abs(val):,.0f}</span>'
    return f'<span style="color:var(--gold3);font-family:var(--f-mono);font-size:1.3rem;font-weight:700;">₹0</span>'


def ticker_item(name, price, pct):
    cls   = "t-up" if pct >= 0 else "t-dn"
    arrow = "▲" if pct >= 0 else "▼"
    return (f'<span class="t-item">'
            f'<span class="t-name">{name}</span>'
            f'<span class="{cls}">{price:,.2f} {arrow}{abs(pct):.2f}%</span>'
            f'</span>')


def metric_card(val, lbl, color="var(--accent2)"):
    return (f'<div class="m-card">'
            f'<div class="m-val" style="color:{color};">{val}</div>'
            f'<div class="m-lbl">{lbl}</div>'
            f'</div>')


def metric_card_trend(val, lbl, color, delta=None, delta_label=""):
    """Metric card with optional up/down delta indicator."""
    delta_html = ""
    if delta is not None:
        dc = "var(--green3)" if delta >= 0 else "var(--red3)"
        da = "▲" if delta >= 0 else "▼"
        delta_html = f'<div style="font-size:.6rem;color:{dc};margin-top:3px;font-family:var(--f-mono);">{da} {abs(delta):.1f}% {delta_label}</div>'
    return (f'<div class="m-card">'
            f'<div class="m-val" style="color:{color};">{val}</div>'
            f'<div class="m-lbl">{lbl}</div>'
            f'{delta_html}'
            f'</div>')


def level_box(label, val, css_class):
    return (f'<div class="lvl {css_class}">'
            f'<div style="font-size:.55rem;opacity:.6;margin-bottom:3px;letter-spacing:1px;">{label}</div>'
            f'₹{val:,.2f}'
            f'</div>')


def profit_book_row(pct, price, label, profit_abs):
    return (f'<div class="pb">'
            f'<span class="pb-pct">+{pct}%</span>'
            f'<span class="pb-pr">₹{price:.2f}</span>'
            f'<span class="pb-lbl">{label}</span>'
            f'<span style="font-family:var(--f-mono);font-size:.78rem;'
            f'color:var(--green3);font-weight:700;">+₹{profit_abs:.0f}</span>'
            f'</div>')


def greek_box(val, label, color="var(--tx)"):
    return (f'<div class="gk">'
            f'<div class="gk-v" style="color:{color}">{val}</div>'
            f'<div class="gk-l">{label}</div>'
            f'</div>')


def pill(text, variant="purple"):
    cls = {
        "purple": "pill",
        "green":  "pill pill-green",
        "red":    "pill pill-red",
        "gold":   "pill pill-gold",
        "teal":   "pill pill-teal",
    }.get(variant, "pill")
    return f'<span class="{cls}">{text}</span>'


def rank_badge(n):
    return f'<span class="rank-badge">#{n}</span>'


def hl_box(val, lbl, color="var(--p4)"):
    return (f'<div class="hl-box">'
            f'<div class="hl-val" style="color:{color};">{val}</div>'
            f'<div class="hl-lbl">{lbl}</div>'
            f'</div>')


def live_position_card(pos, trade_type="equity"):
    """
    Render a Bloomberg-style live position card with:
    - Live dot animation
    - Target progress bar
    - P&L with color coding
    - Entry → CMP → Target flow
    """
    pnl   = pos.get("pnl", 0)
    entry = pos.get("entry", 0)
    cmp   = pos.get("cmp", entry)
    target= pos.get("target", entry)
    sl    = pos.get("sl", entry)
    typ   = pos.get("type", "BUY")
    sym   = pos.get("symbol", "").replace(".NS","").replace(".BO","")

    # Progress toward target
    if typ in ("BUY", "LONG", "CE"):
        progress_range = abs(target - entry)
        progress_done  = max(0, cmp - entry)
    else:
        progress_range = abs(entry - target)
        progress_done  = max(0, entry - cmp)

    prog_pct = min(100, (progress_done / progress_range * 100)) if progress_range > 0 else 0
    prog_cls = "tc-progress-green" if pnl >= 0 else "tc-progress-red"
    card_cls = "profit" if pnl >= 0 else ("loss" if pnl < 0 else "break")
    pnl_cls  = "pnl-pos" if pnl >= 0 else "pnl-neg"
    pnl_sym  = "▲" if pnl >= 0 else "▼"
    trail    = f" | Trail: ₹{pos['trailing_sl']:.2f}" if pos.get("trailing_sl") else ""
    pct_move = (cmp - entry) / entry * 100 if entry > 0 else 0

    return f"""
    <div class="live-pos-card {card_cls}">
      <div class="live-badge"><div class="live-dot"></div>LIVE</div>
      <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;align-items:flex-start;">
        <div>
          <div class="tc-head">{typ} {sym}</div>
          <div class="tc-meta">Entry ₹{entry:.2f} → CMP ₹{cmp:.2f} → Target ₹{target:.2f}
            | SL ₹{sl:.2f}{trail}</div>
          <div class="tc-meta" style="margin-top:3px;">
            Move: <span style="color:{'var(--green3)' if pct_move>=0 else 'var(--red3)'};">{pct_move:+.2f}%</span>
            | Qty: {pos.get('qty', pos.get('lots', 1))}
          </div>
        </div>
        <div style="text-align:right;">
          <div class="{pnl_cls}" style="font-size:1.1rem;">{pnl_sym} ₹{abs(pnl):,.2f}</div>
          <div style="font-size:.62rem;color:var(--muted);margin-top:2px;">Net P&amp;L</div>
        </div>
      </div>
      <div class="tc-progress-wrap" style="margin-top:10px;">
        <div class="tc-progress-fill {prog_cls}" style="width:{prog_pct:.1f}%;"></div>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:.58rem;color:var(--muted);margin-top:3px;">
        <span>Entry</span>
        <span style="color:{'var(--green3)' if prog_pct>50 else 'var(--gold3)'};">{prog_pct:.0f}% to target</span>
        <span>Target</span>
      </div>
    </div>
    """


def daily_pnl_banner(realized_pnl, unrealized_pnl, trades_today, win_rate,
                     daily_goal=5000, trades_closed=0):
    """
    Bloomberg-style daily P&L summary banner with goal tracker.
    """
    total_pnl = realized_pnl + unrealized_pnl
    goal_pct  = min(100, max(0, total_pnl / daily_goal * 100)) if daily_goal > 0 else 0
    goal_cls  = "exceeded" if total_pnl >= daily_goal else ("on-track" if total_pnl > 0 else "behind")
    pnl_col   = "var(--green3)" if total_pnl >= 0 else "var(--red3)"
    rpnl_col  = "var(--green3)" if realized_pnl >= 0 else "var(--red3)"
    upnl_col  = "var(--green3)" if unrealized_pnl >= 0 else "var(--red3)"
    wr_col    = "var(--green3)" if win_rate >= 55 else ("var(--gold3)" if win_rate >= 45 else "var(--red3)")

    return f"""
    <div class="daily-pnl-card" style="margin-bottom:14px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;">
        <div>
          <div style="font-size:.58rem;color:var(--muted);text-transform:uppercase;letter-spacing:2px;font-weight:700;margin-bottom:6px;">Today's Performance</div>
          <div style="font-family:var(--f-mono);font-size:1.8rem;font-weight:700;color:{pnl_col};line-height:1;">
            {'▲' if total_pnl>=0 else '▼'} ₹{abs(total_pnl):,.0f}
          </div>
          <div style="font-size:.68rem;color:var(--tx3);margin-top:4px;">
            Realized: <span style="color:{rpnl_col};font-family:var(--f-mono);">₹{realized_pnl:+,.0f}</span> &nbsp;
            Open: <span style="color:{upnl_col};font-family:var(--f-mono);">₹{unrealized_pnl:+,.0f}</span>
          </div>
        </div>
        <div style="display:flex;gap:24px;align-items:center;">
          <div class="daily-stat">
            <div class="daily-stat-val" style="color:var(--accent2);">{trades_today}</div>
            <div class="daily-stat-lbl">Trades</div>
          </div>
          <div class="daily-sep"></div>
          <div class="daily-stat">
            <div class="daily-stat-val" style="color:{wr_col};">{win_rate:.0f}%</div>
            <div class="daily-stat-lbl">Win Rate</div>
          </div>
          <div class="daily-sep"></div>
          <div class="daily-stat">
            <div class="daily-stat-val" style="color:var(--gold3);">{trades_closed}</div>
            <div class="daily-stat-lbl">Closed</div>
          </div>
        </div>
      </div>
      <div style="margin-top:14px;">
        <div style="display:flex;justify-content:space-between;font-size:.62rem;color:var(--muted);margin-bottom:5px;">
          <span>Daily Goal Progress</span>
          <span style="color:{'var(--green3)' if goal_pct>=100 else 'var(--gold3)'};">
            ₹{total_pnl:+,.0f} / ₹{daily_goal:,.0f} ({goal_pct:.0f}%)
          </span>
        </div>
        <div class="goal-wrap">
          <div class="goal-fill {goal_cls}" style="width:{goal_pct:.1f}%;"></div>
        </div>
      </div>
    </div>
    """
