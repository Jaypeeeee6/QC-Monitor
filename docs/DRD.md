# Data Requirements Document (DRD)
## QC Monitor — Quality Control Daily Checklist System

**Version:** 1.0  
**Date:** April 1, 2026  
**Status:** Draft  

---

## 1. Database Overview

- **Engine:** SQLite 3
- **File location:** `database/qc_monitor.db`
- **Access pattern:** Single-writer, multiple-reader (suitable for team-size usage)
- **Schema file:** `app/schema.sql`

---

## 2. Entity Relationship Summary

```
branches ──< users (Branch Managers belong to a branch)
users ──< checklist_submissions
checklist_templates ──< checklist_items
checklist_submissions ──< checklist_responses (one per item)
checklist_submissions ──< scores (one per submission, by QC Admin)
checklist_submissions ──< comments (many per submission)
```

---

## 3. Table Definitions

### 3.1 `branches`
Stores each restaurant/store branch.

| Column       | Type         | Constraints          | Description                     |
|--------------|--------------|----------------------|---------------------------------|
| `id`         | INTEGER      | PK, AUTOINCREMENT    | Unique branch ID                |
| `name`       | TEXT         | NOT NULL, UNIQUE     | Branch name (e.g. "Branch 1")  |
| `location`   | TEXT         |                      | Physical address or area        |
| `created_at` | DATETIME     | DEFAULT CURRENT_TIMESTAMP | Row creation time          |

---

### 3.2 `users`
Stores all user accounts across all roles.

| Column        | Type     | Constraints                        | Description                                      |
|---------------|----------|------------------------------------|--------------------------------------------------|
| `id`          | INTEGER  | PK, AUTOINCREMENT                  | Unique user ID                                   |
| `full_name`   | TEXT     | NOT NULL                           | Display name                                     |
| `email`       | TEXT     | NOT NULL, UNIQUE                   | Login email                                      |
| `username`    | TEXT     | NOT NULL, UNIQUE                   | Login username                                   |
| `password_hash` | TEXT   | NOT NULL                           | Werkzeug PBKDF2 hash                            |
| `role`        | TEXT     | NOT NULL                           | `branch_manager`, `qc_admin`, `management`       |
| `branch_id`   | INTEGER  | FK → branches.id, NULLABLE         | Set for branch_manager; NULL for QC/Management   |
| `is_active`   | INTEGER  | DEFAULT 1                          | 1 = active, 0 = disabled                         |
| `created_at`  | DATETIME | DEFAULT CURRENT_TIMESTAMP          | Registration timestamp                           |

**Notes:**
- `role` is enforced at application level; CHECK constraint added for safety
- `branch_id` is NULL for `qc_admin` and `management` roles

---

### 3.3 `checklist_templates`
Defines the two checklist types (Food Safety, Cleaning).

| Column        | Type     | Constraints               | Description                              |
|---------------|----------|---------------------------|------------------------------------------|
| `id`          | INTEGER  | PK, AUTOINCREMENT         | Template ID                              |
| `name`        | TEXT     | NOT NULL, UNIQUE          | e.g. `"Food Safety"`, `"Cleaning"`       |
| `description` | TEXT     |                           | Optional description                     |
| `is_active`   | INTEGER  | DEFAULT 1                 | 1 = active, 0 = archived                 |
| `created_at`  | DATETIME | DEFAULT CURRENT_TIMESTAMP | Creation timestamp                       |

**Seeded data:**
```sql
INSERT INTO checklist_templates (name) VALUES ('Food Safety');
INSERT INTO checklist_templates (name) VALUES ('Cleaning');
```

---

### 3.4 `checklist_items`
Individual Yes/No questions within a checklist template.

| Column          | Type     | Constraints               | Description                                        |
|-----------------|----------|---------------------------|----------------------------------------------------|
| `id`            | INTEGER  | PK, AUTOINCREMENT         | Item ID                                            |
| `template_id`   | INTEGER  | FK → checklist_templates.id | Which checklist this item belongs to             |
| `item_text`     | TEXT     | NOT NULL                  | The checklist question/statement                   |
| `display_order` | INTEGER  | DEFAULT 0                 | Order to display items in the form                 |
| `is_active`     | INTEGER  | DEFAULT 1                 | 1 = shown, 0 = hidden/archived                     |
| `created_at`    | DATETIME | DEFAULT CURRENT_TIMESTAMP | Creation timestamp                                 |

---

### 3.5 `checklist_submissions`
A single daily submission of a checklist by a branch.

| Column          | Type     | Constraints                        | Description                                      |
|-----------------|----------|------------------------------------|--------------------------------------------------|
| `id`            | INTEGER  | PK, AUTOINCREMENT                  | Submission ID                                    |
| `branch_id`     | INTEGER  | FK → branches.id, NOT NULL         | Which branch submitted                           |
| `submitted_by`  | INTEGER  | FK → users.id, NOT NULL            | Branch Manager user who submitted                |
| `template_id`   | INTEGER  | FK → checklist_templates.id        | Which checklist type (Food Safety / Cleaning)    |
| `submission_date` | DATE   | NOT NULL                           | The calendar date of the submission              |
| `submitted_at`  | DATETIME | DEFAULT CURRENT_TIMESTAMP          | Exact time of submission                         |
| `is_locked`     | INTEGER  | DEFAULT 1                          | 1 = locked after submit (Branch Mgr cannot edit) |

**Unique constraint:** `(branch_id, template_id, submission_date)` — one submission per branch per type per day.

---

### 3.6 `checklist_responses`
Individual Yes/No answers for each item in a submission.

| Column          | Type     | Constraints                        | Description                                      |
|-----------------|----------|------------------------------------|--------------------------------------------------|
| `id`            | INTEGER  | PK, AUTOINCREMENT                  | Response ID                                      |
| `submission_id` | INTEGER  | FK → checklist_submissions.id      | Parent submission                                |
| `item_id`       | INTEGER  | FK → checklist_items.id            | Which checklist item this answers                |
| `answer`        | TEXT     | NOT NULL                           | `'yes'` or `'no'`                                |
| `reason`        | TEXT     | NULLABLE                           | Mandatory when `answer = 'no'`; NULL when `'yes'`|

**Application rule:** If `answer = 'no'` and `reason` is empty → validation error, form not saved.

---

### 3.7 `scores`
QC Admin's score for a Food Safety submission.

| Column          | Type     | Constraints                        | Description                                      |
|-----------------|----------|------------------------------------|--------------------------------------------------|
| `id`            | INTEGER  | PK, AUTOINCREMENT                  | Score record ID                                  |
| `submission_id` | INTEGER  | FK → checklist_submissions.id, UNIQUE | One score per submission                      |
| `scored_by`     | INTEGER  | FK → users.id                      | QC Admin who scored                              |
| `score`         | INTEGER  | NOT NULL, CHECK(score BETWEEN 0 AND 100) | Score value (0–100)                      |
| `notes`         | TEXT     |                                    | Optional scoring note from QC Admin              |
| `scored_at`     | DATETIME | DEFAULT CURRENT_TIMESTAMP          | When the score was recorded                      |
| `updated_at`    | DATETIME |                                    | Last update time (if score is revised)           |

---

### 3.8 `comments`
Communication thread on a submission (Branch Manager ↔ QC Admin).

| Column          | Type     | Constraints                        | Description                                      |
|-----------------|----------|------------------------------------|--------------------------------------------------|
| `id`            | INTEGER  | PK, AUTOINCREMENT                  | Comment ID                                       |
| `submission_id` | INTEGER  | FK → checklist_submissions.id      | Which submission this comment belongs to         |
| `user_id`       | INTEGER  | FK → users.id                      | Author of the comment                            |
| `message`       | TEXT     | NOT NULL                           | Comment body text                                |
| `created_at`    | DATETIME | DEFAULT CURRENT_TIMESTAMP          | Timestamp of comment                             |

---

## 4. Seeded Data

On `--init-db`, the following records are inserted:

### Branches (placeholder — to be confirmed with Naila)
```sql
INSERT INTO branches (name) VALUES ('Branch 1');
INSERT INTO branches (name) VALUES ('Branch 2');
INSERT INTO branches (name) VALUES ('Branch 3');
```

### QC Admin Accounts
```sql
INSERT INTO users (full_name, username, email, password_hash, role)
VALUES ('Naila', 'naila', 'naila@qc.internal', '<hashed>', 'qc_admin');

INSERT INTO users (full_name, username, email, password_hash, role)
VALUES ('Al Qassim', 'alqassim', 'alqassim@qc.internal', '<hashed>', 'qc_admin');

INSERT INTO users (full_name, username, email, password_hash, role)
VALUES ('Mehraz', 'mehraz', 'mehraz@qc.internal', '<hashed>', 'qc_admin');
```

### Management (View-Only) Accounts
```sql
INSERT INTO users (full_name, username, email, password_hash, role)
VALUES ('Omran', 'omran', 'omran@qc.internal', '<hashed>', 'management');

INSERT INTO users (full_name, username, email, password_hash, role)
VALUES ('Asaad', 'asaad', 'asaad@qc.internal', '<hashed>', 'management');

INSERT INTO users (full_name, username, email, password_hash, role)
VALUES ('Ali', 'ali', 'ali@qc.internal', '<hashed>', 'management');
```

### Checklist Templates
```sql
INSERT INTO checklist_templates (name) VALUES ('Food Safety');
INSERT INTO checklist_templates (name) VALUES ('Cleaning');
```

---

## 5. Key Queries

### Get today's submissions for all branches (QC Admin dashboard)
```sql
SELECT b.name AS branch, ct.name AS checklist_type, cs.submitted_at, u.full_name AS submitted_by,
       s.score
FROM checklist_submissions cs
JOIN branches b ON b.id = cs.branch_id
JOIN checklist_templates ct ON ct.id = cs.template_id
JOIN users u ON u.id = cs.submitted_by
LEFT JOIN scores s ON s.submission_id = cs.id
WHERE cs.submission_date = DATE('now')
ORDER BY b.name, ct.name;
```

### Get compliance rate for a submission
```sql
SELECT
  COUNT(*) AS total,
  SUM(CASE WHEN answer = 'yes' THEN 1 ELSE 0 END) AS yes_count,
  ROUND(SUM(CASE WHEN answer = 'yes' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS compliance_pct
FROM checklist_responses
WHERE submission_id = ?;
```

### Get comments for a submission (newest last)
```sql
SELECT c.message, c.created_at, u.full_name, u.role
FROM comments c
JOIN users u ON u.id = c.user_id
WHERE c.submission_id = ?
ORDER BY c.created_at ASC;
```

---

## 6. Data Retention

- All submissions and scores are retained indefinitely in v1
- No automated archiving or deletion
- SQLite DB can be backed up by copying the `.db` file

---

## 7. Data Validation Rules

| Field                   | Rule                                                      |
|-------------------------|-----------------------------------------------------------|
| `answer`                | Must be `'yes'` or `'no'` only                            |
| `reason` (when No)      | Required, minimum 5 characters                            |
| `score`                 | Integer between 0 and 100 inclusive                       |
| `submission_date`       | Cannot be a future date                                   |
| One submission per day  | Unique constraint on `(branch_id, template_id, submission_date)` |
| `role`                  | Must be one of: `branch_manager`, `qc_admin`, `management` |
| `username` / `email`    | Unique across all users                                   |
| `password`              | Minimum 8 characters (enforced at form level)             |
