# 🧾 BillAI — Local AI Bill Scanner & Categorizer

A standalone Python web application that extracts structured information from receipt/bill photos using the open-weights **Indra OCR model** ([`datalab-to/indra-ocr`](https://huggingface.co/datalab-to/indra-ocr)) from Hugging Face.

**100% on-device CPU execution — Zero API keys required.**

---

## ✨ Features

- **🤖 Local Hugging Face Model**: Powered by [`datalab-to/indra-ocr`](https://huggingface.co/datalab-to/indra-ocr) running on ONNX Runtime. Fast, private, and runs completely offline without sending your financial receipts to any cloud API.
- **📸 Direct Bill Ingestion**: Drag & drop receipt photos, browse files, or snap directly using a mobile camera.
- **📑 Structured Extraction & Differentiation**:
  - Vendor / Merchant Name
  - Bill Date & Invoice / Receipt Number
  - Individual Line Items (Description, Quantity, Unit Price, Line Total)
  - Tax Amount & Grand Total
  - Payment Method (Cash, Card, Bank Transfer, Online)
  - Currency Detection (LKR, USD, EUR, etc.)
  - Full raw OCR text
- **🏷️ Smart Categorization**: Auto-assigns categories (*Food & Dining, Groceries, Transportation, Utilities, Office Supplies, Medical, Entertainment, Communication, Rent & Housing, Other*).
- **✏️ Side-by-Side Review**: View the uploaded receipt photo alongside an editable form with real-time automatic total recalculation.
- **📊 Category Spending Table**: Displays bill counts, total expenditures, and percentage breakdown per category with visual progress bars.
- **🔍 Filter & Search**: Search by merchant or invoice number, filter by category and date range.
- **📥 One-Click Export**: Export all bills and line items to CSV.
- **⚡ Zero Setup**: Uses SQLite (`bills.db`) — no database server required.

---

## 🚀 Quick Start

### 1. Launch the App
```bash
cd /Users/roshanedias/Downloads/Roshane_Practice/bill_scanner_app
./run.sh
```

### 2. Open in Browser
👉 **[http://127.0.0.1:5002](http://127.0.0.1:5002)**

*The first time you scan, the ONNX model weights (~75 MB total) are downloaded from Hugging Face and cached locally in `~/.cache/huggingface/` for offline reuse.*

---

## 📂 Project Structure

```
bill_scanner_app/
├── app.py              # Flask server & endpoints
├── indra_extractor.py  # Local Indra OCR extraction (datalab-to/indra-ocr)
├── ai_extractor.py     # Optional Gemini Vision fallback
├── database.py         # SQLite database management (bills.db)
├── run.sh              # One-click startup script
├── requirements.txt    # Project dependencies (Flask, indra-ocr, Pillow, etc.)
├── static/
│   └── uploads/        # Stored receipt photos
└── templates/
    ├── base.html       # Base layout with Tailwind CSS
    └── index.html      # Unified scanner dashboard
```
