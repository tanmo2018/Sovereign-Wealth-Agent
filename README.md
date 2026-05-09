# Sovereign Wealth Agent — Setup Guide

## 🚀 Quick Start (3 steps)

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set your Groq API key
Get a FREE key at: https://console.groq.com
```bash
# Create a .env file in this folder:
echo "GROQ_API_KEY=your_key_here" > .env
```
OR set it directly:
```bash
export GROQ_API_KEY=your_key_here   # Linux/Mac
set GROQ_API_KEY=your_key_here      # Windows
```

### 3. Run the backend
```bash
uvicorn api:app --reload --port 8000
```

### 4. Open the UI
Open `index.html` directly in your browser (double-click it).

---

## 📁 File Structure

```
sovereign_agent/
├── database.py       ← SQLite DB with your portfolio data
├── tax_engine.py     ← Indian tax rules (LTCG/STCG/STCG, withdrawal optimizer)
├── agent.py          ← LangGraph-style agent with Groq (llama-3.3-70b)
├── api.py            ← FastAPI REST backend
├── index.html        ← Full frontend UI
├── requirements.txt
├── investments.db    ← Auto-created on first run (your data lives here)
└── .env              ← Your GROQ_API_KEY (create this)
```

---

## 🤖 Best Free Groq Models (2025)

| Model | Speed | Context | Best For |
|-------|-------|---------|----------|
| `llama-3.3-70b-versatile` ⭐ | Fast | 128K | **Used by default** — best quality |
| `llama-3.1-8b-instant` | Very Fast | 128K | Quick queries, low latency |
| `mixtral-8x7b-32768` | Fast | 32K | Alternative option |

To switch models, edit `GROQ_MODEL` in `agent.py`.

---

## 💼 Adding Your Investments

1. Open the app → **Portfolio** tab → **+ Add Holding**
2. Fill in your instrument details manually
3. The database (`investments.db`) stores everything locally

Or edit `investments.db` directly using any SQLite browser (e.g. DB Browser for SQLite).

---

## 💸 How Withdrawal Optimization Works

The agent ranks your holdings by **tax efficiency score**:

```
Priority 1: Savings / idle cash         → 0% tax
Priority 2: Loss-making positions       → Book losses (tax harvest)  
Priority 3: LTCG within ₹1.25L limit   → 0% effective tax
Priority 4: LTCG above limit           → 12.5% tax
Priority 5: STCG positions             → 20% tax
Priority 6: FD / income-slab items     → Your slab rate (worst)
```

Locked-in instruments (ELSS lock-in, PPF) are automatically excluded.

---

## 🏗 Architecture

```
User Query
    ↓
InputProcessor (Groq) → intent: invest | withdraw | analyze
    ↓
DataFetcher → SQLite (your holdings, profile, LTCG tracker)
    ↓
Router
    ├─→ WithdrawalOptimizer (Tax Engine, no LLM needed)
    └─→ InvestmentAdvisor (Groq llama-3.3-70b)
        ↓
MarketSentimentCritic (Groq)
    ↓
OutputStructurer → JSON
    ↓
HITL Break → You approve before any action
```

---

## ⚠️ Disclaimer

This is a personal tool for financial planning assistance. It is NOT SEBI-registered financial advice. Always verify recommendations with a qualified financial advisor before making investment decisions.
