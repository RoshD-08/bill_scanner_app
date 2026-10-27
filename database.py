"""
database.py
SQLite database management for the standalone AI Bill Scanner application.
Supports user authentication, preferred currency customization, multi-tenant bill scoping,
and live converted category summaries.
Zero external configuration required.
"""

import os
import sqlite3
import json
import csv
import io
import calendar
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from currency import normalize_currency, convert_currency

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bills.db")

CATEGORIES = [
    "Food & Dining",
    "Groceries",
    "Transportation",
    "Utilities",
    "Office Supplies",
    "Medical",
    "Entertainment",
    "Communication",
    "Rent & Housing",
    "Other",
]

INCOME_CATEGORIES = [
    "Salary",
    "Freelance & Consulting",
    "Business Profits",
    "Investments & Dividends",
    "Rental Income",
    "Gifts & Grants",
    "Refunds & Reimbursements",
    "Other Income",
]

CATEGORY_COLORS = {
    "Food & Dining": "#F59E0B",      # Amber
    "Groceries": "#10B981",          # Emerald
    "Transportation": "#3B82F6",     # Blue
    "Utilities": "#06B6D4",          # Cyan
    "Office Supplies": "#8B5CF6",    # Purple
    "Medical": "#EF4444",            # Red
    "Entertainment": "#EC4899",      # Pink
    "Communication": "#6366F1",      # Indigo
    "Rent & Housing": "#14B8A6",     # Teal
    "Other": "#94A3B8",              # Slate
}

INCOME_COLORS = {
    "Salary": "#10B981",                   # Emerald
    "Freelance & Consulting": "#3B82F6",   # Blue
    "Business Profits": "#8B5CF6",         # Purple
    "Investments & Dividends": "#F59E0B",   # Amber
    "Rental Income": "#14B8A6",            # Teal
    "Gifts & Grants": "#EC4899",           # Pink
    "Refunds & Reimbursements": "#06B6D4", # Cyan
    "Other Income": "#94A3B8",             # Slate
}


def get_db():
    """Connect to SQLite database with Row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database schema and migrate user columns if needed."""
    conn = get_db()
    try:
        with conn:
            # 1. Users table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                    email TEXT UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    preferred_currency TEXT NOT NULL DEFAULT 'LKR',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")

            # Check and migrate preferred_currency, theme, and avatar_path columns into users if missing
            cursor = conn.execute("PRAGMA table_info(users);")
            user_cols = [row["name"] for row in cursor.fetchall()]
            if "preferred_currency" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN preferred_currency TEXT NOT NULL DEFAULT 'LKR';")
            if "theme" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT 'light';")
            if "avatar_path" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT DEFAULT NULL;")

            # 2. Bills table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(id),
                    vendor_name TEXT,
                    bill_date TEXT,
                    bill_number TEXT,
                    category TEXT NOT NULL DEFAULT 'Other',
                    total_amount REAL NOT NULL DEFAULT 0.0,
                    tax_amount REAL DEFAULT 0.0,
                    currency TEXT DEFAULT 'LKR',
                    payment_method TEXT,
                    items_json TEXT DEFAULT '[]',
                    image_path TEXT NOT NULL,
                    raw_text TEXT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bills_date ON bills(bill_date);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bills_category ON bills(category);")

            # Check and migrate user_id column into bills if missing
            cursor = conn.execute("PRAGMA table_info(bills);")
            bill_cols = [row["name"] for row in cursor.fetchall()]
            if "user_id" not in bill_cols:
                conn.execute("ALTER TABLE bills ADD COLUMN user_id INTEGER REFERENCES users(id);")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_bills_user ON bills(user_id);")

            # 3. Incomes table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS incomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    source TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'Salary',
                    amount REAL NOT NULL DEFAULT 0.0,
                    currency TEXT DEFAULT 'LKR',
                    income_date TEXT NOT NULL,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_incomes_user ON incomes(user_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_incomes_date ON incomes(income_date);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_incomes_category ON incomes(category);")
    finally:
        conn.close()


# ==========================================
# User Authentication Functions
# ==========================================

def create_user(username, password, email=None, preferred_currency="LKR"):
    """
    Register a new user with hashed password and preferred currency.
    Returns (user_dict, None) on success or (None, error_str) on validation/conflict failure.
    Every account is strictly private.
    """
    username = (username or "").strip()
    email = (email or "").strip().lower() or None
    password = (password or "").strip()
    preferred_currency = normalize_currency(preferred_currency or "LKR")

    if not username or len(username) < 3:
        return None, "Username must be at least 3 characters long."
    if len(username) > 30:
        return None, "Username cannot exceed 30 characters."
    if not password or len(password) < 6:
        return None, "Password must be at least 6 characters long."

    pwd_hash = generate_password_hash(password)

    conn = get_db()
    try:
        with conn:
            cursor = conn.execute("""
                INSERT INTO users (username, email, password_hash, preferred_currency)
                VALUES (?, ?, ?, ?)
            """, (username, email, pwd_hash, preferred_currency))
            user_id = cursor.lastrowid

            row = conn.execute(
                "SELECT id, username, email, preferred_currency, created_at FROM users WHERE id = ?",
                (user_id,)
            ).fetchone()
            return dict(row), None
    except sqlite3.IntegrityError as e:
        err = str(e).lower()
        if "username" in err:
            return None, "That username is already taken. Please choose another."
        elif "email" in err:
            return None, "An account with that email already exists."
        return None, "An account with these details already exists."
    finally:
        conn.close()


def authenticate_user(username_or_email, password):
    """
    Verify user credentials by username or email.
    Returns user dict on success, None on invalid credentials.
    """
    val = (username_or_email or "").strip()
    pwd = (password or "").strip()
    if not val or not pwd:
        return None

    conn = get_db()
    try:
        cursor = conn.execute("PRAGMA table_info(users);")
        cols = [r["name"] for r in cursor.fetchall()]
        theme_expr = "theme" if "theme" in cols else "'light' as theme"
        avatar_expr = "avatar_path" if "avatar_path" in cols else "NULL as avatar_path"

        row = conn.execute(f"""
            SELECT id, username, email, password_hash, preferred_currency,
                   {theme_expr}, {avatar_expr}, created_at
            FROM users
            WHERE username = ? OR email = ?
        """, (val, val.lower())).fetchone()
        if not row:
            return None

        if check_password_hash(row["password_hash"], pwd):
            return {
                "id": row["id"],
                "username": row["username"],
                "email": row["email"],
                "preferred_currency": row["preferred_currency"] or "LKR",
                "theme": row["theme"] or "light",
                "avatar_path": row["avatar_path"],
                "created_at": row["created_at"],
            }
        return None
    finally:
        conn.close()


def get_user_by_id(user_id):
    """Retrieve user record by ID (safe without password hash)."""
    if not user_id:
        return None
    conn = get_db()
    try:
        cursor = conn.execute("PRAGMA table_info(users);")
        cols = [r["name"] for r in cursor.fetchall()]
        theme_expr = "theme" if "theme" in cols else "'light' as theme"
        avatar_expr = "avatar_path" if "avatar_path" in cols else "NULL as avatar_path"

        sql = f"SELECT id, username, email, preferred_currency, {theme_expr}, {avatar_expr}, created_at FROM users WHERE id = ?"

        row = conn.execute(sql, (user_id,)).fetchone()
        if row:
            d = dict(row)
            d["theme"] = d.get("theme") or "light"
            return d
        return None
    finally:
        conn.close()


def update_user_currency(user_id, currency):
    """Update user's preferred currency."""
    if not user_id:
        return False
    clean_curr = normalize_currency(currency)
    conn = get_db()
    try:
        with conn:
            conn.execute("UPDATE users SET preferred_currency = ? WHERE id = ?", (clean_curr, user_id))
        return True
    finally:
        conn.close()


def update_user_theme(user_id, theme):
    """Update user's theme preference ('light', 'dark', 'auto')."""
    if not user_id:
        return False
    t = (theme or "light").strip().lower()
    if t not in ["light", "dark", "auto"]:
        t = "light"
    conn = get_db()
    try:
        with conn:
            conn.execute("UPDATE users SET theme = ? WHERE id = ?", (t, user_id))
        return True
    finally:
        conn.close()


def update_user_profile(user_id, email=None, preferred_currency=None, theme=None, avatar_path=None):
    """Update user's profile information (email, preferred_currency, theme, avatar_path)."""
    if not user_id:
        return False, "User not found."
    conn = get_db()
    try:
        with conn:
            fields = []
            params = []
            if email is not None:
                clean_email = email.strip().lower() or None
                fields.append("email = ?")
                params.append(clean_email)
            if preferred_currency is not None:
                clean_curr = normalize_currency(preferred_currency)
                fields.append("preferred_currency = ?")
                params.append(clean_curr)
            if theme is not None:
                t = theme.strip().lower()
                if t in ["light", "dark", "auto"]:
                    fields.append("theme = ?")
                    params.append(t)
            if avatar_path is not None:
                fields.append("avatar_path = ?")
                params.append(avatar_path if avatar_path != "" else None)
            if fields:
                params.append(user_id)
                conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", params)
            return True, None
    except sqlite3.IntegrityError:
        return False, "That email is already registered to another account."
    finally:
        conn.close()


def update_user_avatar(user_id, avatar_path):
    """Update or remove user's avatar image path."""
    if not user_id:
        return False, "User not found."
    conn = get_db()
    try:
        with conn:
            conn.execute("UPDATE users SET avatar_path = ? WHERE id = ?", (avatar_path if avatar_path else None, user_id))
        return True, None
    finally:
        conn.close()


def update_user_password(user_id, current_password, new_password):
    """Verify current password and update to new password."""
    if not user_id or not current_password or not new_password:
        return False, "All password fields are required."
    if len(new_password) < 6:
        return False, "New password must be at least 6 characters long."

    conn = get_db()
    try:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not check_password_hash(row["password_hash"], current_password):
            return False, "Current password does not match our records."

        new_hash = generate_password_hash(new_password)
        with conn:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
        return True, None
    finally:
        conn.close()


def get_user_stats(user_id, target_currency="LKR"):
    """Compute aggregate stats for user profile page."""
    bills = get_all_bills(user_id=user_id, target_currency=target_currency)
    conn = get_db()
    try:
        incomes_rows = conn.execute("SELECT amount, currency FROM incomes WHERE user_id = ?", (user_id,)).fetchall()
        total_income_conv = sum(convert_currency(r["amount"], r["currency"], target_currency) for r in incomes_rows)
        total_spent_conv = sum(b.get("total_amount_converted", 0.0) for b in bills)

        return {
            "bill_count": len(bills),
            "total_spent": total_spent_conv,
            "income_count": len(incomes_rows),
            "total_income": total_income_conv,
            "net_balance": total_income_conv - total_spent_conv,
            "target_currency": target_currency
        }
    finally:
        conn.close()


# ==========================================
# Bill Management Functions (Strictly User-Scoped)
# ==========================================

def save_bill(data, user_id):
    """Insert a new bill record into the database associated with user_id."""
    if not user_id:
        raise ValueError("Cannot save bill without a valid user account.")

    conn = get_db()
    try:
        items = data.get("items", [])
        if isinstance(items, (list, dict)):
            items_str = json.dumps(items)
        else:
            items_str = str(items or "[]")

        with conn:
            cursor = conn.execute("""
                INSERT INTO bills (
                    user_id, vendor_name, bill_date, bill_number, category,
                    total_amount, tax_amount, currency, payment_method,
                    items_json, image_path, raw_text, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                data.get("vendor_name", "").strip(),
                data.get("bill_date", "").strip() or None,
                data.get("bill_number", "").strip() or None,
                data.get("category", "Other").strip() or "Other",
                float(data.get("total_amount", 0.0) or 0.0),
                float(data.get("tax_amount", 0.0) or 0.0),
                data.get("currency", "LKR").strip() or "LKR",
                data.get("payment_method", "").strip() or None,
                items_str,
                data.get("image_path", "").strip(),
                data.get("raw_text", "").strip(),
                data.get("notes", "").strip(),
            ))
            return cursor.lastrowid
    finally:
        conn.close()


def get_all_bills(user_id, target_currency="LKR", category=None, start_date=None, end_date=None, query=None):
    """
    Retrieve bills strictly for the specified user_id.
    Each bill includes total_amount_converted in the user's preferred target_currency.
    """
    if not user_id:
        return []

    target_curr = normalize_currency(target_currency or "LKR")

    conn = get_db()
    try:
        sql = "SELECT * FROM bills WHERE user_id = ?"
        params = [user_id]

        if category and category.strip():
            sql += " AND category = ?"
            params.append(category.strip())

        if start_date and start_date.strip():
            sql += " AND bill_date >= ?"
            params.append(start_date.strip())

        if end_date and end_date.strip():
            sql += " AND bill_date <= ?"
            params.append(end_date.strip())

        if query and query.strip():
            sql += " AND (vendor_name LIKE ? OR bill_number LIKE ? OR notes LIKE ?)"
            term = f"%{query.strip()}%"
            params.extend([term, term, term])

        sql += " ORDER BY bill_date DESC, id DESC"

        rows = conn.execute(sql, params).fetchall()
        bills = []
        for r in rows:
            b = dict(r)
            try:
                parsed = json.loads(b["items_json"]) if b["items_json"] else []
            except Exception:
                parsed = []
            b["line_items"] = parsed
            b["items"] = parsed
            b["total_amount_converted"] = convert_currency(
                b.get("total_amount", 0.0),
                b.get("currency", "LKR"),
                target_curr
            )
            b["target_currency"] = target_curr
            bills.append(b)
        return bills
    finally:
        conn.close()


def get_bill_by_id(bill_id, user_id):
    """Fetch single bill details by ID, strictly enforcing user ownership."""
    if not user_id:
        return None
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM bills WHERE id = ? AND user_id = ?",
            (bill_id, user_id)
        ).fetchone()
        if not row:
            return None
        b = dict(row)
        try:
            parsed = json.loads(b["items_json"]) if b["items_json"] else []
        except Exception:
            parsed = []
        b["line_items"] = parsed
        b["items"] = parsed
        return b
    finally:
        conn.close()


def delete_bill(bill_id, user_id):
    """Delete a bill by ID strictly owned by user_id and return its image_path."""
    if not user_id:
        return None
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT image_path FROM bills WHERE id = ? AND user_id = ?",
            (bill_id, user_id)
        ).fetchone()
        if not row:
            return None
        image_path = row["image_path"]
        with conn:
            conn.execute("DELETE FROM bills WHERE id = ? AND user_id = ?", (bill_id, user_id))
        return image_path
    finally:
        conn.close()


def get_category_summary(user_id, target_currency="LKR"):
    """
    Aggregate category statistics strictly for the specified user_id,
    with all bills converted into the user's preferred target_currency.
    """
    target_curr = normalize_currency(target_currency or "LKR")

    if not user_id:
        return {
            "categories": [],
            "grand_total": 0.0,
            "total_count": 0,
            "target_currency": target_curr,
        }

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT id, category, total_amount, currency, bill_date
            FROM bills
            WHERE user_id = ?
        """, (user_id,)).fetchall()

        cat_data = {}
        grand_total_conv = 0.0
        total_count = len(rows)

        for r in rows:
            cat = r["category"] or "Other"
            raw_amt = float(r["total_amount"] or 0.0)
            curr = r["currency"] or "LKR"
            amt_conv = convert_currency(raw_amt, curr, target_curr)
            bill_date = r["bill_date"] or ""

            if cat not in cat_data:
                cat_data[cat] = {
                    "category": cat,
                    "bill_count": 0,
                    "total_amount": 0.0,
                    "latest_date": "",
                }

            cat_data[cat]["bill_count"] += 1
            cat_data[cat]["total_amount"] += amt_conv
            if bill_date and (not cat_data[cat]["latest_date"] or bill_date > cat_data[cat]["latest_date"]):
                cat_data[cat]["latest_date"] = bill_date

            grand_total_conv += amt_conv

        grand_total_conv = round(grand_total_conv, 2)

        summary = []
        for cat, data in cat_data.items():
            tot = round(data["total_amount"], 2)
            pct = round((tot / grand_total_conv * 100.0), 1) if grand_total_conv > 0 else 0.0
            summary.append({
                "category": cat,
                "bill_count": data["bill_count"],
                "total_amount": tot,
                "percentage": pct,
                "latest_date": data["latest_date"] or "-",
            })

        # Sort categories by total_amount descending
        summary.sort(key=lambda x: (x["total_amount"], x["bill_count"]), reverse=True)

        return {
            "categories": summary,
            "grand_total": grand_total_conv,
            "total_count": total_count,
            "target_currency": target_curr,
        }
    finally:
        conn.close()


def export_bills_to_csv(user_id, target_currency="LKR"):
    """Generate a clean CSV string of bills for a user including converted amounts in target_currency."""
    target_curr = normalize_currency(target_currency or "LKR")
    bills = get_all_bills(user_id=user_id, target_currency=target_curr)
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "ID",
        "Bill Date",
        "Vendor Name",
        "Bill Number",
        "Category",
        "Total Amount",
        "Currency",
        f"Total Amount ({target_curr})",
        "Tax Amount",
        "Payment Method",
        "Items Summary",
        "Notes",
        "Created At",
    ])

    for b in bills:
        # Summarize line items
        items_desc = []
        for item in b.get("line_items", []):
            name = item.get("name", "")
            qty = item.get("quantity") if item.get("quantity") is not None else (item.get("qty") or 1)
            price = item.get("unit_price") or 0.0
            items_desc.append(f"{name} (x{qty} @ {price})")
        items_summary = "; ".join(items_desc)

        writer.writerow([
            b["id"],
            b["bill_date"] or "",
            b["vendor_name"] or "",
            b["bill_number"] or "",
            b["category"],
            f"{b['total_amount']:.2f}",
            b["currency"],
            f"{b.get('total_amount_converted', b['total_amount']):.2f}",
            f"{b['tax_amount']:.2f}",
            b["payment_method"] or "",
            items_summary,
            b["notes"] or "",
            b["created_at"],
        ])

    return output.getvalue()


# ==========================================
# Income Management Functions (User Scoped)
# ==========================================

def save_income(data, user_id):
    """
    Save a new income record strictly for user_id.
    data dict contains: source, category, amount, currency, income_date, notes
    """
    if not user_id:
        raise ValueError("user_id is required to record income.")

    source = (data.get("source") or "Income").strip()
    category = data.get("category") or "Salary"
    if category not in INCOME_CATEGORIES:
        category = "Other Income"

    try:
        amount = float(data.get("amount") or 0.0)
    except (ValueError, TypeError):
        amount = 0.0

    if amount <= 0:
        raise ValueError("Income amount must be greater than zero.")

    currency = normalize_currency(data.get("currency") or "LKR")
    income_date = (data.get("income_date") or datetime.now().strftime("%Y-%m-%d")).strip()
    if len(income_date) >= 10:
        income_date = income_date[:10]

    notes = (data.get("notes") or "").strip()

    conn = get_db()
    try:
        with conn:
            cursor = conn.execute("""
                INSERT INTO incomes (user_id, source, category, amount, currency, income_date, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (user_id, source, category, amount, currency, income_date, notes))
            return cursor.lastrowid
    finally:
        conn.close()


def delete_income(income_id, user_id):
    """Delete an income record strictly belonging to user_id."""
    if not user_id:
        return False
    conn = get_db()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM incomes WHERE id = ? AND user_id = ?;", (income_id, user_id))
            return cursor.rowcount > 0
    finally:
        conn.close()


def get_income_by_id(income_id, user_id):
    """Retrieve an income record strictly belonging to user_id."""
    if not user_id:
        return None
    conn = get_db()
    try:
        cursor = conn.execute("SELECT * FROM incomes WHERE id = ? AND user_id = ?;", (income_id, user_id))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_incomes(user_id, year_month=None, target_currency="LKR"):
    """
    Retrieve incomes for user_id, optionally filtered by year_month ('YYYY-MM').
    Amounts are converted to target_currency.
    """
    if not user_id:
        return []
    target_curr = normalize_currency(target_currency or "LKR")
    conn = get_db()
    try:
        if year_month:
            cursor = conn.execute("""
                SELECT * FROM incomes
                WHERE user_id = ? AND substr(income_date, 1, 7) = ?
                ORDER BY income_date DESC, id DESC;
            """, (user_id, year_month))
        else:
            cursor = conn.execute("""
                SELECT * FROM incomes
                WHERE user_id = ?
                ORDER BY income_date DESC, id DESC;
            """, (user_id,))

        rows = cursor.fetchall()
        incomes = []
        for r in rows:
            inc = dict(r)
            inc["amount"] = round(float(inc["amount"]), 2)
            inc["currency"] = normalize_currency(inc.get("currency") or "LKR")
            inc["amount_converted"] = round(
                convert_currency(inc["amount"], inc["currency"], target_curr), 2
            )
            incomes.append(inc)
        return incomes
    finally:
        conn.close()


# ==========================================
# Monthly Financial Reports Engine
# ==========================================

def get_monthly_financial_report(user_id, year=None, month=None, target_currency="LKR"):
    """
    Compute comprehensive monthly income & expenditure report for user_id.
    Converts all amounts to target_currency.
    Includes:
      - Total Incomes and Category Breakdown
      - Total Expenditures and Category Breakdown (for Pie chart)
      - Net Savings / Cash Flow & Savings Rate %
      - Daily spending timeline
      - Comparison with previous month
      - List of itemized bills & incomes
      - Available months for selector
    """
    if not user_id:
        return {
            "period": "",
            "period_label": "",
            "target_currency": target_currency or "LKR",
            "total_income": 0.0,
            "total_expenditure": 0.0,
            "net_savings": 0.0,
            "savings_rate": 0.0,
            "expense_ratio": 0.0,
            "daily_avg_spend": 0.0,
            "expense_categories": [],
            "income_categories": [],
            "daily_timeline": {"days": [], "expenses": [], "incomes": []},
            "bills": [],
            "incomes": [],
            "available_months": [],
            "prev_month": "",
            "next_month": "",
        }

    target_curr = normalize_currency(target_currency or "LKR")
    now = datetime.now()

    # Support 'YYYY-MM' passed as year
    if isinstance(year, str) and "-" in year and month is None:
        parts = year.split("-")
        year, month = parts[0], parts[1]

    try:
        yr = int(year) if year is not None else now.year
        mo = int(month) if month is not None else now.month
        if not (1 <= mo <= 12):
            mo = now.month
    except (ValueError, TypeError):
        yr = now.year
        mo = now.month

    ym_str = f"{yr:04d}-{mo:02d}"
    dt_period = datetime(yr, mo, 1)
    period_label = dt_period.strftime("%B %Y")

    # Previous and Next month calculations
    if mo == 1:
        prev_yr, prev_mo = yr - 1, 12
    else:
        prev_yr, prev_mo = yr, mo - 1
    prev_ym_str = f"{prev_yr:04d}-{prev_mo:02d}"

    if mo == 12:
        next_yr, next_mo = yr + 1, 1
    else:
        next_yr, next_mo = yr, mo + 1
    next_ym_str = f"{next_yr:04d}-{next_mo:02d}"

    _, num_days = calendar.monthrange(yr, mo)

    conn = get_db()
    try:
        # 1. Fetch Bills for this month
        cursor = conn.execute("""
            SELECT id, vendor_name, bill_date, bill_number, category,
                   total_amount, tax_amount, currency, payment_method,
                   items_json, image_path, notes, created_at
            FROM bills
            WHERE user_id = ? AND substr(bill_date, 1, 7) = ?
            ORDER BY bill_date DESC, id DESC;
        """, (user_id, ym_str))
        bill_rows = cursor.fetchall()

        bills = []
        expense_by_cat = {}
        daily_expense = {d: 0.0 for d in range(1, num_days + 1)}
        total_expense_conv = 0.0

        for r in bill_rows:
            b = dict(r)
            b["total_amount"] = round(float(b["total_amount"]), 2)
            b["tax_amount"] = round(float(b["tax_amount"] or 0.0), 2)
            b["currency"] = normalize_currency(b["currency"] or "LKR")
            b_conv = round(convert_currency(b["total_amount"], b["currency"], target_curr), 2)
            b["total_amount_converted"] = b_conv
            total_expense_conv += b_conv

            cat = b["category"] or "Other"
            if cat not in expense_by_cat:
                expense_by_cat[cat] = {
                    "category": cat,
                    "total_amount": 0.0,
                    "bill_count": 0,
                    "color": CATEGORY_COLORS.get(cat, "#94A3B8"),
                }
            expense_by_cat[cat]["total_amount"] += b_conv
            expense_by_cat[cat]["bill_count"] += 1

            # Day of month for daily timeline
            try:
                day_num = int(b["bill_date"].split("-")[2])
                if 1 <= day_num <= num_days:
                    daily_expense[day_num] += b_conv
            except (ValueError, IndexError, AttributeError):
                pass

            bills.append(b)

        total_expense_conv = round(total_expense_conv, 2)

        # Build expenditure category summary with percentages
        expense_categories = []
        for cat_info in expense_by_cat.values():
            amt = round(cat_info["total_amount"], 2)
            pct = round((amt / total_expense_conv * 100.0), 1) if total_expense_conv > 0 else 0.0
            expense_categories.append({
                "category": cat_info["category"],
                "total_amount": amt,
                "bill_count": cat_info["bill_count"],
                "percentage": pct,
                "color": cat_info["color"],
            })
        expense_categories.sort(key=lambda x: x["total_amount"], reverse=True)

        # 2. Fetch Incomes for this month
        cursor = conn.execute("""
            SELECT id, source, category, amount, currency, income_date, notes, created_at
            FROM incomes
            WHERE user_id = ? AND substr(income_date, 1, 7) = ?
            ORDER BY income_date DESC, id DESC;
        """, (user_id, ym_str))
        income_rows = cursor.fetchall()

        incomes = []
        income_by_cat = {}
        daily_income = {d: 0.0 for d in range(1, num_days + 1)}
        total_income_conv = 0.0

        for r in income_rows:
            inc = dict(r)
            inc["amount"] = round(float(inc["amount"]), 2)
            inc["currency"] = normalize_currency(inc["currency"] or "LKR")
            inc_conv = round(convert_currency(inc["amount"], inc["currency"], target_curr), 2)
            inc["amount_converted"] = inc_conv
            total_income_conv += inc_conv

            cat = inc["category"] or "Other Income"
            if cat not in income_by_cat:
                income_by_cat[cat] = {
                    "category": cat,
                    "total_amount": 0.0,
                    "count": 0,
                    "color": INCOME_COLORS.get(cat, "#94A3B8"),
                }
            income_by_cat[cat]["total_amount"] += inc_conv
            income_by_cat[cat]["count"] += 1

            # Day of month for daily timeline
            try:
                day_num = int(inc["income_date"].split("-")[2])
                if 1 <= day_num <= num_days:
                    daily_income[day_num] += inc_conv
            except (ValueError, IndexError, AttributeError):
                pass

            incomes.append(inc)

        total_income_conv = round(total_income_conv, 2)

        # Build income category summary with percentages
        income_categories_summary = []
        for cat_info in income_by_cat.values():
            amt = round(cat_info["total_amount"], 2)
            pct = round((amt / total_income_conv * 100.0), 1) if total_income_conv > 0 else 0.0
            income_categories_summary.append({
                "category": cat_info["category"],
                "total_amount": amt,
                "count": cat_info["count"],
                "percentage": pct,
                "color": cat_info["color"],
            })
        income_categories_summary.sort(key=lambda x: x["total_amount"], reverse=True)

        # 3. Key Financial Metrics
        net_savings = round(total_income_conv - total_expense_conv, 2)
        savings_rate = round((net_savings / total_income_conv * 100.0), 1) if total_income_conv > 0 else 0.0
        expense_ratio = round((total_expense_conv / total_income_conv * 100.0), 1) if total_income_conv > 0 else 0.0
        daily_avg_spend = round(total_expense_conv / num_days, 2)

        # 4. Previous Month Comparison
        cursor = conn.execute("""
            SELECT total_amount, currency FROM bills
            WHERE user_id = ? AND substr(bill_date, 1, 7) = ?;
        """, (user_id, prev_ym_str))
        prev_exp = sum(convert_currency(r["total_amount"], r["currency"] or "LKR", target_curr) for r in cursor.fetchall())

        cursor = conn.execute("""
            SELECT amount, currency FROM incomes
            WHERE user_id = ? AND substr(income_date, 1, 7) = ?;
        """, (user_id, prev_ym_str))
        prev_inc = sum(convert_currency(r["amount"], r["currency"] or "LKR", target_curr) for r in cursor.fetchall())

        prev_exp = round(prev_exp, 2)
        prev_inc = round(prev_inc, 2)

        exp_change_pct = round(((total_expense_conv - prev_exp) / prev_exp * 100.0), 1) if prev_exp > 0 else None
        inc_change_pct = round(((total_income_conv - prev_inc) / prev_inc * 100.0), 1) if prev_inc > 0 else None

        # 5. Daily Timeline
        timeline_days = list(range(1, num_days + 1))
        daily_expenses_list = [round(daily_expense[d], 2) for d in timeline_days]
        daily_incomes_list = [round(daily_income[d], 2) for d in timeline_days]

        # 6. Available Distinct Months for Dropdown
        cursor = conn.execute("""
            SELECT DISTINCT substr(bill_date, 1, 7) AS ym FROM bills
            WHERE user_id = ? AND bill_date IS NOT NULL AND length(bill_date) >= 7
            UNION
            SELECT DISTINCT substr(income_date, 1, 7) AS ym FROM incomes
            WHERE user_id = ? AND income_date IS NOT NULL AND length(income_date) >= 7
            ORDER BY ym DESC;
        """, (user_id, user_id))
        raw_months = {row["ym"] for row in cursor.fetchall()}
        raw_months.add(ym_str)
        raw_months.add(now.strftime("%Y-%m"))

        available_months = []
        for m_str in sorted(list(raw_months), reverse=True):
            try:
                m_yr, m_mo = int(m_str[:4]), int(m_str[5:7])
                lbl = datetime(m_yr, m_mo, 1).strftime("%B %Y")
                available_months.append({"value": m_str, "label": lbl})
            except Exception:
                pass

        return {
            "period": ym_str,
            "period_label": period_label,
            "year": yr,
            "month": mo,
            "num_days": num_days,
            "prev_month": prev_ym_str,
            "next_month": next_ym_str,
            "target_currency": target_curr,
            "total_income": total_income_conv,
            "total_expenditure": total_expense_conv,
            "net_savings": net_savings,
            "savings_rate": savings_rate,
            "expense_ratio": expense_ratio,
            "daily_avg_spend": daily_avg_spend,
            "top_expense_category": expense_categories[0] if expense_categories else None,
            "top_income_category": income_categories_summary[0] if income_categories_summary else None,
            "prev_expenditure": prev_exp,
            "prev_income": prev_inc,
            "exp_change_pct": exp_change_pct,
            "inc_change_pct": inc_change_pct,
            "expense_categories": expense_categories,
            "income_categories": income_categories_summary,
            "daily_timeline": {
                "days": timeline_days,
                "expenses": daily_expenses_list,
                "incomes": daily_incomes_list,
            },
            "bills": bills,
            "incomes": incomes,
            "available_months": available_months,
        }
    finally:
        conn.close()


def export_monthly_report_to_csv(user_id, year, month, target_currency="LKR"):
    """Generate a detailed CSV for a monthly financial report."""
    report = get_monthly_financial_report(user_id, year=year, month=month, target_currency=target_currency)
    output = io.StringIO()
    writer = csv.writer(output)

    # 1. Summary Header
    writer.writerow(["MONTHLY FINANCIAL REPORT"])
    writer.writerow(["Period", report["period_label"]])
    writer.writerow(["Standardized Currency", report["target_currency"]])
    writer.writerow(["Total Income", f"{report['total_income']:.2f} {report['target_currency']}"])
    writer.writerow(["Total Expenditure", f"{report['total_expenditure']:.2f} {report['target_currency']}"])
    writer.writerow(["Net Savings / Cash Flow", f"{report['net_savings']:.2f} {report['target_currency']}"])
    writer.writerow(["Savings Rate", f"{report['savings_rate']:.1f}%"])
    writer.writerow(["Daily Average Spend", f"{report['daily_avg_spend']:.2f} {report['target_currency']}"])
    writer.writerow([])

    # 2. Category Spending Summary
    writer.writerow(["EXPENDITURE CATEGORY SUMMARY"])
    writer.writerow(["Category", "Bill Count", f"Total Amount ({report['target_currency']})", "Share of Spend (%)"])
    for cat in report["expense_categories"]:
        writer.writerow([cat["category"], cat["bill_count"], f"{cat['total_amount']:.2f}", f"{cat['percentage']:.1f}%"])
    writer.writerow([])

    # 3. Itemized Expenditures
    writer.writerow(["ITEMIZED BILLS & EXPENDITURES"])
    writer.writerow(["Date", "Vendor Name", "Category", "Original Currency", "Original Total", f"Amount ({report['target_currency']})", "Payment Method", "Bill Number", "Notes"])
    for b in report["bills"]:
        writer.writerow([
            b["bill_date"] or "",
            b["vendor_name"] or "",
            b["category"],
            b["currency"],
            f"{b['total_amount']:.2f}",
            f"{b['total_amount_converted']:.2f}",
            b["payment_method"] or "",
            b["bill_number"] or "",
            b["notes"] or "",
        ])
    writer.writerow([])

    # 4. Itemized Incomes
    writer.writerow(["ITEMIZED INCOMES"])
    writer.writerow(["Date", "Source / Employer", "Category", "Original Currency", "Original Amount", f"Amount ({report['target_currency']})", "Notes"])
    for inc in report["incomes"]:
        writer.writerow([
            inc["income_date"] or "",
            inc["source"] or "",
            inc["category"],
            inc["currency"],
            f"{inc['amount']:.2f}",
            f"{inc['amount_converted']:.2f}",
            inc["notes"] or "",
        ])

    return output.getvalue()
