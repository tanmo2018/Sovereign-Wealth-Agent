"""
Local SQLite database for storing investment data.
You manually insert your holdings here via the UI or directly.
"""

import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "investments.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize all tables."""
    conn = get_connection()
    c = conn.cursor()

    # Holdings table - your investments
    c.execute("DROP TABLE IF EXISTS holdings")
    c.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instrument_name TEXT NOT NULL,
            instrument_type TEXT NOT NULL,  -- 'equity_mf', 'debt_mf', 'liquid_mf', 'stocks', 'fd', 'ppf', 'nps', 'gold_etf', 'savings'
            units REAL,                     -- for MF/stocks
            buy_price REAL,                 -- per unit
            current_price REAL,             -- per unit (update manually)
            invested_amount REAL NOT NULL,
            current_value REAL NOT NULL,
            buy_date TEXT NOT NULL,         -- YYYY-MM-DD
            exit_load_percent REAL DEFAULT 0,
            exit_load_days INTEGER DEFAULT 0,   -- exit load applies within these days
            lock_in_end_date TEXT,          -- for ELSS, PPF etc
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # LTCG tracker - tracks how much LTCG exemption used this FY
    c.execute("DROP TABLE IF EXISTS ltcg_tracker")
    c.execute("""
        CREATE TABLE IF NOT EXISTS ltcg_tracker (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            financial_year TEXT NOT NULL,   -- e.g. '2024-25'
            realized_ltcg REAL DEFAULT 0,   -- total LTCG realized this FY
            realized_stcg REAL DEFAULT 0,
            tax_paid REAL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Transaction history
    c.execute("DROP TABLE IF EXISTS transactions")
    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            holding_id INTEGER,
            transaction_type TEXT,   -- 'buy', 'sell', 'withdraw'
            amount REAL,
            units REAL,
            price_per_unit REAL,
            tax_amount REAL DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (holding_id) REFERENCES holdings(id)
        )
    """)

    # Profile - user financial profile
    c.execute("DROP TABLE IF EXISTS profile")
    c.execute("""
        CREATE TABLE IF NOT EXISTS profile (
            id INTEGER PRIMARY KEY,
            name TEXT,
            tax_regime TEXT DEFAULT 'new',  -- 'old' or 'new'
            income_slab_rate REAL DEFAULT 30.0,  -- marginal tax rate %
            is_nri INTEGER DEFAULT 0,
            pan TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            age INTEGER,
            monthly_salary REAL,
            family_background TEXT,
            goals_json TEXT,
            monthly_expense REAL,
            recommendation_mode TEXT DEFAULT 'weighted')
    """)

    # Loan liabilities - supports multiple active loans
    c.execute("DROP TABLE IF EXISTS loans")
    c.execute("""
        CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loan_name TEXT,
            loan_amount REAL NOT NULL,
            taken_date TEXT NOT NULL,             -- YYYY-MM-DD
            duration_months INTEGER NOT NULL,
            monthly_emi REAL NOT NULL,
            interest_rate REAL NOT NULL,          -- annual rate %
            pending_months INTEGER NOT NULL,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Insert default profile if not exists
    c.execute("INSERT OR IGNORE INTO profile (id, name) VALUES (1, 'Investor')")

    # Seed sample data for demo
    c.execute("SELECT COUNT(*) FROM holdings")
    if c.fetchone()[0] == 0:
        _seed_sample_data(c)

    conn.commit()
    conn.close()
    print(f"✅ Database initialized at {DB_PATH}")



def _seed_sample_data(c):
    """Insert sample portfolio for demo purposes."""
    # id INTEGER PRIMARY KEY AUTOINCREMENT,
    #         instrument_name TEXT NOT NULL,
    #         instrument_type TEXT NOT NULL,  -- 'equity_mf', 'debt_mf', 'liquid_mf', 'stocks', 'fd', 'ppf', 'nps', 'gold_etf', 'savings'
    #         units REAL,                     -- for MF/stocks
    #         buy_price REAL,                 -- per unit
    #         current_price REAL,             -- per unit (update manually)
    #         invested_amount REAL NOT NULL,
    #         current_value REAL NOT NULL,
    #         buy_date TEXT NOT NULL,         -- YYYY-MM-DD
    #         exit_load_percent REAL DEFAULT 0,
    #         exit_load_days INTEGER DEFAULT 0,   -- exit load applies within these days
    #         lock_in_end_date TEXT,          -- for ELSS, PPF etc
    #         notes TEXT,
    #         created_at TEXT DEFAULT CURRENT_TIMESTAMP
    sample = [
        ("ICICI Prudential Silver ETF", "silver_etf", 56, 303.94, 237.79, 17020.64, 13316.24, "2026-01-30", 0, 0, None),
        ("ICICI Prudential Gold ETF", "gold_etf", 68, 139.92, 128.30, 9514.56, 8724.40, "2026-03-12", 0, 0, None),
        ("REC Limited", "stocks", 1, 395.50, 374, 395.50, 374, "2025-07-11", 0, 0, None),
        ("Sintex Plastics Technology", "stocks", 3, 13.05, 1.06, 39.15, 3.15, "2021-12-21", 0, 0, None),
        ("DSP Midcap Direct Plan Growth", "equity_mf", 50.27, 154.13, 165.47, 7747.92, 8317.98, "2026-04-24", 0, 0, None),
        ("Groww Nifty EV & New Age Automotive ETF FoF Direct Growth", "equity_mf", 196.864, 9.14, 9.04, 1800.01, 1778.82, "2026-03-26", 0, 0, None),
        ("Aditya Birla Sun Life Liquid Fund Direct Growth", "liquid_mf", 1.541, 416.04, 447.76, 641.11, 690, "2025-03-02", 0, 0, None),
        ("Savings Account", "savings", None, None, None, 47000.0, 47000.0, "2026-01-01", 0, 0, None)
        # ("Parag Parikh Flexi Cap Fund", "equity_mf", 250.5, 45.2, 58.7, 11330.6, 14713.35, "2022-03-15", 1.0, 365, None),
        # ("SBI Liquid Fund", "liquid_mf", 500.0, 3200.0, 3410.0, 160000.0, 170500.0, "2023-11-01", 0, 7, None),
        # ("Nifty 50 Index Fund", "equity_mf", 1000.0, 120.0, 185.0, 120000.0, 185000.0, "2021-06-10", 0, 0, None),
        # ("HDFC FD", "fd", None, None, None, 100000.0, 108000.0, "2023-04-01", 1.0, 365, None),
        # ("Savings Account", "savings", None, None, None, 80000.0, 80000.0, "2020-01-01", 0, 0, None),
        # ("Tata Motors", "stocks", 200.0, 450.0, 820.0, 90000.0, 164000.0, "2022-08-20", 0, 0, None),
        # ("Sovereign Gold Bond 2023", "gold_etf", 10.0, 5800.0, 6750.0, 58000.0, 67500.0, "2023-01-15", 0, 0, None),
        # ("Mirae ELSS Fund", "equity_mf", 300.0, 80.0, 145.0, 24000.0, 43500.0, "2022-01-10", 0, 0, "2025-01-10"),  # 3yr lock-in
    ]
    c.executemany("""
        INSERT INTO holdings 
        (instrument_name, instrument_type, units, buy_price, current_price, invested_amount, current_value, buy_date, exit_load_percent, exit_load_days, lock_in_end_date)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, sample)

    # Seed LTCG tracker for current FY
    c.execute("""
        INSERT INTO ltcg_tracker (financial_year, realized_ltcg) VALUES ('2024-25', 45000)
    """)


# ─── CRUD Operations ───────────────────────────────────────────────────────────

def get_all_holdings() -> List[Dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM holdings ORDER BY instrument_type, buy_date").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_holding(data: Dict) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO holdings 
        (instrument_name, instrument_type, units, buy_price, current_price, 
         invested_amount, current_value, buy_date, exit_load_percent, exit_load_days, 
         lock_in_end_date, notes)
        VALUES (:instrument_name, :instrument_type, :units, :buy_price, :current_price,
                :invested_amount, :current_value, :buy_date, :exit_load_percent, :exit_load_days,
                :lock_in_end_date, :notes)
    """, data)
    id_ = c.lastrowid
    conn.commit()
    conn.close()
    return id_


def update_holding(id_: int, data: Dict):
    conn = get_connection()
    fields = ", ".join(f"{k}=:{k}" for k in data)
    data["id"] = id_
    conn.execute(f"UPDATE holdings SET {fields} WHERE id=:id", data)
    conn.commit()
    conn.close()


def delete_holding(id_: int):
    conn = get_connection()
    conn.execute("DELETE FROM holdings WHERE id=?", (id_,))
    conn.commit()
    conn.close()


def get_profile() -> Dict:
    conn = get_connection()
    row = conn.execute("SELECT * FROM profile WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else {}


def get_all_loans() -> List[Dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM loans ORDER BY taken_date DESC, id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_loan(data: Dict) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO loans
        (loan_name, loan_amount, taken_date, duration_months, monthly_emi, interest_rate, pending_months, notes)
        VALUES (:loan_name, :loan_amount, :taken_date, :duration_months, :monthly_emi, :interest_rate, :pending_months, :notes)
        """,
        data,
    )
    id_ = c.lastrowid
    conn.commit()
    conn.close()
    return id_


def update_loan(id_: int, data: Dict):
    conn = get_connection()
    fields = ", ".join(f"{k}=:{k}" for k in data)
    data["id"] = id_
    conn.execute(
        f"UPDATE loans SET {fields}, updated_at=CURRENT_TIMESTAMP WHERE id=:id",
        data,
    )
    conn.commit()
    conn.close()


def delete_loan(id_: int):
    conn = get_connection()
    conn.execute("DELETE FROM loans WHERE id=?", (id_,))
    conn.commit()
    conn.close()


def update_profile(data: Dict):
    conn = get_connection()
    fields = ", ".join(f"{k}=:{k}" for k in data)
    conn.execute(f"UPDATE profile SET {fields}, updated_at=CURRENT_TIMESTAMP WHERE id=1", data)
    conn.commit()
    conn.close()


def get_ltcg_used_this_fy() -> float:
    fy = _current_fy()
    conn = get_connection()
    row = conn.execute(
        "SELECT realized_ltcg FROM ltcg_tracker WHERE financial_year=?", (fy,)
    ).fetchone()
    conn.close()
    return row["realized_ltcg"] if row else 0.0


def log_transaction(data: Dict):
    conn = get_connection()
    conn.execute("""
        INSERT INTO transactions (holding_id, transaction_type, amount, units, price_per_unit, tax_amount, notes)
        VALUES (:holding_id, :transaction_type, :amount, :units, :price_per_unit, :tax_amount, :notes)
    """, data)
    conn.commit()
    conn.close()


def _current_fy() -> str:
    now = datetime.now()
    if now.month >= 4:
        return f"{now.year}-{str(now.year+1)[2:]}"
    return f"{now.year-1}-{str(now.year)[2:]}"


def process_partial_sell(holding_id: int, units_to_sell: float):
    conn = get_connection()
    c = conn.cursor()
    
    # 1. Fetch holding [cite: 24]
    h = c.execute("SELECT * FROM holdings WHERE id=?", (holding_id,)).fetchone()
    if not h or units_to_sell > h["units"]:
        conn.close()
        return False
    
    # 2. Proportional Math
    sell_ratio = units_to_sell / h["units"]
    invested_portion = h["invested_amount"] * sell_ratio
    value_portion = h["current_value"] * sell_ratio
    realized_gain = value_portion - invested_portion
    
    # 3. Tax Classification 
    from tax_engine import LONG_TERM_DAYS
    from datetime import datetime, date
    days_held = (date.today() - datetime.strptime(h["buy_date"], "%Y-%m-%d").date()).days
    is_long_term = LONG_TERM_DAYS.get(h["instrument_type"]) is not None and days_held >= LONG_TERM_DAYS[h["instrument_type"]]
    
    # 4. UPDATE EXISTING LTCG/STCG VALUES [cite: 23]
    fy = _current_fy()
    col = "realized_ltcg" if is_long_term else "realized_stcg"
    c.execute(
        """
        INSERT OR IGNORE INTO ltcg_tracker (financial_year, realized_ltcg, realized_stcg)
        VALUES (?, 0, 0)
        """,
        (fy,),
    )
    # This adds the new gain to the previous stored value in the table [cite: 23, 150]
    c.execute(
        f"UPDATE ltcg_tracker SET {col} = {col} + ?, updated_at = CURRENT_TIMESTAMP WHERE financial_year = ?",
        (realized_gain, fy),
    )
    updated_tracker = c.execute(
        "SELECT realized_ltcg, realized_stcg FROM ltcg_tracker WHERE financial_year = ?",
        (fy,),
    ).fetchone()
    print(
        f"[DEBUG] FY {fy} updated: LTCG={updated_tracker['realized_ltcg']}, STCG={updated_tracker['realized_stcg']}"
    )
    # 5. Log Transaction [cite: 20, 21]
    c.execute("""
        INSERT INTO transactions (holding_id, transaction_type, amount, units, tax_amount)
        VALUES (?, 'sell', ?, ?, 0)
    """, (holding_id, value_portion, units_to_sell))
    
    # 6. Update or Delete the holding [cite: 24]
    new_units = h["units"] - units_to_sell
    if new_units <= 0.001: 
        c.execute("DELETE FROM holdings WHERE id=?", (holding_id,))
    else:
        c.execute("""
            UPDATE holdings 
            SET units = ?, invested_amount = invested_amount - ?, current_value = current_value - ?
            WHERE id = ?
        """, (new_units, invested_portion, value_portion, holding_id))
    
    conn.commit()
    conn.close()
    return True