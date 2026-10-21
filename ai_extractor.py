"""
ai_extractor.py
Module for extracting structured data from bill / receipt images
using Google Gemini Vision (gemini-2.0-flash).
"""

import os
import json
import re
from dotenv import load_dotenv

load_dotenv()

try:
    import google.generativeai as genai
    from PIL import Image
    HAS_LIBS = True
except ImportError:
    HAS_LIBS = False

from database import CATEGORIES

_DEFAULT_DATA = {
    "vendor_name": "",
    "bill_date": "",
    "bill_number": "",
    "items": [],
    "tax_amount": 0.0,
    "total_amount": 0.0,
    "currency": "LKR",
    "payment_method": "",
    "category": "Other",
    "raw_text": "",
}


def extract_bill_data(image_path: str) -> dict:
    """Analyze a bill image and extract structured data using Gemini Vision."""
    if not HAS_LIBS:
        err = dict(_DEFAULT_DATA)
        err["error"] = "Required libraries not installed. Run: pip install -r requirements.txt"
        return err

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "your_gemini_api_key_here":
        err = dict(_DEFAULT_DATA)
        err["error"] = (
            "GEMINI_API_KEY is not configured in .env. "
            "Please obtain an API key from https://aistudio.google.com/ and add it to .env"
        )
        return err

    try:
        genai.configure(api_key=api_key)
        img = Image.open(image_path)

        categories_str = ", ".join(CATEGORIES)
        prompt = f"""
You are an expert AI for receipt and bill OCR and financial analysis.
Analyze this photo of a bill/receipt and extract all structured data.
Return ONLY a valid JSON object without markdown code blocks, preamble, or commentary.

JSON format required:
{{
  "vendor_name": "Merchant / store / company name",
  "bill_date": "YYYY-MM-DD format (if ambiguous or partial, use your best estimate; leave empty string if not found)",
  "bill_number": "Invoice, receipt, or reference number, or empty string",
  "items": [
    {{
      "name": "Item description",
      "quantity": 1.0,
      "unit_price": 0.0,
      "total": 0.0
    }}
  ],
  "tax_amount": 0.0,
  "total_amount": 0.0,
  "currency": "Detected 3-letter currency code (e.g. LKR, USD, EUR, GBP, INR). Default to 'LKR' if unclear.",
  "payment_method": "Cash / Card / Bank Transfer / Online / Other (if indicated)",
  "category": "Choose exactly ONE of: {categories_str}",
  "raw_text": "All raw readable text on the bill, preserving line breaks"
}}

Rules:
1. All monetary fields must be numbers (float or int), not strings with currency symbols.
2. Item quantity can be integer or decimal (e.g. 0.5, 1.250). If quantity or unit price is missing, use quantity: 1 and unit_price: item total.
3. If no line items can be distinguished, create one item with the vendor name or 'Total' and the grand total.
4. Choose the most specific category from the allowed list.
"""

        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content([prompt, img])
        response_text = response.text.strip()

        # Handle markdown ```json ... ``` wrapper if present
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", response_text, re.DOTALL)
        clean_json = match.group(1).strip() if match else response_text

        data = json.loads(clean_json)

        # Merge with defaults
        result = dict(_DEFAULT_DATA)
        for k in _DEFAULT_DATA:
            if k in data and data[k] is not None:
                result[k] = data[k]

        # Ensure valid category
        if result["category"] not in CATEGORIES:
            result["category"] = "Other"

        # Coerce numbers safely
        try:
            result["total_amount"] = float(result["total_amount"])
        except (TypeError, ValueError):
            result["total_amount"] = 0.0

        try:
            result["tax_amount"] = float(result["tax_amount"])
        except (TypeError, ValueError):
            result["tax_amount"] = 0.0

        # Validate items
        cleaned_items = []
        for item in result.get("items", []):
            if isinstance(item, dict):
                try:
                    raw_q = item.get("quantity") if item.get("quantity") is not None else item.get("qty")
                    qty = float(raw_q) if raw_q is not None else 1.0
                    if qty <= 0:
                        qty = 1.0
                except (TypeError, ValueError):
                    qty = 1.0
                try:
                    price = float(item.get("unit_price") or 0.0)
                except (TypeError, ValueError):
                    price = 0.0
                try:
                    tot = float(item.get("total") or (qty * price))
                except (TypeError, ValueError):
                    tot = qty * price

                cleaned_items.append({
                    "name": str(item.get("name") or "Item").strip(),
                    "quantity": qty,
                    "unit_price": price,
                    "total": tot,
                })
        result["items"] = cleaned_items

        return result

    except Exception as exc:
        err = dict(_DEFAULT_DATA)
        err["error"] = f"Extraction failed: {str(exc)}"
        return err
