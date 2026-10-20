"""
indra_extractor.py
Local OCR & Intelligent Bill / Receipt Parser using the Hugging Face model:
datalab-to/indra-ocr (ONNX Runtime, runs 100% on CPU, zero API keys required).
"""

import os
import re
from datetime import datetime
from database import CATEGORIES

_conv = None


def get_converter():
    """Lazy-load the Indra OCR model pipeline."""
    global _conv
    if _conv is None:
        import indra_ocr
        _conv = indra_ocr.load()
    return _conv


def parse_date(text: str) -> str:
    """Extract and normalize date to YYYY-MM-DD format."""
    # Matches: 2026-10-18, 2026/10/18, 2026.10.18
    m = re.search(r"\b(20\d{2})[-/.](0[1-9]|1[0-2])[-/.](0[1-9]|[12]\d|3[01])\b", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # Matches: 18-10-2026, 18/10/2026, 18.10.2026
    m = re.search(r"\b(0[1-9]|[12]\d|3[01])[-/.](0[1-9]|1[0-2])[-/.](20\d{2})\b", text)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    # Matches text months: 18 Oct 2026, Oct 18, 2026
    month_names = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
        "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12"
    }
    m = re.search(r"\b(\d{1,2})\s+([a-zA-Z]{3,9})[,\s]+(20\d{2})\b", text, re.IGNORECASE)
    if m:
        day = m.group(1).zfill(2)
        mon = month_names.get(m.group(2)[:3].lower())
        year = m.group(3)
        if mon:
            return f"{year}-{mon}-{day}"

    return ""


def parse_currency(text: str) -> str:
    """Detect currency from text symbols or abbreviations."""
    upper = text.upper()
    if "LKR" in upper or "RS." in upper or "RS " in upper or "RUPEES" in upper:
        return "LKR"
    if "USD" in upper or "$" in text:
        return "USD"
    if "EUR" in upper or "€" in text:
        return "EUR"
    if "GBP" in upper or "£" in text:
        return "GBP"
    if "INR" in upper or "₹" in text:
        return "INR"
    return "LKR"


def parse_payment_method(text: str) -> str:
    """Detect payment method."""
    upper = text.upper()
    if "CASH" in upper:
        return "Cash"
    if any(k in upper for k in ["VISA", "MASTERCARD", "CARD", "CREDIT", "DEBIT", "AMEX"]):
        return "Card"
    if any(k in upper for k in ["BANK TRANSFER", "EFT", "DIRECT DEPOSIT"]):
        return "Bank Transfer"
    if any(k in upper for k in ["ONLINE", "PAYPAL", "STRIPE", "QR", "UPI", "GOOGLE PAY", "APPLE PAY"]):
        return "Online"
    return "Other"


def clean_vendor_name(lines, full_text: str) -> str:
    """Extract and normalize vendor/store name."""
    text_upper = full_text.upper()

    # Known popular brands and supermarkets
    known_merchants = [
        ("SPAR", ["SPAR", "SSPAR", "SPARSRILANKA", "SPARLANKA"]),
        ("Keells Super", ["KEELLS"]),
        ("Cargills Food City", ["CARGILLS", "FOOD CITY"]),
        ("Arpico Supercentre", ["ARPICO"]),
        ("Softlogic GLOMARK", ["GLOMARK"]),
        ("Laugfs Supermarket", ["LAUGFS"]),
        ("Pizza Hut", ["PIZZA HUT"]),
        ("Domino's Pizza", ["DOMINO"]),
        ("McDonald's", ["MCDONALD"]),
        ("KFC", ["KFC"]),
        ("Burger King", ["BURGER KING"]),
        ("Uber", ["UBER", "UBER EATS"]),
        ("PickMe", ["PICKME"]),
        ("Dialog Axiata", ["DIALOG"]),
        ("Mobitel / SLT", ["MOBITEL", "SLT"]),
    ]

    for brand, triggers in known_merchants:
        for trig in triggers:
            if trig in text_upper:
                top_line = lines[0].strip() if lines else ""
                clean_top = re.sub(r"[^A-Za-z0-9\s]", "", top_line).strip()
                if clean_top and clean_top.upper() != brand.upper() and len(clean_top) < 25:
                    return f"{brand} ({clean_top.title()})"
                return brand

    # Fallback to first non-generic top line
    skip = {
        "RECEIPT", "TAX INVOICE", "INVOICE", "CASH RECEIPT", "BILL",
        "WELCOME", "CUSTOMER COPY", "OFFICIAL RECEIPT", "TAX"
    }
    for l in lines[:5]:
        clean = re.sub(r"^[#\s*_-]+", "", l).strip()
        clean = re.sub(r"[^A-Za-z0-9\s&\'.-]", "", clean).strip()
        if clean and clean.upper() not in skip and len(clean) > 2 and not re.match(r"^\d+$", clean):
            return clean.title()

    return "Unknown Vendor"


def categorize_bill(text: str, vendor: str) -> str:
    """Assign spending category based on keywords in bill content and vendor name."""
    combined = (vendor + " " + text).lower()

    rules = [
        ("Food & Dining", [
            "restaurant", "cafe", "coffee", "bistro", "burger", "pizza", "bar",
            "diner", "bakery", "kitchen", "grill", "food", "beverage", "tea room",
            "kfc", "mcdonald", "subway", "domino", "starbucks", "lunch", "dinner", "breakfast"
        ]),
        ("Groceries", [
            "supermarket", "grocery", "groceries", "market", "mart", "keells", "cargills",
            "spar", "costco", "walmart", "whole foods", "produce", "meat", "veg",
            "milk", "bread", "eggs", "rice", "flour", "sugar", "biscuit", "butter", "cheese"
        ]),
        ("Transportation", [
            "taxi", "cab", "uber", "pickme", "lyft", "fuel", "petrol", "diesel",
            "gas station", "parking", "toll", "airline", "flight", "railway", "train", "bus"
        ]),
        ("Utilities", [
            "electricity", "water", "sewerage", "ceb", "leco", "energy", "power",
            "garbage", "utility", "gas bill"
        ]),
        ("Office Supplies", [
            "stationery", "paper", "printer", "toner", "ink", "stapler", "pen",
            "desk", "office", "supplies", "bookstore"
        ]),
        ("Medical", [
            "pharmacy", "chemist", "hospital", "clinic", "doctor", "dental",
            "medicine", "drugs", "pharma", "lab test", "healthcare", "optical"
        ]),
        ("Communication", [
            "telecom", "broadband", "internet", "dialog", "mobitel", "slt", "airtel",
            "hutch", "mobile recharge", "phone bill", "data plan"
        ]),
        ("Entertainment", [
            "cinema", "movie", "theatre", "concert", "ticket", "game", "bowling",
            "amusement", "club", "karaoke"
        ]),
        ("Rent & Housing", [
            "rent", "lease", "apartment", "tenancy", "maintenance fee", "condo fee"
        ]),
    ]

    for category, keywords in rules:
        if any(kw in combined for kw in keywords):
            return category

    return "Other"


def extract_receipt_items(pages) -> list:
    """Extract individual line items, quantities, and prices using spatial and textual analysis."""
    lines = [l for p in pages for l in getattr(p, "lines", []) if l.text.strip()]
    if not lines:
        return []

    # 1. Spatial Y-Coordinate Clustering into Receipt Rows
    sorted_lines = sorted(lines, key=lambda l: (l.box[1], l.box[0]))
    rows = []
    curr_row = []
    curr_y = None

    for l in sorted_lines:
        y_mid = (l.box[1] + l.box[3]) / 2.0
        h = l.box[3] - l.box[1]
        if curr_y is None or abs(y_mid - curr_y) < (h * 0.6):
            curr_row.append(l)
            curr_y = y_mid if curr_y is None else (curr_y + y_mid) / 2.0
        else:
            curr_row.sort(key=lambda item: item.box[0])
            rows.append(curr_row)
            curr_row = [l]
            curr_y = y_mid

    if curr_row:
        curr_row.sort(key=lambda item: item.box[0])
        rows.append(curr_row)

    stop_words = [
        "SUBTOTAL", "SUB TOTAL", "TOTAL", "NET TOTAL", "NET AMOUNT", "AMOUNT DUE",
        "BALANCE", "CASH", "CHANGE", "TENDERED", "ITEMS :", "ITEMS:", "ITEM COUNT",
        "TOTAL QUANTITY", "PAYMENT", "THANK YOU", "CARD", "VISA", "MASTERCARD",
        "CASHIER", "TERMINAL", "END TIME", "NON-DISCOUNTED"
    ]
    header_words = ["PRODUC", "ITEM", "DESCRIPTION", "PRICE", "QTY", "0TY", "AMOUNT", "AMOUN", "RATE"]

    def is_header(text):
        u = text.upper()
        return sum(1 for hw in header_words if hw in u) >= 2 or any(hw in u for hw in ["PRODUC", "DESCRIPTION"])

    def is_stop(text):
        u = text.upper()
        return any(sw in u for sw in stop_words)

    def extract_prices(text):
        matches = re.findall(r"\b\d{1,6}(?:\.\d{2})\b", text)
        return [float(m.replace(",", "")) for m in matches]

    items = []
    in_items = False
    pending_desc = None
    has_explicit_header = any(is_header(" ".join(l.text.strip() for l in r)) for r in rows)

    for r in rows:
        row_str = " ".join(l.text.strip() for l in r)
        upper_str = row_str.upper()

        if has_explicit_header and not in_items:
            if is_header(row_str):
                in_items = True
            continue
        elif not has_explicit_header and not in_items:
            if any(k in upper_str for k in ["DATE", "INVOICE", "TIME", "BILL NO", "ORDER"]):
                in_items = True
                continue

        if in_items:
            if is_stop(row_str):
                break

            prices = extract_prices(row_str)

            if not prices:
                # Text row without price: clean description (remove leading index like '1 ' or '01 ')
                clean = re.sub(r"^\d+\s+", "", row_str).strip()
                if len(clean) > 2 and re.search(r"[a-zA-Z]", clean):
                    if pending_desc:
                        pending_desc += " " + clean
                    else:
                        pending_desc = clean
            else:
                # Prices detected on this row
                if pending_desc:
                    # Multi-row item: description was on preceding row
                    unit_p = prices[-2] if len(prices) >= 2 else prices[0]
                    tot = prices[-1]
                    qty = 1.0
                    if unit_p > 0:
                        computed_qty = round(tot / unit_p, 2)
                        if 0.05 <= computed_qty <= 100:
                            qty = computed_qty

                    items.append({
                        "name": pending_desc,
                        "quantity": qty,
                        "unit_price": unit_p,
                        "total": tot
                    })
                    pending_desc = None
                else:
                    # Single-row item: description & price on same row
                    desc_part = re.sub(r"\b\d{1,6}(?:\.\d{2})\b", "", row_str).strip()
                    desc_part = re.sub(r"\b\d+\s*[xX@]\s*", "", desc_part).strip()
                    desc_part = re.sub(r"^\d+\s+", "", desc_part).strip()
                    desc_part = re.sub(r"[^a-zA-Z0-9\s&\'/.-]", "", desc_part).strip()

                    if len(desc_part) > 2 and re.search(r"[a-zA-Z]", desc_part):
                        unit_p = prices[0]
                        tot = prices[-1]
                        qty = 1.0
                        if unit_p > 0:
                            computed_qty = round(tot / unit_p, 2)
                            if 0.05 <= computed_qty <= 100:
                                qty = computed_qty

                        items.append({
                            "name": desc_part,
                            "quantity": qty,
                            "unit_price": unit_p,
                            "total": tot
                        })

    return items


def extract_bill_data_local(image_path: str) -> dict:
    """Extract bill information using local Hugging Face model datalab-to/indra-ocr."""
    try:
        conv = get_converter()
        raw_pages = list(conv.convert(image_path))
        pages = [page for _, page in raw_pages]
        if not pages:
            return {
                "error": "Indra OCR produced no output from the image.",
                "vendor_name": "", "bill_date": "", "bill_number": "",
                "items": [], "tax_amount": 0.0, "total_amount": 0.0,
                "currency": "LKR", "payment_method": "Cash",
                "category": "Other", "raw_text": "",
            }

        full_markdown = "\n\n".join(p.markdown for p in pages)
        all_lines = []
        for p in pages:
            for l in getattr(p, "lines", []):
                t = l.text.strip()
                if t:
                    all_lines.append(t)

        if not all_lines:
            all_lines = [line.strip() for line in full_markdown.splitlines() if line.strip()]

        raw_text = "\n".join(all_lines)

        # 1. Vendor Name
        vendor_name = clean_vendor_name(all_lines, raw_text)

        # 2. Bill Date
        bill_date = parse_date(raw_text)

        # 3. Bill / Invoice Number
        bill_number = ""
        inv_match = re.search(
            r"(?:inv[a-z]*|receipt|rec[a-z]*|bill|order|ref)[.\s]*(?:no|num|number)?\s*[:#.-]\s*([A-Za-z0-9\-_]{3,30})",
            raw_text, re.IGNORECASE
        )
        if inv_match:
            bill_number = inv_match.group(1).strip()

        # 4. Extract Line Items using Spatial / Multi-row Parser
        items = extract_receipt_items(pages)

        # 5. Total & Tax Amount
        total_amount = 0.0
        tax_amount = 0.0

        for line in reversed(all_lines):
            upper_line = line.upper()
            if any(k in upper_line for k in ["TOTAL", "NET AMOUNT", "AMOUNT DUE", "BALANCE DUE", "PAYABLE"]):
                nums = re.findall(r"(\d{1,6}(?:[.,]\d{2}))", line)
                if nums:
                    val_str = nums[-1].replace(",", "")
                    try:
                        val = float(val_str)
                        if val > total_amount:
                            total_amount = val
                            break
                    except ValueError:
                        pass

        for line in all_lines:
            upper_line = line.upper()
            if any(k in upper_line for k in ["TAX", "VAT", "GST", "SERVICE CHARGE"]):
                nums = re.findall(r"(\d{1,6}(?:[.,]\d{2}))", line)
                if nums:
                    try:
                        tax_amount = float(nums[-1].replace(",", ""))
                        break
                    except ValueError:
                        pass

        # Cross-validate total against items sum
        sum_of_items = sum(it["total"] for it in items)
        if sum_of_items > 0:
            if total_amount == 0.0 or abs(total_amount - sum_of_items) > (sum_of_items * 0.5):
                total_amount = sum_of_items + tax_amount

        # Fallback if no items detected but total is known
        if not items and total_amount > 0:
            items = [{"name": vendor_name or "Total Purchase", "quantity": 1.0, "unit_price": total_amount, "total": total_amount}]

        # 6. Currency & Payment Method
        currency = parse_currency(raw_text)
        payment_method = parse_payment_method(raw_text)

        # 7. Category
        category = categorize_bill(raw_text, vendor_name)

        return {
            "vendor_name": vendor_name,
            "bill_date": bill_date,
            "bill_number": bill_number,
            "items": items,
            "tax_amount": tax_amount,
            "total_amount": total_amount,
            "currency": currency,
            "payment_method": payment_method,
            "category": category,
            "raw_text": raw_text,
            "model_used": "Indra OCR (Hugging Face: datalab-to/indra-ocr)",
        }

    except Exception as exc:
        return {
            "error": f"Indra OCR extraction failed: {str(exc)}",
            "vendor_name": "", "bill_date": "", "bill_number": "",
            "items": [], "tax_amount": 0.0, "total_amount": 0.0,
            "currency": "LKR", "payment_method": "Cash",
            "category": "Other", "raw_text": "",
        }
