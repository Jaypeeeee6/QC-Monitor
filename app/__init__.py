import os
from flask import Flask, redirect, url_for, render_template
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect

login_manager = LoginManager()
csrf = CSRFProtect()


def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config.from_object('app.config.Config')

    os.makedirs(os.path.dirname(app.config['DATABASE']), exist_ok=True)

    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'warning'

    csrf.init_app(app)

    from app.db import init_app as db_init_app
    db_init_app(app)

    from app.auth import auth_bp
    from app.checklist import checklist_bp
    from app.dashboard import dashboard_bp
    from app.admin import admin_bp
    from app.reports import reports_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(checklist_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(reports_bp)

    @app.template_filter('timeformat')
    def timeformat_filter(value):
        """Convert a datetime string like '2026-04-06 09:30' to '9:30 AM'."""
        if not value:
            return ''
        try:
            time_part = str(value)[11:16]
            hour, minute = int(time_part[:2]), time_part[3:5]
            period = 'AM' if hour < 12 else 'PM'
            hour12 = hour % 12 or 12
            return f'{hour12}:{minute} {period}'
        except Exception:
            return str(value)[11:16]

    @login_manager.user_loader
    def load_user(user_id):
        from app.db import get_db
        from app.models import User
        db = get_db()
        row = db.execute(
            '''SELECT u.*, b.name as branch_name
               FROM users u
               LEFT JOIN branches b ON b.id = u.branch_id
               WHERE u.id = ? AND u.is_active = 1''',
            (int(user_id),)
        ).fetchone()
        return User.from_db_row(row)

    @app.route('/')
    def index():
        if current_user.is_authenticated:
            if current_user.role == 'branch_manager':
                return redirect(url_for('checklist.index'))
            if current_user.role == 'it_admin':
                return redirect(url_for('admin.users'))
            return redirect(url_for('dashboard.index'))
        return redirect(url_for('auth.login'))

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    return app
