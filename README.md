# 🧾 BillAI — Local AI Bill Scanner & Categorizer with Llama 3.2 & Indra OCR

A standalone Python web application that extracts structured information from receipt/bill photos using:
1. **Meta Llama 3.2 via Ollama** ([`ollama.com/library/llama3.2`](https://ollama.com/library/llama3.2)) for intelligent LLM reasoning and semantic categorization.
2. **Indra OCR** ([`datalab-to/indra-ocr`](https://huggingface.co/datalab-to/indra-ocr)) for fast, on-device OCR text recognition.

**100% local on-device execution — Zero cloud API keys required.**

---

## ✨ Features

- **🦙 Meta Llama 3.2 Integration**: Uses Llama 3.2 running locally via [Ollama](https://ollama.com/library/llama3.2) to analyze bill text, handle complex formats, extract line items, and auto-categorize expenses.
- **⚡ Fast On-Device OCR**: Powered by Hugging Face's `datalab-to/indra-ocr` (ONNX Runtime, ~18MB weights).
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
*(The startup script automatically verifies that the local Ollama daemon is running).*

### 2. Open in Browser
👉 **[http://127.0.0.1:5002](http://127.0.0.1:5002)**

Select your model on the dashboard:
- **🦙 Meta Llama 3.2 (Ollama)** *(Recommended)*
- **⚡ Indra OCR (Local ONNX)**
- **☁️ Google Gemini 2.0**

---

## 📂 Project Structure

```
bill_scanner_app/
├── app.py              # Flask server & routing
├── ollama_extractor.py # Llama 3.2 extraction via Ollama
├── indra_extractor.py  # Local Indra OCR extraction (datalab-to/indra-ocr)
├── ai_extractor.py     # Optional Gemini Vision fallback
├── database.py         # SQLite database management (bills.db)
├── run.sh              # One-click startup script (auto-starts Ollama & Flask)
├── requirements.txt    # Project dependencies
├── static/
│   └── uploads/        # Stored receipt photos
└── templates/
    ├── base.html       # Base layout with Tailwind CSS
    └── index.html      # Unified scanner dashboard
```
