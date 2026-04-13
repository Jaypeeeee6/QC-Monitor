import os
import sqlite3
import click
from flask import current_app, g


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def _table_has_column(db, table, col):
    rows = db.execute(f'PRAGMA table_info({table})').fetchall()
    return any(r['name'] == col for r in rows)


def migrate_db(conn):
    """Apply additive schema upgrades for existing SQLite databases."""
    if not _table_has_column(conn, 'checklist_templates', 'parent_template_id'):
        conn.execute(
            'ALTER TABLE checklist_templates ADD COLUMN parent_template_id INTEGER REFERENCES checklist_templates(id)'
        )
    if not _table_has_column(conn, 'checklist_templates', 'root_template_id'):
        conn.execute(
            'ALTER TABLE checklist_templates ADD COLUMN root_template_id INTEGER REFERENCES checklist_templates(id)'
        )
    if not _table_has_column(conn, 'checklist_templates', 'template_scope'):
        conn.execute("ALTER TABLE checklist_templates ADD COLUMN template_scope TEXT DEFAULT 'legacy'")
    if not _table_has_column(conn, 'checklist_templates', 'brand_id'):
        conn.execute('ALTER TABLE checklist_templates ADD COLUMN brand_id INTEGER REFERENCES brands(id)')
    if not _table_has_column(conn, 'checklist_templates', 'branch_id'):
        conn.execute('ALTER TABLE checklist_templates ADD COLUMN branch_id INTEGER REFERENCES branches(id)')
    if not _table_has_column(conn, 'checklist_templates', 'template_status'):
        conn.execute("ALTER TABLE checklist_templates ADD COLUMN template_status TEXT DEFAULT 'active'")
    if not _table_has_column(conn, 'checklist_templates', 'version'):
        conn.execute('ALTER TABLE checklist_templates ADD COLUMN version INTEGER DEFAULT 1')

    if not _table_has_column(conn, 'checklist_sections', 'overrides_section_id'):
        conn.execute(
            'ALTER TABLE checklist_sections ADD COLUMN overrides_section_id INTEGER REFERENCES checklist_sections(id)'
        )

    if not _table_has_column(conn, 'checklist_items', 'overrides_item_id'):
        conn.execute(
            'ALTER TABLE checklist_items ADD COLUMN overrides_item_id INTEGER REFERENCES checklist_items(id)'
        )

    if not _table_has_column(conn, 'checklist_submissions', 'template_name_snapshot'):
        conn.execute('ALTER TABLE checklist_submissions ADD COLUMN template_name_snapshot TEXT')
        conn.execute(
            '''UPDATE checklist_submissions
               SET template_name_snapshot = (
                   SELECT ct.name FROM checklist_templates ct WHERE ct.id = checklist_submissions.template_id
               )
               WHERE template_name_snapshot IS NULL'''
        )

    conn.executescript(
        '''
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
        '''
    )

    conn.execute(
        'UPDATE checklist_templates SET root_template_id = id WHERE root_template_id IS NULL'
    )
    conn.execute(
        "UPDATE checklist_templates SET template_scope = 'global' WHERE template_scope IS NULL OR template_scope = ''"
    )
    conn.execute(
        "UPDATE checklist_templates SET template_status = 'active' WHERE template_status IS NULL OR template_status = ''"
    )

    lib_cnt = conn.execute('SELECT COUNT(*) FROM checklist_section_library').fetchone()[0]
    if lib_cnt == 0:
        cur = conn.execute(
            '''INSERT INTO checklist_section_library (name, description)
               VALUES (?, ?)''',
            ('Temperature monitoring', 'Reusable library section — add to any template from the checklist editor.'),
        )
        lid = cur.lastrowid
        samples = [
            ('Walk-in fridge reading 0–4°C', 0),
            ('Hot holding unit above 63°C', 1),
            ('Probe thermometer calibrated / available', 2),
        ]
        for text, order in samples:
            conn.execute(
                '''INSERT INTO checklist_section_library_items
                   (library_section_id, item_text, display_order) VALUES (?, ?, ?)''',
                (lid, text, order),
            )


def _run_migrations_on_disk(database_path):
    os.makedirs(os.path.dirname(database_path), exist_ok=True)
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    migrate_db(conn)
    conn.commit()
    conn.close()


def init_db():
    db = get_db()
    with current_app.open_resource('schema.sql') as f:
        db.executescript(f.read().decode('utf8'))
    _seed_db(db)


def _seed_db(db):
    db.execute(
        '''INSERT OR IGNORE INTO checklist_templates
           (name, description, template_scope, template_status, version)
           VALUES (?, ?, 'global', 'active', 1)''',
        ('Food Safety', 'Daily food safety compliance checklist'),
    )
    db.execute(
        '''INSERT OR IGNORE INTO checklist_templates
           (name, description, template_scope, template_status, version)
           VALUES (?, ?, 'global', 'active', 1)''',
        ('Cleaning', 'Daily cleaning and sanitation checklist'),
    )
    db.execute(
        'UPDATE checklist_templates SET root_template_id = id WHERE root_template_id IS NULL'
    )
    db.commit()


@click.command('init-db')
def init_db_command():
    init_db()
    click.echo('Database initialized.')


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    _run_migrations_on_disk(app.config['DATABASE'])
