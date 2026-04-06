from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import abort
from flask_login import current_user

# Oman Standard Time = UTC+4 (no DST)
_OMAN_TZ = timezone(timedelta(hours=4))


def local_now() -> datetime:
    """Current datetime in Oman time (UTC+4)."""
    return datetime.now(_OMAN_TZ).replace(tzinfo=None)


def local_today() -> str:
    """Today's date string (YYYY-MM-DD) in Oman time."""
    return local_now().strftime('%Y-%m-%d')


def local_now_str() -> str:
    """Current datetime string (YYYY-MM-DD HH:MM:SS) in Oman time."""
    return local_now().strftime('%Y-%m-%d %H:%M:%S')


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator
