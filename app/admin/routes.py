from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash

from app.admin import admin_bp
from app.db import get_db
from app.utils import role_required


@admin_bp.route('/users')
@login_required
@role_required('it_admin')
def users():
    db = get_db()

    filter_role = request.args.get('role', '')
    filter_branch = request.args.get('branch_id', '')

    query = '''
        SELECT u.*, b.name as branch_name
        FROM users u
        LEFT JOIN branches b ON b.id = u.branch_id
        WHERE 1=1
    '''
    params = []

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
