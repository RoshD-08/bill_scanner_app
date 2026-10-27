"""
app.py
Standalone AI Bill Scanner & Categorizer Web Application.
Supports private user accounts, custom preferred currencies, multi-currency conversion,
and strictly user-isolated dashboards.
"""

import os
import uuid
import json
from functools import wraps
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, jsonify, Response, session
)
from dotenv import load_dotenv

from database import (
    init_db, create_user, authenticate_user, get_user_by_id,
    update_user_currency, update_user_theme, update_user_profile,
    update_user_avatar, update_user_password, get_user_stats, save_bill, get_all_bills, delete_bill,
    get_category_summary, export_bills_to_csv, CATEGORIES,
    INCOME_CATEGORIES, CATEGORY_COLORS, INCOME_COLORS,
    save_income, delete_income, get_incomes,
    get_monthly_financial_report, export_monthly_report_to_csv
)
from currency import (
    SUPPORTED_CURRENCIES, get_common_exchange_rates,
    fetch_live_rates, get_rate, convert_currency, normalize_currency
)
from indra_extractor import extract_bill_data_local
from ai_extractor import extract_bill_data
from ollama_extractor import (
    extract_bill_data_ollama, is_ollama_running,
    get_available_models
)

# Load .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bill-scanner-secret-key-2026")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB max file upload
app.config["UPLOAD_FOLDER"] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static", "uploads"
)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "pdf"}
ALLOWED_AVATAR_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_avatar_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_AVATAR_EXTENSIONS


# Ensure tables and uploads folder exist at startup
with app.app_context():
    init_db()
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


# ==========================================
# Authentication Helpers
# ==========================================

def login_required(f):
    """Decorator to require login on protected endpoints."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            if request.is_json or request.path.startswith("/api/") or request.path == "/upload":
                return jsonify({"error": "Authentication required. Please log in."}), 401
            flash("Please sign in to access your bill dashboard.", "error")
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function


@app.context_processor
def inject_user():
    """Make current_user and supported currencies available to all Jinja templates."""
    user_id = session.get("user_id")
    current_user = None
    if user_id:
        current_user = get_user_by_id(user_id)
        if not current_user:
            session.clear()
    return dict(
        current_user=current_user,
        supported_currencies=SUPPORTED_CURRENCIES
    )


# ==========================================
# Auth Routes (Login, Register, Logout)
# ==========================================

@app.route("/login", methods=["GET", "POST"])
def login():
    """Sign in existing user."""
    if "user_id" in session:
        return redirect(url_for("index"))

    next_url = request.args.get("next") or request.form.get("next") or url_for("index")

    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "").strip()

        if not identifier or not password:
            flash("Please enter both your username/email and password.", "error")
            return render_template("login.html", identifier=identifier, next_url=next_url)

        user = authenticate_user(identifier, password)
        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["preferred_currency"] = user.get("preferred_currency", "LKR")
            flash(f"Welcome back, {user['username']}!", "success")
            return redirect(next_url)
        else:
            flash("Invalid username/email or password. Please try again.", "error")
            return render_template("login.html", identifier=identifier, next_url=next_url)

    return render_template("login.html", next_url=next_url)


@app.route("/register", methods=["GET", "POST"])
def register():
    """Create a new private user account with preferred currency."""
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        preferred_currency = request.form.get("preferred_currency", "LKR").strip().upper()

        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template(
                "register.html",
                username=username,
                email=email,
                preferred_currency=preferred_currency
            )

        if password != confirm_password:
            flash("Passwords do not match. Please re-enter.", "error")
            return render_template(
                "register.html",
                username=username,
                email=email,
                preferred_currency=preferred_currency
            )

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "error")
            return render_template(
                "register.html",
                username=username,
                email=email,
                preferred_currency=preferred_currency
            )

        user, error = create_user(
            username=username,
            password=password,
            email=email if email else None,
            preferred_currency=preferred_currency
        )
        if error:
            flash(error, "error")
            return render_template(
                "register.html",
                username=username,
                email=email,
                preferred_currency=preferred_currency
            )

        # Log new user in automatically with their chosen currency
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["preferred_currency"] = user.get("preferred_currency", "LKR")
        flash(f"Account created successfully! Your dashboard currency is set to {user['preferred_currency']}.", "success")
        return redirect(url_for("index"))

    return render_template("register.html", preferred_currency="LKR")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    """Sign out current user."""
    session.clear()
    flash("You have been signed out successfully.", "success")
    return redirect(url_for("login"))


@app.route("/api/user/currency", methods=["POST"])
@login_required
def api_update_currency():
    """Allow user to update their preferred dashboard currency on the fly."""
    user_id = session.get("user_id")
    if request.is_json and request.json:
        new_curr = request.json.get("currency")
    else:
        new_curr = request.form.get("currency")

    if not new_curr:
        return jsonify({"error": "No currency specified."}), 400

    clean_curr = normalize_currency(new_curr)
    update_user_currency(user_id, clean_curr)
    session["preferred_currency"] = clean_curr
    flash(f"Preferred currency updated to {clean_curr}.", "success")
    if request.is_json:
        return jsonify({"success": True, "currency": clean_curr})
    return redirect(request.referrer or url_for("profile"))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """User profile & account settings page (theme, currency, email, password, stats)."""
    user_id = session.get("user_id")
    user = get_user_by_id(user_id)
    target_curr = user.get("preferred_currency", "LKR") if user else "LKR"

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "update_settings":
            new_theme = request.form.get("theme", user.get("theme", "light"))
            new_curr = request.form.get("preferred_currency", target_curr)
            new_email = request.form.get("email", user.get("email", ""))

            success, error = update_user_profile(
                user_id=user_id,
                email=new_email,
                preferred_currency=new_curr,
                theme=new_theme
            )
            if success:
                session["preferred_currency"] = normalize_currency(new_curr)
                session["theme"] = new_theme
                flash("Profile and preferences updated successfully!", "success")
            else:
                flash(error or "Failed to update profile.", "error")
            return redirect(url_for("profile"))

        elif action == "upload_avatar":
            file = request.files.get("avatar")
            if not file or file.filename == "":
                flash("No file selected for profile picture.", "error")
            elif not allowed_avatar_file(file.filename):
                flash("Invalid image format. Please upload a PNG, JPG, JPEG, WEBP, or GIF image.", "error")
            else:
                ext = file.filename.rsplit(".", 1)[1].lower()
                unique_name = f"avatar_{user_id}_{uuid.uuid4().hex[:8]}.{ext}"
                dest_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
                file.save(dest_path)

                # Remove old avatar file if present
                old_avatar = user.get("avatar_path")
                if old_avatar:
                    old_path = os.path.join(app.config["UPLOAD_FOLDER"], old_avatar)
                    if os.path.exists(old_path) and os.path.isfile(old_path):
                        try:
                            os.remove(old_path)
                        except Exception:
                            pass

                update_user_avatar(user_id, unique_name)
                flash("Profile picture updated successfully!", "success")
            return redirect(url_for("profile"))

        elif action == "remove_avatar":
            old_avatar = user.get("avatar_path")
            if old_avatar:
                old_path = os.path.join(app.config["UPLOAD_FOLDER"], old_avatar)
                if os.path.exists(old_path) and os.path.isfile(old_path):
                    try:
                        os.remove(old_path)
                    except Exception:
                        pass
            update_user_avatar(user_id, None)
            flash("Profile picture removed. Initials restored.", "success")
            return redirect(url_for("profile"))

        elif action == "change_password":
            current_pwd = request.form.get("current_password", "").strip()
            new_pwd = request.form.get("new_password", "").strip()
            confirm_pwd = request.form.get("confirm_password", "").strip()

            if new_pwd != confirm_pwd:
                flash("New passwords do not match. Please try again.", "error")
            elif len(new_pwd) < 6:
                flash("New password must be at least 6 characters long.", "error")
            else:
                success, error = update_user_password(user_id, current_pwd, new_pwd)
                if success:
                    flash("Password changed successfully!", "success")
                else:
                    flash(error or "Password update failed.", "error")
            return redirect(url_for("profile"))

    # Refresh user
    user = get_user_by_id(user_id)
    target_curr = user.get("preferred_currency", "LKR") if user else "LKR"
    stats = get_user_stats(user_id=user_id, target_currency=target_curr)

    return render_template(
        "profile.html",
        user=user,
        stats=stats,
        supported_currencies=SUPPORTED_CURRENCIES,
        target_currency=target_curr
    )


@app.route("/api/user/theme", methods=["POST"])
@login_required
def api_update_theme():
    """Update user theme preference dynamically via JSON or form."""
    user_id = session.get("user_id")
    if request.is_json and request.json:
        theme = request.json.get("theme")
    else:
        theme = request.form.get("theme")

    if not theme:
        return jsonify({"error": "No theme specified."}), 400

    clean_theme = theme.lower().strip()
    if clean_theme not in ["light", "dark", "auto"]:
        clean_theme = "light"

    update_user_theme(user_id, clean_theme)
    session["theme"] = clean_theme

    if request.is_json:
        return jsonify({"success": True, "theme": clean_theme})
    flash(f"Theme updated to {clean_theme.title()} Mode.", "success")
    return redirect(request.referrer or url_for("profile"))


@app.route("/api/user/avatar", methods=["POST", "DELETE"])
@login_required
def api_user_avatar():
    """Upload or remove user profile picture via API."""
    user_id = session.get("user_id")
    user = get_user_by_id(user_id)

    if request.method == "DELETE" or (request.method == "POST" and request.form.get("action") == "remove"):
        old_avatar = user.get("avatar_path")
        if old_avatar:
            old_path = os.path.join(app.config["UPLOAD_FOLDER"], old_avatar)
            if os.path.exists(old_path) and os.path.isfile(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass
        update_user_avatar(user_id, None)
        return jsonify({"success": True, "avatar_url": None})

    file = request.files.get("avatar")
    if not file or file.filename == "":
        return jsonify({"error": "No avatar file provided."}), 400

    if not allowed_avatar_file(file.filename):
        return jsonify({"error": "Invalid file type. Allowed: PNG, JPG, JPEG, WEBP, GIF."}), 400

    ext = file.filename.rsplit(".", 1)[1].lower()
    unique_name = f"avatar_{user_id}_{uuid.uuid4().hex[:8]}.{ext}"
    dest_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
    file.save(dest_path)

    old_avatar = user.get("avatar_path")
    if old_avatar:
        old_path = os.path.join(app.config["UPLOAD_FOLDER"], old_avatar)
        if os.path.exists(old_path) and os.path.isfile(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass

    update_user_avatar(user_id, unique_name)
    avatar_url = url_for("static", filename=f"uploads/{unique_name}")
    return jsonify({"success": True, "avatar_url": avatar_url, "avatar_path": unique_name})


# ==========================================
# Main Dashboard & Bill Routes (Strictly Scoped)
# ==========================================

@app.route("/")
@login_required
def index():
    """Main dashboard displaying upload zone, category summary, and user's private bills in their preferred currency."""
    user_id = session.get("user_id")
    user = get_user_by_id(user_id)
    target_curr = user.get("preferred_currency", "LKR") if user else "LKR"

    summary = get_category_summary(user_id=user_id, target_currency=target_curr)
    bills = get_all_bills(user_id=user_id, target_currency=target_curr)
    rates = get_common_exchange_rates(base_currency=target_curr)

    return render_template(
        "index.html",
        summary=summary,
        bills=bills,
        categories=CATEGORIES,
        income_categories=INCOME_CATEGORIES,
        currencies=SUPPORTED_CURRENCIES,
        supported_currencies=SUPPORTED_CURRENCIES,
        exchange_rates=rates,
        target_currency=target_curr
    )


@app.route("/bills")
@login_required
def bills_page():
    """Dedicated page for viewing, searching, and filtering all user bills and receipts."""
    user_id = session.get("user_id")
    user = get_user_by_id(user_id)
    target_curr = user.get("preferred_currency", "LKR") if user else "LKR"

    bills = get_all_bills(user_id=user_id, target_currency=target_curr)

    return render_template(
        "bills.html",
        bills=bills,
        categories=CATEGORIES,
        currencies=SUPPORTED_CURRENCIES,
        supported_currencies=SUPPORTED_CURRENCIES,
        target_currency=target_curr
    )


@app.route("/api/currency/rates")
def api_currency_rates():
    """Return current exchange rates relative to base currency."""
    user_id = session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    default_base = user.get("preferred_currency", "LKR") if user else "LKR"
    base = request.args.get("base", default_base)

    return jsonify({
        "base": base,
        "rates": fetch_live_rates(),
        "common": get_common_exchange_rates(base_currency=base)
    })


@app.route("/upload", methods=["POST"])
@login_required
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

    # Model extraction: Defaults to Meta Llama 3.2 via Ollama
    engine = request.form.get("engine", "llama3.2").lower()

    if engine in ["llama", "llama3.2", "ollama"]:
        extracted = extract_bill_data_ollama(file_path)
        # If Ollama is not yet started, fallback smoothly to local Indra OCR
        if extracted.get("error"):
            fallback = extract_bill_data_local(file_path)
            if not fallback.get("error"):
                fallback["warning"] = f"Llama 3.2: {extracted['error']} (used Indra OCR fallback)"
                extracted = fallback
    elif engine == "gemini":
        extracted = extract_bill_data(file_path)
        if extracted.get("error"):
            extracted = extract_bill_data_local(file_path)
    else:
        extracted = extract_bill_data_local(file_path)

    extracted["image_path"] = unique_name

    # If currency not detected, default to user's preferred currency
    user_id = session.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    if user and not extracted.get("currency"):
        extracted["currency"] = user.get("preferred_currency", "LKR")

    return jsonify(extracted)


@app.route("/api/ollama/status")
def ollama_status():
    """Return local Ollama daemon status and available models."""
    return jsonify({
        "running": is_ollama_running(),
        "models": get_available_models()
    })


@app.route("/save", methods=["POST"])
@login_required
def save():
    """Save user-confirmed or manually entered bill data to SQLite associated with current user."""
    user_id = session.get("user_id")
    image_path = request.form.get("image_path", "").strip()

    items_json = request.form.get("items_json", "[]")
    try:
        items = json.loads(items_json) if items_json else []
    except Exception:
        items = []

    try:
        total_amount = float(request.form.get("total_amount", 0.0) or 0.0)
    except (ValueError, TypeError):
        total_amount = 0.0

    if total_amount <= 0 and items:
        total_amount = sum(float(it.get("total") or 0.0) for it in items)

    if total_amount <= 0:
        flash("Please enter a bill amount greater than zero.", "error")
        return redirect(request.referrer or url_for("index"))

    try:
        tax_amount = float(request.form.get("tax_amount", 0.0) or 0.0)
    except (ValueError, TypeError):
        tax_amount = 0.0

    user = get_user_by_id(user_id)
    default_curr = user.get("preferred_currency", "LKR") if user else "LKR"
    raw_curr = request.form.get("currency", default_curr).strip()
    vendor_name = request.form.get("vendor_name", "").strip() or "Manual Expense"
    bill_date = request.form.get("bill_date", "").strip() or datetime.now().strftime("%Y-%m-%d")

    data = {
        "vendor_name": vendor_name,
        "bill_date": bill_date,
        "bill_number": request.form.get("bill_number", "").strip(),
        "category": request.form.get("category", "Other").strip(),
        "total_amount": total_amount,
        "tax_amount": tax_amount,
        "currency": normalize_currency(raw_curr),
        "payment_method": request.form.get("payment_method", "Cash").strip(),
        "items": items,
        "image_path": image_path,
        "raw_text": request.form.get("raw_text", "").strip(),
        "notes": request.form.get("notes", "").strip(),
    }

    try:
        save_bill(data, user_id=user_id)
        flash(f"Bill from '{data['vendor_name']}' saved successfully!", "success")
    except Exception as e:
        flash(f"Failed to save bill: {str(e)}", "error")

    redirect_target = request.form.get("redirect_to") or url_for("bills_page")
    return redirect(redirect_target)


@app.route("/delete/<int:bill_id>", methods=["POST"])
@login_required
def delete(bill_id):
    """Delete a bill record owned by current user and clean up image file."""
    user_id = session.get("user_id")
    image_path = delete_bill(bill_id, user_id=user_id)
    if image_path:
        full_path = os.path.join(app.config["UPLOAD_FOLDER"], image_path)
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except Exception:
                pass
        flash("Bill deleted successfully.", "success")
    else:
        flash("Bill not found or you do not have permission to delete it.", "error")

    return redirect(request.referrer or url_for("bills_page"))


@app.route("/export/csv")
@login_required
def export_csv():
    """Export current user's bill records to CSV format with their preferred currency."""
    user_id = session.get("user_id")
    user = get_user_by_id(user_id)
    target_curr = user.get("preferred_currency", "LKR") if user else "LKR"
    csv_data = export_bills_to_csv(user_id=user_id, target_currency=target_curr)
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=bills_summary_{target_curr.lower()}.csv"}
    )


@app.route("/api/summary")
@login_required
def api_summary():
    """Return JSON category breakdown in current user's preferred currency."""
    user_id = session.get("user_id")
    user = get_user_by_id(user_id)
    target_curr = user.get("preferred_currency", "LKR") if user else "LKR"
    return jsonify(get_category_summary(user_id=user_id, target_currency=target_curr))


# ==========================================
# Monthly Financial Reports & Incomes Routes
# ==========================================

@app.route("/reports")
@login_required
def reports():
    """Separate dedicated dashboard for monthly financial reports (incomes, expenditures, pie charts)."""
    user_id = session.get("user_id")
    user = get_user_by_id(user_id)
    target_curr = user.get("preferred_currency", "LKR") if user else "LKR"

    month_param = request.args.get("month") or datetime.now().strftime("%Y-%m")
    try:
        parts = month_param.split("-")
        year, month = int(parts[0]), int(parts[1])
    except Exception:
        now = datetime.now()
        year, month = now.year, now.month

    report = get_monthly_financial_report(user_id=user_id, year=year, month=month, target_currency=target_curr)

    return render_template(
        "reports.html",
        report=report,
        selected_month=report["period"],
        available_months=report["available_months"],
        income_categories=INCOME_CATEGORIES,
        cat_colors=CATEGORY_COLORS,
        inc_colors=INCOME_COLORS,
        currencies=SUPPORTED_CURRENCIES,
        target_currency=target_curr
    )


@app.route("/api/incomes", methods=["POST"])
@login_required
def api_add_income():
    """Add a new income record for the current user."""
    user_id = session.get("user_id")

    if request.is_json and request.json:
        data = request.json
    else:
        data = request.form.to_dict()

    try:
        income_id = save_income(data, user_id=user_id)
        flash("Income entry recorded successfully.", "success")
        if request.is_json:
            return jsonify({"success": True, "income_id": income_id})
    except ValueError as e:
        flash(str(e), "error")
        if request.is_json:
            return jsonify({"error": str(e)}), 400

    redirect_target = request.form.get("redirect_to")
    if redirect_target:
        return redirect(redirect_target)

    income_date = data.get("income_date", "")
    month_query = income_date[:7] if len(income_date) >= 7 else None
    if month_query:
        return redirect(url_for("reports", month=month_query))
    return redirect(url_for("reports"))


@app.route("/api/incomes/<int:income_id>/delete", methods=["POST"])
@login_required
def api_delete_income(income_id):
    """Delete an income record belonging to current user."""
    user_id = session.get("user_id")
    month_query = request.form.get("month") or request.args.get("month")

    success = delete_income(income_id, user_id=user_id)
    if success:
        flash("Income entry deleted successfully.", "success")
        if request.is_json:
            return jsonify({"success": True})
    else:
        flash("Income entry not found or access denied.", "error")
        if request.is_json:
            return jsonify({"error": "Income entry not found or access denied."}), 404

    if month_query:
        return redirect(url_for("reports", month=month_query))
    return redirect(url_for("reports"))


@app.route("/reports/export/csv")
@login_required
def export_monthly_report_csv():
    """Export monthly income and expenditure financial report to CSV."""
    user_id = session.get("user_id")
    user = get_user_by_id(user_id)
    target_curr = user.get("preferred_currency", "LKR") if user else "LKR"

    month_param = request.args.get("month") or datetime.now().strftime("%Y-%m")
    try:
        parts = month_param.split("-")
        year, month = int(parts[0]), int(parts[1])
    except Exception:
        now = datetime.now()
        year, month = now.year, now.month

    csv_data = export_monthly_report_to_csv(user_id=user_id, year=year, month=month, target_currency=target_curr)
    filename = f"financial_report_{year:04d}_{month:02d}_{target_curr.lower()}.csv"

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/api/reports")
@login_required
def api_reports():
    """Return JSON monthly financial report data for current user."""
    user_id = session.get("user_id")
    user = get_user_by_id(user_id)
    target_curr = user.get("preferred_currency", "LKR") if user else "LKR"

    month_param = request.args.get("month") or datetime.now().strftime("%Y-%m")
    try:
        parts = month_param.split("-")
        year, month = int(parts[0]), int(parts[1])
    except Exception:
        now = datetime.now()
        year, month = now.year, now.month

    report = get_monthly_financial_report(user_id=user_id, year=year, month=month, target_currency=target_curr)
    return jsonify(report)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    print(f"Starting Standalone AI Bill Scanner on http://127.0.0.1:{port}")
    app.run(debug=True, port=port)
