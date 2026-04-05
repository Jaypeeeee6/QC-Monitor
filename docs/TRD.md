# Technical Requirements Document (TRD)
## QC Monitor — Quality Control Daily Checklist System

**Version:** 1.0  
**Date:** April 1, 2026  
**Status:** Draft  

---

## 1. Technology Stack

| Component        | Technology                        | Justification                                      |
|------------------|-----------------------------------|----------------------------------------------------|
| Language         | Python 3.11+                      | Specified by client                                |
| Web Framework    | Flask 3.x                         | Lightweight, Python-native, easy SQLite integration|
| Database         | SQLite 3                          | Specified by client; sufficient for team-size load |
| ORM / DB Access  | Raw sqlite3 (stdlib)              | No extra dependencies; full control over queries   |
| Templating       | Jinja2 (bundled with Flask)       | Server-side rendering, simple role-based views     |
| Auth             | Flask-Login + Werkzeug            | Session management and password hashing            |
| Frontend         | HTML5 / CSS3 / Vanilla JS         | No heavy JS framework needed for v1                |
| CSS Framework    | Bootstrap 5                       | Rapid, responsive UI development                   |
| Forms            | Flask-WTF / WTForms               | CSRF protection, validation                        |

---

## 2. Architecture Overview

```
Browser (Chrome/Edge)
        │
        ▼
  Flask App (run.py)
        │
   ┌────┴────────────────┐
   │   Blueprints        │
   │  ┌──────────────┐   │
   │  │ auth/        │   │  ← Login, Register, Logout
   │  │ checklist/   │   │  ← Submit, View, Score, Comment
   │  │ dashboard/   │   │  ← KPI Cards, Reports
   │  └──────────────┘   │
   └────────┬────────────┘
            │
     sqlite3 (stdlib)
            │
     qc_monitor.db (SQLite file)
```

### 2.1 Blueprint Structure

| Blueprint   | Prefix         | Responsibility                             |
|-------------|----------------|--------------------------------------------|
| `auth`      | `/auth`        | Registration, login, logout                |
| `checklist` | `/checklist`   | Daily checklist submit, view, score, comment |
| `dashboard` | `/dashboard`   | KPI dashboard, branch comparisons, reports  |

---

## 3. Authentication & Authorization

### 3.1 Authentication
- Session-based authentication via `Flask-Login`
- Passwords hashed using `werkzeug.security.generate_password_hash` (PBKDF2-SHA256)
- Login creates a secure server-side session
- Logout clears the session

### 3.2 Authorization — Role Guards

```python
# Decorators used to protect routes
@login_required          # Must be logged in
@role_required('qc_admin')      # QC Admin only
@role_required('management', 'qc_admin')  # Management or QC Admin
```

| Route                        | Allowed Roles                         |
|------------------------------|---------------------------------------|
| `/auth/register/branch`      | Public                                |
| `/auth/register/qc`          | Public (requires admin key)           |
| `/auth/login`                | Public                                |
| `/checklist/submit`          | Branch Manager                        |
| `/checklist/view/<id>`       | Branch Manager (own), QC Admin        |
| `/checklist/score/<id>`      | QC Admin                              |
| `/checklist/comment/<id>`    | Branch Manager (own), QC Admin        |
| `/dashboard/`                | QC Admin, Management                  |
| `/dashboard/reports`         | QC Admin, Management                  |

### 3.3 Admin Key
QC Admin registration requires a secret `ADMIN_KEY` set in the app config (environment variable). This prevents unauthorized admin account creation.

---

## 4. Application Configuration

```python
# config.py
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-in-prod')
DATABASE = os.path.join(basedir, 'database', 'qc_monitor.db')
ADMIN_KEY = os.environ.get('ADMIN_KEY', 'qc-admin-2026')
```

---

## 5. Route Specifications

### 5.1 Auth Blueprint (`/auth`)

| Method | Route               | Description                        |
|--------|---------------------|------------------------------------|
| GET    | `/login`            | Render login form                  |
| POST   | `/login`            | Authenticate user, set session     |
| GET    | `/logout`           | Clear session, redirect to login   |
| GET    | `/register/branch`  | Render Branch Manager register form|
| POST   | `/register/branch`  | Create Branch Manager account      |
| GET    | `/register/qc`      | Render QC Admin register form      |
| POST   | `/register/qc`      | Validate admin key, create account |

### 5.2 Checklist Blueprint (`/checklist`)

| Method | Route                     | Description                                      |
|--------|---------------------------|--------------------------------------------------|
| GET    | `/`                       | List today's checklists for current user's branch|
| GET    | `/submit/<type>`          | Render checklist form (type: food_safety/cleaning)|
| POST   | `/submit/<type>`          | Save checklist submission                        |
| GET    | `/view/<submission_id>`   | View submitted checklist details + score + comments|
| POST   | `/score/<submission_id>`  | QC Admin submits/updates score (0–100)           |
| POST   | `/comment/<submission_id>`| Add a comment to a submission                    |
| GET    | `/history`                | Branch Manager views own submission history      |
| GET    | `/all`                    | QC Admin views all branches' submissions         |

### 5.3 Dashboard Blueprint (`/dashboard`)

| Method | Route          | Description                                |
|--------|----------------|--------------------------------------------|
| GET    | `/`            | KPI dashboard — all branches               |
| GET    | `/branch/<id>` | KPI detail for a single branch             |
| GET    | `/reports`     | Reports page with filters                  |
| GET    | `/reports/export` | CSV export of filtered report (v1 nice-to-have) |

---

## 6. Checklist Scoring Logic

- Food Safety score is entered by QC Admin (integer 0–100)
- Compliance rate = `(count of Yes answers / total items) * 100`
- Dashboard KPI = latest score for the branch (per day)
- If no score has been entered yet, display "Pending"

---

## 7. Daily Submission Rules

- Each branch can submit each checklist type **once per calendar day**
- Submission is locked after saving (Branch Manager cannot edit after submit)
- QC Admin can add/edit score at any time after submission
- QC Admin can add comments at any time; Branch Manager can add comments only on their own branch's submissions

---

## 8. Security Considerations

| Concern               | Mitigation                                           |
|-----------------------|------------------------------------------------------|
| SQL Injection         | Parameterized queries throughout (no string concat)  |
| XSS                   | Jinja2 auto-escaping enabled                         |
| CSRF                  | Flask-WTF CSRF tokens on all forms                   |
| Unauthorized access   | `@login_required` + role decorators on all routes    |
| Password storage      | Werkzeug PBKDF2-SHA256 hashing                       |
| Admin registration    | Admin key required, not exposed in UI                |

---

## 9. Development Environment

```
Python 3.11+
Flask 3.x
Flask-Login
Flask-WTF
Werkzeug
Bootstrap 5 (CDN)
SQLite 3 (stdlib — no install needed)
```

### requirements.txt
```
flask>=3.0.0
flask-login>=0.6.3
flask-wtf>=1.2.1
werkzeug>=3.0.0
```

---

## 10. Deployment

- Run locally on Windows using `python run.py`
- Accessible on local network via `http://<host-ip>:5000`
- SQLite DB file stored in `database/qc_monitor.db`
- No external server or cloud hosting required for v1
- For production-like deployment: Waitress (pure-Python WSGI server for Windows)

```
pip install waitress
waitress-serve --port=5000 run:app
```

---

## 11. File & Folder Conventions

- Templates: `app/<blueprint>/templates/<blueprint>/filename.html`
- Static files: `app/static/css/`, `app/static/js/`
- Database helpers: `app/db.py` — `get_db()`, `init_db()`, `close_db()`
- All SQL schema in `app/schema.sql`
