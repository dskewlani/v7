"""
ui.py — ProTrader Terminal v7 — Complete UI Layer
=================================================
All 14 Blocks — UI Helpers & CSS:
  ✅ Block 4a  — Light / Dark theme CSS toggle
  ✅ Block 4c  — Command Palette (Ctrl+K)
  ✅ Block 4d  — Toast notification system
  ✅ Block 5a  — Full light theme CSS variables
  ✅ Block 5b  — Chart theme sync helper
  ✅ Block 5c  — Compact / Comfortable density
  ✅ Block 5d  — Custom accent color picker
  ✅ Block 14e — Mobile-responsive layout
"""

import streamlit as st


# ─── CSS Variables & Theme ────────────────────────────────────────────────────

ACCENT_PALETTES = {
    "Blue":   {"--accent": "#3B82F6", "--accent2": "#1D4ED8", "--p": "#7C3AED", "--p3": "#EDE9FE", "--p4": "#C4B5FD"},
    "Gold":   {"--accent": "#F59E0B", "--accent2": "#D97706", "--p": "#7C3AED", "--p3": "#FEF3C7", "--p4": "#FDE68A"},
    "Teal":   {"--accent": "#14B8A6", "--accent2": "#0F766E", "--p": "#8B5CF6", "--p3": "#CCFBF1", "--p4": "#99F6E4"},
    "Purple": {"--accent": "#A855F7", "--accent2": "#7E22CE", "--p": "#3B82F6", "--p3": "#F3E8FF", "--p4": "#D8B4FE"},
    "Green":  {"--accent": "#22C55E", "--accent2": "#15803D", "--p": "#F59E0B", "--p3": "#DCFCE7", "--p4": "#BBF7D0"},
}


def _palette_vars(name: str) -> str:
    pal = ACCENT_PALETTES.get(name, ACCENT_PALETTES["Blue"])
    return "; ".join(f"{k}: {v}" for k, v in pal.items())


TERMINAL_CSS = """
<style>
/* ── Reset & Font Imports ─────────────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600&family=Space+Grotesk:wght@400;500;600;700&display=swap');

/* ── Dark Theme (default) ─────────────────────────────────────────────── */
:root {
  --bg:      #0A0E1A;
  --bg2:     #0F1525;
  --bg3:     #141B2E;
  --surface: #1A2235;
  --surface2:#1E2840;
  --border:  #263050;
  --border2: #1E2840;

  --accent:  #3B82F6;
  --accent2: #1D4ED8;
  --accent3: rgba(59,130,246,0.12);

  --green:   #22C55E;
  --green2:  #16A34A;
  --green3:  rgba(34,197,94,0.12);
  --red:     #EF4444;
  --red2:    #DC2626;
  --red3:    rgba(239,68,68,0.12);
  --gold:    #F59E0B;
  --gold2:   #D97706;
  --gold3:   rgba(245,158,11,0.12);
  --teal:    #14B8A6;
  --teal2:   #0F766E;
  --teal3:   rgba(20,184,166,0.12);
  --p:       #A855F7;
  --p2:      #7E22CE;
  --p3:      rgba(168,85,247,0.12);
  --p4:      #C4B5FD;

  --tx:      #F0F4FF;
  --tx2:     #A8B4CC;
  --tx3:     #5C6B88;
  --muted:   #3A4560;

  --f-mono:  'IBM Plex Mono', 'Cascadia Code', monospace;
  --f-head:  'Space Grotesk', 'IBM Plex Sans', sans-serif;
  --f-ui:    'IBM Plex Sans', 'Segoe UI', sans-serif;

  --r:       6px;
  --r2:      10px;
  --r3:      14px;

  /* density: comfortable */
  --fs:      14px;
  --lh:      1.6;
  --gap:     16px;
  --pad:     16px 20px;
}

/* ── Light Theme ──────────────────────────────────────────────────────── */
:root[data-theme='light'] {
  --bg:      #F0F4F8;
  --bg2:     #E8EDF5;
  --bg3:     #DDEAF5;
  --surface: #FFFFFF;
  --surface2:#F8FAFC;
  --border:  #CBD5E1;
  --border2: #E2E8F0;

  --accent3: rgba(59,130,246,0.08);

  --green3:  rgba(34,197,94,0.10);
  --red3:    rgba(239,68,68,0.10);
  --gold3:   rgba(245,158,11,0.10);
  --teal3:   rgba(20,184,166,0.10);
  --p3:      rgba(168,85,247,0.10);

  --tx:      #1E293B;
  --tx2:     #475569;
  --tx3:     #94A3B8;
  --muted:   #CBD5E1;
}

/* ── Compact Density ──────────────────────────────────────────────────── */
:root[data-density='compact'] {
  --fs:  13px;
  --lh:  1.45;
  --gap: 10px;
  --pad: 10px 14px;
}

/* ── Base Styles ──────────────────────────────────────────────────────── */
html, body, [data-testid="stAppViewContainer"] {
  background: var(--bg) !important;
  color: var(--tx) !important;
  font-family: var(--f-ui) !important;
  font-size: var(--fs) !important;
  line-height: var(--lh) !important;
}

/* Hide Streamlit chrome */
#MainMenu, footer, [data-testid="stToolbar"],
[data-testid="stDeployButton"], header { display: none !important; }

[data-testid="stSidebar"] {
  background: var(--bg2) !important;
  border-right: 1px solid var(--border) !important;
}

/* Scrollbars */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: var(--bg2); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 99px; }
::-webkit-scrollbar-thumb:hover { background: var(--muted); }

/* Streamlit elements */
[data-testid="stVerticalBlock"] > div { gap: 0 !important; }
.stTabs [data-baseweb="tab-list"] {
  background: var(--bg2) !important;
  border-bottom: 1px solid var(--border) !important;
  gap: 2px;
}
.stTabs [data-baseweb="tab"] {
  background: transparent !important;
  color: var(--tx3) !important;
  border-radius: var(--r) var(--r) 0 0 !important;
  font-family: var(--f-ui) !important;
  font-size: 13px !important;
  padding: 8px 16px !important;
  border: none !important;
}
.stTabs [aria-selected="true"] {
  background: var(--bg) !important;
  color: var(--accent) !important;
  border-bottom: 2px solid var(--accent) !important;
}
.stTabs [data-testid="stTabsContent"] {
  background: var(--bg) !important;
  border: none !important;
  padding: 0 !important;
}

/* Buttons */
.stButton > button {
  background: var(--accent3) !important;
  color: var(--accent) !important;
  border: 1px solid var(--accent) !important;
  border-radius: var(--r) !important;
  font-family: var(--f-ui) !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  padding: 6px 16px !important;
  transition: all .15s ease !important;
}
.stButton > button:hover {
  background: var(--accent) !important;
  color: #fff !important;
}

/* Inputs, selectboxes */
input, textarea, select,
[data-baseweb="input"] input,
[data-baseweb="select"] > div {
  background: var(--surface) !important;
  color: var(--tx) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--r) !important;
  font-family: var(--f-ui) !important;
  font-size: var(--fs) !important;
}
input:focus, textarea:focus {
  border-color: var(--accent) !important;
  outline: none !important;
  box-shadow: 0 0 0 3px var(--accent3) !important;
}

/* Metrics */
[data-testid="stMetric"] {
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--r2) !important;
  padding: 12px 16px !important;
}
[data-testid="stMetric"] label { color: var(--tx3) !important; font-size: 12px !important; }
[data-testid="stMetricValue"] { color: var(--tx) !important; font-size: 22px !important; font-family: var(--f-head) !important; }
[data-testid="stMetricDelta"] svg { display: none !important; }

/* Expanders */
[data-testid="stExpander"] {
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--r2) !important;
}

/* DataFrame */
[data-testid="stDataFrame"] { background: var(--surface) !important; border-radius: var(--r2) !important; }

/* Sliders */
[data-testid="stSlider"] [data-baseweb="slider"] div[role="slider"] {
  background: var(--accent) !important;
}

/* ── Cards & Surfaces ─────────────────────────────────────────────────── */
.pt-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: var(--pad);
  transition: border-color .15s;
}
.pt-card:hover { border-color: var(--border); }

.pt-card-accent {
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: var(--r2);
  padding: var(--pad);
}

.pt-card-green  { border-left: 3px solid var(--green) !important; }
.pt-card-red    { border-left: 3px solid var(--red)   !important; }
.pt-card-gold   { border-left: 3px solid var(--gold)  !important; }
.pt-card-purple { border-left: 3px solid var(--p)     !important; }

/* ── Typography ───────────────────────────────────────────────────────── */
.pt-h1 { font-family: var(--f-head); font-size: 22px; font-weight: 700; color: var(--tx); margin: 0 0 4px; letter-spacing: -.3px; }
.pt-h2 { font-family: var(--f-head); font-size: 17px; font-weight: 600; color: var(--tx); margin: 0 0 2px; }
.pt-h3 { font-family: var(--f-head); font-size: 14px; font-weight: 600; color: var(--tx2); margin: 0; text-transform: uppercase; letter-spacing: .6px; }
.pt-mono { font-family: var(--f-mono); font-size: 13px; }
.pt-label { font-size: 11px; color: var(--tx3); text-transform: uppercase; letter-spacing: .8px; }

/* ── Badges ───────────────────────────────────────────────────────────── */
.badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 8px; border-radius: 99px;
  font-size: 11px; font-weight: 600; letter-spacing: .4px;
  font-family: var(--f-mono);
}
.badge-buy    { background: var(--green3); color: var(--green); border: 1px solid var(--green2); }
.badge-sell   { background: var(--red3);   color: var(--red);   border: 1px solid var(--red2); }
.badge-neutral{ background: var(--bg3);    color: var(--tx3);   border: 1px solid var(--border); }
.badge-gold   { background: var(--gold3);  color: var(--gold);  border: 1px solid var(--gold2); }
.badge-teal   { background: var(--teal3);  color: var(--teal);  border: 1px solid var(--teal2); }
.badge-purple { background: var(--p3);     color: var(--p);     border: 1px solid var(--p2); }
.badge-be     { background: var(--teal3);  color: var(--teal);  border: 1px solid var(--teal); font-size: 10px; }

/* ── Strength Bar ─────────────────────────────────────────────────────── */
.strength-wrap { display: flex; align-items: center; gap: 6px; }
.strength-bar  { flex: 1; height: 4px; background: var(--bg3); border-radius: 99px; overflow: hidden; }
.strength-fill { height: 100%; border-radius: 99px; transition: width .4s ease; }

/* ── Ticker Bar ───────────────────────────────────────────────────────── */
.ticker-bar {
  background: var(--bg2);
  border-bottom: 1px solid var(--border);
  padding: 6px 20px;
  display: flex; gap: 28px; overflow-x: auto;
  white-space: nowrap; scrollbar-width: none;
}
.ticker-bar::-webkit-scrollbar { display: none; }
.ticker-item { display: flex; flex-direction: column; align-items: center; }
.ticker-sym  { font-size: 10px; color: var(--tx3); font-family: var(--f-mono); }
.ticker-val  { font-size: 13px; font-weight: 600; font-family: var(--f-mono); }
.ticker-chg  { font-size: 10px; }

/* ── Position Card ────────────────────────────────────────────────────── */
.pos-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 14px 16px;
  margin-bottom: 8px;
  position: relative;
  transition: border-color .15s;
}
.pos-card:hover { border-color: var(--accent); }
.pos-card-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.pos-card-sym  { font-family: var(--f-head); font-size: 16px; font-weight: 700; color: var(--tx); }
.pos-card-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.pos-card-stat { display: flex; flex-direction: column; }
.pos-card-stat .lbl { font-size: 10px; color: var(--tx3); margin-bottom: 1px; }
.pos-card-stat .val { font-size: 13px; font-family: var(--f-mono); font-weight: 500; color: var(--tx); }

/* ── Banner / Goal Bar ────────────────────────────────────────────────── */
.banner {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 14px 20px;
  display: flex; align-items: center; gap: 24px;
  flex-wrap: wrap;
}
.goal-bar { flex: 1; min-width: 180px; }
.goal-track { height: 6px; background: var(--bg3); border-radius: 99px; overflow: hidden; }
.goal-fill  { height: 100%; background: var(--green); border-radius: 99px; transition: width .6s ease; }

/* ── Scanners ─────────────────────────────────────────────────────────── */
.scan-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px; border-radius: var(--r); cursor: pointer;
  border-bottom: 1px solid var(--border2);
  transition: background .1s;
}
.scan-row:hover { background: var(--surface); }
.scan-row:last-child { border-bottom: none; }

/* ── Options Chain ────────────────────────────────────────────────────── */
.chain-table { width: 100%; border-collapse: collapse; font-size: 12px; font-family: var(--f-mono); }
.chain-table th { background: var(--bg2); color: var(--tx3); font-size: 10px; letter-spacing: .6px; text-transform: uppercase; padding: 6px 8px; border-bottom: 1px solid var(--border); }
.chain-table td { padding: 5px 8px; border-bottom: 1px solid var(--border2); color: var(--tx2); }
.chain-table tr.atm td { background: var(--accent3) !important; color: var(--tx) !important; font-weight: 600; }
.chain-table tr:hover td { background: var(--surface); }

/* ── Command Palette ──────────────────────────────────────────────────── */
#pt-palette-overlay {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,.6); backdrop-filter: blur(6px);
  z-index: 9999; align-items: flex-start; justify-content: center;
  padding-top: 15vh;
}
#pt-palette-overlay.open { display: flex; }
#pt-palette {
  width: min(600px, 90vw);
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r3); overflow: hidden;
  box-shadow: 0 24px 80px rgba(0,0,0,.5);
}
#pt-palette-input {
  width: 100%; padding: 14px 18px;
  background: transparent; border: none; border-bottom: 1px solid var(--border);
  color: var(--tx); font-size: 16px; font-family: var(--f-ui);
  outline: none;
}
#pt-palette-results { max-height: 340px; overflow-y: auto; }
.pt-pal-item {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 18px; cursor: pointer; font-size: 14px; color: var(--tx2);
}
.pt-pal-item:hover, .pt-pal-item.active { background: var(--accent3); color: var(--tx); }
.pt-pal-item .ico { font-size: 16px; width: 22px; }

/* ── Toast Notifications ─────────────────────────────────────────────── */
#pt-toast-stack {
  position: fixed; top: 20px; right: 20px;
  z-index: 10000; display: flex; flex-direction: column; gap: 8px;
  pointer-events: none;
}
.pt-toast {
  display: flex; align-items: center; gap: 10px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r2); padding: 12px 16px;
  font-size: 13px; color: var(--tx);
  box-shadow: 0 8px 30px rgba(0,0,0,.35);
  pointer-events: all; min-width: 260px; max-width: 360px;
  animation: toastIn .25s ease;
}
.pt-toast.buy  { border-left: 3px solid var(--green); }
.pt-toast.sell { border-left: 3px solid var(--red); }
.pt-toast.warn { border-left: 3px solid var(--gold); }
.pt-toast.info { border-left: 3px solid var(--accent); }
@keyframes toastIn { from { opacity:0; transform: translateX(30px); } to { opacity:1; transform:none; } }
@keyframes toastOut { to { opacity:0; transform: translateX(30px); } }

/* ── Heatmap Calendar ─────────────────────────────────────────────────── */
.cal-grid { display: flex; gap: 3px; }
.cal-day  {
  width: 12px; height: 12px; border-radius: 2px;
  background: var(--bg3); cursor: pointer;
  transition: transform .1s;
}
.cal-day:hover { transform: scale(1.4); }

/* ── Mobile Responsive ────────────────────────────────────────────────── */
@media (max-width: 768px) {
  :root { --fs: 13px; --pad: 10px 12px; }
  .pos-card-grid { grid-template-columns: repeat(2, 1fr); }
  .ticker-bar { gap: 16px; }
  .banner { flex-direction: column; gap: 12px; }
  .stButton > button { min-height: 44px !important; }  /* touch targets */
  [data-testid="stSidebar"] { display: none; }
}

/* ── Dividers ─────────────────────────────────────────────────────────── */
.pt-divider { border: none; border-top: 1px solid var(--border); margin: 14px 0; }

/* ── Blinking dot ─────────────────────────────────────────────────────── */
@keyframes blink { 50% { opacity: 0; } }
.blink { animation: blink 1.2s step-start infinite; }
</style>
"""


def inject_css(theme: str = "dark", density: str = "comfortable", palette: str = "Blue") -> None:
    """Inject full CSS + set theme/density/palette attributes via JS."""
    pal_vars = _palette_vars(palette)
    js = f"""
    <script>
    (function(){{
      var r = document.documentElement;
      r.setAttribute('data-theme', '{theme}');
      r.setAttribute('data-density', '{density}');
      // Apply palette overrides
      var vars = `{pal_vars}`.split(';');
      vars.forEach(function(v){{
        var parts = v.split(':');
        if(parts.length===2) r.style.setProperty(parts[0].trim(), parts[1].trim());
      }});
    }})();
    </script>
    """
    st.markdown(TERMINAL_CSS + js, unsafe_allow_html=True)


def inject_command_palette(symbols: list = None, tabs: list = None) -> None:
    """Block 4c: Inject JS-based Ctrl+K command palette."""
    sym_list = symbols or []
    tab_list = tabs or ["Equity", "Options", "Futures", "ETF", "MCX", "Auto-Trade", "Analytics", "Journal"]

    items_json = str([
        *[{"icon": "📈", "label": s, "action": f"symbol:{s}"} for s in sym_list[:30]],
        *[{"icon": "🗂️", "label": t, "action": f"tab:{t}"}   for t in tab_list],
        {"icon": "🌓", "label": "Toggle Theme",     "action": "theme"},
        {"icon": "🔍", "label": "Scan All",          "action": "scan"},
        {"icon": "📊", "label": "Analytics",         "action": "tab:Analytics"},
        {"icon": "📓", "label": "Journal",           "action": "tab:Journal"},
    ]).replace("'", '"').replace("True", "true").replace("False", "false")

    html = f"""
    <div id="pt-palette-overlay">
      <div id="pt-palette">
        <input id="pt-palette-input" placeholder="Search symbols, tabs, actions..." autocomplete="off"/>
        <div id="pt-palette-results"></div>
        <div style="padding:8px 18px;font-size:11px;color:var(--tx3);border-top:1px solid var(--border)">
          ↑↓ navigate &nbsp;·&nbsp; Enter select &nbsp;·&nbsp; Esc close
        </div>
      </div>
    </div>
    <div id="pt-toast-stack"></div>

    <script>
    (function(){{
      var ITEMS = {items_json};
      var overlay = document.getElementById('pt-palette-overlay');
      var input   = document.getElementById('pt-palette-input');
      var results = document.getElementById('pt-palette-results');
      var active  = 0;

      function render(q){{
        var filtered = q ? ITEMS.filter(i => i.label.toLowerCase().includes(q.toLowerCase())) : ITEMS;
        results.innerHTML = filtered.slice(0,12).map(function(i,idx){{
          return '<div class="pt-pal-item'+(idx===0?' active':'')+'" data-action="'+i.action+'">'
               + '<span class="ico">'+i.icon+'</span>'+i.label+'</div>';
        }}).join('');
        active = 0;
        attachClicks();
      }}

      function attachClicks(){{
        var items = results.querySelectorAll('.pt-pal-item');
        items.forEach(function(el){{
          el.addEventListener('click', function(){{ execAction(el.dataset.action); }});
        }});
      }}

      function execAction(action){{
        overlay.classList.remove('open');
        if(action.startsWith('symbol:')){{
          window.ptShowSymbol && window.ptShowSymbol(action.slice(7));
        }} else if(action === 'theme'){{
          var r = document.documentElement;
          var t = r.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
          r.setAttribute('data-theme', t);
        }} else {{
          showToast('Navigating to ' + action.replace('tab:',''), 'info');
        }}
      }}

      document.addEventListener('keydown', function(e){{
        if((e.ctrlKey || e.metaKey) && e.key === 'k'){{
          e.preventDefault();
          overlay.classList.toggle('open');
          if(overlay.classList.contains('open')){{ input.value=''; render(''); input.focus(); }}
        }}
        if(overlay.classList.contains('open')){{
          var items = results.querySelectorAll('.pt-pal-item');
          if(e.key==='ArrowDown'){{ active=Math.min(active+1,items.length-1); }}
          if(e.key==='ArrowUp'){{ active=Math.max(active-1,0); }}
          if(e.key==='Escape'){{ overlay.classList.remove('open'); }}
          if(e.key==='Enter' && items[active]){{ execAction(items[active].dataset.action); }}
          items.forEach(function(el,i){{ el.classList.toggle('active', i===active); }});
        }}
      }});

      overlay.addEventListener('click', function(e){{ if(e.target===overlay) overlay.classList.remove('open'); }});
      input.addEventListener('input', function(){{ render(input.value); active=0; }});
      render('');

      /* ── Toast system ───────────────────────────────────────────── */
      window.showToast = function(msg, type, dur){{
        type = type || 'info'; dur = dur || 4000;
        var icons = {{ buy:'✅', sell:'🔴', warn:'⚠️', info:'ℹ️' }};
        var stack = document.getElementById('pt-toast-stack');
        var t = document.createElement('div');
        t.className = 'pt-toast ' + type;
        t.innerHTML = '<span>'+icons[type]+'</span><span>'+msg+'</span>';
        stack.appendChild(t);
        setTimeout(function(){{
          t.style.animation = 'toastOut .3s ease forwards';
          setTimeout(function(){{ t.remove(); }}, 300);
        }}, dur);
      }};
    }})();
    </script>
    """
    st.markdown(html, unsafe_allow_html=True)


def show_toast_js(message: str, toast_type: str = "info") -> None:
    """Trigger a toast notification from Python side."""
    st.markdown(
        f'<script>window.showToast && window.showToast({repr(message)}, "{toast_type}");</script>',
        unsafe_allow_html=True,
    )


# ─── Colour helpers ───────────────────────────────────────────────────────────

def pnl_color(pnl: float) -> str:
    return "var(--green)" if pnl >= 0 else "var(--red)"


def pnl_fmt(pnl: float, show_pct: float = None) -> str:
    sign = "+" if pnl >= 0 else ""
    base = f"₹{sign}{pnl:,.0f}"
    if show_pct is not None:
        sp = "+" if show_pct >= 0 else ""
        base += f" ({sp}{show_pct:.2f}%)"
    return base


def sig_badge(rec: str) -> str:
    rec_u = rec.upper()
    if "STRONG BUY"  in rec_u: cls, lbl = "buy",    "⬆ STRONG BUY"
    elif "BUY"       in rec_u: cls, lbl = "buy",    "▲ BUY"
    elif "STRONG SEL" in rec_u: cls, lbl = "sell",  "⬇ STRONG SELL"
    elif "SELL"      in rec_u: cls, lbl = "sell",   "▼ SELL"
    elif "WEAK"      in rec_u: cls, lbl = "gold",   "~ WEAK"
    else:                       cls, lbl = "neutral","● NEUTRAL"
    return f'<span class="badge badge-{cls}">{lbl}</span>'


def strength_bar(strength: int, rec: str = "") -> str:
    color = "var(--green)" if "BUY" in rec.upper() else ("var(--red)" if "SELL" in rec.upper() else "var(--tx3)")
    return f"""
    <div class="strength-wrap">
      <div class="strength-bar">
        <div class="strength-fill" style="width:{strength}%;background:{color}"></div>
      </div>
      <span style="font-size:11px;font-family:var(--f-mono);color:{color};min-width:32px">{strength}%</span>
    </div>"""


# ─── Component helpers ────────────────────────────────────────────────────────

def metric_card(label: str, value: str, delta: str = "", color: str = "var(--tx)",
                icon: str = "", suffix: str = "") -> str:
    delta_html = ""
    if delta:
        d_color = "var(--green)" if "+" in delta else ("var(--red)" if "-" in delta else "var(--tx3)")
        delta_html = f'<span style="font-size:12px;color:{d_color};margin-left:6px">{delta}</span>'
    return f"""
    <div class="pt-card" style="text-align:center">
      <div class="pt-label">{icon} {label}</div>
      <div style="font-size:22px;font-weight:700;font-family:var(--f-head);color:{color};margin-top:4px">
        {value}{suffix}{delta_html}
      </div>
    </div>"""


def live_position_card(pos: dict, lp: float | None = None) -> str:
    """Render a rich position card with P&L, SL, targets, badges."""
    sym     = pos.get("symbol", "N/A")
    typ     = pos.get("type",   "BUY")
    entry   = pos.get("entry",  0)
    target  = pos.get("target", 0)
    sl_     = pos.get("sl",     0)
    qty     = pos.get("qty",    pos.get("lots", 1))
    pnl     = pos.get("pnl",   0.0)
    stren   = pos.get("strength", 0)
    rec     = pos.get("rec",   "NEUTRAL")
    be_badge = pos.get("be_badge", "")
    cmp     = lp or pos.get("cmp", entry)
    trail   = pos.get("trailing_sl")
    t1      = pos.get("target_1", target)
    t2      = pos.get("target_2", target)
    pnl_col = pnl_color(pnl)
    typ_col = "var(--green)" if typ in ("BUY","CE","LONG") else "var(--red)"
    be_html = f'<span class="badge badge-be">{be_badge}</span>' if be_badge else ""
    trail_html = (f'<div class="pos-card-stat"><span class="lbl">Trail SL</span>'
                  f'<span class="val" style="color:var(--gold)">₹{trail:,.2f}</span></div>') if trail else ""

    return f"""
    <div class="pos-card">
      <div class="pos-card-header">
        <div style="display:flex;align-items:center;gap:8px">
          <span class="pos-card-sym">{sym}</span>
          <span style="font-size:11px;font-weight:700;color:{typ_col}">{typ}</span>
          {sig_badge(rec)} {be_html}
        </div>
        <div style="font-size:18px;font-weight:700;font-family:var(--f-mono);color:{pnl_col}">
          {pnl_fmt(pnl)}
        </div>
      </div>
      {strength_bar(stren, rec)}
      <div class="pos-card-grid" style="margin-top:10px">
        <div class="pos-card-stat"><span class="lbl">Entry</span><span class="val">₹{entry:,.2f}</span></div>
        <div class="pos-card-stat"><span class="lbl">CMP</span><span class="val" style="color:{typ_col}">₹{cmp:,.2f}</span></div>
        <div class="pos-card-stat"><span class="lbl">Target T1</span><span class="val" style="color:var(--green)">₹{t1:,.2f}</span></div>
        <div class="pos-card-stat"><span class="lbl">Target T2</span><span class="val" style="color:var(--green)">₹{t2:,.2f}</span></div>
        <div class="pos-card-stat"><span class="lbl">Stop Loss</span><span class="val" style="color:var(--red)">₹{sl_:,.2f}</span></div>
        <div class="pos-card-stat"><span class="lbl">Qty</span><span class="val">{qty}</span></div>
        {trail_html}
      </div>
    </div>"""


def daily_pnl_banner(stats: dict) -> str:
    """Render daily P&L banner with goal progress bar."""
    total    = stats.get("total", 0)
    realized = stats.get("realized", 0)
    unreal   = stats.get("unrealized", 0)
    goal     = stats.get("daily_goal", 5000)
    goal_pct = min(150, stats.get("goal_pct", 0))
    wr       = stats.get("win_rate", 0)
    trades   = stats.get("trades_today", 0)
    t_open   = stats.get("trades_open", 0)
    var95    = stats.get("var_95", 0)
    t_color  = pnl_color(total)
    bar_color = "var(--green)" if total >= 0 else "var(--red)"
    bar_w     = min(100, abs(goal_pct))

    return f"""
    <div class="banner">
      <div style="display:flex;flex-direction:column;min-width:120px">
        <span class="pt-label">Total P&amp;L</span>
        <span style="font-size:26px;font-weight:700;font-family:var(--f-head);color:{t_color}">{pnl_fmt(total)}</span>
        <span style="font-size:11px;color:var(--tx3)">
          Realized: {pnl_fmt(realized)} &nbsp;|&nbsp; Open: {pnl_fmt(unreal)}
        </span>
      </div>
      <div class="goal-bar">
        <div style="display:flex;justify-content:space-between;margin-bottom:4px">
          <span class="pt-label">Daily Goal ₹{goal:,.0f}</span>
          <span style="font-size:12px;color:{bar_color};font-weight:600">{goal_pct:.0f}%</span>
        </div>
        <div class="goal-track">
          <div class="goal-fill" style="width:{bar_w}%;background:{bar_color}"></div>
        </div>
      </div>
      <div style="display:flex;gap:20px;flex-wrap:wrap">
        <div><span class="pt-label">Win Rate</span><br>
          <span style="font-size:16px;font-weight:600;color:var(--green)">{wr:.0f}%</span></div>
        <div><span class="pt-label">Trades</span><br>
          <span style="font-size:16px;font-weight:600">{trades} closed · {t_open} open</span></div>
        <div><span class="pt-label">VaR 95%</span><br>
          <span style="font-size:16px;font-weight:600;color:var(--red)">₹{var95:,.0f}</span></div>
      </div>
    </div>"""


def ticker_bar(indices: dict) -> str:
    """Render top market indices ticker bar."""
    names = {"NF": "NIFTY", "BN": "BANK NF", "VIX": "VIX", "SX": "SENSEX", "IT": "IT", "MID": "MIDCAP"}
    items = []
    for key, label in names.items():
        d = indices.get(key, {})
        p   = d.get("p", 0)
        pct = d.get("pct", 0)
        col = "var(--green)" if pct >= 0 else "var(--red)"
        arr = "▲" if pct >= 0 else "▼"
        items.append(f"""
        <div class="ticker-item">
          <span class="ticker-sym">{label}</span>
          <span class="ticker-val">{p:,.2f}</span>
          <span class="ticker-chg" style="color:{col}">{arr} {abs(pct):.2f}%</span>
        </div>""")
    return f'<div class="ticker-bar">{"".join(items)}</div>'


def regime_badge(regime: str) -> str:
    mapping = {
        "TRENDING": ("badge-buy",    "📈 TRENDING"),
        "SIDEWAYS": ("badge-neutral","↔ SIDEWAYS"),
        "VOLATILE": ("badge-sell",   "⚡ VOLATILE"),
        "NEUTRAL":  ("badge-gold",   "● NEUTRAL"),
    }
    cls, lbl = mapping.get(regime, ("badge-neutral", regime))
    return f'<span class="badge {cls}">{lbl}</span>'


def time_filter_badge(valid: bool, msg: str) -> str:
    cls = "badge-buy" if valid else "badge-gold"
    return f'<span class="badge {cls}">{msg}</span>'


def section_header(title: str, subtitle: str = "", icon: str = "") -> str:
    sub = f'<div style="font-size:12px;color:var(--tx3);margin-top:2px">{subtitle}</div>' if subtitle else ""
    return f"""
    <div style="margin: 18px 0 12px">
      <div class="pt-h2">{icon} {title}</div>
      {sub}
    </div>"""


def get_chart_theme(theme: str = None) -> dict:
    """Block 5b: Return Plotly theme dict for current mode."""
    t = theme or st.session_state.get("theme", "dark")
    if t == "light":
        return {
            "paper_bgcolor": "#FFFFFF",
            "plot_bgcolor":  "#F8FAFC",
            "font_color":    "#1E293B",
            "grid_color":    "#E2E8F0",
            "border_color":  "#CBD5E1",
        }
    return {
        "paper_bgcolor": "#0F1525",
        "plot_bgcolor":  "#0A0E1A",
        "font_color":    "#A8B4CC",
        "grid_color":    "#1E2840",
        "border_color":  "#263050",
    }


def accent_color_picker() -> None:
    """Block 5d: Render palette selector buttons."""
    st.markdown('<div class="pt-label" style="margin-bottom:8px">Accent Palette</div>', unsafe_allow_html=True)
    cols = st.columns(len(ACCENT_PALETTES))
    for i, (name, pal) in enumerate(ACCENT_PALETTES.items()):
        with cols[i]:
            if st.button(name, key=f"pal_{name}"):
                st.session_state["palette"] = name
                import storage as db
                db.save("ui_palette", name)
                st.rerun()
    # Apply selected
    current = st.session_state.get("palette", "Blue")
    pal_vars = _palette_vars(current)
    st.markdown(
        f"<script>var r=document.documentElement;`{pal_vars}`.split(';').forEach(function(v){{"
        "var p=v.split(':');if(p.length===2)r.style.setProperty(p[0].trim(),p[1].trim());}});</script>",
        unsafe_allow_html=True,
    )


def density_toggle() -> None:
    """Block 5c: Density toggle."""
    density = st.session_state.get("density", "comfortable")
    label = "⬛ Compact" if density == "comfortable" else "⬜ Comfortable"
    if st.button(label, key="density_toggle"):
        new = "compact" if density == "comfortable" else "comfortable"
        st.session_state["density"] = new
        import storage as db
        db.save("ui_density", new)
        st.markdown(
            f"<script>document.documentElement.setAttribute('data-density','{new}');</script>",
            unsafe_allow_html=True,
        )


def theme_toggle_button() -> None:
    """Block 4a: Light/Dark theme toggle button."""
    current = st.session_state.get("theme", "dark")
    icon = "☀️" if current == "dark" else "🌙"
    if st.button(f"{icon}", key="theme_toggle", help="Toggle light/dark theme"):
        new = "light" if current == "dark" else "dark"
        st.session_state["theme"] = new
        import storage as db
        db.save("ui_theme", new)
        st.markdown(
            f"<script>document.documentElement.setAttribute('data-theme','{new}');</script>",
            unsafe_allow_html=True,
        )
        st.rerun()


def sidebar_watchlist(symbols: list, prices: dict) -> None:
    """Block 4b: Collapsible sidebar watchlist with live prices."""
    st.sidebar.markdown(section_header("📌 Watchlist"), unsafe_allow_html=True)
    for sym in symbols[:20]:
        d = prices.get(sym, {})
        p   = d.get("p",   0)
        pct = d.get("pct", 0)
        col = "var(--green)" if pct >= 0 else "var(--red)"
        arr = "▲" if pct >= 0 else "▼"
        short = sym.replace(".NS","").replace(".BO","")
        st.sidebar.markdown(
            f"""<div style="display:flex;justify-content:space-between;padding:4px 0;
            border-bottom:1px solid var(--border2);cursor:pointer">
              <span style="font-size:13px;font-weight:600">{short}</span>
              <div style="text-align:right">
                <div style="font-size:13px;font-family:var(--f-mono)">₹{p:,.2f}</div>
                <div style="font-size:11px;color:{col}">{arr}{abs(pct):.2f}%</div>
              </div>
            </div>""",
            unsafe_allow_html=True,
        )
