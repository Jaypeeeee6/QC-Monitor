import os
import uuid
from flask import render_template, request, redirect, url_for, flash, abort, jsonify, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.checklist import checklist_bp
from app.checklist.effective import (
    branch_layer_template,
    default_editing_layer_id,
    ensure_brand_branch_layer,
    ensure_inheritance_branch_layer,
    fallback_branch_id,
    fetch_sections_for_template_layer,
    get_effective_flat_items,
    get_root_template,
    group_items_into_sections,
    is_brand_root,
    layer_chain_for_manage,
    list_branch_manager_templates,
    sections_data_merged_for_manage,
    uses_inheritance_chain,
)
from app.db import get_db
from app.utils import role_required, local_today, local_now_str


def _wants_ajax_json():
    """POST from manage checklist uses fetch + JSON (no full page reload)."""
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    accept = request.headers.get('Accept', '') or ''
    return 'application/json' in accept


def _redirect_manage_for_item(db, item_id):
    row = db.execute(
        '''SELECT cs.branch_id, cs.template_id FROM checklist_items ci
           JOIN checklist_sections cs ON cs.id = ci.section_id WHERE ci.id = ?''',
        (item_id,),
    ).fetchone()
    if not row:
        return redirect(url_for('checklist.manage_items'))
    root = get_root_template(db, row['template_id'])
    rid = root['id'] if root else row['template_id']
    bid = fallback_branch_id(db, row['template_id'], row['branch_id'])
    return redirect(
        url_for(
            'checklist.manage_items',
            branch_id=bid,
            template_id=rid,
            layer_id=row['template_id'],
        )
    )


def _redirect_manage_for_section(db, section_row):
    root = get_root_template(db, section_row['template_id'])
    rid = root['id'] if root else section_row['template_id']
    bid = fallback_branch_id(db, section_row['template_id'], section_row['branch_id'])
    return redirect(
        url_for(
            'checklist.manage_items',
            branch_id=bid,
            template_id=rid,
            layer_id=section_row['template_id'],
        )
    )


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

    templates = list_branch_manager_templates(db, current_user.branch_id)

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
    focused_template_id = request.args.get('template_id', type=int)

    templates = list_branch_manager_templates(db, current_user.branch_id)
    if not templates:
        flash('No checklists are configured for your branch yet. Contact your QC Admin.', 'info')
        return redirect(url_for('checklist.index'))

    focused_template = None
    if focused_template_id:
        focused_template = next((t for t in templates if t['id'] == focused_template_id), None)
        if not focused_template:
            abort(404)
        # When a template is chosen from index, show only that template's checklist.
        templates = [focused_template]

    # Which templates are already submitted today?
    existing = {}
    for tmpl in templates:
        sub = db.execute(
            '''SELECT id FROM checklist_submissions
               WHERE branch_id = ? AND template_id = ? AND submission_date = ?''',
            (current_user.branch_id, tmpl['id'], today)
        ).fetchone()
        existing[tmpl['id']] = sub['id'] if sub else None

    if focused_template and existing[focused_template['id']] is not None:
        flash('You have already submitted this checklist today.', 'info')
        return redirect(url_for('checklist.view', submission_id=existing[focused_template['id']]))

    # If everything already submitted, redirect back
    if templates and all(existing[t['id']] is not None for t in templates):
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

        all_items = get_effective_flat_items(db, tmpl['id'], current_user.branch_id)
        items_by_template[tmpl['id']] = all_items
        sections_by_template[tmpl['id']] = group_items_into_sections(all_items)

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
                if answer == 'no' and not reason:
                    all_errors.append(
                        f'Remarks are required when unchecked for: "{item["item_text"]}"'
                    )
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
                                   today=today,
                                   focused_template_id=focused_template_id)

        did_submit = False
        for tmpl in templates:
            if existing[tmpl['id']] is not None:
                continue
            items = items_by_template.get(tmpl['id'], [])
            if not items:
                continue
            did_submit = True
            cursor = db.execute(
                '''INSERT INTO checklist_submissions
                   (branch_id, submitted_by, template_id, template_name_snapshot,
                    submission_date, submitted_at)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (
                    current_user.branch_id,
                    current_user.id,
                    tmpl['id'],
                    tmpl['name'],
                    today,
                    local_now_str(),
                ),
            )
            submission_id = cursor.lastrowid
            for r in all_responses[tmpl['id']]:
                db.execute(
                    '''INSERT INTO checklist_responses (submission_id, item_id, answer, reason)
                       VALUES (?, ?, ?, ?)''',
                    (submission_id, r['item_id'], r['answer'], r['reason'])
                )

        if not did_submit:
            flash(
                'No checklist items are configured for your pending templates. Contact your QC Admin.',
                'warning',
            )
            return render_template(
                'checklist/submit_all.html',
                templates=templates,
                sections_by_template=sections_by_template,
                items_by_template=items_by_template,
                existing=existing,
                today=today,
                focused_template_id=focused_template_id,
            )

        db.commit()
        if focused_template:
            flash(f'{focused_template["name"]} checklist submitted successfully!', 'success')
        else:
            flash('Daily checklist submitted successfully!', 'success')
        return redirect(url_for('checklist.index'))

    return render_template('checklist/submit_all.html',
                           templates=templates,
                           sections_by_template=sections_by_template,
                           items_by_template=items_by_template,
                           existing=existing,
                           today=today,
                           focused_template_id=focused_template_id)


@checklist_bp.route('/submit/<int:template_id>', methods=['GET', 'POST'])
@login_required
@role_required('branch_manager')
def submit(template_id):
    db = get_db()
    today = local_today()

    template = db.execute(
        '''SELECT * FROM checklist_templates
           WHERE id = ? AND is_active = 1 AND parent_template_id IS NULL''',
        (template_id,),
    ).fetchone()
    if not template:
        abort(404)

    visible = {t['id'] for t in list_branch_manager_templates(db, current_user.branch_id)}
    if template['id'] not in visible:
        abort(404)

    existing = db.execute(
        '''SELECT id FROM checklist_submissions
           WHERE branch_id = ? AND template_id = ? AND submission_date = ?''',
        (current_user.branch_id, template_id, today)
    ).fetchone()

    if existing:
        flash('You have already submitted this checklist today.', 'info')
        return redirect(url_for('checklist.view', submission_id=existing['id']))

    items = get_effective_flat_items(db, template_id, current_user.branch_id)

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
               (branch_id, submitted_by, template_id, template_name_snapshot,
                submission_date, submitted_at)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (
                current_user.branch_id,
                current_user.id,
                template_id,
                template['name'],
                today,
                local_now_str(),
            ),
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
                  COALESCE(cs.template_name_snapshot, ct.name) AS template_display_name,
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

    template = (
        'checklist/view_panel.html'
        if request.args.get('panel')
        else 'checklist/view.html'
    )
    return render_template(
        template,
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
        '''SELECT cs.id,
                  MAX(cs.submission_date) AS submission_date,
                  MAX(cs.submitted_at) AS submitted_at,
                  COALESCE(MAX(cs.template_name_snapshot), MAX(ct.name)) AS template_name,
                  COUNT(cr.id) as total,
                  SUM(CASE WHEN cr.answer = 'yes' THEN 1 ELSE 0 END) as yes_count,
                  MAX(s.score) AS score
           FROM checklist_submissions cs
           LEFT JOIN checklist_templates ct ON ct.id = cs.template_id
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
    filter_search = request.args.get('search', '').strip()

    brands = db.execute('SELECT id, name FROM brands WHERE is_active = 1 ORDER BY name').fetchall()
    branches = db.execute(
        '''SELECT b.id, b.name, br.name as brand_name
           FROM branches b
           LEFT JOIN brands br ON br.id = b.brand_id
           ORDER BY br.name NULLS LAST, b.name'''
    ).fetchall()

    query = '''
        SELECT cs.id, cs.submission_date, cs.submitted_at,
               COALESCE(cs.template_name_snapshot, ct.name) AS template_name,
               b.name as branch_name,
               br.name as brand_name,
               u.full_name as submitted_by_name,
               COUNT(cr.id) as total,
               SUM(CASE WHEN cr.answer = 'yes' THEN 1 ELSE 0 END) as yes_count,
               s.score,
               (SELECT COUNT(*) FROM comments c WHERE c.submission_id = cs.id) as comment_count
        FROM checklist_submissions cs
        LEFT JOIN checklist_templates ct ON ct.id = cs.template_id
        JOIN branches b ON b.id = cs.branch_id
        LEFT JOIN brands br ON br.id = b.brand_id
        JOIN users u ON u.id = cs.submitted_by
        LEFT JOIN checklist_responses cr ON cr.submission_id = cs.id
        LEFT JOIN scores s ON s.submission_id = cs.id
        WHERE cs.submission_date = ?
    '''
    params = [filter_date]

    if filter_search:
        query += ' AND u.full_name LIKE ?'
        params.append(f'%{filter_search}%')

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


def _append_manage_scope_url_kwargs(db, url_kw, layer_template_id, form):
    """Append company_wide / all_brand query args for redirects back to manage_items."""
    root = get_root_template(db, layer_template_id)
    if not root:
        return
    if (
        form.get('company_wide') == '1'
        and uses_inheritance_chain(root)
        and layer_template_id == root['id']
    ):
        url_kw['company_wide'] = 1
    if (
        form.get('all_brand') == '1'
        and is_brand_root(root)
        and layer_template_id == root['id']
    ):
        url_kw['all_brand'] = 1


def _peer_reuse_widget_visible(root_row, editing_layer, layer_id, selected_branch_id, branches):
    """True when the peer-reuse row should appear (options may still be empty)."""
    if not root_row or not editing_layer or not selected_branch_id or not branches:
        return False
    root_id = root_row['id']
    root_scope = (root_row['template_scope'] or 'legacy').lower()
    el_scope = (editing_layer['template_scope'] or 'legacy').lower()
    if root_scope == 'legacy' and layer_id == root_id and not editing_layer['parent_template_id']:
        return True
    if root_scope == 'global' and layer_id == root_id and el_scope == 'global':
        return True
    if (uses_inheritance_chain(root_row) or is_brand_root(root_row)) and el_scope == 'branch':
        if editing_layer['branch_id'] != selected_branch_id or layer_id != editing_layer['id']:
            return False
        if len(branches) < 2:
            return False
        return True
    return False


def _peer_reuse_block_options(
    db, root_row, editing_layer, layer_id, selected_branch_id, branches, sections_data
):
    """
    Sections that exist on other branches' checklists (same root) but not in the
    current editor context — legacy per-branch rows, or merged other-branch view vs shared global base.
    """
    if not root_row or not editing_layer or not branches or not selected_branch_id:
        return []
    root_id = root_row['id']
    root_scope = (root_row['template_scope'] or 'legacy').lower()
    el_scope = (editing_layer['template_scope'] or 'legacy').lower()

    def current_name_set():
        names = set()
        for row in sections_data or []:
            sec = row['section']
            if sec is None:
                continue
            raw = sec['name']
            if raw is None:
                continue
            lk = str(raw).strip().lower()
            if lk:
                names.add(lk)
        return names

    cur_names = current_name_set()

    # Legacy root: compare per-branch section rows on the root template
    if (
        root_scope == 'legacy'
        and layer_id == root_id
        and not editing_layer['parent_template_id']
    ):
        options = []
        seen_lower = set()
        for br in branches:
            if br['id'] == selected_branch_id:
                continue
            secs = db.execute(
                '''SELECT id, name FROM checklist_sections
                   WHERE template_id = ? AND branch_id = ? AND COALESCE(is_active, 1) = 1
                   ORDER BY display_order, id''',
                (root_id, br['id']),
            ).fetchall()
            for s in secs:
                lk = (s['name'] or '').strip().lower()
                if not lk or lk in cur_names or lk in seen_lower:
                    continue
                seen_lower.add(lk)
                options.append(
                    {
                        'section_id': s['id'],
                        'label': f'{s["name"]} ({br["name"]})',
                    }
                )
        options.sort(key=lambda x: x['label'].lower())
        return options

    # Global shared base: merged view on other branches vs sections on this layer.
    if root_scope == 'global' and layer_id == root_id and el_scope == 'global':
        options = []
        seen_lower = set()
        for br in branches:
            if br['id'] == selected_branch_id:
                continue
            flat = get_effective_flat_items(db, root_id, br['id'])
            grouped = group_items_into_sections(flat)
            for sec in grouped:
                sid = sec.get('id')
                nm = (sec.get('name') or '').strip()
                if not sid or not nm:
                    continue
                lk = nm.lower()
                if lk in cur_names or lk in seen_lower:
                    continue
                seen_lower.add(lk)
                options.append(
                    {
                        'section_id': sid,
                        'label': f'{nm} ({br["name"]})',
                    }
                )
        options.sort(key=lambda x: x['label'].lower())
        return options

    # This branch only: copy whole sections (+ items) from other branches' branch layers
    # into the current branch's branch layer; exclude names already on this branch's merged view.
    if (uses_inheritance_chain(root_row) or is_brand_root(root_row)) and el_scope == 'branch':
        if editing_layer['branch_id'] != selected_branch_id or layer_id != editing_layer['id']:
            return []
        options = []
        seen_lower = set()
        for br in branches:
            if br['id'] == selected_branch_id:
                continue
            if is_brand_root(root_row):
                ensure_brand_branch_layer(db, root_row, br['id'])
            elif uses_inheritance_chain(root_row):
                ensure_inheritance_branch_layer(db, root_row, br['id'])
            peer_layer = branch_layer_template(db, root_id, br['id'])
            if not peer_layer:
                continue
            for s in fetch_sections_for_template_layer(db, peer_layer['id'], br['id']):
                lk = (s['name'] or '').strip().lower()
                if not lk or lk in cur_names or lk in seen_lower:
                    continue
                seen_lower.add(lk)
                options.append(
                    {
                        'section_id': s['id'],
                        'label': f'{s["name"]} ({br["name"]})',
                    }
                )
        options.sort(key=lambda x: x['label'].lower())
        return options

    return []


@checklist_bp.route('/items')
@login_required
@role_required('qc_admin', 'it_admin')
def manage_items():
    db = get_db()
    all_branches = db.execute(
        '''SELECT b.id, b.name, br.name as brand_name, b.brand_id
           FROM branches b LEFT JOIN brands br ON br.id = b.brand_id
           ORDER BY br.name, b.name'''
    ).fetchall()

    selected_template_id = request.args.get('template_id', type=int)
    root_row = None
    if selected_template_id:
        root_row = get_root_template(db, selected_template_id)
    if not root_row:
        root_row = db.execute(
            '''SELECT * FROM checklist_templates
               WHERE is_active = 1 AND parent_template_id IS NULL
               ORDER BY id LIMIT 1'''
        ).fetchone()

    root_id = root_row['id'] if root_row else 1
    selected_template = root_row

    if root_row and is_brand_root(root_row) and root_row['brand_id']:
        branches = [b for b in all_branches if b['brand_id'] == root_row['brand_id']]
        if not branches:
            branches = list(all_branches)
    else:
        branches = list(all_branches)

    selected_branch_id = request.args.get('branch_id', type=int)
    if not selected_branch_id and branches:
        selected_branch_id = branches[0]['id']

    company_wide = request.args.get('company_wide', type=int) == 1
    if not (root_row and uses_inheritance_chain(root_row)):
        company_wide = False

    all_brand = request.args.get('all_brand', type=int) == 1
    if not (root_row and is_brand_root(root_row)):
        all_brand = False

    brand_all_branches_name = ''
    if root_row and is_brand_root(root_row):
        if root_row['brand_id']:
            bn = db.execute('SELECT name FROM brands WHERE id = ?', (root_row['brand_id'],)).fetchone()
            if bn:
                brand_all_branches_name = bn['name']
        if not brand_all_branches_name and len(branches) > 0:
            brand_all_branches_name = (branches[0]['brand_name'] or '').strip()

    layer_id = request.args.get('layer_id', type=int)
    if company_wide:
        layer_id = root_id
    elif all_brand:
        layer_id = root_id
    elif selected_branch_id:
        if root_row and is_brand_root(root_row) and not all_brand:
            ensure_brand_branch_layer(db, root_row, selected_branch_id)
        if root_row and uses_inheritance_chain(root_row):
            ensure_inheritance_branch_layer(db, root_row, selected_branch_id)
        if not layer_id:
            layer_id = default_editing_layer_id(db, root_id, selected_branch_id)
    else:
        layer_id = root_id

    layer_tabs = []
    if selected_branch_id and root_row and (
        uses_inheritance_chain(root_row) or is_brand_root(root_row)
    ):
        layer_tabs = layer_chain_for_manage(db, root_id, selected_branch_id)

    selected_branch = None
    sections_data = []

    if selected_branch_id:
        selected_branch = db.execute('SELECT * FROM branches WHERE id = ?', (selected_branch_id,)).fetchone()
        if root_row and (uses_inheritance_chain(root_row) or is_brand_root(root_row)):
            sections_data = sections_data_merged_for_manage(db, root_id, selected_branch_id)
        else:
            for sec in fetch_sections_for_template_layer(db, layer_id, selected_branch_id):
                items = db.execute(
                    'SELECT * FROM checklist_items WHERE section_id = ? ORDER BY display_order, id',
                    (sec['id'],),
                ).fetchall()
                sections_data.append({'section': sec, 'items': items})

    editing_layer = None
    if layer_id:
        editing_layer = db.execute(
            'SELECT * FROM checklist_templates WHERE id = ?', (layer_id,)
        ).fetchone()

    peer_reuse_visible = False
    peer_reuse_options = []
    if selected_template and editing_layer:
        peer_reuse_visible = _peer_reuse_widget_visible(
            selected_template, editing_layer, layer_id, selected_branch_id, branches
        )
        if peer_reuse_visible:
            peer_reuse_options = _peer_reuse_block_options(
                db,
                selected_template,
                editing_layer,
                layer_id,
                selected_branch_id,
                branches,
                sections_data,
            )

    parent_template = None
    if editing_layer and editing_layer['parent_template_id']:
        parent_template = db.execute(
            'SELECT id, name FROM checklist_templates WHERE id = ?',
            (editing_layer['parent_template_id'],),
        ).fetchone()

    dup_brands = []
    dup_all_templates = []
    dup_suggested_name = ''
    dup_source_template_id = None
    if selected_template:
        dup_brands = db.execute(
            'SELECT id, name FROM brands WHERE is_active = 1 ORDER BY name'
        ).fetchall()
        dup_all_templates = db.execute(
            '''SELECT id, name, template_scope, parent_template_id FROM checklist_templates
               WHERE is_active = 1 OR id = ?
               ORDER BY name''',
            (root_id,),
        ).fetchall()
        dup_suggested_name = f'{selected_template["name"]} (copy)'
        dup_source_template_id = root_id

    return render_template(
        'checklist/manage_items.html',
        branches=branches,
        dup_branches=all_branches,
        selected_branch=selected_branch,
        selected_branch_id=selected_branch_id,
        sections_data=sections_data,
        template_id=root_id,
        root_template_id=root_id,
        editing_layer_id=layer_id,
        editing_layer=editing_layer,
        selected_template=selected_template,
        parent_template=parent_template,
        layer_tabs=layer_tabs,
        peer_reuse_visible=peer_reuse_visible,
        peer_reuse_options=peer_reuse_options,
        dup_brands=dup_brands,
        dup_all_templates=dup_all_templates,
        dup_suggested_name=dup_suggested_name,
        dup_source_template_id=dup_source_template_id,
        company_wide=company_wide,
        all_brand=all_brand,
        brand_all_branches_name=brand_all_branches_name,
    )


@checklist_bp.route('/items/create', methods=['GET', 'POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def create_item():
    db = get_db()
    templates = db.execute(
        '''SELECT id, name FROM checklist_templates
           WHERE is_active = 1 AND parent_template_id IS NULL
           ORDER BY name'''
    ).fetchall()
    branches = db.execute('SELECT id, name FROM branches ORDER BY name').fetchall()

    if request.method == 'POST':
        item_text = request.form.get('item_text', '').strip()
        layer_template_id = request.form.get('layer_template_id') or request.form.get('template_id', '')
        section_id = request.form.get('section_id', '') or None
        display_order = request.form.get('display_order', '0').strip()
        branch_scope = request.form.get('branch_scope', 'all')
        branch_ids = request.form.getlist('branch_ids')

        errors = []
        if not item_text:
            errors.append('Item text is required.')
        if not layer_template_id:
            errors.append('Please select a checklist type.')
        if branch_scope == 'specific' and not branch_ids:
            errors.append('Please select at least one branch, or choose "All Branches".')

        insert_template_id = int(layer_template_id) if layer_template_id else None
        if section_id and insert_template_id:
            sec = db.execute(
                'SELECT * FROM checklist_sections WHERE id = ?', (int(section_id),)
            ).fetchone()
            if not sec or sec['template_id'] != insert_template_id:
                errors.append('That section does not belong to the selected template layer.')

        try:
            order_int = int(display_order)
        except ValueError:
            order_int = 0

        if errors:
            if _wants_ajax_json():
                return jsonify(ok=False, errors=errors), 400
            for e in errors:
                flash(e, 'danger')
            return render_template('checklist/create_item.html',
                                   templates=templates, branches=branches)

        cursor = db.execute(
            '''INSERT INTO checklist_items (template_id, section_id, item_text, display_order, created_by)
               VALUES (?, ?, ?, ?, ?)''',
            (insert_template_id, int(section_id) if section_id else None, item_text, order_int, current_user.id)
        )
        new_item_id = cursor.lastrowid

        if branch_scope == 'specific':
            for bid in branch_ids:
                db.execute(
                    'INSERT INTO checklist_item_branches (item_id, branch_id) VALUES (?, ?)',
                    (new_item_id, int(bid))
                )

        db.commit()

        if _wants_ajax_json():
            return jsonify(
                ok=True,
                item={
                    'id': new_item_id,
                    'item_text': item_text,
                    'is_active': 1,
                },
            )

        flash('Item added.', 'success')
        focus_section_id = None
        if section_id:
            try:
                focus_section_id = int(section_id)
            except (TypeError, ValueError):
                focus_section_id = None

        branch_id_redirect = request.form.get('branch_id_redirect', type=int)
        root_tid = request.form.get('root_template_id', type=int)
        layer_tid = request.form.get('layer_template_id', type=int) or insert_template_id
        if branch_id_redirect and root_tid:
            url_kw = dict(
                branch_id=branch_id_redirect,
                template_id=root_tid,
                layer_id=layer_tid,
            )
            _append_manage_scope_url_kwargs(db, url_kw, layer_tid, request.form)
            if focus_section_id is not None:
                url_kw['focus_section'] = focus_section_id
            return redirect(url_for('checklist.manage_items', **url_kw))
        if branch_id_redirect:
            if focus_section_id is not None:
                return redirect(
                    url_for(
                        'checklist.manage_items',
                        branch_id=branch_id_redirect,
                        focus_section=focus_section_id,
                    )
                )
            return redirect(url_for('checklist.manage_items', branch_id=branch_id_redirect))
        if focus_section_id is not None:
            return redirect(url_for('checklist.manage_items', focus_section=focus_section_id))
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
    return _redirect_manage_for_item(db, item_id)


@checklist_bp.route('/items/<int:item_id>/toggle', methods=['POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def toggle_item(item_id):
    db = get_db()
    item = db.execute('SELECT * FROM checklist_items WHERE id = ?', (item_id,)).fetchone()
    if not item:
        abort(404)
    new_status = 0 if item['is_active'] else 1
    db.execute('UPDATE checklist_items SET is_active = ? WHERE id = ?', (new_status, item_id))
    db.commit()
    flash('Item ' + ('activated.' if new_status else 'deactivated.'), 'success')
    return _redirect_manage_for_item(db, item_id)


@checklist_bp.route('/items/<int:item_id>/delete', methods=['POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def delete_item(item_id):
    db = get_db()
    item = db.execute('SELECT * FROM checklist_items WHERE id = ?', (item_id,)).fetchone()
    if not item:
        abort(404)
    in_use = db.execute(
        'SELECT COUNT(*) AS cnt FROM checklist_responses WHERE item_id = ?', (item_id,)
    ).fetchone()['cnt']

    if in_use > 0:
        flash(f'Cannot delete — {in_use} response(s) exist. Deactivate it instead.', 'danger')
        return _redirect_manage_for_item(db, item_id)

    db.execute('DELETE FROM checklist_item_branches WHERE item_id = ?', (item_id,))
    db.execute('DELETE FROM checklist_items WHERE id = ?', (item_id,))
    db.commit()
    flash('Item deleted.', 'success')
    return _redirect_manage_for_item(db, item_id)


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

@checklist_bp.route('/sections/from-library', methods=['POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def section_from_library():
    db = get_db()
    library_section_id = request.form.get('library_section_id', type=int)
    layer_template_id = request.form.get('template_id', type=int)
    context_branch_id = request.form.get('context_branch_id', type=int)

    if not library_section_id or not layer_template_id:
        flash('Library section and template layer are required.', 'danger')
        return redirect(url_for('checklist.manage_items'))

    lib = db.execute(
        'SELECT * FROM checklist_section_library WHERE id = ?', (library_section_id,)
    ).fetchone()
    if not lib:
        flash('Library section not found.', 'danger')
        return redirect(url_for('checklist.manage_items'))

    layer = db.execute(
        'SELECT * FROM checklist_templates WHERE id = ?', (layer_template_id,)
    ).fetchone()
    if not layer:
        flash('Invalid template layer.', 'danger')
        return redirect(url_for('checklist.manage_items'))

    scope = (layer['template_scope'] or 'legacy').lower()
    if scope in ('global', 'brand'):
        branch_id_val = None
    elif scope in ('branch', 'legacy'):
        branch_id_val = context_branch_id or layer['branch_id']
        if not branch_id_val:
            flash('Select a branch before adding from the library.', 'danger')
            return redirect(url_for('checklist.manage_items'))
    else:
        branch_id_val = context_branch_id

    max_order = db.execute(
        '''SELECT COALESCE(MAX(display_order), 0) FROM checklist_sections
           WHERE template_id = ? AND COALESCE(branch_id, -1) = COALESCE(?, -1)''',
        (layer_template_id, branch_id_val),
    ).fetchone()[0]

    cur = db.execute(
        '''INSERT INTO checklist_sections (template_id, branch_id, name, display_order)
           VALUES (?, ?, ?, ?)''',
        (layer_template_id, branch_id_val, lib['name'], max_order + 1),
    )
    new_sec_id = cur.lastrowid

    lib_items = db.execute(
        '''SELECT item_text, display_order FROM checklist_section_library_items
           WHERE library_section_id = ? ORDER BY display_order, id''',
        (library_section_id,),
    ).fetchall()
    for idx, li in enumerate(lib_items):
        db.execute(
            '''INSERT INTO checklist_items (template_id, section_id, item_text, display_order, created_by)
               VALUES (?, ?, ?, ?, ?)''',
            (layer_template_id, new_sec_id, li['item_text'], idx, current_user.id),
        )

    db.commit()
    flash(f'Section "{lib["name"]}" added from library.', 'success')
    root = get_root_template(db, layer_template_id)
    rid = root['id'] if root else layer_template_id
    nav_branch = fallback_branch_id(db, layer_template_id, context_branch_id)
    url_kw = dict(
        branch_id=nav_branch,
        template_id=rid,
        layer_id=layer_template_id,
    )
    _append_manage_scope_url_kwargs(db, url_kw, layer_template_id, request.form)
    return redirect(url_for('checklist.manage_items', **url_kw))


@checklist_bp.route('/sections/from-peer', methods=['POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def section_from_peer():
    """Copy a section + items from another branch / merged view into the current layer."""
    db = get_db()
    peer_section_id = request.form.get('peer_section_id', type=int)
    layer_template_id = request.form.get('template_id', type=int)
    context_branch_id = request.form.get('context_branch_id', type=int)
    root_tid = request.form.get('root_template_id', type=int)

    if not peer_section_id or not layer_template_id or not root_tid:
        flash('Pick a section to copy and try again.', 'danger')
        return redirect(url_for('checklist.manage_items'))

    src_sec = db.execute('SELECT * FROM checklist_sections WHERE id = ?', (peer_section_id,)).fetchone()
    if not src_sec:
        flash('That section no longer exists.', 'danger')
        return redirect(url_for('checklist.manage_items'))

    root_peer = get_root_template(db, src_sec['template_id'])
    if not root_peer or root_peer['id'] != root_tid:
        flash('That section does not belong to this checklist.', 'danger')
        return redirect(url_for('checklist.manage_items'))

    layer = db.execute(
        'SELECT * FROM checklist_templates WHERE id = ?', (layer_template_id,)
    ).fetchone()
    if not layer:
        flash('Invalid template layer.', 'danger')
        return redirect(url_for('checklist.manage_items'))

    scope = (layer['template_scope'] or 'legacy').lower()
    if scope in ('global', 'brand'):
        branch_id_val = None
    elif scope in ('branch', 'legacy'):
        branch_id_val = context_branch_id or layer['branch_id']
        if not branch_id_val:
            flash('Select a branch before copying a section here.', 'danger')
            return redirect(url_for('checklist.manage_items'))
    else:
        branch_id_val = context_branch_id

    dup = db.execute(
        '''SELECT id FROM checklist_sections
           WHERE template_id = ? AND name = ?
             AND COALESCE(branch_id, -1) = COALESCE(?, -1)''',
        (layer_template_id, src_sec['name'], branch_id_val),
    ).fetchone()
    if dup:
        flash(f'A section named "{src_sec["name"]}" already exists here.', 'danger')
        nav_branch = fallback_branch_id(db, layer_template_id, context_branch_id)
        rid = root_tid
        url_kw = dict(branch_id=nav_branch, template_id=rid, layer_id=layer_template_id)
        _append_manage_scope_url_kwargs(db, url_kw, layer_template_id, request.form)
        return redirect(url_for('checklist.manage_items', **url_kw))

    max_order = db.execute(
        '''SELECT COALESCE(MAX(display_order), 0) FROM checklist_sections
           WHERE template_id = ? AND COALESCE(branch_id, -1) = COALESCE(?, -1)''',
        (layer_template_id, branch_id_val),
    ).fetchone()[0]

    cur = db.execute(
        '''INSERT INTO checklist_sections (template_id, branch_id, name, display_order)
           VALUES (?, ?, ?, ?)''',
        (layer_template_id, branch_id_val, src_sec['name'], max_order + 1),
    )
    new_sec_id = cur.lastrowid

    items = db.execute(
        '''SELECT item_text, display_order FROM checklist_items
           WHERE section_id = ? AND COALESCE(is_active, 1) = 1
           ORDER BY display_order, id''',
        (peer_section_id,),
    ).fetchall()
    for idx, it in enumerate(items):
        db.execute(
            '''INSERT INTO checklist_items (template_id, section_id, item_text, display_order, created_by)
               VALUES (?, ?, ?, ?, ?)''',
            (layer_template_id, new_sec_id, it['item_text'], idx, current_user.id),
        )

    db.commit()
    flash(f'Section "{src_sec["name"]}" added from another branch.', 'success')
    nav_branch = fallback_branch_id(db, layer_template_id, context_branch_id)
    url_kw = dict(
        branch_id=nav_branch,
        template_id=root_tid,
        layer_id=layer_template_id,
    )
    _append_manage_scope_url_kwargs(db, url_kw, layer_template_id, request.form)
    return redirect(url_for('checklist.manage_items', **url_kw))


@checklist_bp.route('/sections/create', methods=['POST'])
@login_required
@role_required('qc_admin', 'it_admin')
def create_section():
    db = get_db()
    wants_json = request.method == 'POST' and _wants_ajax_json()
    name = request.form.get('name', '').strip()
    template_id = request.form.get('template_id', '')
    branch_id = request.form.get('branch_id', '')
    context_branch_id = request.form.get('context_branch_id', type=int)

    if not name or not template_id:
        if wants_json:
            return jsonify(ok=False, errors=['Section name is required.']), 400
        flash('Section name is required.', 'danger')
        return redirect(url_for('checklist.manage_items', branch_id=context_branch_id or None))

    layer = db.execute(
        'SELECT * FROM checklist_templates WHERE id = ?', (int(template_id),)
    ).fetchone()
    if not layer:
        if wants_json:
            return jsonify(ok=False, errors=['Invalid template layer.']), 400
        flash('Invalid template layer.', 'danger')
        return redirect(url_for('checklist.manage_items'))

    scope = (layer['template_scope'] or 'legacy').lower()
    if scope in ('global', 'brand'):
        branch_id_val = None
    elif scope in ('branch', 'legacy'):
        branch_id_val = context_branch_id or (int(branch_id) if branch_id else None) or layer['branch_id']
        if not branch_id_val:
            if wants_json:
                return jsonify(
                    ok=False,
                    errors=['Select a branch before adding a section to this layer.'],
                ), 400
            flash('Select a branch before adding a section to this layer.', 'danger')
            return redirect(url_for('checklist.manage_items'))
    else:
        branch_id_val = context_branch_id or (int(branch_id) if branch_id else None)

    existing = db.execute(
        '''SELECT id FROM checklist_sections
           WHERE template_id = ? AND name = ?
             AND COALESCE(branch_id, -1) = COALESCE(?, -1)''',
        (int(template_id), name, branch_id_val),
    ).fetchone()
    if existing:
        if wants_json:
            return jsonify(
                ok=False,
                errors=[f'A section named "{name}" already exists on this template layer.'],
            ), 400
        flash(f'A section named "{name}" already exists on this template layer.', 'danger')
        nav_branch = fallback_branch_id(db, int(template_id), context_branch_id)
        root = get_root_template(db, int(template_id))
        rid = root['id'] if root else int(template_id)
        url_kw = dict(
            branch_id=nav_branch,
            template_id=rid,
            layer_id=int(template_id),
        )
        _append_manage_scope_url_kwargs(db, url_kw, int(template_id), request.form)
        return redirect(url_for('checklist.manage_items', **url_kw))

    max_order = db.execute(
        '''SELECT COALESCE(MAX(display_order), 0) FROM checklist_sections
           WHERE template_id = ? AND COALESCE(branch_id, -1) = COALESCE(?, -1)''',
        (int(template_id), branch_id_val),
    ).fetchone()[0]

    cur = db.execute(
        'INSERT INTO checklist_sections (template_id, branch_id, name, display_order) VALUES (?, ?, ?, ?)',
        (int(template_id), branch_id_val, name, max_order + 1),
    )
    new_id = cur.lastrowid
    db.commit()
    new_sec = db.execute('SELECT * FROM checklist_sections WHERE id = ?', (new_id,)).fetchone()

    nav_branch = fallback_branch_id(db, int(template_id), context_branch_id)
    root = get_root_template(db, int(template_id))
    rid = root['id'] if root else int(template_id)
    url_kw = dict(
        branch_id=nav_branch,
        template_id=rid,
        layer_id=int(template_id),
    )
    _append_manage_scope_url_kwargs(db, url_kw, int(template_id), request.form)

    if wants_json:
        html = render_template(
            'checklist/_manage_section_card.html',
            sec_entry={'section': new_sec, 'items': []},
            root_template_id=rid,
            selected_branch_id=nav_branch,
            company_wide=request.form.get('company_wide') == '1',
            all_brand=request.form.get('all_brand') == '1',
        )
        return jsonify(
            ok=True,
            html=html,
            section_id=new_id,
            message=f'Section "{name}" added.',
        )

    flash(f'Section "{name}" added.', 'success')
    return redirect(url_for('checklist.manage_items', **url_kw))


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
        return _redirect_manage_for_section(db, section)

    db.execute('UPDATE checklist_sections SET name = ? WHERE id = ?', (name, section_id))
    db.commit()
    flash('Section updated.', 'success')
    return _redirect_manage_for_section(db, section)


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

    if item_count > 0:
        flash(f'Cannot delete "{section["name"]}" — it has {item_count} active item(s). Deactivate them first.', 'danger')
        return _redirect_manage_for_section(db, section)

    db.execute('DELETE FROM checklist_items WHERE section_id = ?', (section_id,))
    db.execute('DELETE FROM checklist_sections WHERE id = ?', (section_id,))
    db.commit()
    flash(f'Section "{section["name"]}" deleted.', 'success')
    return _redirect_manage_for_section(db, section)
