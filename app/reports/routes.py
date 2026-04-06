import os
import uuid
from flask import (render_template, request, redirect, url_for,
                   flash, abort, current_app)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.reports import reports_bp
from app.db import get_db
from app.utils import role_required, local_today, local_now_str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _allowed_photo(filename):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ext in current_app.config['ALLOWED_PHOTO_EXTENSIONS']


def _save_photos(files):
    """Validate and save uploaded photo files. Returns list of (filename, original_name)."""
    saved = []
    upload_folder = current_app.config['UPLOAD_FOLDER']
    max_photos = current_app.config['MAX_PHOTOS_PER_UPLOAD']

    for f in files[:max_photos]:
        if not f or not f.filename:
            continue
        if not _allowed_photo(f.filename):
            flash(f'"{f.filename}" is not a supported image type (jpg, png, gif, webp).', 'warning')
            continue
        ext = f.filename.rsplit('.', 1)[-1].lower()
        unique_name = f'{uuid.uuid4().hex}.{ext}'
        f.save(os.path.join(upload_folder, unique_name))
        saved.append((unique_name, secure_filename(f.filename)))

    return saved


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@reports_bp.route('/')
@login_required
def inbox():
    db = get_db()

    if current_user.role == 'branch_manager':
        today = local_today()
        reports = db.execute(
            '''SELECT dr.*, b.name as branch_name, u.full_name as sender_name,
                      (SELECT COUNT(*) FROM report_replies rr WHERE rr.report_id = dr.id) as reply_count
               FROM daily_reports dr
               JOIN branches b ON b.id = dr.branch_id
               JOIN users u ON u.id = dr.submitted_by
               WHERE dr.submitted_by = ?
               ORDER BY dr.created_at DESC''',
            (current_user.id,)
        ).fetchall()
        today_report = db.execute(
            '''SELECT id FROM daily_reports
               WHERE submitted_by = ? AND report_date = ?''',
            (current_user.id, today)
        ).fetchone()
        return render_template('reports/sent.html', reports=reports,
                               today=today, today_report=today_report)

    elif current_user.role in ('qc_admin', 'it_admin', 'management'):
        branches = db.execute('SELECT id, name FROM branches ORDER BY name').fetchall()

        branch_filter = request.args.get('branch_id', '', type=str).strip()
        search = request.args.get('q', '', type=str).strip()

        query = '''SELECT dr.*, b.name as branch_name, u.full_name as sender_name,
                          (SELECT COUNT(*) FROM report_replies rr WHERE rr.report_id = dr.id) as reply_count
                   FROM daily_reports dr
                   JOIN branches b ON b.id = dr.branch_id
                   JOIN users u ON u.id = dr.submitted_by
                   WHERE 1=1'''
        params = []

        if branch_filter:
            query += ' AND dr.branch_id = ?'
            params.append(int(branch_filter))

        if search:
            query += ' AND (dr.subject LIKE ? OR dr.body LIKE ? OR u.full_name LIKE ?)'
            like = f'%{search}%'
            params.extend([like, like, like])

        query += ' ORDER BY dr.created_at DESC'
        reports = db.execute(query, params).fetchall()

        unread_count = db.execute(
            'SELECT COUNT(*) FROM daily_reports WHERE is_read = 0'
        ).fetchone()[0]

        return render_template('reports/inbox.html',
                               reports=reports,
                               unread_count=unread_count,
                               branches=branches,
                               branch_filter=branch_filter,
                               search=search)

    abort(403)


@reports_bp.route('/compose', methods=['GET', 'POST'])
@login_required
@role_required('branch_manager')
def compose():
    db = get_db()
    today = local_today()

    qc_admins = db.execute(
        '''SELECT full_name, email FROM users
           WHERE role = 'qc_admin' AND is_active = 1
           ORDER BY full_name'''
    ).fetchall()

    # Block if today's report already submitted
    existing_today = db.execute(
        '''SELECT id FROM daily_reports
           WHERE submitted_by = ? AND report_date = ?''',
        (current_user.id, today)
    ).fetchone()
    if existing_today:
        flash("You've already submitted today's daily report.", 'warning')
        return redirect(url_for('reports.inbox'))

    if request.method == 'POST':
        subject = request.form.get('subject', '').strip()
        body = request.form.get('body', '').strip()
        report_date = request.form.get('report_date', today).strip() or today
        photos = request.files.getlist('photos')

        errors = []
        # Re-check on POST in case of double-submit
        duplicate = db.execute(
            'SELECT id FROM daily_reports WHERE submitted_by = ? AND report_date = ?',
            (current_user.id, report_date)
        ).fetchone()
        if duplicate:
            errors.append(f'A report for {report_date} has already been submitted.')
        if not subject:
            errors.append('Subject is required.')
        if len(subject) > 200:
            errors.append('Subject must be 200 characters or less.')
        if not body:
            errors.append('Report body is required.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('reports/compose.html',
                                   today=today, subject=subject,
                                   body=body, report_date=report_date,
                                   qc_admins=qc_admins)

        cursor = db.execute(
            '''INSERT INTO daily_reports (branch_id, submitted_by, subject, body, report_date, created_at)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (current_user.branch_id, current_user.id, subject, body, report_date, local_now_str())
        )
        report_id = cursor.lastrowid

        saved = _save_photos(photos)
        for filename, original_name in saved:
            db.execute(
                '''INSERT INTO report_attachments (report_id, filename, original_name, uploaded_by)
                   VALUES (?, ?, ?, ?)''',
                (report_id, filename, original_name, current_user.id)
            )

        db.commit()
        flash('Daily report sent successfully!', 'success')
        return redirect(url_for('reports.inbox'))

    return render_template('reports/compose.html', today=today,
                           subject='', body='', report_date=today,
                           qc_admins=qc_admins)


@reports_bp.route('/<int:report_id>')
@login_required
def view(report_id):
    db = get_db()

    report = db.execute(
        '''SELECT dr.*, b.name as branch_name,
                  u.full_name as sender_name, u.email as sender_email, u.role as sender_role
           FROM daily_reports dr
           JOIN branches b ON b.id = dr.branch_id
           JOIN users u ON u.id = dr.submitted_by
           WHERE dr.id = ?''',
        (report_id,)
    ).fetchone()

    if not report:
        abort(404)

    if current_user.role == 'branch_manager' and report['submitted_by'] != current_user.id:
        abort(403)

    # Mark as read when QC/IT/Management opens it
    if current_user.role in ('qc_admin', 'it_admin', 'management') and not report['is_read']:
        db.execute('UPDATE daily_reports SET is_read = 1 WHERE id = ?', (report_id,))
        db.commit()

    qc_admins = db.execute(
        '''SELECT full_name, email FROM users
           WHERE role = 'qc_admin' AND is_active = 1
           ORDER BY full_name'''
    ).fetchall()

    # Fetch report attachments
    attachments = db.execute(
        '''SELECT ra.* FROM report_attachments ra
           WHERE ra.report_id = ? AND ra.reply_id IS NULL
           ORDER BY ra.created_at''',
        (report_id,)
    ).fetchall()

    # Fetch replies with their author info and attachments
    replies_raw = db.execute(
        '''SELECT rr.*, u.full_name as author_name, u.role as author_role
           FROM report_replies rr
           JOIN users u ON u.id = rr.user_id
           WHERE rr.report_id = ?
           ORDER BY rr.created_at''',
        (report_id,)
    ).fetchall()

    replies = []
    for reply in replies_raw:
        reply_attachments = db.execute(
            'SELECT * FROM report_attachments WHERE reply_id = ? ORDER BY created_at',
            (reply['id'],)
        ).fetchall()
        replies.append({'reply': reply, 'attachments': reply_attachments})

    return render_template('reports/view.html',
                           report=report,
                           attachments=attachments,
                           replies=replies,
                           qc_admins=qc_admins)


@reports_bp.route('/<int:report_id>/reply', methods=['POST'])
@login_required
def reply(report_id):
    if current_user.role not in ('qc_admin', 'it_admin'):
        abort(403)

    db = get_db()
    report = db.execute('SELECT id FROM daily_reports WHERE id = ?', (report_id,)).fetchone()
    if not report:
        abort(404)

    body = request.form.get('body', '').strip()
    photos = request.files.getlist('photos')

    if not body:
        flash('Reply message cannot be empty.', 'danger')
        return redirect(url_for('reports.view', report_id=report_id))

    cursor = db.execute(
        'INSERT INTO report_replies (report_id, user_id, body, created_at) VALUES (?, ?, ?, ?)',
        (report_id, current_user.id, body, local_now_str())
    )
    reply_id = cursor.lastrowid

    saved = _save_photos(photos)
    for filename, original_name in saved:
        db.execute(
            '''INSERT INTO report_attachments (reply_id, filename, original_name, uploaded_by)
               VALUES (?, ?, ?, ?)''',
            (reply_id, filename, original_name, current_user.id)
        )

    db.commit()
    flash('Reply sent.', 'success')
    return redirect(url_for('reports.view', report_id=report_id))


@reports_bp.route('/<int:report_id>/delete', methods=['POST'])
@login_required
@role_required('branch_manager')
def delete(report_id):
    db = get_db()
    report = db.execute(
        'SELECT submitted_by FROM daily_reports WHERE id = ?', (report_id,)
    ).fetchone()

    if not report:
        abort(404)
    if report['submitted_by'] != current_user.id:
        abort(403)

    # Delete physical attachment files
    upload_folder = current_app.config['UPLOAD_FOLDER']
    files = db.execute(
        '''SELECT filename FROM report_attachments
           WHERE report_id = ? OR reply_id IN (
               SELECT id FROM report_replies WHERE report_id = ?
           )''',
        (report_id, report_id)
    ).fetchall()
    for f in files:
        try:
            os.remove(os.path.join(upload_folder, f['filename']))
        except OSError:
            pass

    db.execute('DELETE FROM daily_reports WHERE id = ?', (report_id,))
    db.commit()
    flash('Report deleted.', 'success')
    return redirect(url_for('reports.inbox'))
