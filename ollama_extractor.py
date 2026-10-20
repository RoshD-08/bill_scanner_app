"""
ollama_extractor.py
Receipt and Bill extraction using Meta's Llama 3.2 via Ollama (https://ollama.com/library/llama3.2).
Runs 100% locally on your machine with zero cloud API keys.
"""

import os
import json
import re
import urllib.request
import urllib.error
import subprocess
from database import CATEGORIES
from indra_extractor import get_converter

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")

_DEFAULT_DATA = {
    "vendor_name": "",
    "bill_date": "",
    "bill_number": "",
    "items": [],
    "tax_amount": 0.0,
    "total_amount": 0.0,
    "currency": "LKR",
    "payment_method": "Cash",
    "category": "Other",
    "raw_text": "",
}


def is_ollama_running() -> bool:
    """Check if the local Ollama server is responding."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", headers={"User-Agent": "bill-scanner"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_ollama_running():
    """Attempt to start Ollama server in background if not already active."""
    if is_ollama_running():
        return True

    # Try starting ollama serve in background
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        import time
        for _ in range(10):
            time.sleep(1)
            if is_ollama_running():
                return True
    except Exception:
        pass

    return False


def get_available_models() -> list:
    """Get list of downloaded model tags from Ollama."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


def ensure_model_pulled(model_name: str = DEFAULT_OLLAMA_MODEL):
    """Ensure the requested model is available locally; pull if missing."""
    models = get_available_models()
    # Check if exact match or tag match (e.g. 'llama3.2' matches 'llama3.2:latest')
    if any(m == model_name or m.startswith(f"{model_name}:") for m in models):
        return True

    try:
        print(f"Pulling {model_name} from Ollama library...")
        pull_url = f"{OLLAMA_HOST}/api/pull"
        data = json.dumps({"name": model_name, "stream": False}).encode("utf-8")
        req = urllib.request.Request(pull_url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"Failed to auto-pull {model_name}: {e}")
        return False


def extract_bill_data_ollama(image_path: str, model_name: str = DEFAULT_OLLAMA_MODEL) -> dict:
    """
    Extract structured receipt information by:
    1. Running Indra OCR locally to read all text from the image.
    2. Passing the OCR text to Llama 3.2 via Ollama with JSON structured formatting.
    """
    # Step 1: Ensure Ollama is running
    if not is_ollama_running():
        started = ensure_ollama_running()
        if not started:
            err = dict(_DEFAULT_DATA)
            err["error"] = (
                "Ollama server is not running at http://localhost:11434. "
                "Please run 'ollama serve' or start the Ollama application."
            )
            return err

    # Step 2: Extract raw text from the image using Indra OCR
    try:
        conv = get_converter()
        raw_pages = list(conv.convert(image_path))
        pages = [p for _, p in raw_pages]
        if not pages:
            err = dict(_DEFAULT_DATA)
            err["error"] = "OCR produced no text from the uploaded bill image."
            return err

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
    except Exception as e:
        err = dict(_DEFAULT_DATA)
        err["error"] = f"OCR failed before LLM processing: {str(e)}"
        return err

    # Step 3: Ensure Llama 3.2 model is downloaded
    ensure_model_pulled(model_name)

    # Step 4: Prompt Llama 3.2 with JSON output format
    categories_str = ", ".join(CATEGORIES)
    prompt = f"""You are an expert AI for receipt and invoice analysis.
Analyze the following text extracted from a bill/receipt and extract all structured fields.
Return ONLY a valid JSON object matching the requested schema. Do not write any other explanation or markdown code block.

Allowed categories (pick exactly one):
{categories_str}

JSON Schema required:
{{
  "vendor_name": "Merchant / store / company name",
  "bill_date": "YYYY-MM-DD (e.g. 2026-10-18, or empty string if not found)",
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
  "currency": "3-letter currency code (e.g. LKR, USD, EUR). Default: 'LKR'",
  "payment_method": "Cash / Card / Bank Transfer / Online / Other",
  "category": "One category from the allowed list above",
  "notes": "Brief summary or notes"
}}

Rules:
1. "vendor_name": Extract the brand or store name (e.g. "SPAR", "Keells", "Cargills", "Walmart"). Do NOT use an email address, website, or city name alone.
2. Every item must have its name, numeric quantity, unit price, and line total.
3. For each item: "total" should equal "quantity * unit_price".
4. "total_amount": The actual net bill total to pay (items total + tax), NOT cash tendered or change given. For example, if items total is 175.00 and Cash is 200.00 and change is 25.00, total_amount is 175.00, NEVER 200.00.
5. If the bill total is not explicitly written or unclear, compute total_amount as sum of items + tax.
6. "category": Choose the most fitting category (e.g. Supermarket, groceries, household items, repellents -> "Groceries").

BILL TEXT:
{raw_text}
"""

    try:
        chat_url = f"{OLLAMA_HOST}/api/chat"
        payload = {
            "model": model_name,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "format": "json",
            "stream": False,
            "options": {
                "temperature": 0.1
            }
        }

        req = urllib.request.Request(
            chat_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            content = res_json.get("message", {}).get("content", "").strip()

            clean_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.DOTALL).strip()
            data = json.loads(clean_json)

            result = dict(_DEFAULT_DATA)
            for k in _DEFAULT_DATA:
                if k in data and data[k] is not None:
                    result[k] = data[k]

            # Coerce numbers
            try:
                result["total_amount"] = float(result.get("total_amount", 0.0) or 0.0)
            except (ValueError, TypeError):
                result["total_amount"] = 0.0

            try:
                result["tax_amount"] = float(result.get("tax_amount", 0.0) or 0.0)
            except (ValueError, TypeError):
                result["tax_amount"] = 0.0

            # Validate items list & ensure arithmetic consistency
            cleaned_items = []
            sum_items = 0.0
            for it in result.get("items", []):
                if isinstance(it, dict):
                    try:
                        q = float(it.get("quantity") or 1.0)
                    except (ValueError, TypeError):
                        q = 1.0
                    try:
                        p = float(it.get("unit_price") or 0.0)
                    except (ValueError, TypeError):
                        p = 0.0
                    try:
                        t = float(it.get("total") or (q * p))
                    except (ValueError, TypeError):
                        t = q * p

                    # Fix item total if mismatched from quantity * unit_price
                    if q > 0 and p > 0:
                        calc_tot = round(q * p, 2)
                        if abs(t - calc_tot) > 0.01:
                            t = calc_tot

                    sum_items += t
                    cleaned_items.append({
                        "name": str(it.get("name") or "Item").strip(),
                        "quantity": q,
                        "unit_price": p,
                        "total": t,
                    })
            result["items"] = cleaned_items

            # Reconcile total_amount with items sum if tender amount or subtotal mismatch
            if sum_items > 0:
                expected_total = round(sum_items + result["tax_amount"], 2)
                diff = round(result["total_amount"] - expected_total, 2)
                # Check if difference matches change returned on receipt
                change_detected = (
                    diff > 0 and (
                        f"{diff:.2f}" in raw_text or
                        f"{int(diff)}" in raw_text or
                        re.search(rf"\b{int(diff)}(\.00)?\b", raw_text) is not None
                    )
                )
                # If tender amount was captured, or total was 0, or total is higher than items sum with no tax
                if result["total_amount"] == 0.0 or change_detected or (diff > 0 and result["tax_amount"] == 0.0):
                    result["total_amount"] = expected_total

            # Clean vendor name if it captured an email address
            if "@" in result["vendor_name"] or ".com" in result["vendor_name"].lower():
                from indra_extractor import clean_vendor_name
                result["vendor_name"] = clean_vendor_name(all_lines, raw_text)

            # Validate category
            if result["category"] not in CATEGORIES:
                from indra_extractor import categorize_bill
                result["category"] = categorize_bill(raw_text, result["vendor_name"])

            result["raw_text"] = raw_text
            result["model_used"] = f"Llama 3.2 via Ollama ({model_name})"
            return result

    except Exception as exc:
        err = dict(_DEFAULT_DATA)
        err["error"] = f"Llama 3.2 extraction failed: {str(exc)}"
        err["raw_text"] = raw_text
        return err
