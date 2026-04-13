CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_active INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER REFERENCES brands(id),
    name TEXT NOT NULL UNIQUE,
    location TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('branch_manager', 'qc_admin', 'management', 'it_admin')),
    branch_id INTEGER REFERENCES branches(id),
    is_active INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS checklist_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    is_active INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    parent_template_id INTEGER REFERENCES checklist_templates(id),
    root_template_id INTEGER REFERENCES checklist_templates(id),
    template_scope TEXT DEFAULT 'global',
    brand_id INTEGER REFERENCES brands(id),
    branch_id INTEGER REFERENCES branches(id),
    template_status TEXT DEFAULT 'active',
    version INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS checklist_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL REFERENCES checklist_templates(id),
    branch_id INTEGER REFERENCES branches(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    display_order INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    overrides_section_id INTEGER REFERENCES checklist_sections(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS checklist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL REFERENCES checklist_templates(id),
    section_id INTEGER REFERENCES checklist_sections(id) ON DELETE SET NULL,
    item_text TEXT NOT NULL,
    display_order INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_by INTEGER REFERENCES users(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    overrides_item_id INTEGER REFERENCES checklist_items(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS checklist_item_branches (
    item_id INTEGER NOT NULL REFERENCES checklist_items(id) ON DELETE CASCADE,
    branch_id INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, branch_id)
);

CREATE TABLE IF NOT EXISTS checklist_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    submitted_by INTEGER NOT NULL REFERENCES users(id),
    template_id INTEGER NOT NULL REFERENCES checklist_templates(id),
    template_name_snapshot TEXT,
    submission_date DATE NOT NULL,
    submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_locked INTEGER DEFAULT 1,
    UNIQUE(branch_id, template_id, submission_date)
);

CREATE TABLE IF NOT EXISTS checklist_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES checklist_submissions(id),
    item_id INTEGER NOT NULL REFERENCES checklist_items(id),
    answer TEXT NOT NULL CHECK(answer IN ('yes', 'no')),
    reason TEXT
);

CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL UNIQUE REFERENCES checklist_submissions(id),
    scored_by INTEGER NOT NULL REFERENCES users(id),
    score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
    notes TEXT,
    scored_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES checklist_submissions(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    message TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS comment_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    original_name TEXT NOT NULL,
    uploaded_by INTEGER NOT NULL REFERENCES users(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS checklist_section_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS checklist_section_library_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    library_section_id INTEGER NOT NULL REFERENCES checklist_section_library(id) ON DELETE CASCADE,
    item_text TEXT NOT NULL,
    display_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    submitted_by INTEGER NOT NULL REFERENCES users(id),
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    report_date TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_read INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS report_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES daily_reports(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS report_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER REFERENCES daily_reports(id) ON DELETE CASCADE,
    reply_id INTEGER REFERENCES report_replies(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    original_name TEXT NOT NULL,
    uploaded_by INTEGER NOT NULL REFERENCES users(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
