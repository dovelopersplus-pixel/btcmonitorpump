# 🐋 BTC Pre-Pump Alert Dashboard — WhaleRadar

Real-time BTC whale order monitoring across **6 major exchanges** to detect pre-pump signals.

## 🚀 Live Demo
Deployed on Streamlit Cloud: *(coming soon)*

## 📡 Exchanges Covered
| Exchange | Data Source |
|---|---|
| 🔶 Binance | REST API v3 |
| 🟠 Bybit | REST API v5 |
| 🔵 Coinbase | Exchange API |
| 🟣 Kraken | Public API |
| 🩵 Gate.io | v4 API |
| 🟢 Hyperliquid | Info API |

## ✨ Features
- **Real-time whale order detection** — Orders above $50,000 (adjustable)
- **Pre-pump signal score** — 0–100% pump probability
- **Buy/Sell pressure bar** — Visual dominance meter
- **Exchange breakdown chart** — Per-exchange volume comparison
- **Whale tier classification** — 🐟 Whale / 🦈 Big Whale / 🐳 Mega Whale
- **Alert log** — Tracks $500K+ and $1M+ orders
- **Price range filter** — Only real market orders (±5% of current price)
- **Parallel data fetching** — All 6 exchanges fetched simultaneously
- **Auto refresh** — 5s / 10s / 15s / 30s intervals

## 🛠️ Run Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run on port 8503
streamlit run app.py --server.port 8503
```

Then open: **http://localhost:8503**

## 📦 Requirements
- Python 3.8+
- streamlit
- requests
- pandas
- plotly

## ⚠️ Disclaimer
This tool is for **informational purposes only**. Not financial advice.
