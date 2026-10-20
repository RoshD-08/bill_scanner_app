"""
app.py
Standalone AI Bill Scanner & Categorizer Web Application.
"""

import os
import uuid
import json
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, jsonify, Response
)
from dotenv import load_dotenv

from database import (
    init_db, save_bill, get_all_bills,
    delete_bill, get_category_summary,
    export_bills_to_csv, CATEGORIES
)
from indra_extractor import extract_bill_data_local
from ai_extractor import extract_bill_data

# Load .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bill-scanner-secret-key-2026")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB max file upload
app.config["UPLOAD_FOLDER"] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static", "uploads"
)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "pdf"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# Ensure tables and uploads folder exist at startup
with app.app_context():
    init_db()
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


@app.route("/")
def index():
    """Main dashboard displaying upload zone, category summary, and all bills."""
    summary = get_category_summary()
    bills = get_all_bills()
    return render_template(
        "index.html",
        summary=summary,
        bills=bills,
        categories=CATEGORIES
    )


@app.route("/upload", methods=["POST"])
def upload():
    """Handle receipt image upload and trigger AI vision extraction."""
    file = request.files.get("bill_image") or request.files.get("file")
    if not file or file.filename == "":
        return jsonify({"error": "No file was selected for upload."}), 400

    if not allowed_file(file.filename):
        return jsonify({
            "error": "Unsupported file format. Please upload an image (PNG, JPG, WEBP, etc.)"
        }), 400

    # Save to disk with unique name
    ext = file.filename.rsplit(".", 1)[1].lower()
    unique_name = f"bill_{uuid.uuid4().hex[:12]}.{ext}"
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
    file.save(file_path)

    # Perform extraction using the local Hugging Face model (indra-ocr) by default
    engine = request.form.get("engine", "indra").lower()
    if engine == "gemini":
        extracted = extract_bill_data(file_path)
        # If Gemini fails or lacks key, fallback to local model
        if extracted.get("error"):
            extracted = extract_bill_data_local(file_path)
    else:
        extracted = extract_bill_data_local(file_path)

    extracted["image_path"] = unique_name

    return jsonify(extracted)


@app.route("/save", methods=["POST"])
def save():
    """Save user-confirmed or edited bill data to SQLite."""
    image_path = request.form.get("image_path", "").strip()
    if not image_path:
        flash("Cannot save bill without receipt image.", "error")
        return redirect(url_for("index"))

    items_json = request.form.get("items_json", "[]")
    try:
        items = json.loads(items_json) if items_json else []
    except Exception:
        items = []

    try:
        total_amount = float(request.form.get("total_amount", 0.0) or 0.0)
    except (ValueError, TypeError):
        total_amount = 0.0

    try:
        tax_amount = float(request.form.get("tax_amount", 0.0) or 0.0)
    except (ValueError, TypeError):
        tax_amount = 0.0

    data = {
        "vendor_name": request.form.get("vendor_name", "").strip(),
        "bill_date": request.form.get("bill_date", "").strip(),
        "bill_number": request.form.get("bill_number", "").strip(),
        "category": request.form.get("category", "Other").strip(),
        "total_amount": total_amount,
        "tax_amount": tax_amount,
        "currency": request.form.get("currency", "LKR").strip(),
        "payment_method": request.form.get("payment_method", "").strip(),
        "items": items,
        "image_path": image_path,
        "raw_text": request.form.get("raw_text", "").strip(),
        "notes": request.form.get("notes", "").strip(),
    }

    try:
        save_bill(data)
        flash(f"Bill from '{data['vendor_name'] or 'Unknown'}' saved successfully!", "success")
    except Exception as e:
        flash(f"Failed to save bill: {str(e)}", "error")

    return redirect(url_for("index"))


@app.route("/delete/<int:bill_id>", methods=["POST"])
def delete(bill_id):
    """Delete a bill record and clean up image file."""
    image_path = delete_bill(bill_id)
    if image_path:
        full_path = os.path.join(app.config["UPLOAD_FOLDER"], image_path)
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except Exception:
                pass
        flash("Bill deleted successfully.", "success")
    else:
        flash("Bill not found.", "error")

    return redirect(url_for("index"))


@app.route("/export/csv")
def export_csv():
    """Export all bill records to CSV format."""
    csv_data = export_bills_to_csv()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=bills_summary.csv"}
    )


@app.route("/api/summary")
def api_summary():
    """Return JSON category breakdown."""
    return jsonify(get_category_summary())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    print(f"Starting Standalone AI Bill Scanner on http://127.0.0.1:{port}")
    app.run(debug=True, port=port)
