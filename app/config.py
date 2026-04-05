import os

basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'qc-monitor-dev-secret-2026-change-in-prod')
    DATABASE = os.path.join(basedir, 'database', 'qc_monitor.db')
    ADMIN_KEY = os.environ.get('ADMIN_KEY', 'qc-admin-2026')
    WTF_CSRF_ENABLED = True
    UPLOAD_FOLDER = os.path.join(basedir, 'app', 'static', 'uploads', 'reports')
    COMMENT_UPLOAD_FOLDER = os.path.join(basedir, 'app', 'static', 'uploads', 'comments')
    ALLOWED_PHOTO_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
    MAX_PHOTOS_PER_UPLOAD = 5
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024  # 32 MB max request size
