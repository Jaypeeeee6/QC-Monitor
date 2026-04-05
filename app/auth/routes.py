from flask import render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash

from app.auth import auth_bp
from app.db import get_db
from app.models import User


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not email or not password:
            flash('Please enter your email and password.', 'danger')
            return render_template('auth/login.html')

        db = get_db()
        row = db.execute(
            '''SELECT u.*, b.name as branch_name
               FROM users u
               LEFT JOIN branches b ON b.id = u.branch_id
               WHERE u.email = ? AND u.is_active = 1''',
            (email,)
        ).fetchone()

        if row and check_password_hash(row['password_hash'], password):
            user = User.from_db_row(row)
            login_user(user)
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            if user.role == 'branch_manager':
                return redirect(url_for('checklist.index'))
            if user.role == 'it_admin':
                return redirect(url_for('admin.users'))
            return redirect(url_for('dashboard.index'))

        flash('Invalid email or password. Please try again.', 'danger')

    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/register/branch')
@auth_bp.route('/register/qc')
def register_disabled():
    flash('Account registration is managed by the IT Admin. Please contact your IT department.', 'warning')
    return redirect(url_for('auth.login'))
