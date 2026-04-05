# QC Monitor — Quality Control Daily Checklist System

A web-based Quality Control monitoring system built with **Python (Flask)** and **SQLite**. It enables Branch Managers to submit daily checklists, QC Admins to score and comment, and Management to view KPI dashboards across all branches.

---

## Features

- **Role-based access** — Branch Manager, QC Admin, and Management (view-only)
- **Registration & Login** — Separate registration flows for Branch Managers and QC Admins
- **Daily Checklists** — Food Safety and Cleaning checklists with Yes/No answers; mandatory reason required when answering No
- **QC Scoring** — QC Admins score Food Safety submissions per branch (max 100)
- **Comment / Communication Thread** — Inline comments per checklist entry (similar to IT ticketing)
- **Dashboard** — KPI cards showing branch scores and compliance rates
- **Reports Page** — View historical submissions and scores per branch

---

## Tech Stack

| Layer      | Technology            |
|------------|-----------------------|
| Backend    | Python 3.11+ / Flask  |
| Database   | SQLite 3              |
| Frontend   | HTML / CSS / JS (Jinja2 templates) |
| Auth       | Flask-Login + Werkzeug password hashing |

---

## User Roles

| Role            | Permissions                                                   |
|-----------------|---------------------------------------------------------------|
| Branch Manager  | Submit daily checklists, view own branch data, comment        |
| QC Admin        | View all branches, score Food Safety, comment, manage checklist templates |
| Management      | View-only access to dashboards and reports (all branches)     |

---

## Project Structure

```
QC-Monitor/
├── app/
│   ├── __init__.py          # Flask app factory
│   ├── models.py            # SQLAlchemy / raw SQLite models
│   ├── auth/
│   │   ├── routes.py        # Login, register, logout
│   │   └── templates/       # Login & registration pages
│   ├── checklist/
│   │   ├── routes.py        # Checklist CRUD & submission
│   │   └── templates/       # Checklist form, history
│   ├── dashboard/
│   │   ├── routes.py        # KPI dashboard & reports
│   │   └── templates/       # Dashboard & report pages
│   └── static/              # CSS, JS, assets
├── database/
│   └── qc_monitor.db        # SQLite database file
├── docs/
│   ├── PRD.md
│   ├── TRD.md
│   └── DRD.md
├── requirements.txt
├── run.py                   # App entry point
└── README.md
```

---

## Getting Started

### Prerequisites
- Python 3.11+
- pip

### Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd QC-Monitor

# 2. Create a virtual environment
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate  # Mac/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Initialize the database
python run.py --init-db

# 5. Run the app
python run.py
```

App will be available at `http://localhost:5000`

---

## Default QC Admin Accounts

On first run, three default QC Admin accounts are seeded:

| Name       | Username    | Role     |
|------------|-------------|----------|
| Naila      | naila       | QC Admin |
| Al Qassim  | alqassim    | QC Admin |
| Mehraz     | mehraz      | QC Admin |

Management view-only accounts:

| Name   | Username | Role       |
|--------|----------|------------|
| Omran  | omran    | Management |
| Asaad  | asaad    | Management |
| Ali    | ali      | Management |

---

## Meeting

**Draft review:** April 15, 2026

---

## License

Internal use only.
