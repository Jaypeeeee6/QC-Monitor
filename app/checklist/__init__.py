from flask import Blueprint

checklist_bp = Blueprint('checklist', __name__, url_prefix='/checklist')

from app.checklist import routes  # noqa: F401, E402
