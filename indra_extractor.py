"""
indra_extractor.py
Local OCR & Bill Information Extraction using the Hugging Face model:
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
            "milk", "bread", "eggs", "rice", "flour", "sugar"
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

        # Combine all lines and markdown
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

        # 1. Vendor Name: Usually the first non-generic heading
        skip_words = {"RECEIPT", "TAX INVOICE", "INVOICE", "CASH RECEIPT", "BILL", "WELCOME", "CUSTOMER COPY"}
        vendor_name = "Unknown Vendor"
        for line in all_lines[:5]:
            clean_l = re.sub(r"^[#\s*_-]+", "", line).strip()
            if clean_l and clean_l.upper() not in skip_words and len(clean_l) > 2:
                vendor_name = clean_l
                break

        # 2. Bill Date
        bill_date = parse_date(raw_text)

        # 3. Bill / Invoice Number
        bill_number = ""
        inv_match = re.search(
            r"(?:inv(?:oice)?|rec(?:eipt)?|bill|order|ref)[#:\s.-]*([A-Za-z0-9\-_]{3,20})",
            raw_text, re.IGNORECASE
        )
        if inv_match:
            bill_number = inv_match.group(1).strip()

        # 4. Total Amount: search from bottom up for total keywords
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

        # 5. Tax Amount
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

        # 6. Currency & Payment Method
        currency = parse_currency(raw_text)
        payment_method = parse_payment_method(raw_text)

        # 7. Category
        category = categorize_bill(raw_text, vendor_name)

        # 8. Line items parsing
        items = []
        for line in all_lines:
            # Check for pattern: Description ... Qty x Price ... Total
            # e.g., "Milk Pack 1L 1 x 450.00 450.00" or "Bread 2 @ 150 300.00"
            m = re.search(r"^(.*?)\s+(\d+)\s*[xX@]\s*(\d+(?:\.\d{2})?)\s+(\d+(?:\.\d{2})?)$", line)
            if m:
                desc = m.group(1).strip()
                qty = float(m.group(2))
                price = float(m.group(3))
                tot = float(m.group(4))
                if desc and tot > 0:
                    items.append({"name": desc, "quantity": qty, "unit_price": price, "total": tot})
                continue

            # Check for pattern: Description ... Amount
            m2 = re.search(r"^(.*?)[\t ]{2,}(\d+(?:\.\d{2}))\s*$", line)
            if m2:
                desc = m2.group(1).strip()
                amt = float(m2.group(2))
                upper_desc = desc.upper()
                if not any(k in upper_desc for k in ["TOTAL", "SUBTOTAL", "TAX", "CASH", "CHANGE", "CARD", "BALANCE"]):
                    items.append({"name": desc, "quantity": 1.0, "unit_price": amt, "total": amt})

        # If total was not found from bottom line, fallback to sum of items
        if total_amount == 0.0 and items:
            total_amount = sum(it["total"] for it in items) + tax_amount

        # If no items found, create single item with vendor/bill name
        if not items and total_amount > 0:
            items = [{"name": vendor_name or "Total Purchase", "quantity": 1.0, "unit_price": total_amount, "total": total_amount}]

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
