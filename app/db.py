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


def init_db():
    db = get_db()
    with current_app.open_resource('schema.sql') as f:
        db.executescript(f.read().decode('utf8'))
    _seed_db(db)


def _seed_db(db):
    # Seed the two checklist template types (structural — required for the app to function)
    db.execute(
        "INSERT OR IGNORE INTO checklist_templates (name, description) VALUES (?, ?)",
        ('Food Safety', 'Daily food safety compliance checklist')
    )
    db.execute(
        "INSERT OR IGNORE INTO checklist_templates (name, description) VALUES (?, ?)",
        ('Cleaning', 'Daily cleaning and sanitation checklist')
    )

    db.commit()


@click.command('init-db')
def init_db_command():
    init_db()
    click.echo('Database initialized.')


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
