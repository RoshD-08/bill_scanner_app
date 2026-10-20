"""
database.py
SQLite database management for the standalone AI Bill Scanner application.
Zero external configuration required.
"""

import os
import sqlite3
import json
import csv
import io
from datetime import datetime

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
    """Initialize the database schema if tables don't exist."""
    conn = get_db()
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    finally:
        conn.close()


def save_bill(data):
    """Insert a new bill record into the database."""
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
                    vendor_name, bill_date, bill_number, category,
                    total_amount, tax_amount, currency, payment_method,
                    items_json, image_path, raw_text, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
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


def get_all_bills(category=None, start_date=None, end_date=None, query=None):
    """Retrieve bills with optional filters."""
    conn = get_db()
    try:
        sql = "SELECT * FROM bills WHERE 1=1"
        params = []

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
            bills.append(b)
        return bills
    finally:
        conn.close()


def get_bill_by_id(bill_id):
    """Fetch single bill details by ID."""
    conn = get_db()
    try:
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


def delete_bill(bill_id):
    """Delete a bill by ID and return its image_path."""
    conn = get_db()
    try:
        row = conn.execute("SELECT image_path FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if not row:
            return None
        image_path = row["image_path"]
        with conn:
            conn.execute("DELETE FROM bills WHERE id = ?", (bill_id,))
        return image_path
    finally:
        conn.close()


def get_category_summary():
    """Aggregate statistics grouped by category."""
    conn = get_db()
    try:
        # Grand total first
        gt_row = conn.execute("""
            SELECT COUNT(*) AS total_count, COALESCE(SUM(total_amount), 0.0) AS grand_total
            FROM bills
        """).fetchone()

        total_count = gt_row["total_count"] if gt_row else 0
        grand_total = float(gt_row["grand_total"]) if gt_row else 0.0

        # Group by category
        rows = conn.execute("""
            SELECT category,
                   COUNT(*) AS bill_count,
                   COALESCE(SUM(total_amount), 0.0) AS category_total,
                   MAX(bill_date) AS latest_date
            FROM bills
            GROUP BY category
            ORDER BY category_total DESC, bill_count DESC
        """).fetchall()

        summary = []
        for r in rows:
            cat_total = float(r["category_total"])
            pct = (cat_total / grand_total * 100.0) if grand_total > 0 else 0.0
            summary.append({
                "category": r["category"],
                "bill_count": r["bill_count"],
                "total_amount": cat_total,
                "percentage": round(pct, 1),
                "latest_date": r["latest_date"] or "-",
            })

        return {
            "categories": summary,
            "grand_total": grand_total,
            "total_count": total_count,
        }
    finally:
        conn.close()


def export_bills_to_csv():
    """Generate a clean CSV string of all bills."""
    bills = get_all_bills()
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
            qty = item.get("quantity") or item.get("qty") or 1
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
            f"{b['tax_amount']:.2f}",
            b["payment_method"] or "",
            items_summary,
            b["notes"] or "",
            b["created_at"],
        ])

    return output.getvalue()
