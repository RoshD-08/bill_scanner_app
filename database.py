"""
database.py
SQLite database management for the standalone AI Bill Scanner application.
Supports user authentication, multi-tenant bill scoping, and category summaries.
Zero external configuration required.
"""

import os
import sqlite3
import json
import csv
import io
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")

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

            # 3. Check and migrate user_id column into bills if missing
            cursor = conn.execute("PRAGMA table_info(bills);")
            columns = [row["name"] for row in cursor.fetchall()]
            if "user_id" not in columns:
                conn.execute("ALTER TABLE bills ADD COLUMN user_id INTEGER REFERENCES users(id);")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_bills_user ON bills(user_id);")
    finally:
        conn.close()


# ==========================================
# User Authentication Functions
# ==========================================

def create_user(username, password, email=None):
    """
    Register a new user with hashed password.
    Returns (user_dict, None) on success or (None, error_str) on validation/conflict failure.
    """
    username = (username or "").strip()
    email = (email or "").strip().lower() or None
    password = (password or "").strip()

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
                INSERT INTO users (username, email, password_hash)
                VALUES (?, ?, ?)
            """, (username, email, pwd_hash))
            user_id = cursor.lastrowid

            # If this is the first registered user, claim existing unowned bills
            count_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            if count_users == 1:
                conn.execute("UPDATE bills SET user_id = ? WHERE user_id IS NULL", (user_id,))

            row = conn.execute(
                "SELECT id, username, email, created_at FROM users WHERE id = ?",
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
        row = conn.execute("""
            SELECT id, username, email, password_hash, created_at
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
        row = conn.execute("""
            SELECT id, username, email, created_at
            FROM users
            WHERE id = ?
        """, (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_username(username):
    """Retrieve user record by username."""
    if not username:
        return None
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT id, username, email, created_at
            FROM users
            WHERE username = ?
        """, (username.strip(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ==========================================
# Bill Management Functions (User-Scoped)
# ==========================================

def save_bill(data, user_id=None):
    """Insert a new bill record into the database associated with user_id."""
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


def get_all_bills(user_id=None, category=None, start_date=None, end_date=None, query=None):
    """Retrieve bills for a specific user with optional filters."""
    conn = get_db()
    try:
        sql = "SELECT * FROM bills WHERE 1=1"
        params = []

        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)

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
        from currency import convert_to_lkr
        for r in rows:
            b = dict(r)
            try:
                parsed = json.loads(b["items_json"]) if b["items_json"] else []
            except Exception:
                parsed = []
            b["line_items"] = parsed
            b["items"] = parsed
            b["total_amount_lkr"] = convert_to_lkr(b.get("total_amount", 0.0), b.get("currency", "LKR"))
            bills.append(b)
        return bills
    finally:
        conn.close()


def get_bill_by_id(bill_id, user_id=None):
    """Fetch single bill details by ID, validating user ownership if user_id given."""
    conn = get_db()
    try:
        if user_id is not None:
            row = conn.execute("SELECT * FROM bills WHERE id = ? AND user_id = ?", (bill_id, user_id)).fetchone()
        else:
            row = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
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


def delete_bill(bill_id, user_id=None):
    """Delete a bill by ID (restricted to user_id if given) and return its image_path."""
    conn = get_db()
    try:
        if user_id is not None:
            row = conn.execute(
                "SELECT image_path FROM bills WHERE id = ? AND user_id = ?",
                (bill_id, user_id)
            ).fetchone()
        else:
            row = conn.execute("SELECT image_path FROM bills WHERE id = ?", (bill_id,)).fetchone()

        if not row:
            return None
        image_path = row["image_path"]
        with conn:
            if user_id is not None:
                conn.execute("DELETE FROM bills WHERE id = ? AND user_id = ?", (bill_id, user_id))
            else:
                conn.execute("DELETE FROM bills WHERE id = ?", (bill_id,))
        return image_path
    finally:
        conn.close()


def get_category_summary(user_id=None):
    """
    Aggregate statistics grouped by category for a specific user,
    with all bill totals converted to LKR.
    """
    from currency import convert_to_lkr

    conn = get_db()
    try:
        if user_id is not None:
            rows = conn.execute("""
                SELECT id, category, total_amount, currency, bill_date
                FROM bills
                WHERE user_id = ?
            """, (user_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, category, total_amount, currency, bill_date
                FROM bills
            """).fetchall()

        cat_data = {}
        grand_total_lkr = 0.0
        total_count = len(rows)

        for r in rows:
            cat = r["category"] or "Other"
            raw_amt = float(r["total_amount"] or 0.0)
            curr = r["currency"] or "LKR"
            amt_lkr = convert_to_lkr(raw_amt, curr)
            bill_date = r["bill_date"] or ""

            if cat not in cat_data:
                cat_data[cat] = {
                    "category": cat,
                    "bill_count": 0,
                    "total_amount": 0.0,
                    "latest_date": "",
                }

            cat_data[cat]["bill_count"] += 1
            cat_data[cat]["total_amount"] += amt_lkr
            if bill_date and (not cat_data[cat]["latest_date"] or bill_date > cat_data[cat]["latest_date"]):
                cat_data[cat]["latest_date"] = bill_date

            grand_total_lkr += amt_lkr

        grand_total_lkr = round(grand_total_lkr, 2)

        summary = []
        for cat, data in cat_data.items():
            tot = round(data["total_amount"], 2)
            pct = round((tot / grand_total_lkr * 100.0), 1) if grand_total_lkr > 0 else 0.0
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
            "grand_total": grand_total_lkr,
            "total_count": total_count,
        }
    finally:
        conn.close()


def export_bills_to_csv(user_id=None):
    """Generate a clean CSV string of bills for a user including LKR converted amounts."""
    bills = get_all_bills(user_id=user_id)
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
        "Total Amount (LKR)",
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
            f"{b.get('total_amount_lkr', b['total_amount']):.2f}",
            f"{b['tax_amount']:.2f}",
            b["payment_method"] or "",
            items_summary,
            b["notes"] or "",
            b["created_at"],
        ])

    return output.getvalue()
