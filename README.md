# 🤖 Botify — Multi-Tenant WhatsApp Bot SaaS Platform

<p align="center">
  <img width="100%" alt="whatsapp bot" src="https://github.com/user-attachments/assets/e0dd6170-7279-4def-8c58-977a2ce04428" />
</p>

<p align="center">
  <strong>Build. Connect. Automate.</strong><br>
  Deploy custom AI-powered WhatsApp chatbots for retail businesses in seconds — no coding required.
</p>

<p align="center">
  <a href="https://web-production-10039.up.railway.app/login?next=%2F"><img src="https://img.shields.io/badge/Live-Demo-brightgreen?style=for-the-badge" alt="Live Demo"/></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python"/></a>
  <a href="https://flask.palletsprojects.com"><img src="https://img.shields.io/badge/Flask-3.1-lightgrey?style=for-the-badge&logo=flask&logoColor=white" alt="Flask"/></a>
  <a href="https://groq.com"><img src="https://img.shields.io/badge/AI-Groq%20LLM-orange?style=for-the-badge" alt="Groq AI"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" alt="License"/></a>
</p>

---

## 📖 Table of Contents
1. [Overview](#-overview)
2. [System Architecture](#%EF%B8%8F-system-architecture)
3. [Key Features](#-key-features)
4. [Tech Stack](#%EF%B8%8F-tech-stack)
5. [Project Structure](#-project-structure)
6. [Installation & Setup](#-installation--setup)
7. [Environment Variables](#-environment-variables)
8. [License](#-license)

---

## 🌟 Overview

**Botify** is a production-grade multi-tenant SaaS application designed to empower small to medium-sized businesses by automating their WhatsApp customer engagement. Business owners sign up, scan a QR code to link their WhatsApp instance, and instantly deploy an AI agent trained specifically on their company profile, working hours, and product/service menu. The bot can handle customer inquiries, recommend services, and book appointments automatically, syncing all data to an intuitive admin dashboard.

---

## 🛠️ System Architecture

The application is built on a split-service architecture separating the web interface and business logic (Flask) from the real-time WhatsApp engine (Node.js).

```mermaid
graph TD
    classDef main fill:#1f4287,stroke:#071e3d,stroke-width:2px,color:#fff;
    classDef service fill:#21bf73,stroke:#107a4b,stroke-width:2px,color:#fff;
    classDef db fill:#00d2c4,stroke:#009e94,stroke-width:2px,color:#000;
    classDef ext fill:#ff9f43,stroke:#c46914,stroke-width:2px,color:#fff;

    Customer([End User / Customer]) -->|WhatsApp Msg| WA[whatsapp-web.js Service Node.js]::service
    WA -->|Webhook Payload| Flask[Flask SaaS Backend Python]::main
    Flask -->|CRUD Operations| DB[(PostgreSQL Database)]::db
    Flask -->|Retrieves context & prompts| Groq[Groq LLM API]::ext
    Groq -->|Generated AI Reply| Flask
    Flask -->|Send Message Action| WA
    WA -->|Delivers reply| Customer

    BizOwner[Business Owner] -->|Manage settings & view stats| AdminPortal[Flask Admin Dashboard]::main
    AdminPortal -->|Read/Write config| DB
```

---

## ✨ Key Features

- 🤖 **Context-Aware AI Replies:** Integrates with Groq LLM API to deliver instant, natural responses based on business information, service lists, and operating hours.
- 📅 **Automated Appointment Booking:** Parses customer intent to book time slots directly through the WhatsApp conversation and schedules them in the database.
- ⚡ **Instant QR Connection:** Employs a dedicated Node.js helper to spin up dynamic WhatsApp Web sessions, displaying a QR code on the admin portal for instant login without needing official Meta API approval.
- 💳 **Tiered SaaS Subscriptions:** Implements Free, Starter, Pro, and Business tiers. Supports daily usage limits, usage bars, and quota tracking.
- 📊 **Comprehensive Admin Portal:** A full-featured control panel to monitor registered businesses, review message counts, approve subscriptions, and inspect real-time transaction activity.
- 🔒 **Secure Multi-Tenancy:** Robust database-level and session-level isolation ensures company settings, customer logs, and credentials remain private and secure.

---

## 🛠️ Tech Stack

- **Backend Logic & Web:** Python (Flask 3.1), SQLAlchemy 2.0 (ORM), Flask-Login
- **Database:** PostgreSQL (Production), SQLite (Local Testing)
- **AI Core:** Groq LLM API, Custom Prompt Engineering Templates
- **Bot Engine:** Node.js, `whatsapp-web.js` (using Puppeteer to control WhatsApp Web instances)
- **Frontend Panel:** Jinja2 Templates, HTML5, CSS3, Vanilla JavaScript (optimized with responsive glassmorphism styles)
- **Deployment & Hosting:** Railway (PaaS) with persistent database connections

---

## 📂 Project Structure

```bash
botify/
├── flask-app/            # Main SaaS web panel & API backend
│   ├── app.py            # Flask server initialization & routing
│   ├── models.py         # SQLAlchemy database schemas (User, Business, MessageLog, etc.)
│   ├── templates/        # Jinja2 HTML templates (Dashboard, login, register, QR scanner)
│   ├── static/           # Styling (CSS), scripts (JS), and images
│   └── requirements.txt  # Python package dependencies
├── baileys-service/      # Node.js service managing WhatsApp connections
│   ├── server.js         # Express server handling socket/API triggers
│   ├── package.json      # Node.js dependencies (whatsapp-web.js, express, socket.io)
│   └── README.md         # Service-specific guidelines
├── .gitignore            # Version control exclusion rules
├── LICENSE               # Project license (MIT)
└── README.md             # This comprehensive repository guide
```

---

## 🚀 Installation & Setup

### Prerequisites
- Python 3.10+
- Node.js 18+
- PostgreSQL (or use SQLite local fallback)

### 1. Set Up the Backend (Flask App)
```bash
# Navigate to flask-app
cd flask-app

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run migrations / Initialize SQLite database
python -c "from models import db, app; db.create_all(app=app)"

# Launch Flask development server
python app.py
```

### 2. Set Up the Bot Engine (Node.js Service)
```bash
# Navigate to the whatsapp service directory
cd ../baileys-service

# Install dependencies
npm install

# Start the service
npm start
```

---

## ⚙️ Environment Variables

Create a `.env` file inside the `flask-app/` directory:

```env
SECRET_KEY=your_flask_secret_key
DATABASE_URL=postgresql://username:password@localhost:5432/botify_db
GROQ_API_KEY=your_groq_api_key
WHATSAPP_SERVICE_URL=http://localhost:3000
```

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.
