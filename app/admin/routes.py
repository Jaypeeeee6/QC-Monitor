import sqlite3

from flask import render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash

from app.admin import admin_bp
from app.checklist.effective import (
    clone_template_structure,
    get_root_template,
    list_branch_manager_templates,
    reconcile_child_layers_after_root_scope_change,
)
from app.db import get_db
from app.utils import role_required, local_today


def _wants_modal_json():
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _checklist_template_form_prefill():
    """Preserve form state when re-rendering after validation or DB errors."""
    return {
        'name': (request.form.get('name') or '').strip(),
        'description': (request.form.get('description') or '').strip(),
        'template_scope': (request.form.get('template_scope') or 'global').strip().lower(),
        'brand_id': request.form.get('brand_id', type=int),
        'template_status': (request.form.get('template_status') or 'active').strip().lower(),
        'start_from': (request.form.get('start_from') or 'blank').strip(),
        'copy_source_id': request.form.get('copy_source_id', type=int),
        'copy_branch_id': request.form.get('copy_branch_id', type=int),
    }


@admin_bp.route('/users')
@login_required
@role_required('it_admin')
def users():
    db = get_db()

    filter_role = request.args.get('role', '')
    filter_branch = request.args.get('branch_id', '')
    filter_search = request.args.get('search', '').strip()

    query = '''
        SELECT u.*, b.name as branch_name
        FROM users u
        LEFT JOIN branches b ON b.id = u.branch_id
        WHERE 1=1
    '''
    params = []

    if filter_search:
        query += ' AND (u.full_name LIKE ? OR u.email LIKE ?)'
        params.extend([f'%{filter_search}%', f'%{filter_search}%'])

    if filter_role:
        query += ' AND u.role = ?'
        params.append(filter_role)

    if filter_branch:
        query += ' AND u.branch_id = ?'
        params.append(filter_branch)

    query += ' ORDER BY u.role, u.full_name'

    all_users = db.execute(query, params).fetchall()
    branches = db.execute('SELECT id, name FROM branches ORDER BY name').fetchall()

    return render_template(
        'admin/users.html',
        users=all_users,
        branches=branches,
        filter_role=filter_role,
        filter_branch=filter_branch,
    )


@admin_bp.route('/users/create', methods=['GET', 'POST'])
@login_required
@role_required('it_admin')
def create_user():
    db = get_db()
    branches = db.execute(
        '''SELECT b.id, b.name, br.name as brand_name
           FROM branches b LEFT JOIN brands br ON br.id = b.brand_id
           ORDER BY br.name, b.name'''
    ).fetchall()

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        role = request.form.get('role', '')
        branch_id = request.form.get('branch_id', '') or None

        errors = []

        valid_roles = ('branch_manager', 'qc_admin', 'management', 'it_admin')
        if not full_name:
            errors.append('Full name is required.')
        if not email or '@' not in email:
            errors.append('A valid email address is required.')
        if not password or len(password) < 6:
            errors.append('Password must be at least 6 characters.')
        if password != confirm_password:
            errors.append('Passwords do not match.')
        if role not in valid_roles:
            errors.append('Please select a valid role.')
        if role == 'branch_manager' and not branch_id:
            errors.append('Branch is required for Branch Manager accounts.')

        if not errors:
            existing = db.execute(
                'SELECT id FROM users WHERE email = ?', (email,)
            ).fetchone()
            if existing:
                errors.append('This email address is already registered.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('admin/create_user.html', branches=branches)

        username = email  # use email as internal username
        db.execute(
            '''INSERT INTO users (full_name, email, username, password_hash, role, branch_id)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (full_name, email, username, generate_password_hash(password),
             role, int(branch_id) if branch_id else None)
        )
        db.commit()
        flash(f'Account for {full_name} created successfully.', 'success')
        return redirect(url_for('admin.users'))

    return render_template('admin/create_user.html', branches=branches)


@admin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('it_admin')
def edit_user(user_id):
    db = get_db()
    user = db.execute(
        'SELECT * FROM users WHERE id = ?', (user_id,)
    ).fetchone()

    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin.users'))

    branches = db.execute(
        '''SELECT b.id, b.name, br.name as brand_name
           FROM branches b LEFT JOIN brands br ON br.id = b.brand_id
           ORDER BY br.name, b.name'''
    ).fetchall()

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        role = request.form.get('role', '')
        branch_id = request.form.get('branch_id', '') or None
        is_active = 1 if request.form.get('is_active') == 'on' else 0
        new_password = request.form.get('new_password', '').strip()
        confirm_new_password = request.form.get('confirm_new_password', '').strip()

        errors = []

        valid_roles = ('branch_manager', 'qc_admin', 'management', 'it_admin')
        if not full_name:
            errors.append('Full name is required.')
        if not email or '@' not in email:
            errors.append('A valid email address is required.')
        if role not in valid_roles:
            errors.append('Please select a valid role.')
        if role == 'branch_manager' and not branch_id:
            errors.append('Branch is required for Branch Manager accounts.')
        if new_password and len(new_password) < 6:
            errors.append('New password must be at least 6 characters.')
        if new_password and new_password != confirm_new_password:
            errors.append('New passwords do not match.')

        if not errors:
            conflict = db.execute(
                'SELECT id FROM users WHERE email = ? AND id != ?',
                (email, user_id)
            ).fetchone()
            if conflict:
                errors.append('This email address is already used by another account.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('admin/edit_user.html', user=user, branches=branches)

        if new_password:
            db.execute(
                '''UPDATE users SET full_name=?, email=?, username=?, role=?,
                   branch_id=?, is_active=?, password_hash=? WHERE id=?''',
                (full_name, email, email, role,
                 int(branch_id) if branch_id else None, is_active,
                 generate_password_hash(new_password), user_id)
            )
        else:
            db.execute(
                '''UPDATE users SET full_name=?, email=?, username=?, role=?,
                   branch_id=?, is_active=? WHERE id=?''',
                (full_name, email, email, role,
                 int(branch_id) if branch_id else None, is_active, user_id)
            )

        db.commit()
        flash(f'{full_name}\'s account updated successfully.', 'success')
        return redirect(url_for('admin.users'))

    return render_template('admin/edit_user.html', user=user, branches=branches)


@admin_bp.route('/users/<int:user_id>/toggle', methods=['POST'])
@login_required
@role_required('it_admin')
def toggle_user(user_id):
    if user_id == current_user.id:
        flash('You cannot deactivate your own account.', 'danger')
        return redirect(url_for('admin.users'))

    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin.users'))

    new_status = 0 if user['is_active'] else 1
    db.execute('UPDATE users SET is_active = ? WHERE id = ?', (new_status, user_id))
    db.commit()

    status_text = 'activated' if new_status else 'deactivated'
    flash(f'{user["full_name"]}\'s account has been {status_text}.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@role_required('it_admin')
def delete_user(user_id):
    if user_id == current_user.id:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('admin.users'))

    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin.users'))

    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()
    flash(f'{user["full_name"]}\'s account has been permanently deleted.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/branches')
@login_required
@role_required('it_admin', 'qc_admin')
def branches():
    db = get_db()
    brands = db.execute('SELECT * FROM brands ORDER BY name').fetchall()
    all_branches = db.execute(
        '''SELECT b.*, br.name as brand_name, COUNT(u.id) as manager_count
           FROM branches b
           LEFT JOIN brands br ON br.id = b.brand_id
           LEFT JOIN users u ON u.branch_id = b.id AND u.role = 'branch_manager' AND u.is_active = 1
           GROUP BY b.id ORDER BY br.name, b.name'''
    ).fetchall()
    return render_template('admin/branches.html', brands=brands, branches=all_branches)


@admin_bp.route('/branches/<int:branch_id>/dashboard')
@login_required
@role_required('it_admin', 'qc_admin')
def branch_dashboard(branch_id):
    db = get_db()
    today = local_today()
    tab = request.args.get('tab', 'snapshot')
    if tab not in ('snapshot', 'checklists', 'reports'):
        tab = 'snapshot'

    branch = db.execute(
        '''SELECT b.*, br.name AS brand_name
           FROM branches b
           LEFT JOIN brands br ON br.id = b.brand_id
           WHERE b.id = ?''',
        (branch_id,)
    ).fetchone()
    if not branch:
        abort(404)

    # --- Today snapshot data ---
    checklist_today = db.execute(
        '''SELECT cs.id, cs.submission_date, cs.submitted_at,
                  u.full_name AS submitted_by_name,
                  COUNT(cr.id) AS total,
                  SUM(CASE WHEN cr.answer = 'yes' THEN 1 ELSE 0 END) AS yes_count,
                  s.score
           FROM checklist_submissions cs
           JOIN users u ON u.id = cs.submitted_by
           LEFT JOIN checklist_responses cr ON cr.submission_id = cs.id
           LEFT JOIN scores s ON s.submission_id = cs.id
           WHERE cs.branch_id = ? AND cs.submission_date = ?
           GROUP BY cs.id
           ORDER BY cs.submitted_at DESC
           LIMIT 1''',
        (branch_id, today)
    ).fetchone()

    report_today = db.execute(
        '''SELECT dr.id, dr.report_date, dr.created_at, dr.subject,
                  dr.is_read,
                  u.full_name AS submitted_by_name,
                  (SELECT COUNT(*) FROM report_replies rr WHERE rr.report_id = dr.id) AS reply_count
           FROM daily_reports dr
           JOIN users u ON u.id = dr.submitted_by
           WHERE dr.branch_id = ? AND dr.report_date = ?
           ORDER BY dr.created_at DESC
           LIMIT 1''',
        (branch_id, today)
    ).fetchone()

    active_template_count = len(list_branch_manager_templates(db, branch_id))
    submitted_template_count = db.execute(
        '''SELECT COUNT(DISTINCT template_id)
           FROM checklist_submissions
           WHERE branch_id = ? AND submission_date = ?''',
        (branch_id, today)
    ).fetchone()[0]

    checklist_missing_today = (
        active_template_count > 0 and submitted_template_count < active_template_count
    )
    report_missing_today = report_today is None

    # --- Checklist history ---
    checklist_from = request.args.get('c_from', '')
    checklist_to = request.args.get('c_to', '')
    checklist_user = request.args.get('c_user', type=int)
    checklist_page = request.args.get('c_page', 1, type=int)
    checklist_per_page = 12
    checklist_offset = max(checklist_page - 1, 0) * checklist_per_page

    checklist_where = ['cs.branch_id = ?']
    checklist_params = [branch_id]

    if checklist_from:
        checklist_where.append('cs.submission_date >= ?')
        checklist_params.append(checklist_from)
    if checklist_to:
        checklist_where.append('cs.submission_date <= ?')
        checklist_params.append(checklist_to)
    if checklist_user is not None:
        checklist_where.append('cs.submitted_by = ?')
        checklist_params.append(checklist_user)

    checklist_where_sql = ' AND '.join(checklist_where)
    checklist_count = db.execute(
        f'''SELECT COUNT(*) FROM checklist_submissions cs
            WHERE {checklist_where_sql}''',
        checklist_params
    ).fetchone()[0]

    checklist_rows = db.execute(
        f'''SELECT cs.id, cs.submission_date, cs.submitted_at,
                   u.full_name AS submitted_by_name,
                   COUNT(cr.id) AS total,
                   SUM(CASE WHEN cr.answer = 'yes' THEN 1 ELSE 0 END) AS yes_count,
                   s.score,
                   (SELECT COUNT(*) FROM comments c WHERE c.submission_id = cs.id) AS comment_count
            FROM checklist_submissions cs
            JOIN users u ON u.id = cs.submitted_by
            LEFT JOIN checklist_responses cr ON cr.submission_id = cs.id
            LEFT JOIN scores s ON s.submission_id = cs.id
            WHERE {checklist_where_sql}
            GROUP BY cs.id
            ORDER BY cs.submission_date DESC, cs.submitted_at DESC
            LIMIT ? OFFSET ?''',
        checklist_params + [checklist_per_page, checklist_offset]
    ).fetchall()
    checklist_total_pages = (checklist_count + checklist_per_page - 1) // checklist_per_page

    # --- Report history ---
    report_from = request.args.get('r_from', '')
    report_to = request.args.get('r_to', '')
    report_user = request.args.get('r_user', type=int)
    report_q = request.args.get('r_q', '').strip()
    report_page = request.args.get('r_page', 1, type=int)
    report_per_page = 12
    report_offset = max(report_page - 1, 0) * report_per_page

    report_where = ['dr.branch_id = ?']
    report_params = [branch_id]

    if report_from:
        report_where.append('dr.report_date >= ?')
        report_params.append(report_from)
    if report_to:
        report_where.append('dr.report_date <= ?')
        report_params.append(report_to)
    if report_user is not None:
        report_where.append('dr.submitted_by = ?')
        report_params.append(report_user)
    if report_q:
        report_where.append('(dr.subject LIKE ? OR dr.body LIKE ?)')
        like = f'%{report_q}%'
        report_params.extend([like, like])

    report_where_sql = ' AND '.join(report_where)
    report_count = db.execute(
        f'''SELECT COUNT(*) FROM daily_reports dr
            WHERE {report_where_sql}''',
        report_params
    ).fetchone()[0]

    report_rows = db.execute(
        f'''SELECT dr.id, dr.report_date, dr.created_at, dr.subject, dr.is_read,
                   u.full_name AS submitted_by_name,
                   (SELECT COUNT(*) FROM report_replies rr WHERE rr.report_id = dr.id) AS reply_count
            FROM daily_reports dr
            JOIN users u ON u.id = dr.submitted_by
            WHERE {report_where_sql}
            ORDER BY dr.report_date DESC, dr.created_at DESC
            LIMIT ? OFFSET ?''',
        report_params + [report_per_page, report_offset]
    ).fetchall()
    report_total_pages = (report_count + report_per_page - 1) // report_per_page

    branch_managers = db.execute(
        '''SELECT id, full_name
           FROM users
           WHERE role = 'branch_manager' AND is_active = 1 AND branch_id = ?
           ORDER BY full_name''',
        (branch_id,)
    ).fetchall()

    return render_template(
        'admin/branch_dashboard.html',
        tab=tab,
        branch=branch,
        today=today,
        checklist_today=checklist_today,
        report_today=report_today,
        checklist_missing_today=checklist_missing_today,
        report_missing_today=report_missing_today,
        branch_managers=branch_managers,
        checklist_rows=checklist_rows,
        checklist_from=checklist_from,
        checklist_to=checklist_to,
        checklist_user=checklist_user,
        checklist_page=checklist_page,
        checklist_total_pages=checklist_total_pages,
        report_rows=report_rows,
        report_from=report_from,
        report_to=report_to,
        report_user=report_user,
        report_q=report_q,
        report_page=report_page,
        report_total_pages=report_total_pages,
    )


@admin_bp.route('/brands/create', methods=['POST'])
@login_required
@role_required('it_admin', 'qc_admin')
def create_brand():
    db = get_db()
    name = request.form.get('name', '').strip()
    if not name:
        flash('Brand name is required.', 'danger')
        return redirect(url_for('admin.branches'))
    existing = db.execute('SELECT id FROM brands WHERE name = ?', (name,)).fetchone()
    if existing:
        flash(f'Brand "{name}" already exists.', 'danger')
        return redirect(url_for('admin.branches'))
    db.execute('INSERT INTO brands (name) VALUES (?)', (name,))
    db.commit()
    flash(f'Brand "{name}" created.', 'success')
    return redirect(url_for('admin.branches'))


@admin_bp.route('/brands/<int:brand_id>/delete', methods=['POST'])
@login_required
@role_required('it_admin', 'qc_admin')
def delete_brand(brand_id):
    db = get_db()
    brand = db.execute('SELECT * FROM brands WHERE id = ?', (brand_id,)).fetchone()
    if not brand:
        flash('Brand not found.', 'danger')
        return redirect(url_for('admin.branches'))
    count = db.execute('SELECT COUNT(*) FROM branches WHERE brand_id = ?', (brand_id,)).fetchone()[0]
    if count > 0:
        flash(f'Cannot delete "{brand["name"]}" — it has {count} branch(es). Remove them first.', 'danger')
        return redirect(url_for('admin.branches'))
    db.execute('DELETE FROM brands WHERE id = ?', (brand_id,))
    db.commit()
    flash(f'Brand "{brand["name"]}" deleted.', 'success')
    return redirect(url_for('admin.branches'))


@admin_bp.route('/branches/create', methods=['POST'])
@login_required
@role_required('it_admin', 'qc_admin')
def create_branch():
    db = get_db()
    name = request.form.get('name', '').strip()
    location = request.form.get('location', '').strip()
    brand_id = request.form.get('brand_id', '') or None

    if not name:
        flash('Branch name is required.', 'danger')
        return redirect(url_for('admin.branches'))

    existing = db.execute('SELECT id FROM branches WHERE name = ?', (name,)).fetchone()
    if existing:
        flash(f'A branch named "{name}" already exists.', 'danger')
        return redirect(url_for('admin.branches'))

    db.execute(
        'INSERT INTO branches (name, location, brand_id) VALUES (?, ?, ?)',
        (name, location or None, int(brand_id) if brand_id else None)
    )
    db.commit()
    flash(f'Branch "{name}" created successfully.', 'success')
    return redirect(url_for('admin.branches'))


def _delete_template_layer(db, template_id):
    """Remove sections and items for one checklist_templates row (no child rows)."""
    sections = db.execute('SELECT id FROM checklist_sections WHERE template_id = ?', (template_id,)).fetchall()
    for s in sections:
        sid = s['id']
        for it in db.execute('SELECT id FROM checklist_items WHERE section_id = ?', (sid,)):
            iid = it['id']
            db.execute('DELETE FROM checklist_item_branches WHERE item_id = ?', (iid,))
            db.execute('DELETE FROM checklist_items WHERE id = ?', (iid,))
        db.execute('DELETE FROM checklist_sections WHERE id = ?', (sid,))
    db.execute('DELETE FROM checklist_items WHERE template_id = ? AND section_id IS NULL', (template_id,))


def _delete_template_family(db, root_id):
    """Delete deepest children first, then root."""
    children = db.execute(
        'SELECT id FROM checklist_templates WHERE parent_template_id = ?', (root_id,)
    ).fetchall()
    for c in children:
        _delete_template_family(db, c['id'])
    _delete_template_layer(db, root_id)
    db.execute('DELETE FROM checklist_templates WHERE id = ?', (root_id,))


@admin_bp.route('/checklist-templates')
@login_required
@role_required('it_admin', 'qc_admin')
def checklist_templates():
    db = get_db()
    templates = db.execute(
        '''SELECT ct.*,
                  (SELECT COUNT(*) FROM checklist_items ci
                     JOIN checklist_sections cs ON cs.id = ci.section_id
                     WHERE cs.template_id IN (
                        SELECT id FROM checklist_templates t2
                        WHERE t2.id = ct.id OR t2.root_template_id = ct.id
                     )
                  ) AS item_count,
                  (SELECT COUNT(*) FROM checklist_templates t3
                     WHERE t3.root_template_id = ct.id AND t3.id != ct.id
                  ) AS tree_extra
           FROM checklist_templates ct
           WHERE ct.parent_template_id IS NULL
           ORDER BY ct.id'''
    ).fetchall()
    brand_rows = db.execute(
        'SELECT id, name FROM brands WHERE is_active = 1 ORDER BY name'
    ).fetchall()
    brands_json = [{'id': b['id'], 'name': b['name']} for b in brand_rows]
    return render_template(
        'admin/checklist_templates.html',
        templates=templates,
        brands_json=brands_json,
    )


@admin_bp.route('/checklist-templates/new', methods=['GET', 'POST'])
@login_required
@role_required('it_admin', 'qc_admin')
def checklist_template_create():
    db = get_db()
    brands = db.execute('SELECT id, name FROM brands WHERE is_active = 1 ORDER BY name').fetchall()
    branches = db.execute(
        '''SELECT b.id, b.name, br.name AS brand_name
           FROM branches b LEFT JOIN brands br ON br.id = b.brand_id
           ORDER BY br.name, b.name'''
    ).fetchall()
    dup_from = request.args.get('duplicate_from', type=int)
    if dup_from:
        src_root = db.execute(
            '''SELECT id, name FROM checklist_templates
               WHERE id = ? AND parent_template_id IS NULL''',
            (dup_from,),
        ).fetchone()
        if not src_root:
            dup_from = None
    else:
        src_root = None

    if dup_from:
        all_templates = db.execute(
            '''SELECT id, name, template_scope, parent_template_id FROM checklist_templates
               WHERE is_active = 1 OR id = ?
               ORDER BY name''',
            (dup_from,),
        ).fetchall()
    else:
        all_templates = db.execute(
            '''SELECT id, name, template_scope, parent_template_id FROM checklist_templates
               WHERE is_active = 1 ORDER BY name'''
        ).fetchall()

    suggested_name = f'{src_root["name"]} (copy)' if src_root else ''
    duplicate_source_id = dup_from if src_root else None

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        template_scope = request.form.get('template_scope', 'global').strip().lower()
        brand_id = request.form.get('brand_id', type=int)
        branch_id = request.form.get('branch_id', type=int)
        parent_template_id = request.form.get('parent_template_id', type=int)
        template_status = request.form.get('template_status', 'active').strip().lower() or 'active'
        start_from = request.form.get('start_from', 'blank').strip()
        copy_source_id = request.form.get('copy_source_id', type=int)
        copy_branch_id = request.form.get('copy_branch_id', type=int)

        errors = []
        if not name:
            errors.append('Template name is required.')
        if template_scope not in ('global', 'brand'):
            errors.append('Invalid template scope.')
        if template_status not in ('draft', 'active'):
            errors.append('Invalid status.')

        parent_template_id = None
        if template_scope == 'global':
            brand_id = None
            branch_id = None
        elif template_scope == 'brand':
            branch_id = None
            if not brand_id:
                errors.append('Select a brand for a brand-scoped template.')

        if start_from == 'copy_selected' and not copy_source_id:
            errors.append('Select a template to copy from, or choose “Blank”.')
        if name:
            name_taken = db.execute(
                'SELECT id FROM checklist_templates WHERE name = ?',
                (name,),
            ).fetchone()
            if name_taken:
                errors.append(
                    'A template with this name already exists. Please choose a different name.'
                )

        if errors:
            fc = request.form.get('duplicate_from_context', type=int)
            if not fc:
                fc = request.args.get('duplicate_from', type=int)
            if fc:
                src_fix = db.execute(
                    '''SELECT id, name FROM checklist_templates
                       WHERE id = ? AND parent_template_id IS NULL''',
                    (fc,),
                ).fetchone()
                if src_fix:
                    duplicate_source_id = fc
                    suggested_name = f'{src_fix["name"]} (copy)'
                    all_templates = db.execute(
                        '''SELECT id, name, template_scope, parent_template_id
                           FROM checklist_templates
                           WHERE is_active = 1 OR id = ?
                           ORDER BY name''',
                        (fc,),
                    ).fetchall()
            if _wants_modal_json():
                return jsonify(ok=False, errors=errors), 400
            return render_template(
                'admin/checklist_template_form.html',
                brands=brands,
                branches=branches,
                all_templates=all_templates,
                duplicate_source_id=duplicate_source_id,
                suggested_name=suggested_name,
                template_errors=errors,
                form_prefill=_checklist_template_form_prefill(),
                is_edit=False,
                tree_extra=0,
            )

        try:
            cur = db.execute(
                '''INSERT INTO checklist_templates
                   (name, description, is_active, parent_template_id, template_scope,
                    brand_id, branch_id, template_status, version)
                   VALUES (?, ?, 1, ?, ?, ?, ?, ?, 1)''',
                (
                    name,
                    description or None,
                    parent_template_id,
                    template_scope,
                    brand_id,
                    branch_id,
                    template_status,
                ),
            )
            new_id = cur.lastrowid
            root_row = get_root_template(db, new_id)
            root_id = root_row['id'] if root_row else new_id
            db.execute(
                'UPDATE checklist_templates SET root_template_id = ? WHERE id = ?',
                (root_id, new_id),
            )

            do_copy = start_from in ('copy_global', 'copy_brand', 'copy_branch', 'copy_selected')
            source_tid = copy_source_id if do_copy and copy_source_id else parent_template_id
            if do_copy and source_tid:
                src = db.execute('SELECT * FROM checklist_templates WHERE id = ?', (source_tid,)).fetchone()
                if src:
                    clone_template_structure(db, source_tid, new_id, None, source_branch_id=None)

            db.commit()
        except sqlite3.IntegrityError as exc:
            db.rollback()
            msg = str(exc).lower()
            if 'checklist_templates.name' in msg:
                db_errors = [
                    'A template with this name already exists. Please choose a different name.'
                ]
            else:
                db_errors = [
                    'This template could not be saved because it conflicts with existing data. '
                    'Please check your choices and try again.'
                ]
            if _wants_modal_json():
                return jsonify(ok=False, errors=db_errors), 400
            return render_template(
                'admin/checklist_template_form.html',
                brands=brands,
                branches=branches,
                all_templates=all_templates,
                duplicate_source_id=duplicate_source_id,
                suggested_name=suggested_name,
                template_errors=db_errors,
                form_prefill=_checklist_template_form_prefill(),
                is_edit=False,
                tree_extra=0,
            )

        manage_kw = {'template_id': root_id}
        if template_scope == 'global':
            manage_kw['company_wide'] = 1
            manage_kw['layer_id'] = root_id
            if branches:
                manage_kw['branch_id'] = branches[0]['id']
        elif template_scope == 'brand' and brand_id:
            br_first = db.execute(
                'SELECT id FROM branches WHERE brand_id = ? ORDER BY name LIMIT 1',
                (brand_id,),
            ).fetchone()
            manage_kw['all_brand'] = 1
            manage_kw['layer_id'] = root_id
            if br_first:
                manage_kw['branch_id'] = br_first['id']

        if _wants_modal_json():
            return jsonify(
                ok=True,
                redirect_url=url_for('checklist.manage_items', **manage_kw),
            )
        flash(
            f'Template "{name}" created. Use the checklist editor to add sections, or switch layers when this template inherits from another.',
            'success',
        )
        return redirect(url_for('checklist.manage_items', **manage_kw))

    return render_template(
        'admin/checklist_template_form.html',
        brands=brands,
        branches=branches,
        all_templates=all_templates,
        duplicate_source_id=duplicate_source_id,
        suggested_name=suggested_name,
        template_errors=[],
        form_prefill={},
        is_edit=False,
        tree_extra=0,
    )


@admin_bp.route('/checklist-templates/<int:template_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('it_admin', 'qc_admin')
def checklist_template_edit(template_id):
    """Edit root template metadata (scope, brand, name, status)."""
    db = get_db()
    row = db.execute(
        '''SELECT * FROM checklist_templates
           WHERE id = ? AND parent_template_id IS NULL''',
        (template_id,),
    ).fetchone()
    if not row:
        abort(404)

    brands = db.execute('SELECT id, name FROM brands WHERE is_active = 1 ORDER BY name').fetchall()
    branches = db.execute(
        '''SELECT b.id, b.name, br.name AS brand_name
           FROM branches b LEFT JOIN brands br ON br.id = b.brand_id
           ORDER BY br.name, b.name'''
    ).fetchall()
    all_templates = db.execute(
        '''SELECT id, name, template_scope, parent_template_id FROM checklist_templates
           WHERE is_active = 1 ORDER BY name'''
    ).fetchall()

    root_id = row['id']
    tree_extra = db.execute(
        '''SELECT COUNT(*) AS c FROM checklist_templates
           WHERE root_template_id = ? AND id != ?''',
        (root_id, root_id),
    ).fetchone()['c']

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        template_scope = request.form.get('template_scope', 'global').strip().lower()
        brand_id = request.form.get('brand_id', type=int)
        branch_id = request.form.get('branch_id', type=int)
        parent_template_id = request.form.get('parent_template_id', type=int)
        template_status = request.form.get('template_status', 'active').strip().lower() or 'active'

        errors = []
        if not name:
            errors.append('Template name is required.')
        if template_scope not in ('global', 'brand'):
            errors.append('Invalid template scope.')
        if template_status not in ('draft', 'active'):
            errors.append('Invalid status.')

        parent_template_id = None
        if template_scope == 'global':
            brand_id = None
            branch_id = None
        elif template_scope == 'brand':
            branch_id = None
            if not brand_id:
                errors.append('Select a brand for a brand-scoped template.')

        if name and name != row['name']:
            name_taken = db.execute(
                'SELECT id FROM checklist_templates WHERE name = ? AND id != ?',
                (name, root_id),
            ).fetchone()
            if name_taken:
                errors.append(
                    'A template with this name already exists. Please choose a different name.'
                )

        if errors:
            if _wants_modal_json():
                return jsonify(ok=False, errors=errors), 400
            return render_template(
                'admin/checklist_template_form.html',
                brands=brands,
                branches=branches,
                all_templates=all_templates,
                duplicate_source_id=None,
                suggested_name='',
                template_errors=errors,
                form_prefill=_checklist_template_form_prefill(),
                is_edit=True,
                edit_template_id=root_id,
                tree_extra=tree_extra,
            )

        db.execute(
            '''UPDATE checklist_templates SET name = ?, description = ?, template_scope = ?,
                   brand_id = ?, branch_id = ?, parent_template_id = ?, template_status = ?
               WHERE id = ?''',
            (
                name,
                description or None,
                template_scope,
                brand_id,
                branch_id,
                parent_template_id,
                template_status,
                root_id,
            ),
        )
        n_reconciled = reconcile_child_layers_after_root_scope_change(db, root_id)
        db.commit()
        msg = f'Template "{name}" was updated.'
        if n_reconciled:
            msg += (
                f' {n_reconciled} linked layer(s) that no longer match this scope were hidden from '
                'new checklists. Past submissions are unchanged.'
            )
        flash(msg, 'success')
        return redirect(url_for('admin.checklist_templates'))

    raw_scope = (row['template_scope'] or 'global').lower()
    pref_scope = raw_scope
    pref_brand = row['brand_id']
    if raw_scope not in ('global', 'brand'):
        if raw_scope == 'branch' and row['branch_id']:
            br = db.execute(
                'SELECT brand_id FROM branches WHERE id = ?', (row['branch_id'],)
            ).fetchone()
            pref_scope = 'brand'
            pref_brand = br['brand_id'] if br and br['brand_id'] is not None else None
        else:
            pref_scope = 'global'
            pref_brand = None

    form_prefill = {
        'name': row['name'] or '',
        'description': (row['description'] or '').strip(),
        'template_scope': pref_scope,
        'brand_id': pref_brand,
        'template_status': (row['template_status'] or 'active').lower(),
        'start_from': 'blank',
    }
    return render_template(
        'admin/checklist_template_form.html',
        brands=brands,
        branches=branches,
        all_templates=all_templates,
        duplicate_source_id=None,
        suggested_name='',
        template_errors=[],
        form_prefill=form_prefill,
        is_edit=True,
        edit_template_id=root_id,
        tree_extra=tree_extra,
    )


@admin_bp.route('/checklist-templates/<int:template_id>/scope', methods=['POST'])
@login_required
@role_required('it_admin', 'qc_admin')
def checklist_template_quick_scope(template_id):
    """Update root template scope from the templates list (JSON + CSRF header)."""
    data = request.get_json(silent=True) or {}
    template_scope = (data.get('template_scope') or '').strip().lower()
    if template_scope not in ('global', 'brand'):
        return jsonify(ok=False, errors=['Invalid template scope.']), 400

    def _int_or_none(key):
        v = data.get(key)
        if v is None or v == '':
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    brand_id_in = _int_or_none('brand_id')

    db = get_db()
    row = db.execute(
        '''SELECT * FROM checklist_templates
           WHERE id = ? AND parent_template_id IS NULL''',
        (template_id,),
    ).fetchone()
    if not row:
        return jsonify(ok=False, errors=['Template not found.']), 404

    root_id = row['id']

    parent_template_id = None
    if template_scope == 'global':
        brand_id = None
        branch_id = None
    else:  # brand
        branch_id = None
        brand_id = brand_id_in or row['brand_id']
        if not brand_id:
            return jsonify(ok=False, errors=['Select a brand for a brand-scoped template.']), 400

    db.execute(
        '''UPDATE checklist_templates SET template_scope = ?,
               brand_id = ?, branch_id = ?, parent_template_id = ?
           WHERE id = ?''',
        (template_scope, brand_id, branch_id, parent_template_id, root_id),
    )
    reconcile_child_layers_after_root_scope_change(db, root_id)
    db.commit()
    return jsonify(ok=True, template_scope=template_scope)


@admin_bp.route('/checklist-templates/<int:template_id>/toggle-active', methods=['POST'])
@login_required
@role_required('it_admin', 'qc_admin')
def toggle_checklist_template_active(template_id):
    db = get_db()
    row = db.execute(
        '''SELECT * FROM checklist_templates
           WHERE id = ? AND parent_template_id IS NULL''',
        (template_id,),
    ).fetchone()
    if not row:
        flash('Only root checklist templates can be activated or deactivated from this page.', 'danger')
        return redirect(url_for('admin.checklist_templates'))

    root_id = row['id']
    new_active = 0 if row['is_active'] else 1
    db.execute(
        '''UPDATE checklist_templates SET is_active = ?
           WHERE id = ? OR root_template_id = ?''',
        (new_active, root_id, root_id),
    )
    db.commit()
    if new_active:
        flash(f'"{row["name"]}" and its layers are now active.', 'success')
    else:
        flash(f'"{row["name"]}" and its layers are now inactive (hidden from branch managers).', 'success')
    return redirect(url_for('admin.checklist_templates'))


@admin_bp.route('/checklist-templates/<int:template_id>/delete', methods=['POST'])
@login_required
@role_required('it_admin', 'qc_admin')
def delete_checklist_template(template_id):
    db = get_db()
    template = db.execute(
        'SELECT * FROM checklist_templates WHERE id = ?', (template_id,)
    ).fetchone()

    if not template:
        flash('Template not found.', 'danger')
        return redirect(url_for('admin.checklist_templates'))

    root = get_root_template(db, template_id)
    if root['id'] != template_id:
        flash('Open the parent checklist card to delete an entire template family.', 'warning')
        return redirect(url_for('admin.checklist_templates'))

    in_use = db.execute(
        'SELECT COUNT(*) FROM checklist_submissions WHERE template_id = ?', (root['id'],)
    ).fetchone()[0]

    if in_use > 0:
        flash(
            f'Cannot delete "{template["name"]}" — it has {in_use} submission(s) on record. '
            'Deactivate it instead.',
            'danger',
        )
        return redirect(url_for('admin.checklist_templates'))

    _delete_template_family(db, root['id'])
    db.commit()
    flash(f'Template "{template["name"]}" and its layers were deleted.', 'success')
    return redirect(url_for('admin.checklist_templates'))


@admin_bp.route('/branches/<int:branch_id>/delete', methods=['POST'])
@login_required
@role_required('it_admin', 'qc_admin')
def delete_branch(branch_id):
    db = get_db()
    branch = db.execute('SELECT * FROM branches WHERE id = ?', (branch_id,)).fetchone()

    if not branch:
        flash('Branch not found.', 'danger')
        return redirect(url_for('admin.branches'))

    assigned = db.execute(
        'SELECT COUNT(*) as cnt FROM users WHERE branch_id = ?', (branch_id,)
    ).fetchone()['cnt']

    if assigned > 0:
        flash(f'Cannot delete "{branch["name"]}" — it has {assigned} assigned user(s). Reassign them first.', 'danger')
        return redirect(url_for('admin.branches'))

    db.execute('DELETE FROM branches WHERE id = ?', (branch_id,))
    db.commit()
    flash(f'Branch "{branch["name"]}" deleted.', 'success')
    return redirect(url_for('admin.branches'))
