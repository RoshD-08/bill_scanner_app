"""
financial_advisor.py
Intelligent Financial Advisor & Insights Engine for BillAI.
Analyzes user expenses, incomes, cash flow, and categories to provide
actionable spending reduction advice and next-month budget planning.
Supports Ollama (llama3.2), Google Gemini Vision/Text, and a fast built-in rule-based financial engine.
"""

import os
import json
import datetime
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

from database import (
    get_user_by_id, get_user_stats, get_monthly_financial_report,
    get_all_bills, get_incomes
)
from currency import normalize_currency

load_dotenv()

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()


def get_financial_context(user_id: int, target_currency: Optional[str] = None) -> Dict[str, Any]:
    """
    Extract comprehensive, structured financial metrics for the user
    covering current month, category distributions, discretionary vs fixed spend,
    and 50/30/20 budget framework comparisons.
    """
    now = datetime.datetime.now()
    user = get_user_by_id(user_id)
    if not user:
        return {}

    curr = normalize_currency(target_currency or user.get("preferred_currency", "LKR"))

    # 1. Monthly report for current period
    report = get_monthly_financial_report(user_id=user_id, year=now.year, month=now.month, target_currency=curr)

    # 2. Lifetime vault stats
    stats = get_user_stats(user_id=user_id, target_currency=curr)

    # 3. Categorize into Needs (Essentials), Wants (Discretionary), and Savings
    needs_categories = {"Rent & Housing", "Utilities", "Groceries", "Healthcare", "Insurance", "Education"}
    wants_categories = {"Food & Dining", "Entertainment", "Shopping", "Travel", "Personal Care"}

    total_exp = report.get("total_expenditure", 0.0)
    total_inc = report.get("total_income", 0.0)
    net_savings = report.get("net_savings", 0.0)
    savings_rate = report.get("savings_rate", 0.0)

    needs_spend = 0.0
    wants_spend = 0.0
    other_spend = 0.0

    category_breakdown = []
    for c in report.get("expense_categories", []):
        cat_name = c["category"]
        amt = c["total_amount"]
        pct = c["percentage"]
        category_breakdown.append({
            "category": cat_name,
            "amount": amt,
            "percentage": pct,
            "count": c.get("bill_count", 0)
        })
        if cat_name in needs_categories:
            needs_spend += amt
        elif cat_name in wants_categories:
            wants_spend += amt
        else:
            other_spend += amt

    # 4. Top vendors
    vendor_totals = {}
    for b in report.get("bills", []):
        v = (b.get("vendor_name") or "Unknown Vendor").strip()
        vendor_totals[v] = vendor_totals.get(v, 0.0) + b.get("total_amount_converted", 0.0)

    top_vendors = sorted(
        [{"vendor": k, "amount": round(v, 2)} for k, v in vendor_totals.items()],
        key=lambda x: x["amount"],
        reverse=True
    )[:5]

    # 5. Day-of-month and burn-rate calculation
    day_of_month = now.day
    days_in_month = report.get("num_days", 30)
    days_remaining = max(1, days_in_month - day_of_month)
    daily_burn_rate = round(total_exp / max(1, day_of_month), 2)
    projected_month_end = round(daily_burn_rate * days_in_month, 2)

    return {
        "username": user.get("username", "User"),
        "currency": curr,
        "period_label": report.get("period_label", now.strftime("%B %Y")),
        "days_in_month": days_in_month,
        "day_of_month": day_of_month,
        "days_remaining": days_remaining,
        "total_expenditure": round(total_exp, 2),
        "total_income": round(total_inc, 2),
        "net_savings": round(net_savings, 2),
        "savings_rate": savings_rate,
        "daily_burn_rate": daily_burn_rate,
        "projected_month_end": projected_month_end,
        "needs_spend": round(needs_spend, 2),
        "needs_pct": round((needs_spend / total_exp * 100), 1) if total_exp > 0 else 0.0,
        "wants_spend": round(wants_spend, 2),
        "wants_pct": round((wants_spend / total_exp * 100), 1) if total_exp > 0 else 0.0,
        "other_spend": round(other_spend, 2),
        "top_category": report.get("top_expense_category"),
        "categories": category_breakdown,
        "top_vendors": top_vendors,
        "bill_count": len(report.get("bills", [])),
        "lifetime_bills": stats.get("bill_count", 0),
        "lifetime_spent": round(stats.get("total_spent", 0.0), 2),
    }


def generate_rule_based_advice(context: Dict[str, Any], message: str) -> str:
    """
    Generate accurate, mathematical, deeply actionable financial guidance
    directly from the user's authentic data when local/cloud AI is unavailable or as a baseline.
    """
    curr = context.get("currency", "LKR")
    total_exp = context.get("total_expenditure", 0.0)
    total_inc = context.get("total_income", 0.0)
    net_savings = context.get("net_savings", 0.0)
    savings_rate = context.get("savings_rate", 0.0)
    categories = context.get("categories", [])
    top_cat = context.get("top_category")
    top_vendors = context.get("top_vendors", [])
    burn_rate = context.get("daily_burn_rate", 0.0)
    projected = context.get("projected_month_end", 0.0)
    wants_spend = context.get("wants_spend", 0.0)
    wants_pct = context.get("wants_pct", 0.0)
    days_rem = context.get("days_remaining", 15)

    msg_lower = (message or "").lower()

    # 1. SPENDING REDUCTION ADVICE
    if any(k in msg_lower for k in ["reduce", "cut", "save money", "spending less", "trim", "lower", "expensive", "stop spending", "areas"]):
        if not categories or total_exp == 0:
            return (
                f"You haven't recorded any expenses for {context.get('period_label')}. "
                "Upload or scan your receipts first, and I will analyze your spending habits and pinpoint where you can trim costs."
            )

        lines = [
            f"### 💡 Spending Reduction Strategy for {context.get('period_label')}\n",
            f"Your current recorded expenditure is **{curr} {total_exp:,.2f}** across {len(categories)} categories. "
            f"Here are the most impactful areas to cut spending:\n"
        ]

        # Check top expense category
        if top_cat:
            cat_name = top_cat.get("category", "")
            cat_amt = top_cat.get("total_amount", 0.0)
            cat_pct = top_cat.get("percentage", 0.0)
            trim_10 = cat_amt * 0.10
            trim_20 = cat_amt * 0.20
            lines.append(
                f"1. **Audit Your Primary Cost: {cat_name}**\n"
                f"   - **{cat_pct}%** of your budget is absorbed here (**{curr} {cat_amt:,.2f}**).\n"
                f"   - A modest **10% to 20% reduction** here would save you **{curr} {trim_10:,.2f} to {curr} {trim_20:,.2f}** each month.\n"
            )

        # Check discretionary / wants
        if wants_spend > 0:
            lines.append(
                f"2. **Trim Discretionary Wants ({wants_pct}% of budget)**\n"
                f"   - You've spent **{curr} {wants_spend:,.2f}** on lifestyle categories (Dining, Entertainment, Shopping).\n"
                f"   - **Tactical Tip:** Implement a 48-hour delay on non-essential purchases and set a weekly dining-out cap.\n"
            )

        # Top vendors
        if top_vendors:
            v_list = ", ".join([f"**{v['vendor']}** ({curr} {v['amount']:,.2f})" for v in top_vendors[:3]])
            lines.append(
                f"3. **Vendor Concentration**\n"
                f"   - Your highest vendor transactions were with: {v_list}.\n"
                f"   - Check for subscription renewals or bulk purchase discounts with these vendors.\n"
            )

        # Daily burn rate guidance
        lines.append(
            f"4. **Control Daily Burn Rate**\n"
            f"   - You are currently spending an average of **{curr} {burn_rate:,.2f} / day**.\n"
            f"   - Over the remaining **{days_rem} days** of this month, capping daily spending to **{curr} {burn_rate * 0.8:,.2f}** will yield an additional **{curr} {(burn_rate * 0.2 * days_rem):,.2f}** in savings.\n"
        )

        return "\n".join(lines)

    # 2. NEXT MONTH BUDGET / PLANNING ADVICE
    elif any(k in msg_lower for k in ["next month", "budget", "manage", "plan", "forecast", "future", "how to manage"]):
        lines = [
            f"### 🗓️ Financial Management Plan for Next Month\n",
            f"Based on your actual spending trajectory (**{curr} {total_exp:,.2f}** this month), here is your customized blueprint:\n"
        ]

        if total_inc > 0:
            needs_target = total_inc * 0.50
            wants_target = total_inc * 0.30
            savings_target = total_inc * 0.20

            lines.append(
                f"1. **The 50/30/20 Allocation Rule (Based on {curr} {total_inc:,.2f} Monthly Income):**\n"
                f"   - **50% Needs / Essentials Target:** {curr} {needs_target:,.2f} (Housing, Groceries, Utilities)\n"
                f"   - **30% Wants / Lifestyle Target:** {curr} {wants_target:,.2f} (Dining out, Leisure, Shopping)\n"
                f"   - **20% Wealth / Savings Target:** {curr} {savings_target:,.2f} (Emergency fund & investments)\n"
            )
        else:
            lines.append(
                f"1. **Establish Income Tracking:**\n"
                f"   - You haven't recorded a monthly income yet! Go to **Monthly Reports > Add Income** to record your salary. "
                f"This will unlock precise surplus and savings rate tracking.\n"
            )

        # Category budgets based on history
        lines.append(f"2. **Recommended Category Caps for Next Month:**\n")
        for c in categories[:4]:
            suggested_cap = round(c['amount'] * 0.90, 2)
            lines.append(f"   - **{c['category']}:** Cap at **{curr} {suggested_cap:,.2f}** (10% reduction target)")

        # Pace projection
        lines.append(
            f"\n3. **Proactive Cash Flow Cushion:**\n"
            f"   - Your projected month-end spend is **{curr} {projected:,.2f}**.\n"
            f"   - Set up an automated savings transfer at the beginning of next month (Pay Yourself First) rather than saving whatever remains at the end.\n"
        )

        return "\n".join(lines)

    # 3. CATEGORY BREAKDOWN / SUMMARY
    elif any(k in msg_lower for k in ["category", "breakdown", "where did my money", "where does my money", "summary"]):
        if not categories:
            return f"You don't have any categorized bills recorded for {context.get('period_label')}. Upload receipts to generate a breakdown."

        lines = [
            f"### 📊 Spending Breakdown for {context.get('period_label')}\n",
            f"Total expenditures standardized to **{curr} {total_exp:,.2f}**:\n"
        ]
        for c in categories:
            bar_len = int(c['percentage'] / 5)
            bar = "█" * max(1, bar_len)
            lines.append(f"- **{c['category']}**: {curr} {c['amount']:,.2f} ({c['percentage']}%) `{bar}`")

        if top_vendors:
            lines.append(f"\n**Top Vendor:** {top_vendors[0]['vendor']} ({curr} {top_vendors[0]['amount']:,.2f})")

        return "\n".join(lines)

    # 4. SAVINGS RATE & CASH FLOW
    elif any(k in msg_lower for k in ["saving", "surplus", "deficit", "cash flow", "rate"]):
        if total_inc <= 0:
            return (
                f"Your total expenditure this month is **{curr} {total_exp:,.2f}**.\n\n"
                f"To calculate your exact savings rate and net cash flow, please record your income using the "
                f"**Add Income** button in Monthly Reports."
            )

        status_emoji = "🟢" if net_savings >= 0 else "🔴"
        status_word = "Surplus" if net_savings >= 0 else "Deficit"

        return (
            f"### 💰 Cash Flow & Savings Analysis\n\n"
            f"- **Total Income:** {curr} {total_inc:,.2f}\n"
            f"- **Total Expenditure:** {curr} {total_exp:,.2f}\n"
            f"- **Net Cash Flow:** {status_emoji} **{curr} {net_savings:,.2f}** ({status_word})\n"
            f"- **Current Savings Rate:** **{savings_rate}%**\n\n"
            f"{'🎉 Excellent job maintaining a positive savings rate! Aim to invest this surplus in a high-yield account.' if net_savings >= 0 else '⚠️ You are currently operating at a monthly deficit. Review your top category expenses to bring your cash flow back to positive.'}"
        )

    # 5. GENERAL FINANCIAL OVERVIEW & GREETING
    else:
        top_cat_str = f"Your highest expense category is **{top_cat['category']}** ({top_cat['percentage']}%)" if top_cat else "No expenses recorded yet"
        return (
            f"Hello {context.get('username', 'there')}! I am your **BillAI Financial Advisor**.\n\n"
            f"**Your Quick Financial Snapshot for {context.get('period_label')}:**\n"
            f"- Total Recorded Spend: **{curr} {total_exp:,.2f}**\n"
            f"- Total Recorded Income: **{curr} {total_inc:,.2f}**\n"
            f"- Daily Burn Rate: **{curr} {burn_rate:,.2f} / day**\n"
            f"- {top_cat_str}\n\n"
            f"**What would you like to explore?**\n"
            f"1. 💡 *Where should I reduce spending?*\n"
            f"2. 🗓️ *How to manage next month financially?*\n"
            f"3. 📊 *Show my category breakdown*\n"
            f"4. 💰 *Evaluate my savings rate*"
        )


def query_ollama_advisor(prompt: str, context: Dict[str, Any], timeout: int = 12) -> Optional[str]:
    """Query local Ollama instance running llama3.2 with strict financial context."""
    url = f"{OLLAMA_HOST}/api/generate"
    curr = context.get("currency", "LKR")
    categories_json = json.dumps(context.get("categories", []), indent=2)
    top_vendors_json = json.dumps(context.get("top_vendors", []), indent=2)

    system_prompt = f"""You are an elite, highly practical Certified Financial Planner and AI money coach inside the BillAI application.
The user is asking for financial advice based on their REAL financial data stored in their private vault.
You must ground your advice strictly on their actual numbers:
- User: {context.get('username')}
- Standard Currency: {curr}
- Period: {context.get('period_label')}
- Total Expenditure: {curr} {context.get('total_expenditure')}
- Total Income: {curr} {context.get('total_income')}
- Net Savings: {curr} {context.get('net_savings')}
- Savings Rate: {context.get('savings_rate')}%
- Daily Average Spend: {curr} {context.get('daily_burn_rate')}
- Projected Month-End Spend: {curr} {context.get('projected_month_end')}
- Category Breakdown: {categories_json}
- Top Vendors: {top_vendors_json}

Instructions:
- Provide clear, empathetic, actionable, bulleted advice.
- Give concrete financial targets (e.g. cut dining by 15%, saving {curr} X).
- Use clear markdown with bold numbers and bullet points.
- Keep the response concise, punchy, and under 250 words.
"""

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"{system_prompt}\n\nUser Question: {prompt}\n\nFinancial Advisor Response:",
        "stream": False,
        "options": {
            "temperature": 0.4,
            "num_predict": 350
        }
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "BillAI-Advisor"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            res_text = data.get("response", "").strip()
            if res_text:
                return res_text
    except Exception:
        return None
    return None


def get_financial_advisor_reply(user_id: int, message: str, target_currency: Optional[str] = None) -> Dict[str, Any]:
    """
    Main entry point for chatbot inquiries:
    Extracts real context, attempts local/cloud AI generation, and gracefully falls back
    to the rule-based financial reasoning engine.
    """
    context = get_financial_context(user_id=user_id, target_currency=target_currency)
    if not context:
        return {
            "reply": "Unable to retrieve financial context. Please log in.",
            "suggestions": []
        }

    reply_text = None

    # 1. Try local Ollama if available
    try:
        reply_text = query_ollama_advisor(message, context, timeout=10)
    except Exception:
        reply_text = None

    # 2. Fallback to specialized rule-based intelligence
    if not reply_text:
        reply_text = generate_rule_based_advice(context, message)

    suggestions = [
        "Where should I reduce spending?",
        "How to manage next month?",
        "What is my highest category?",
        "Review my savings rate"
    ]

    return {
        "reply": reply_text,
        "context_summary": {
            "currency": context.get("currency"),
            "total_expenditure": context.get("total_expenditure"),
            "total_income": context.get("total_income"),
            "savings_rate": context.get("savings_rate"),
        },
        "suggestions": suggestions
    }
