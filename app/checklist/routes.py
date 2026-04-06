import os
import uuid
from flask import render_template, request, redirect, url_for, flash, abort, jsonify, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.checklist import checklist_bp
from app.db import get_db
from app.utils import role_required, local_today, local_now_str


def _save_comment_photos(files):
    """Save uploaded comment photos; returns list of (filename, original_name)."""
    saved = []
    folder = current_app.config['COMMENT_UPLOAD_FOLDER']
    allowed = current_app.config['ALLOWED_PHOTO_EXTENSIONS']
    max_n = current_app.config['MAX_PHOTOS_PER_UPLOAD']
    for f in files[:max_n]:
        if not f or not f.filename:
            continue
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in allowed:
            flash(f'"{f.filename}" is not a supported image type.', 'warning')
            continue
        unique = f'{uuid.uuid4().hex}.{ext}'
        f.save(os.path.join(folder, unique))
        saved.append((unique, secure_filename(f.filename)))
    return saved


@checklist_bp.route('/')
@login_required
@role_required('branch_manager')
def index():
    db = get_db()
    today = local_today()

    templates = db.execute(
        'SELECT * FROM checklist_templates WHERE is_active = 1 ORDER BY id'
    ).fetchall()

    status = {}
    for tmpl in templates:
        submission = db.execute(
            '''SELECT cs.id, cs.submitted_at,
                      COUNT(cr.id) as total,
                      SUM(CASE WHEN cr.answer = 'yes' THEN 1 ELSE 0 END) as yes_count,
                      s.score
               FROM checklist_submissions cs
               LEFT JOIN checklist_responses cr ON cr.submission_id = cs.id
               LEFT JOIN scores s ON s.submission_id = cs.id
               WHERE cs.branch_id = ? AND cs.template_id = ? AND cs.submission_date = ?
               GROUP BY cs.id''',
            (current_user.branch_id, tmpl['id'], today)
        ).fetchone()
        status[tmpl['id']] = submission

    return render_template('checklist/index.html', templates=templates, status=status, today=today)


@checklist_bp.route('/submit/today', methods=['GET', 'POST'])
@login_required
@role_required('branch_manager')
def submit_today():
    db = get_db()
    today = local_today()

    templates = db.execute(
        'SELECT * FROM checklist_templates WHERE is_active = 1 ORDER BY id'
    ).fetchall()

    # Which templates are already submitted today?
    existing = {}
    for tmpl in templates:
        sub = db.execute(
            '''SELECT id FROM checklist_submissions
               WHERE branch_id = ? AND template_id = ? AND submission_date = ?''',
            (current_user.branch_id, tmpl['id'], today)
        ).fetchone()
        existing[tmpl['id']] = sub['id'] if sub else None

    # If everything already submitted, redirect back
    if all(existing[t['id']] is not None for t in templates):
        flash('All checklists for today have already been submitted.', 'info')
        return redirect(url_for('checklist.index'))

    # Fetch sections+items per template, grouped for display
    sections_by_template = {}
    items_by_template = {}  # flat list still used for POST processing
    for tmpl in templates:
        if existing[tmpl['id']] is not None:
            sections_by_template[tmpl['id']] = []
            items_by_template[tmpl['id']] = []
            continue

        all_items = db.execute(
            '''SELECT ci.*, cs.name AS section_name, cs.display_order AS sec_order,
                      cs.id AS sec_id
               FROM checklist_items ci
               JOIN checklist_sections cs ON cs.id = ci.section_id
               WHERE cs.branch_id = ? AND ci.is_active = 1
               ORDER BY COALESCE(cs.display_order, 9999), cs.id, ci.display_order, ci.id''',
            (current_user.branch_id,)
        ).fetchall()

        items_by_template[tmpl['id']] = all_items

        # Group into sections
        seen_sections = {}
        ordered_sections = []
        for item in all_items:
            sec_key = item['sec_id'] if item['sec_id'] else 0
            if sec_key not in seen_sections:
                seen_sections[sec_key] = {
                    'id': item['sec_id'],
                    'name': item['section_name'] or 'General',
                    'items': []
                }
                ordered_sections.append(seen_sections[sec_key])
            seen_sections[sec_key]['items'].append(item)
        sections_by_template[tmpl['id']] = ordered_sections

    if request.method == 'POST':
        all_errors = []
        all_responses = {}

        for tmpl in templates:
            if existing[tmpl['id']] is not None:
                continue
            items = items_by_template.get(tmpl['id'], [])
            responses = []
            for item in items:
                key = f'{tmpl["id"]}_{item["id"]}'
                answer = request.form.get(f'answer_{key}')
                reason = request.form.get(f'reason_{key}', '').strip()

                if answer not in ('yes', 'no'):
                    # Treat unanswered as 'no' with no reason
                    answer = 'no'
                responses.append({
                    'item_id': item['id'],
                    'answer': answer,
                    'reason': reason if answer == 'no' else None,
                })
            all_responses[tmpl['id']] = responses

        if all_errors:
            for error in all_errors:
                flash(error, 'danger')
            return render_template('checklist/submit_all.html',
                                   templates=templates,
                                   sections_by_template=sections_by_template,
                                   items_by_template=items_by_template,
                                   existing=existing,
                                   today=today)

        for tmpl in templates:
            if existing[tmpl['id']] is not None:
                continue
            items = items_by_template.get(tmpl['id'], [])
            if not items:
                continue
            cursor = db.execute(
                '''INSERT INTO checklist_submissions
                   (branch_id, submitted_by, template_id, submission_date, submitted_at)
                   VALUES (?, ?, ?, ?, ?)''',
                (current_user.branch_id, current_user.id, tmpl['id'], today, local_now_str())
            )
            submission_id = cursor.lastrowid
            for r in all_responses[tmpl['id']]:
                db.execute(
                    '''INSERT INTO checklist_responses (submission_id, item_id, answer, reason)
                       VALUES (?, ?, ?, ?)''',
                    (submission_id, r['item_id'], r['answer'], r['reason'])
                )

        db.commit()
        flash('Daily checklist submitted successfully!', 'success')
        return redirect(url_for('checklist.index'))

    return render_template('checklist/submit_all.html',
                           templates=templates,
                           sections_by_template=sections_by_template,
                           items_by_template=items_by_template,
                           existing=existing,
                           today=today)


@checklist_bp.route('/submit/<int:template_id>', methods=['GET', 'POST'])
@login_required
@role_required('branch_manager')
def submit(template_id):
    db = get_db()
    today = local_today()

    template = db.execute(
        'SELECT * FROM checklist_templates WHERE id = ? AND is_active = 1', (template_id,)
    ).fetchone()
    if not template:
        abort(404)

    existing = db.execute(
        '''SELECT id FROM checklist_submissions
           WHERE branch_id = ? AND template_id = ? AND submission_date = ?''',
        (current_user.branch_id, template_id, today)
    ).fetchone()

    if existing:
        flash('You have already submitted this checklist today.', 'info')
        return redirect(url_for('checklist.view', submission_id=existing['id']))

    items = db.execute(
        '''SELECT ci.* FROM checklist_items ci
           WHERE ci.template_id = ? AND ci.is_active = 1
           AND (
               NOT EXISTS (SELECT 1 FROM checklist_item_branches cib WHERE cib.item_id = ci.id)
               OR EXISTS (SELECT 1 FROM checklist_item_branches cib WHERE cib.item_id = ci.id AND cib.branch_id = ?)
           )
           ORDER BY ci.display_order, ci.id''',
        (template_id, current_user.branch_id)
    ).fetchall()

    if not items:
        flash(f'No checklist items have been set up for {template["name"]} yet. Contact your QC Admin.', 'warning')
        return redirect(url_for('checklist.index'))

    if request.method == 'POST':
        errors = []
        responses = []

        for item in items:
            answer = request.form.get(f'answer_{item["id"]}')
            reason = request.form.get(f'reason_{item["id"]}', '').strip()

            if answer not in ('yes', 'no'):
                errors.append(f'Please answer: "{item["item_text"]}"')
                continue

            if answer == 'no' and len(reason) < 5:
                errors.append(f'Reason is required (min 5 chars) for: "{item["item_text"]}"')
                continue

            responses.append({
                'item_id': item['id'],
                'answer': answer,
                'reason': reason if answer == 'no' else None,
            })

        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('checklist/submit.html', template=template, items=items, today=today)

        cursor = db.execute(
            '''INSERT INTO checklist_submissions
               (branch_id, submitted_by, template_id, submission_date, submitted_at)
               VALUES (?, ?, ?, ?, ?)''',
            (current_user.branch_id, current_user.id, template_id, today, local_now_str())
        )
        submission_id = cursor.lastrowid

        for r in responses:
            db.execute(
                '''INSERT INTO checklist_responses (submission_id, item_id, answer, reason)
                   VALUES (?, ?, ?, ?)''',
                (submission_id, r['item_id'], r['answer'], r['reason'])
            )

        db.commit()
        flash(f'{template["name"]} checklist submitted successfully!', 'success')
        return redirect(url_for('checklist.view', submission_id=submission_id))

    return render_template('checklist/submit.html', template=template, items=items, today=today)


@checklist_bp.route('/view/<int:submission_id>')
@login_required
def view(submission_id):
    db = get_db()

    submission = db.execute(
        '''SELECT cs.*, b.name as branch_name,
                  u.full_name as submitted_by_name,
                  s.score, s.notes as score_notes,
                  su.full_name as scored_by_name, s.scored_at
           FROM checklist_submissions cs
           JOIN checklist_templates ct ON ct.id = cs.template_id
           JOIN branches b ON b.id = cs.branch_id
           JOIN users u ON u.id = cs.submitted_by
           LEFT JOIN scores s ON s.submission_id = cs.id
           LEFT JOIN users su ON su.id = s.scored_by
           WHERE cs.id = ?''',
        (submission_id,)
    ).fetchone()

    if not submission:
        abort(404)

    if (current_user.role == 'branch_manager'
            and submission['branch_id'] != current_user.branch_id):
        abort(403)

    responses_raw = db.execute(
        '''SELECT cr.*, ci.item_text, ci.display_order,
                  cs.id AS sec_id, cs.name AS section_name, cs.display_order AS sec_order
           FROM checklist_responses cr
           JOIN checklist_items ci ON ci.id = cr.item_id
           LEFT JOIN checklist_sections cs ON cs.id = ci.section_id
           WHERE cr.submission_id = ?
           ORDER BY COALESCE(cs.display_order, 9999), COALESCE(cs.id, 0), ci.display_order, ci.id''',
        (submission_id,)
    ).fetchall()

    # Group responses into sections
    seen_sec = {}
    sections_with_responses = []
    for r in responses_raw:
        sec_key = r['sec_id'] if r['sec_id'] else 0
        if sec_key not in seen_sec:
            seen_sec[sec_key] = {
                'id': r['sec_id'],
                'name': r['section_name'] or 'General',
                'responses': []
            }
            sections_with_responses.append(seen_sec[sec_key])
        seen_sec[sec_key]['responses'].append(r)

    responses = responses_raw  # keep flat list for backward compat

    comments_raw = db.execute(
        '''SELECT c.*, u.full_name, u.role
           FROM comments c
           JOIN users u ON u.id = c.user_id
           WHERE c.submission_id = ?
           ORDER BY c.created_at ASC''',
        (submission_id,)
    ).fetchall()

    # Attach photo list to each comment
    comments = []
    for c in comments_raw:
        photos = db.execute(
            'SELECT * FROM comment_attachments WHERE comment_id = ? ORDER BY created_at',
            (c['id'],)
        ).fetchall()
        comments.append({'comment': c, 'photos': photos})

    total = len(responses)
    yes_count = sum(1 for r in responses if r['answer'] == 'yes')
    compliance_pct = round((yes_count / total * 100), 1) if total > 0 else 0

    return render_template(
        'checklist/view.html',
        submission=submission,
        responses=responses,
        sections_with_responses=sections_with_responses,
        comments=comments,
        total=total,
        yes_count=yes_count,
        compliance_pct=compliance_pct,
    )


@checklist_bp.route('/score/<int:submission_id>', methods=['POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def score(submission_id):
    db = get_db()

    submission = db.execute(
        'SELECT * FROM checklist_submissions WHERE id = ?',
        (submission_id,)
    ).fetchone()

    if not submission:
        abort(404)

    score_val = request.form.get('score', '').strip()
    notes = request.form.get('notes', '').strip()

    try:
        score_int = int(score_val)
        if not (0 <= score_int <= 100):
            raise ValueError
    except ValueError:
        flash('Score must be a whole number between 0 and 100.', 'danger')
        return redirect(url_for('checklist.view', submission_id=submission_id))

    existing_score = db.execute(
        'SELECT id FROM scores WHERE submission_id = ?', (submission_id,)
    ).fetchone()

    if existing_score:
        db.execute(
            'UPDATE scores SET score = ?, notes = ?, scored_by = ?, updated_at = ?, scored_at = ? WHERE submission_id = ?',
            (score_int, notes, current_user.id, local_now_str(), local_now_str(), submission_id)
        )
    else:
        db.execute(
            'INSERT INTO scores (submission_id, scored_by, score, notes, scored_at) VALUES (?, ?, ?, ?, ?)',
            (submission_id, current_user.id, score_int, notes, local_now_str())
        )

    db.commit()
    flash(f'Score of {score_int}/100 saved successfully.', 'success')
    return redirect(url_for('checklist.view', submission_id=submission_id))


@checklist_bp.route('/comment/<int:submission_id>', methods=['POST'])
@login_required
def comment(submission_id):
    db = get_db()

    submission = db.execute(
        'SELECT * FROM checklist_submissions WHERE id = ?', (submission_id,)
    ).fetchone()

    if not submission:
        abort(404)

    if (current_user.role == 'branch_manager'
            and submission['branch_id'] != current_user.branch_id):
        abort(403)

    if current_user.role == 'management':
        abort(403)

    message = request.form.get('message', '').strip()
    photos = request.files.getlist('photos')

    if not message:
        flash('Comment cannot be empty.', 'danger')
        return redirect(url_for('checklist.view', submission_id=submission_id))

    if len(message) > 1000:
        flash('Comment is too long (max 1000 characters).', 'danger')
        return redirect(url_for('checklist.view', submission_id=submission_id))

    cursor = db.execute(
        'INSERT INTO comments (submission_id, user_id, message, created_at) VALUES (?, ?, ?, ?)',
        (submission_id, current_user.id, message, local_now_str())
    )
    comment_id = cursor.lastrowid

    for filename, original_name in _save_comment_photos(photos):
        db.execute(
            '''INSERT INTO comment_attachments (comment_id, filename, original_name, uploaded_by)
               VALUES (?, ?, ?, ?)''',
            (comment_id, filename, original_name, current_user.id)
        )

    db.commit()
    flash('Comment posted.', 'success')
    return redirect(url_for('checklist.view', submission_id=submission_id))


@checklist_bp.route('/history')
@login_required
@role_required('branch_manager')
def history():
    db = get_db()
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    total_count = db.execute(
        'SELECT COUNT(*) FROM checklist_submissions WHERE branch_id = ?',
        (current_user.branch_id,)
    ).fetchone()[0]

    submissions = db.execute(
        '''SELECT cs.id, cs.submission_date, cs.submitted_at,
                  COUNT(cr.id) as total,
                  SUM(CASE WHEN cr.answer = 'yes' THEN 1 ELSE 0 END) as yes_count,
                  s.score
           FROM checklist_submissions cs
           LEFT JOIN checklist_responses cr ON cr.submission_id = cs.id
           LEFT JOIN scores s ON s.submission_id = cs.id
           WHERE cs.branch_id = ?
           GROUP BY cs.id
           ORDER BY cs.submission_date DESC, cs.submitted_at DESC
           LIMIT ? OFFSET ?''',
        (current_user.branch_id, per_page, offset)
    ).fetchall()

    total_pages = (total_count + per_page - 1) // per_page

    return render_template(
        'checklist/history.html',
        submissions=submissions,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
    )


@checklist_bp.route('/all')
@login_required
@role_required('qc_admin', 'management', 'it_admin')
def all_submissions():
    db = get_db()

    filter_brand  = request.args.get('brand_id', '', type=str)
    filter_branch = request.args.get('branch_id', '', type=str)
    filter_date   = request.args.get('date', local_today())

    brands = db.execute('SELECT id, name FROM brands WHERE is_active = 1 ORDER BY name').fetchall()
    branches = db.execute(
        '''SELECT b.id, b.name, br.name as brand_name
           FROM branches b
           LEFT JOIN brands br ON br.id = b.brand_id
           ORDER BY br.name NULLS LAST, b.name'''
    ).fetchall()

    query = '''
        SELECT cs.id, cs.submission_date, cs.submitted_at,
               b.name as branch_name,
               br.name as brand_name,
               u.full_name as submitted_by_name,
               COUNT(cr.id) as total,
               SUM(CASE WHEN cr.answer = 'yes' THEN 1 ELSE 0 END) as yes_count,
               s.score,
               (SELECT COUNT(*) FROM comments c WHERE c.submission_id = cs.id) as comment_count
        FROM checklist_submissions cs
        JOIN branches b ON b.id = cs.branch_id
        LEFT JOIN brands br ON br.id = b.brand_id
        JOIN users u ON u.id = cs.submitted_by
        LEFT JOIN checklist_responses cr ON cr.submission_id = cs.id
        LEFT JOIN scores s ON s.submission_id = cs.id
        WHERE cs.submission_date = ?
    '''
    params = [filter_date]

    if filter_brand:
        query += ' AND b.brand_id = ?'
        params.append(filter_brand)

    if filter_branch:
        query += ' AND cs.branch_id = ?'
        params.append(filter_branch)

    query += ' GROUP BY cs.id ORDER BY br.name NULLS LAST, b.name'

    submissions = db.execute(query, params).fetchall()

    return render_template(
        'checklist/all.html',
        submissions=submissions,
        brands=brands,
        branches=branches,
        filter_brand=filter_brand,
        filter_branch=filter_branch,
        filter_date=filter_date,
    )


# ─────────────────────────────────────────────
#  CHECKLIST ITEM MANAGEMENT  (QC Admin / IT Admin)
# ─────────────────────────────────────────────

@checklist_bp.route('/items')
@login_required
@role_required('qc_admin', 'it_admin')
def manage_items():
    db = get_db()
    branches = db.execute(
        '''SELECT b.id, b.name, br.name as brand_name
           FROM branches b LEFT JOIN brands br ON br.id = b.brand_id
           ORDER BY br.name, b.name'''
    ).fetchall()
    selected_branch_id = request.args.get('branch_id', type=int)
    selected_template_id = request.args.get('template_id', type=int)

    # Default to first branch if none selected
    if not selected_branch_id and branches:
        selected_branch_id = branches[0]['id']

    # Resolve the active template to use
    if selected_template_id:
        tmpl = db.execute(
            'SELECT * FROM checklist_templates WHERE id = ?', (selected_template_id,)
        ).fetchone()
    else:
        tmpl = db.execute(
            'SELECT * FROM checklist_templates WHERE is_active = 1 ORDER BY id LIMIT 1'
        ).fetchone()

    template_id = tmpl['id'] if tmpl else 1
    selected_template = tmpl

    selected_branch = None
    sections_data = []

    if selected_branch_id:
        selected_branch = db.execute('SELECT * FROM branches WHERE id = ?', (selected_branch_id,)).fetchone()
        sections = db.execute(
            'SELECT * FROM checklist_sections WHERE branch_id = ? AND template_id = ? ORDER BY display_order, id',
            (selected_branch_id, template_id)
        ).fetchall()
        for sec in sections:
            items = db.execute(
                'SELECT * FROM checklist_items WHERE section_id = ? ORDER BY display_order, id',
                (sec['id'],)
            ).fetchall()
            sections_data.append({'section': sec, 'items': items})

    return render_template(
        'checklist/manage_items.html',
        branches=branches,
        selected_branch=selected_branch,
        selected_branch_id=selected_branch_id,
        sections_data=sections_data,
        template_id=template_id,
        selected_template=selected_template,
    )


@checklist_bp.route('/items/create', methods=['GET', 'POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def create_item():
    db = get_db()
    templates = db.execute('SELECT id, name FROM checklist_templates WHERE is_active = 1').fetchall()
    branches  = db.execute('SELECT id, name FROM branches ORDER BY name').fetchall()

    if request.method == 'POST':
        item_text    = request.form.get('item_text', '').strip()
        template_id  = request.form.get('template_id', '')
        section_id   = request.form.get('section_id', '') or None
        display_order = request.form.get('display_order', '0').strip()
        branch_scope = request.form.get('branch_scope', 'all')
        branch_ids   = request.form.getlist('branch_ids')

        errors = []
        if not item_text:
            errors.append('Item text is required.')
        if not template_id:
            errors.append('Please select a checklist type.')
        if branch_scope == 'specific' and not branch_ids:
            errors.append('Please select at least one branch, or choose "All Branches".')

        try:
            order_int = int(display_order)
        except ValueError:
            order_int = 0

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('checklist/create_item.html',
                                   templates=templates, branches=branches)

        cursor = db.execute(
            '''INSERT INTO checklist_items (template_id, section_id, item_text, display_order, created_by)
               VALUES (?, ?, ?, ?, ?)''',
            (int(template_id), int(section_id) if section_id else None, item_text, order_int, current_user.id)
        )
        new_item_id = cursor.lastrowid

        if branch_scope == 'specific':
            for bid in branch_ids:
                db.execute(
                    'INSERT INTO checklist_item_branches (item_id, branch_id) VALUES (?, ?)',
                    (new_item_id, int(bid))
                )

        db.commit()

        flash('Item added.', 'success')
        # Redirect back to the branch's manage page if we came from there
        branch_id_redirect = request.form.get('branch_id_redirect', '')
        if branch_id_redirect:
            return redirect(url_for('checklist.manage_items', branch_id=branch_id_redirect))
        return redirect(url_for('checklist.manage_items'))

    # Auto-suggest next display order for each template
    next_order = {}
    for t in templates:
        row = db.execute(
            'SELECT COALESCE(MAX(display_order), 0) + 1 AS next FROM checklist_items WHERE template_id = ?',
            (t['id'],)
        ).fetchone()
        next_order[t['id']] = row['next']

    return render_template('checklist/create_item.html',
                           templates=templates, branches=branches, next_order=next_order)


@checklist_bp.route('/items/<int:item_id>/update-text', methods=['POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def update_item_text(item_id):
    db = get_db()
    item_text = request.form.get('item_text', '').strip()
    if not item_text:
        flash('Item text cannot be empty.', 'danger')
    else:
        db.execute('UPDATE checklist_items SET item_text = ? WHERE id = ?', (item_text, item_id))
        db.commit()
        flash('Item updated.', 'success')
    branch_id, template_id = _get_item_branch(db, item_id)
    return redirect(url_for('checklist.manage_items', branch_id=branch_id, template_id=template_id))


def _get_item_branch(db, item_id):
    """Return (branch_id, template_id) of the section containing this item."""
    row = db.execute(
        '''SELECT cs.branch_id, cs.template_id FROM checklist_items ci
           JOIN checklist_sections cs ON cs.id = ci.section_id
           WHERE ci.id = ?''', (item_id,)
    ).fetchone()
    if row:
        return row['branch_id'], row['template_id']
    # Fall back to template_id on the item itself
    item_row = db.execute('SELECT template_id FROM checklist_items WHERE id = ?', (item_id,)).fetchone()
    return None, (item_row['template_id'] if item_row else None)


@checklist_bp.route('/items/<int:item_id>/toggle', methods=['POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def toggle_item(item_id):
    db = get_db()
    item = db.execute('SELECT * FROM checklist_items WHERE id = ?', (item_id,)).fetchone()
    if not item:
        abort(404)
    branch_id, template_id = _get_item_branch(db, item_id)
    new_status = 0 if item['is_active'] else 1
    db.execute('UPDATE checklist_items SET is_active = ? WHERE id = ?', (new_status, item_id))
    db.commit()
    flash('Item ' + ('activated.' if new_status else 'deactivated.'), 'success')
    return redirect(url_for('checklist.manage_items', branch_id=branch_id, template_id=template_id))


@checklist_bp.route('/items/<int:item_id>/delete', methods=['POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def delete_item(item_id):
    db = get_db()
    item = db.execute('SELECT * FROM checklist_items WHERE id = ?', (item_id,)).fetchone()
    if not item:
        abort(404)
    branch_id, template_id = _get_item_branch(db, item_id)

    in_use = db.execute(
        'SELECT COUNT(*) AS cnt FROM checklist_responses WHERE item_id = ?', (item_id,)
    ).fetchone()['cnt']

    if in_use > 0:
        flash(f'Cannot delete — {in_use} response(s) exist. Deactivate it instead.', 'danger')
        return redirect(url_for('checklist.manage_items', branch_id=branch_id, template_id=template_id))

    db.execute('DELETE FROM checklist_item_branches WHERE item_id = ?', (item_id,))
    db.execute('DELETE FROM checklist_items WHERE id = ?', (item_id,))
    db.commit()
    flash('Item deleted.', 'success')
    return redirect(url_for('checklist.manage_items', branch_id=branch_id, template_id=template_id))


@checklist_bp.route('/items/reorder', methods=['POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def reorder_items():
    data = request.get_json(silent=True) or {}
    item_ids = data.get('item_ids', [])
    if not item_ids:
        return jsonify({'ok': False, 'error': 'No item_ids provided'}), 400
    db = get_db()
    for idx, item_id in enumerate(item_ids):
        db.execute('UPDATE checklist_items SET display_order = ? WHERE id = ?', (idx, item_id))
    db.commit()
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Section CRUD
# ---------------------------------------------------------------------------

@checklist_bp.route('/sections/create', methods=['POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def create_section():
    db = get_db()
    name = request.form.get('name', '').strip()
    template_id = request.form.get('template_id', '')
    branch_id = request.form.get('branch_id', '')

    if not name or not template_id or not branch_id:
        flash('Section name and branch are required.', 'danger')
        return redirect(url_for('checklist.manage_items', branch_id=branch_id or None))

    existing = db.execute(
        'SELECT id FROM checklist_sections WHERE branch_id = ? AND name = ?',
        (int(branch_id), name)
    ).fetchone()
    if existing:
        flash(f'A section named "{name}" already exists for this branch.', 'danger')
        return redirect(url_for('checklist.manage_items', branch_id=branch_id))

    max_order = db.execute(
        'SELECT COALESCE(MAX(display_order), 0) FROM checklist_sections WHERE branch_id = ?',
        (int(branch_id),)
    ).fetchone()[0]

    db.execute(
        'INSERT INTO checklist_sections (template_id, branch_id, name, display_order) VALUES (?, ?, ?, ?)',
        (int(template_id), int(branch_id), name, max_order + 1)
    )
    db.commit()
    flash(f'Section "{name}" added.', 'success')
    return redirect(url_for('checklist.manage_items', branch_id=branch_id, template_id=template_id))


@checklist_bp.route('/sections/<int:section_id>/edit', methods=['POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def edit_section(section_id):
    db = get_db()
    section = db.execute('SELECT * FROM checklist_sections WHERE id = ?', (section_id,)).fetchone()
    if not section:
        abort(404)

    name = request.form.get('name', '').strip()
    if not name:
        flash('Section name cannot be empty.', 'danger')
        return redirect(url_for('checklist.manage_items', branch_id=section['branch_id']))

    db.execute('UPDATE checklist_sections SET name = ? WHERE id = ?', (name, section_id))
    db.commit()
    flash('Section updated.', 'success')
    return redirect(url_for('checklist.manage_items', branch_id=section['branch_id'], template_id=section['template_id']))


@checklist_bp.route('/sections/<int:section_id>/delete', methods=['POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def delete_section(section_id):
    db = get_db()
    section = db.execute('SELECT * FROM checklist_sections WHERE id = ?', (section_id,)).fetchone()
    if not section:
        abort(404)

    item_count = db.execute(
        'SELECT COUNT(*) FROM checklist_items WHERE section_id = ? AND is_active = 1',
        (section_id,)
    ).fetchone()[0]

    branch_id = section['branch_id']

    if item_count > 0:
        flash(f'Cannot delete "{section["name"]}" — it has {item_count} active item(s). Deactivate them first.', 'danger')
        return redirect(url_for('checklist.manage_items', branch_id=branch_id))

    db.execute('DELETE FROM checklist_items WHERE section_id = ?', (section_id,))
    db.execute('DELETE FROM checklist_sections WHERE id = ?', (section_id,))
    db.commit()
    flash(f'Section "{section["name"]}" deleted.', 'success')
    return redirect(url_for('checklist.manage_items', branch_id=branch_id, template_id=section['template_id']))
