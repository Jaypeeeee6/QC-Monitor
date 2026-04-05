# Product Requirements Document (PRD)
## QC Monitor — Quality Control Daily Checklist System

**Version:** 1.0  
**Date:** April 1, 2026  
**Status:** Draft  
**Review Meeting:** April 15, 2026  

---

## 1. Overview

### 1.1 Purpose
QC Monitor replaces the current email-based daily reporting workflow between Branch Managers and the QC Department. It provides a centralized, structured platform for daily Food Safety and Cleaning checklists, scoring, and branch-to-QC communication.

### 1.2 Problem Statement
Branch Managers currently send daily reports via email. This creates:
- No standardized format across branches
- No centralized scoring or KPI tracking
- Difficult communication and follow-up threads
- No historical data visibility for management

### 1.3 Goals
- Digitize daily Food Safety and Cleaning checklists
- Enable QC Admins to score each branch's Food Safety performance
- Provide inline comment threads per checklist for Branch Manager ↔ QC communication
- Give Management a read-only KPI dashboard to compare branches
- Have a working draft ready for review by April 15, 2026

---

## 2. User Roles & Permissions

### 2.1 Branch Manager
- Self-register with branch assignment
- Log in and submit daily Food Safety and Cleaning checklists
- Answer each checklist item with Yes or No; **if No, a reason is mandatory**
- View their own branch's checklist history and scores
- Add and reply to comments on their checklist submissions

### 2.2 QC Admin (also acts as Admin)
Seeded accounts: **Naila**, **Al Qassim**, **Mehraz**
- Log in (no self-registration — accounts seeded by system)
- View all branches' daily checklists
- Score Food Safety submissions per branch (0–100 points)
- Add and reply to comments on any checklist submission
- Manage checklist templates (add/edit checklist items)
- View dashboards and reports for all branches

### 2.3 Management (View Only)
Seeded accounts: **Omran**, **Asaad**, **Ali**
- Log in (no self-registration — accounts seeded by system)
- View-only access to the KPI dashboard and reports for all branches
- Cannot submit checklists, score, or comment

---

## 3. Pages & Features

### 3.1 Registration Page (Branch Manager)
- Fields: Full Name, Email, Username, Password, Confirm Password, Branch (dropdown)
- Validation: unique username/email, password strength, branch must be selected
- On success: account pending or auto-approved (TBD with Naila)

### 3.2 Registration Page (QC Admin)
- Fields: Full Name, Email, Username, Password, Confirm Password, Admin Key (secret code to prevent unauthorized admin registration)
- On success: QC Admin account created

### 3.3 Login Page
- Shared login page for all roles
- Redirects to role-appropriate homepage after login

### 3.4 Daily Checklist — Food Safety
- Branch Manager sees a list of checklist items (configured by QC Admin)
- Each item: Yes / No toggle
  - If **No**: a text field appears requiring a reason (mandatory)
- Submit button locks that day's checklist after submission (one submission per day per branch)
- QC Admin can view submitted checklists and add a score (0–100)
- Both Branch Manager and QC Admin can add comments on the submission

### 3.5 Daily Checklist — Cleaning
- Same structure as Food Safety checklist
- Separate checklist items specific to Cleaning
- No QC scoring on Cleaning (scoring is Food Safety only, unless changed)
- Comment section available

### 3.6 Comment / Communication Thread
- Each checklist submission has a comment thread
- Both Branch Manager (own branch) and QC Admin can post comments
- Comments show: author name, role, timestamp, message
- Threaded or flat list (flat list for simplicity in v1)
- Similar to an IT ticketing system comment trail

### 3.7 QC Dashboard
- Accessible to: QC Admins and Management
- Shows KPI cards per branch:
  - Latest Food Safety Score (out of 100)
  - Food Safety compliance rate (% of Yes answers)
  - Cleaning compliance rate (% of Yes answers)
  - Submission streak (how many consecutive days submitted)
- Branch comparison table/chart (sortable by score)
- Date filter (view by day, week, month)

### 3.8 Reports Page
- Accessible to: QC Admins and Management
- List of all submissions filterable by branch, date range, checklist type
- Click a submission to view full checklist details, score, and comments
- Export to CSV (v1 nice-to-have)

---

## 4. Non-Functional Requirements

| Requirement       | Detail                                              |
|-------------------|-----------------------------------------------------|
| Performance       | Page load < 2 seconds for up to 20 concurrent users |
| Security          | Passwords hashed (bcrypt/werkzeug), session-based auth |
| Availability      | Local or intranet hosted; no cloud required in v1   |
| Browser Support   | Chrome, Edge (latest versions)                       |
| Language          | English (primary), Arabic labels optional in v2     |

---

## 5. Out of Scope (v1)

- Mobile app
- Email/SMS notifications
- PDF export of reports
- Audit log
- Multi-language support
- Branch Manager account approval workflow (auto-approved in v1)

---

## 6. Success Metrics

- 100% of branches submitting daily checklists digitally within 2 weeks of launch
- QC Admins scoring all Food Safety submissions within 24 hours of submission
- Management able to compare branch KPIs without requesting reports manually

---

## 7. Stakeholders

| Name       | Role               | Involvement           |
|------------|--------------------|-----------------------|
| Naila      | QC Admin / Owner   | Requirements, UAT     |
| Al Qassim  | QC Admin           | UAT                   |
| Mehraz     | QC Admin           | UAT                   |
| Omran      | Management         | Dashboard review      |
| Asaad      | Management         | Dashboard review      |
| Ali        | Management         | Dashboard review      |
| JP         | Developer          | Build & delivery      |
