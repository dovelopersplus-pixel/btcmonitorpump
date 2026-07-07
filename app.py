import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─────────────────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="BTC Pre-Pump Alert | WhaleRadar",
    page_icon="🐋",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600&display=swap');

html, body, [data-testid="stAppViewContainer"] {
    background: #060914 !important;
    color: #e8eaf0 !important;
    font-family: 'Inter', sans-serif !important;
}
[data-testid="stAppViewContainer"]::before {
    content:''; position:fixed; inset:0; pointer-events:none; z-index:0;
    background: radial-gradient(ellipse at 15% 15%, rgba(0,255,136,0.05) 0%, transparent 50%),
                radial-gradient(ellipse at 85% 85%, rgba(77,166,255,0.05) 0%, transparent 50%);
}
[data-testid="stSidebar"] {
    background: rgba(8,12,22,0.98) !important;
    border-right: 1px solid rgba(255,255,255,0.07) !important;
}
[data-testid="stSidebar"] * { color: #e8eaf0 !important; }
.stButton > button {
    background: linear-gradient(135deg,#00ff88,#00cc66) !important;
    color: #060914 !important; border:none !important; border-radius:10px !important;
    font-weight:700 !important; text-transform:uppercase !important; letter-spacing:0.05em !important;
    transition: all 0.3s ease !important;
}
.stButton > button:hover { transform:translateY(-2px) !important; box-shadow:0 8px 25px rgba(0,255,136,0.3) !important; }
div[data-testid="metric-container"] {
    background: rgba(13,17,30,0.85) !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
    border-radius: 14px !important;
    padding: 1rem 1.2rem !important;
    backdrop-filter: blur(20px) !important;
}
.stMetric label { color:#8892aa !important; font-size:0.72rem !important; text-transform:uppercase !important; letter-spacing:0.08em !important; }
.stMetric [data-testid="metric-value"] { color:#e8eaf0 !important; font-family:'JetBrains Mono',monospace !important; font-weight:700 !important; }
.stDataFrame { border-radius:12px !important; overflow:hidden !important; }
@keyframes pulseAlert {
    0%,100% { box-shadow:0 0 15px rgba(255,215,0,0.3); }
    50%      { box-shadow:0 0 40px rgba(255,215,0,0.65); }
}
.alert-box { animation: pulseAlert 2s ease-in-out infinite; }
@keyframes slideIn {
    from { opacity:0; transform:translateX(-8px); }
    to   { opacity:1; transform:translateX(0); }
}
.slide-in { animation: slideIn 0.35s ease; }
hr { border-color: rgba(255,255,255,0.07) !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
EXCHANGES = {
    "Binance":     {"color": "#F0B90B", "emoji": "🔶"},
    "Bybit":       {"color": "#F7931A", "emoji": "🟠"},
    "Coinbase":    {"color": "#0052FF", "emoji": "🔵"},
    "Kraken":      {"color": "#5741D9", "emoji": "🟣"},
    "Gate.io":     {"color": "#00B2B2", "emoji": "🩵"},
    "Hyperliquid": {"color": "#00FF88", "emoji": "🟢"},
}
BIG_WHALE        = 500_000
MEGA_WHALE       = 1_000_000
PRICE_RANGE_PCT  = 0.05   # Only orders within ±5% of current BTC price are valid

# ─────────────────────────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────────────────────────
for key, val in [
    ("whale_orders", []),
    ("alerts", []),
    ("last_price", None),
    ("buy_pressure", {}),
    ("sell_pressure", {}),
    ("total_buy_vol", 0.0),
    ("total_sell_vol", 0.0),
    ("exchange_status", {}),   # NEW: track per-exchange success/fail
    ("fetch_count", 0),
]:
    if key not in st.session_state:
        st.session_state[key] = val

# ─────────────────────────────────────────────────────────────────────────────
# FETCH FUNCTIONS — PARALLEL with status reporting
# ─────────────────────────────────────────────────────────────────────────────

def fetch_btc_price():
    """Try multiple sources for BTC price to avoid cloud IP bans."""
    urls = [
        ("Binance", "https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT"),
        ("Kraken",  "https://api.kraken.com/0/public/Ticker?pair=XBTUSD"),
        ("Coinbase","https://api.exchange.coinbase.com/products/BTC-USD/ticker"),
        ("Bybit",   "https://api.bytick.com/v5/market/tickers?category=spot&symbol=BTCUSDT"),
    ]
    for name, url in urls:
        try:
            r = requests.get(url, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                data = r.json()
                if name == "Binance":
                    return float(data["price"])
                elif name == "Kraken":
                    result = data.get("result", {})
                    book = result.get("XXBTZUSD") or result.get("XBTUSD") or list(result.values())[0]
                    return float(book["c"][0])
                elif name == "Coinbase":
                    return float(data["price"])
                elif name == "Bybit":
                    return float(data["result"]["list"][0]["lastPrice"])
        except Exception:
            continue
    return None


def _parse_book(entries, side, exchange, threshold_usd, current_price=None):
    """Convert raw book entries to whale order dicts.
    Filters out orders with prices outside ±PRICE_RANGE_PCT of current market price.
    This removes Coinbase stale/historical price levels (e.g. $744K when BTC=$63K).
    """
    results = []
    for entry in entries:
        try:
            p = float(entry[0])
            q = float(entry[1])
            if p <= 0 or q <= 0:
                continue
            # Price sanity check — must be within ±5% of current BTC price
            if current_price and current_price > 0:
                deviation = abs(p - current_price) / current_price
                if deviation > PRICE_RANGE_PCT:
                    continue  # Skip stale / unrealistic price levels
            usd = p * q
            if usd >= threshold_usd:
                results.append({
                    "exchange": exchange,
                    "side": side,
                    "price": p,
                    "qty_btc": q,
                    "usd_value": usd,
                    "time": datetime.now(),
                })
        except Exception:
            pass
    return results


def fetch_binance(threshold_usd, current_price=None):
    try:
        r = requests.get(
            "https://data-api.binance.vision/api/v3/depth?symbol=BTCUSDT&limit=500",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        r.raise_for_status()
        data = r.json()
        orders  = _parse_book(data.get("bids", []), "BUY",  "Binance", threshold_usd, current_price)
        orders += _parse_book(data.get("asks", []), "SELL", "Binance", threshold_usd, current_price)
        return "Binance", orders, None
    except Exception as e:
        return "Binance", [], str(e)


def fetch_bybit(threshold_usd, current_price=None):
    try:
        r = requests.get(
            "https://api.bytick.com/v5/market/orderbook?category=spot&symbol=BTCUSDT&limit=200",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        r.raise_for_status()
        data = r.json().get("result", {})
        orders  = _parse_book(data.get("b", []), "BUY",  "Bybit", threshold_usd, current_price)
        orders += _parse_book(data.get("a", []), "SELL", "Bybit", threshold_usd, current_price)
        return "Bybit", orders, None
    except Exception as e:
        return "Bybit", [], str(e)


def fetch_coinbase(threshold_usd, current_price=None):
    try:
        r = requests.get(
            "https://api.exchange.coinbase.com/products/BTC-USD/book?level=2",
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        orders  = _parse_book(data.get("bids", []), "BUY",  "Coinbase", threshold_usd, current_price)
        orders += _parse_book(data.get("asks", []), "SELL", "Coinbase", threshold_usd, current_price)
        return "Coinbase", orders, None
    except Exception as e:
        return "Coinbase", [], str(e)


def fetch_kraken(threshold_usd, current_price=None):
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Depth?pair=XBTUSD&count=500",
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        r.raise_for_status()
        result = r.json().get("result", {})
        book = result.get("XXBTZUSD") or result.get("XBTUSD") or result.get("XBT/USD") or {}
        if not book and result:
            book = list(result.values())[0] if result else {}
        orders  = _parse_book(book.get("bids", []), "BUY",  "Kraken", threshold_usd, current_price)
        orders += _parse_book(book.get("asks", []), "SELL", "Kraken", threshold_usd, current_price)
        return "Kraken", orders, None
    except Exception as e:
        return "Kraken", [], str(e)


def fetch_gateio(threshold_usd, current_price=None):
    try:
        r = requests.get(
            "https://api.gateio.ws/api/v4/spot/order_book?currency_pair=BTC_USDT&limit=200",
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        orders  = _parse_book(data.get("bids", []), "BUY",  "Gate.io", threshold_usd, current_price)
        orders += _parse_book(data.get("asks", []), "SELL", "Gate.io", threshold_usd, current_price)
        return "Gate.io", orders, None
    except Exception as e:
        return "Gate.io", [], str(e)


def fetch_hyperliquid(threshold_usd, current_price=None):
    try:
        r = requests.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "l2Book", "coin": "BTC"},
            timeout=10
        )
        r.raise_for_status()
        levels = r.json().get("levels", [[], []])
        bids = levels[0] if len(levels) > 0 else []
        asks = levels[1] if len(levels) > 1 else []
        bids_std = [[e.get("px", 0), e.get("sz", 0)] for e in bids]
        asks_std = [[e.get("px", 0), e.get("sz", 0)] for e in asks]
        orders  = _parse_book(bids_std, "BUY",  "Hyperliquid", threshold_usd, current_price)
        orders += _parse_book(asks_std, "SELL", "Hyperliquid", threshold_usd, current_price)
        return "Hyperliquid", orders, None
    except Exception as e:
        return "Hyperliquid", [], str(e)


FETCHERS = {
    "Binance":     fetch_binance,
    "Bybit":       fetch_bybit,
    "Coinbase":    fetch_coinbase,
    "Kraken":      fetch_kraken,
    "Gate.io":     fetch_gateio,
    "Hyperliquid": fetch_hyperliquid,
}


def fetch_all_parallel(threshold_usd, selected_exchanges, current_price=None):
    """Fetch all exchanges in parallel using threads.
    Passes current_price to each fetcher for ±5% price range filtering.
    """
    all_orders = []
    statuses   = {}

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(FETCHERS[ex], threshold_usd, current_price): ex
            for ex in selected_exchanges if ex in FETCHERS
        }
        for future in as_completed(futures):
            name, orders, error = future.result()
            all_orders.extend(orders)
            statuses[name] = {
                "ok":    error is None,
                "count": len(orders),
                "error": error,
            }

    return all_orders, statuses

# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def analyze_pump_signal(orders, price):
    if not orders or price is None:
        return 0, "AWAITING DATA", "⚪"

    buys  = [o for o in orders if o["side"] == "BUY"]
    sells = [o for o in orders if o["side"] == "SELL"]
    buy_usd  = sum(o["usd_value"] for o in buys)
    sell_usd = sum(o["usd_value"] for o in sells)
    total    = buy_usd + sell_usd
    if total == 0:
        return 0, "AWAITING DATA", "⚪"

    buy_ratio  = buy_usd / total
    mega_buys  = sum(1 for o in buys if o["usd_value"] >= MEGA_WHALE)
    big_buys   = sum(1 for o in buys if o["usd_value"] >= BIG_WHALE)
    near_buys  = sum(o["usd_value"] for o in buys  if abs(o["price"] - price) / price < 0.01)
    near_sells = sum(o["usd_value"] for o in sells if abs(o["price"] - price) / price < 0.01)

    score = buy_ratio * 50
    score += min(mega_buys * 10, 20)
    score += min(big_buys  *  5, 15)
    near_total = near_buys + near_sells
    if near_total > 0:
        score += (near_buys / near_total) * 15
    score = min(100, max(0, score))

    if   score >= 75: return score, "🚀 STRONG PUMP SIGNAL",     "🟢"
    elif score >= 60: return score, "📈 MODERATE BUY PRESSURE",  "🟡"
    elif score <= 25: return score, "🔻 SELL PRESSURE DOMINANT", "🔴"
    elif score <= 40: return score, "📉 MILD SELL PRESSURE",     "🟠"
    else:             return score, "➡️  NEUTRAL / CONSOLIDATING", "⚪"


def whale_tier(usd):
    if   usd >= MEGA_WHALE: return "🐳 MEGA WHALE", "#ff4466"
    elif usd >= BIG_WHALE:  return "🦈 BIG WHALE",  "#ffd700"
    else:                   return "🐟 WHALE",       "#4da6ff"

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style='text-align:center;padding:1.5rem 0 1rem;'>
        <div style='font-size:3rem;'>🐋</div>
        <div style='font-size:1.25rem;font-weight:800;background:linear-gradient(135deg,#00ff88,#4da6ff);
             -webkit-background-clip:text;-webkit-text-fill-color:transparent;'>WhaleRadar</div>
        <div style='font-size:0.7rem;color:#4a5568;letter-spacing:0.15em;text-transform:uppercase;margin-top:0.2rem;'>
            BTC Pre-Pump Alert
        </div>
    </div>
    <hr/>
    """, unsafe_allow_html=True)

    st.markdown("**⚙️ EXCHANGES**")
    selected_exchanges = []
    for exch, meta in EXCHANGES.items():
        status = st.session_state.exchange_status.get(exch, {})
        label = f"{meta['emoji']} {exch}"
        if status:
            label += " ✅" if status.get("ok") else " ❌"
        if st.checkbox(label, value=True, key=f"chk_{exch}"):
            selected_exchanges.append(exch)

    st.markdown("<hr/>", unsafe_allow_html=True)
    st.markdown("**🎚️ THRESHOLD**")
    threshold_k   = st.slider("Min order ($K)", 10, 500, 50, 10)
    threshold_usd = threshold_k * 1_000

    st.markdown("<hr/>", unsafe_allow_html=True)
    st.markdown("**🔄 REFRESH**")
    refresh_secs  = int(st.selectbox("Interval", ["5s","10s","15s","30s"], index=1)[:-1])
    auto_refresh  = st.checkbox("🔁 Auto Refresh", value=True)

    st.markdown("<hr/>", unsafe_allow_html=True)
    st.markdown("**🔔 ALERTS**")
    alert_mega = st.checkbox("MEGA WHALE > $1M", value=True)
    alert_big  = st.checkbox("BIG WHALE > $500K", value=True)

    st.markdown("<hr/>", unsafe_allow_html=True)
    scan_btn = st.button("🔍 SCAN NOW", use_container_width=True)

    # Exchange status legend
    if st.session_state.exchange_status:
        st.markdown("<hr/>", unsafe_allow_html=True)
        st.markdown("**📡 CONNECTION STATUS**")
        for ex, s in st.session_state.exchange_status.items():
            icon  = "✅" if s["ok"] else "❌"
            count = s["count"]
            err   = f" — {s['error'][:30]}" if s.get("error") else ""
            st.markdown(
                f"<div style='font-size:0.75rem;color:#8892aa;margin:0.2rem 0;'>"
                f"{icon} <b>{ex}</b>: {count} orders{err}</div>",
                unsafe_allow_html=True
            )

# ─────────────────────────────────────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<h1 style='margin:0.5rem 0 0;font-size:2rem;font-weight:900;
    background:linear-gradient(135deg,#00ff88,#4da6ff,#a855f7);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;'>
    🐋 BTC Pre-Pump Alert Dashboard
</h1>
<p style='margin:0.3rem 0 1rem;color:#4a5568;font-size:0.85rem;'>
    Real-time whale order monitoring · 6 Exchanges · Min <span style='color:#00ff88;font-weight:700;'>$50,000</span> per order
</p>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCH  (always fetch on every run — auto_refresh triggers st.rerun)
# ─────────────────────────────────────────────────────────────────────────────
do_fetch = scan_btn or (auto_refresh and st.session_state.fetch_count == 0) or scan_btn

# Always fetch when auto_refresh is on (rerun loop) OR button pressed
status_ph = st.empty()
with status_ph.container():
    with st.spinner("🔄 Scanning exchanges in parallel..."):
        # Price
        price = fetch_btc_price()
        if price:
            st.session_state.last_price = price

        # Orders — parallel, pass price for ±5% filter
        orders, statuses = fetch_all_parallel(threshold_usd, selected_exchanges, current_price=price)
        st.session_state.exchange_status = statuses
        st.session_state.fetch_count += 1

        if orders:
            st.session_state.whale_orders = orders
            for ex in selected_exchanges:
                ex_ord = [o for o in orders if o["exchange"] == ex]
                st.session_state.buy_pressure[ex]  = sum(o["usd_value"] for o in ex_ord if o["side"] == "BUY")
                st.session_state.sell_pressure[ex] = sum(o["usd_value"] for o in ex_ord if o["side"] == "SELL")
            st.session_state.total_buy_vol  = sum(o["usd_value"] for o in orders if o["side"] == "BUY")
            st.session_state.total_sell_vol = sum(o["usd_value"] for o in orders if o["side"] == "SELL")

            # Alerts
            new_alerts = []
            for o in orders:
                tier, _ = whale_tier(o["usd_value"])
                if alert_mega and o["usd_value"] >= MEGA_WHALE:
                    new_alerts.append({**o, "tier": tier, "alert_time": datetime.now()})
                elif alert_big and o["usd_value"] >= BIG_WHALE:
                    new_alerts.append({**o, "tier": tier, "alert_time": datetime.now()})
            st.session_state.alerts = (new_alerts + st.session_state.alerts)[:50]

status_ph.empty()

# Working data
orders    = st.session_state.whale_orders
price     = st.session_state.last_price
total_buy = st.session_state.total_buy_vol
total_sell= st.session_state.total_sell_vol
total_vol = total_buy + total_sell
buy_pct   = (total_buy / total_vol * 100) if total_vol > 0 else 50

pump_score, pump_label, pump_icon = analyze_pump_signal(orders, price)

# ─────────────────────────────────────────────────────────────────────────────
# ALERT BANNER
# ─────────────────────────────────────────────────────────────────────────────
if   pump_score >= 75: ac="#00ff88"; ab="rgba(0,255,136,0.07)"; abr="rgba(0,255,136,0.4)"; ae="🚀"
elif pump_score >= 60: ac="#ffd700"; ab="rgba(255,215,0,0.07)";  abr="rgba(255,215,0,0.4)";  ae="⚠️"
elif pump_score <= 25: ac="#ff4466"; ab="rgba(255,68,102,0.07)"; abr="rgba(255,68,102,0.4)"; ae="📉"
else:                  ac="#4da6ff"; ab="rgba(77,166,255,0.06)"; abr="rgba(77,166,255,0.3)"; ae="📊"

st.markdown(f"""
<div class="alert-box" style='background:{ab};border:1px solid {abr};border-radius:18px;
     padding:1.5rem 2rem;margin:0.5rem 0 1.2rem;display:flex;align-items:center;
     justify-content:space-between;backdrop-filter:blur(20px);'>
    <div style='display:flex;align-items:center;gap:1.2rem;'>
        <div style='font-size:2.8rem;line-height:1;'>{ae}</div>
        <div>
            <div style='font-size:1.4rem;font-weight:800;color:{ac};'>{pump_label}</div>
            <div style='font-size:0.82rem;color:#8892aa;margin-top:0.3rem;'>
                {len(orders):,} whale orders &nbsp;·&nbsp; {len(selected_exchanges)} exchanges &nbsp;·&nbsp;
                Min ${threshold_k}K &nbsp;·&nbsp; Scan #{st.session_state.fetch_count}
            </div>
        </div>
    </div>
    <div style='text-align:right;'>
        <div style='font-family:"JetBrains Mono",monospace;font-size:2rem;font-weight:800;color:{ac};'>
            {pump_score:.0f}%
        </div>
        <div style='font-size:0.75rem;color:#4a5568;text-transform:uppercase;letter-spacing:0.1em;'>Pump Score</div>
    </div>
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# TOP METRICS
# ─────────────────────────────────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
with c1: st.metric("₿ BTC Price",        f"${price:,.0f}" if price else "—")
with c2: st.metric("🐋 Whale Orders",     f"{len(orders):,}")
buy_vol_str  = f"${total_buy/1e6:.2f}M" if total_buy >= 1e6 else f"${total_buy/1e3:.0f}K"
sell_vol_str = f"-${total_sell/1e6:.2f}M" if total_sell >= 1e6 else f"-${total_sell/1e3:.0f}K"
with c3: st.metric("🟢 BUY Orders",       f"{len([o for o in orders if o['side']=='BUY']):,}",  delta=buy_vol_str)
with c4: st.metric("🔴 SELL Orders",      f"{len([o for o in orders if o['side']=='SELL']):,}", delta=sell_vol_str)
with c5: st.metric("💰 Total Volume",     f"${total_vol/1e6:.2f}M" if total_vol >= 1e6 else f"${total_vol/1e3:.0f}K")

st.markdown("<div style='margin:0.6rem 0'/>", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# BUY / SELL PRESSURE BAR
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(f"""
<div style='background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);
     border-radius:14px;padding:1.2rem 1.5rem;margin-bottom:1.2rem;'>
    <div style='display:flex;justify-content:space-between;margin-bottom:0.6rem;'>
        <span style='color:#00ff88;font-weight:700;'>🟢 BUY ${total_buy/1e6:.2f}M ({buy_pct:.1f}%)</span>
        <span style='color:#8892aa;font-weight:600;font-size:0.85rem;'>BUY / SELL PRESSURE</span>
        <span style='color:#ff4466;font-weight:700;'>({100-buy_pct:.1f}%) SELL ${total_sell/1e6:.2f}M 🔴</span>
    </div>
    <div style='width:100%;height:20px;background:rgba(255,68,102,0.35);border-radius:10px;overflow:hidden;'>
        <div style='height:100%;width:{buy_pct:.1f}%;background:linear-gradient(90deg,#00cc66,#00ff88);
             border-radius:10px;box-shadow:0 0 15px rgba(0,255,136,0.4);transition:width 0.5s;'></div>
    </div>
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# CHARTS
# ─────────────────────────────────────────────────────────────────────────────
cl, cr = st.columns([1.6, 1])

with cl:
    st.markdown("<div style='font-size:0.8rem;color:#8892aa;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.4rem;font-weight:600;'>📊 Exchange Volume Breakdown</div>", unsafe_allow_html=True)
    if orders:
        exch_data = {}
        for o in orders:
            ex = o["exchange"]
            exch_data.setdefault(ex, {"BUY": 0, "SELL": 0})
            exch_data[ex][o["side"]] += o["usd_value"]

        exl  = list(exch_data.keys())
        bv   = [exch_data[e]["BUY"]  / 1e6 for e in exl]
        sv   = [exch_data[e]["SELL"] / 1e6 for e in exl]

        fig = go.Figure()
        fig.add_trace(go.Bar(name="BUY",  x=exl, y=bv,
            marker_color="rgba(0,255,136,0.75)",
            marker_line=dict(color="#00ff88", width=1.5),
            text=[f"${v:.2f}M" for v in bv], textposition="outside",
            textfont=dict(color="#00ff88", size=11, family="JetBrains Mono"),
            hovertemplate="<b>%{x}</b><br>BUY $%{y:.3f}M<extra></extra>"))
        fig.add_trace(go.Bar(name="SELL", x=exl, y=sv,
            marker_color="rgba(255,68,102,0.75)",
            marker_line=dict(color="#ff4466", width=1.5),
            text=[f"${v:.2f}M" for v in sv], textposition="outside",
            textfont=dict(color="#ff4466", size=11, family="JetBrains Mono"),
            hovertemplate="<b>%{x}</b><br>SELL $%{y:.3f}M<extra></extra>"))
        fig.update_layout(
            barmode="group", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10,r=10,t=20,b=10), height=300,
            legend=dict(orientation="h",x=0.5,xanchor="center",y=1.1,font=dict(color="#8892aa",size=11)),
            font=dict(family="Inter",color="#8892aa"),
            xaxis=dict(gridcolor="rgba(255,255,255,0.04)",color="#4a5568"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.04)",color="#4a5568",ticksuffix="M"),
            bargap=0.25, bargroupgap=0.08,
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    else:
        st.markdown("<div style='text-align:center;padding:3rem;color:#4a5568;border:1px dashed rgba(255,255,255,0.07);border-radius:12px;'>No data yet</div>", unsafe_allow_html=True)

with cr:
    st.markdown("<div style='font-size:0.8rem;color:#8892aa;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.4rem;font-weight:600;'>🎯 Order Distribution</div>", unsafe_allow_html=True)
    if orders and total_vol > 0:
        fig2 = go.Figure(go.Pie(
            labels=["BUY", "SELL"], values=[total_buy, total_sell], hole=0.65,
            marker=dict(colors=["rgba(0,255,136,0.85)","rgba(255,68,102,0.85)"],
                        line=dict(color=["#00ff88","#ff4466"],width=2)),
            textinfo="label+percent",
            textfont=dict(color="#e8eaf0", size=12, family="Inter"),
            hovertemplate="<b>%{label}</b><br>$%{value:,.0f}<extra></extra>",
        ))
        fig2.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=10,r=10,t=10,b=10), height=300, showlegend=False,
            annotations=[dict(text=f"<b>{pump_score:.0f}%</b><br><span style='font-size:11px'>Score</span>",
                              x=0.5, y=0.5, showarrow=False,
                              font=dict(color=ac, size=20, family="JetBrains Mono"))]
        )
        st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})
    else:
        st.markdown("<div style='text-align:center;padding:3rem;color:#4a5568;border:1px dashed rgba(255,255,255,0.07);border-radius:12px;'>Awaiting data...</div>", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# WHALE ORDER TABLE
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(f"<div style='font-size:0.8rem;color:#8892aa;text-transform:uppercase;letter-spacing:0.1em;margin:1rem 0 0.4rem;font-weight:600;'>🐳 Whale Orders (>{threshold_k}K USD) — Top 100</div>", unsafe_allow_html=True)

if orders:
    rows = []
    for o in sorted(orders, key=lambda x: x["usd_value"], reverse=True)[:100]:
        tier, _ = whale_tier(o["usd_value"])
        rows.append({
            "Tier":         tier,
            "Exchange":     f"{EXCHANGES.get(o['exchange'],{}).get('emoji','🔷')} {o['exchange']}",
            "Side":         "🟢 BUY" if o["side"] == "BUY" else "🔴 SELL",
            "Price $":      f"${o['price']:,.0f}",
            "BTC Qty":      f"{o['qty_btc']:.4f}",
            "Value $":      f"${o['usd_value']:,.0f}",
            "Time":         o["time"].strftime("%H:%M:%S"),
        })

    df = pd.DataFrame(rows)

    def _row_style(row):
        if "BUY" in str(row.get("Side", "")):
            return ["background-color:rgba(0,255,136,0.07)"] * len(row)
        return ["background-color:rgba(255,68,102,0.07)"] * len(row)

    st.dataframe(
        df.style.apply(_row_style, axis=1),
        use_container_width=True,
        height=min(460, max(200, len(rows) * 35 + 40)),
        hide_index=True,
    )
else:
    st.warning("No whale orders found. Try lowering the threshold or check connection status in sidebar.")

# ─────────────────────────────────────────────────────────────────────────────
# EXCHANGE CARDS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<div style='font-size:0.8rem;color:#8892aa;text-transform:uppercase;letter-spacing:0.1em;margin:1.2rem 0 0.6rem;font-weight:600;'>🏦 Per-Exchange Pressure</div>", unsafe_allow_html=True)

if selected_exchanges:
    cols = st.columns(len(selected_exchanges))
    for i, ex in enumerate(selected_exchanges):
        bv  = st.session_state.buy_pressure.get(ex, 0)
        sv  = st.session_state.sell_pressure.get(ex, 0)
        tot = bv + sv
        br  = (bv / tot * 100) if tot > 0 else 50
        dom = "BUY" if br >= 50 else "SELL"
        dc  = "#00ff88" if dom == "BUY" else "#ff4466"
        meta = EXCHANGES.get(ex, {})
        status = st.session_state.exchange_status.get(ex, {})
        conn_badge = "🟢" if status.get("ok") else ("🔴" if status else "⚪")
        with cols[i]:
            st.markdown(f"""
            <div style='background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);
                 border-top:2px solid {meta.get("color","#fff")};border-radius:12px;
                 padding:0.9rem;text-align:center;'>
                <div style='font-size:1.5rem;'>{meta.get("emoji","🔷")}</div>
                <div style='font-weight:700;font-size:0.85rem;margin:0.2rem 0;'>{ex} {conn_badge}</div>
                <div style='color:{dc};font-weight:800;font-size:0.9rem;'>{dom}</div>
                <div style='font-size:0.72rem;color:#4a5568;margin-top:0.25rem;'>
                    B:${bv/1e3:.0f}K | S:${sv/1e3:.0f}K | {status.get("count",0)} orders
                </div>
                <div style='width:100%;height:5px;background:rgba(255,68,102,0.3);border-radius:3px;margin-top:0.4rem;overflow:hidden;'>
                    <div style='height:100%;width:{br:.0f}%;background:{dc};border-radius:3px;'></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# ALERT LOG
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.alerts:
    st.markdown("<div style='font-size:0.8rem;color:#8892aa;text-transform:uppercase;letter-spacing:0.1em;margin:1.5rem 0 0.5rem;font-weight:600;'>🔔 Whale Alert Log</div>", unsafe_allow_html=True)
    for alert in st.session_state.alerts[:15]:
        is_mega  = alert["usd_value"] >= MEGA_WHALE
        sc       = "#00ff88" if alert["side"] == "BUY" else "#ff4466"
        bc       = "#ff4466" if is_mega else "#ffd700"
        tier     = alert.get("tier", "🐟 WHALE")
        em       = EXCHANGES.get(alert["exchange"], {}).get("emoji", "")
        st.markdown(f"""
        <div class="slide-in" style='background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.06);
             border-left:3px solid {bc};border-radius:10px;padding:0.75rem 1.2rem;margin-bottom:0.4rem;
             display:flex;align-items:center;justify-content:space-between;'>
            <div style='display:flex;align-items:center;gap:0.75rem;'>
                <div style='font-size:1.4rem;'>{"🚨" if is_mega else "⚠️"}</div>
                <div>
                    <span style='color:{bc};font-weight:700;font-size:0.85rem;'>{tier}</span>
                    <span style='color:#4a5568;font-size:0.78rem;margin-left:0.5rem;'>{em} {alert["exchange"]}</span>
                    <div style='font-size:0.78rem;color:#8892aa;margin-top:0.1rem;'>
                        <span style='color:{sc};font-weight:700;'>{alert["side"]}</span>
                        &nbsp;·&nbsp;${alert["price"]:,.0f}
                        &nbsp;·&nbsp;{alert["qty_btc"]:.4f} BTC
                    </div>
                </div>
            </div>
            <div style='text-align:right;'>
                <div style='font-family:"JetBrains Mono",monospace;color:{bc};font-weight:800;font-size:1rem;'>
                    ${alert["usd_value"]:,.0f}
                </div>
                <div style='font-size:0.7rem;color:#4a5568;'>{alert["alert_time"].strftime("%H:%M:%S")}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(f"""
<div style='margin-top:2rem;padding:1rem;border-top:1px solid rgba(255,255,255,0.07);
     display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem;'>
    <span style='font-size:0.72rem;color:#4a5568;'>🐋 <b>WhaleRadar</b> · Live REST APIs · No API Key Required</span>
    <span style='font-size:0.72rem;color:#4a5568;'>
        Last: <span style='color:#00ff88;'>{datetime.now().strftime("%H:%M:%S")}</span>
        &nbsp;·&nbsp; Next in {refresh_secs}s &nbsp;·&nbsp; Scan #{st.session_state.fetch_count}
    </span>
    <span style='font-size:0.72rem;color:#4a5568;'>⚠️ Not financial advice</span>
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# AUTO REFRESH LOOP
# ─────────────────────────────────────────────────────────────────────────────
if auto_refresh:
    time.sleep(refresh_secs)
    st.rerun()
