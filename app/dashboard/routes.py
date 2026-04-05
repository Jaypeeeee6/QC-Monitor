from datetime import date, timedelta
from flask import render_template, request
from flask_login import login_required

from app.dashboard import dashboard_bp
from app.db import get_db
from app.utils import role_required


@dashboard_bp.route('/')
@login_required
@role_required('qc_admin', 'management', 'it_admin')
def index():
    db = get_db()
    today = date.today().isoformat()
    filter_date = request.args.get('date', today)

    branches = db.execute('SELECT id, name FROM branches ORDER BY name').fetchall()

    kpi_data = []
    for branch in branches:
        branch_id = branch['id']

        fs_row = db.execute(
            '''SELECT cs.id,
                      COUNT(cr.id) as total,
                      SUM(CASE WHEN cr.answer = 'yes' THEN 1 ELSE 0 END) as yes_count,
                      s.score
               FROM checklist_submissions cs
               JOIN checklist_templates ct ON ct.id = cs.template_id AND ct.name = 'Food Safety'
               LEFT JOIN checklist_responses cr ON cr.submission_id = cs.id
               LEFT JOIN scores s ON s.submission_id = cs.id
               WHERE cs.branch_id = ? AND cs.submission_date = ?
               GROUP BY cs.id''',
            (branch_id, filter_date)
        ).fetchone()

        cl_row = db.execute(
            '''SELECT cs.id,
                      COUNT(cr.id) as total,
                      SUM(CASE WHEN cr.answer = 'yes' THEN 1 ELSE 0 END) as yes_count
               FROM checklist_submissions cs
               JOIN checklist_templates ct ON ct.id = cs.template_id AND ct.name = 'Cleaning'
               LEFT JOIN checklist_responses cr ON cr.submission_id = cs.id
               WHERE cs.branch_id = ? AND cs.submission_date = ?
               GROUP BY cs.id''',
            (branch_id, filter_date)
        ).fetchone()

        latest_score_row = db.execute(
            '''SELECT s.score, cs.submission_date
               FROM scores s
               JOIN checklist_submissions cs ON cs.id = s.submission_id
               JOIN checklist_templates ct ON ct.id = cs.template_id AND ct.name = 'Food Safety'
               WHERE cs.branch_id = ?
               ORDER BY cs.submission_date DESC
               LIMIT 1''',
            (branch_id,)
        ).fetchone()

        streak = _get_submission_streak(db, branch_id)

        fs_compliance = None
        cl_compliance = None

        if fs_row and fs_row['total']:
            fs_compliance = round(fs_row['yes_count'] / fs_row['total'] * 100, 1)

        if cl_row and cl_row['total']:
            cl_compliance = round(cl_row['yes_count'] / cl_row['total'] * 100, 1)

        kpi_data.append({
            'branch_id': branch_id,
            'branch_name': branch['name'],
            'fs_submission_id': fs_row['id'] if fs_row else None,
            'cl_submission_id': cl_row['id'] if cl_row else None,
            'fs_score': fs_row['score'] if fs_row else None,
            'fs_compliance': fs_compliance,
            'cl_compliance': cl_compliance,
            'latest_score': latest_score_row['score'] if latest_score_row else None,
            'latest_score_date': latest_score_row['submission_date'] if latest_score_row else None,
            'streak': streak,
            'fs_submitted': fs_row is not None,
            'cl_submitted': cl_row is not None,
        })

    return render_template(
        'dashboard/index.html',
        kpi_data=kpi_data,
        filter_date=filter_date,
        today=today,
    )


def _get_submission_streak(db, branch_id):
    streak = 0
    check_date = date.today()
    for _ in range(60):
        row = db.execute(
            '''SELECT COUNT(*) as cnt FROM checklist_submissions
               WHERE branch_id = ? AND submission_date = ?''',
            (branch_id, check_date.isoformat())
        ).fetchone()
        if row['cnt'] > 0:
            streak += 1
            check_date -= timedelta(days=1)
        else:
            break
    return streak


@dashboard_bp.route('/reports')
@login_required
@role_required('qc_admin', 'management', 'it_admin')
def reports():
    db = get_db()

    today = date.today().isoformat()
    default_from = (date.today() - timedelta(days=30)).isoformat()

    filter_branch = request.args.get('branch_id', '')
    filter_from = request.args.get('date_from', default_from)
    filter_to = request.args.get('date_to', today)
    filter_type = request.args.get('template_id', '')

    branches = db.execute('SELECT id, name FROM branches ORDER BY name').fetchall()
    templates = db.execute('SELECT id, name FROM checklist_templates WHERE is_active = 1').fetchall()

    query = '''
        SELECT cs.id, cs.submission_date, cs.submitted_at,
               ct.name as template_name,
               b.name as branch_name,
               u.full_name as submitted_by_name,
               COUNT(cr.id) as total,
               SUM(CASE WHEN cr.answer = 'yes' THEN 1 ELSE 0 END) as yes_count,
               s.score,
               (SELECT COUNT(*) FROM comments c WHERE c.submission_id = cs.id) as comment_count
        FROM checklist_submissions cs
        JOIN checklist_templates ct ON ct.id = cs.template_id
        JOIN branches b ON b.id = cs.branch_id
        JOIN users u ON u.id = cs.submitted_by
        LEFT JOIN checklist_responses cr ON cr.submission_id = cs.id
        LEFT JOIN scores s ON s.submission_id = cs.id
        WHERE cs.submission_date BETWEEN ? AND ?
    '''
    params = [filter_from, filter_to]

    if filter_branch:
        query += ' AND cs.branch_id = ?'
        params.append(filter_branch)

    if filter_type:
        query += ' AND cs.template_id = ?'
        params.append(filter_type)

    query += ' GROUP BY cs.id ORDER BY cs.submission_date DESC, b.name'

    submissions = db.execute(query, params).fetchall()

    avg_score = None
    scored = [s['score'] for s in submissions if s['score'] is not None]
    if scored:
        avg_score = round(sum(scored) / len(scored), 1)

    return render_template(
        'dashboard/reports.html',
        submissions=submissions,
        branches=branches,
        templates=templates,
        filter_branch=filter_branch,
        filter_from=filter_from,
        filter_to=filter_to,
        filter_type=filter_type,
        avg_score=avg_score,
        total_submissions=len(submissions),
    )
