from datetime import timedelta
from flask import render_template, request
from flask_login import login_required

from app.dashboard import dashboard_bp
from app.db import get_db
from app.utils import role_required, local_today, local_now


@dashboard_bp.route('/')
@login_required
@role_required('qc_admin', 'management', 'it_admin')
def index():
    db = get_db()
    today = local_today()
    filter_date = request.args.get('date', today)

    # Fetch branches with their brand name
    branches = db.execute(
        '''SELECT b.id, b.name, br.name as brand_name
           FROM branches b
           LEFT JOIN brands br ON br.id = b.brand_id
           ORDER BY br.name NULLS LAST, b.name'''
    ).fetchall()

    kpi_data = []
    for branch in branches:
        branch_id = branch['id']

        # Single unified submission per branch per day
        sub_row = db.execute(
            '''SELECT cs.id,
                      COUNT(cr.id) as total,
                      SUM(CASE WHEN cr.answer = 'yes' THEN 1 ELSE 0 END) as yes_count,
                      cs.submitted_at,
                      u.full_name as submitted_by
               FROM checklist_submissions cs
               JOIN users u ON u.id = cs.submitted_by
               LEFT JOIN checklist_responses cr ON cr.submission_id = cs.id
               WHERE cs.branch_id = ? AND cs.submission_date = ?
               GROUP BY cs.id
               LIMIT 1''',
            (branch_id, filter_date)
        ).fetchone()

        # Latest QC score for this branch on the filtered date
        score_row = db.execute(
            '''SELECT s.score
               FROM scores s
               JOIN checklist_submissions cs ON cs.id = s.submission_id
               WHERE cs.branch_id = ? AND cs.submission_date = ?
               ORDER BY s.scored_at DESC
               LIMIT 1''',
            (branch_id, filter_date)
        ).fetchone()

        # Latest score ever (for history display)
        latest_score_row = db.execute(
            '''SELECT s.score, cs.submission_date
               FROM scores s
               JOIN checklist_submissions cs ON cs.id = s.submission_id
               WHERE cs.branch_id = ?
               ORDER BY cs.submission_date DESC, s.scored_at DESC
               LIMIT 1''',
            (branch_id,)
        ).fetchone()

        streak = _get_submission_streak(db, branch_id)

        compliance = None
        if sub_row and sub_row['total']:
            compliance = round(sub_row['yes_count'] / sub_row['total'] * 100, 1)

        kpi_data.append({
            'branch_id': branch_id,
            'branch_name': branch['name'],
            'brand_name': branch['brand_name'],
            'submission_id': sub_row['id'] if sub_row else None,
            'submitted': sub_row is not None,
            'submitted_by': sub_row['submitted_by'] if sub_row else None,
            'submitted_at': sub_row['submitted_at'] if sub_row else None,
            'compliance': compliance,
            'score': score_row['score'] if score_row else None,
            'latest_score': latest_score_row['score'] if latest_score_row else None,
            'latest_score_date': latest_score_row['submission_date'] if latest_score_row else None,
            'streak': streak,
        })

    # Group by brand for display
    brands_map = {}
    for kpi in kpi_data:
        brand = kpi['brand_name'] or 'No Brand'
        brands_map.setdefault(brand, []).append(kpi)

    submitted_count = sum(1 for k in kpi_data if k['submitted'])
    scored_list = [k['score'] for k in kpi_data if k['score'] is not None]
    avg_score = round(sum(scored_list) / len(scored_list), 1) if scored_list else None
    pending_score = sum(1 for k in kpi_data if k['submitted'] and k['score'] is None)

    return render_template(
        'dashboard/index.html',
        kpi_data=kpi_data,
        brands_map=brands_map,
        filter_date=filter_date,
        today=today,
        total_branches=len(kpi_data),
        submitted_count=submitted_count,
        pending_score=pending_score,
        avg_score=avg_score,
    )


def _get_submission_streak(db, branch_id):
    streak = 0
    check_date = local_now().date()
    for _ in range(60):
        row = db.execute(
            'SELECT COUNT(*) as cnt FROM checklist_submissions WHERE branch_id = ? AND submission_date = ?',
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

    today = local_today()
    default_from = today

    filter_branch = request.args.get('branch_id', '')
    filter_from = request.args.get('date_from', default_from)
    filter_to = request.args.get('date_to', today)

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
        WHERE cs.submission_date BETWEEN ? AND ?
    '''
    params = [filter_from, filter_to]

    if filter_branch:
        query += ' AND cs.branch_id = ?'
        params.append(filter_branch)

    query += ' GROUP BY cs.id ORDER BY cs.submission_date DESC, b.name'

    submissions = db.execute(query, params).fetchall()

    scored = [s['score'] for s in submissions if s['score'] is not None]
    avg_score = round(sum(scored) / len(scored), 1) if scored else None

    # Branch score comparison (average scored checklists within current filters)
    branch_score_query = '''
        SELECT b.name AS branch_name,
               AVG(s.score) AS avg_score,
               COUNT(s.id) AS scored_count
        FROM branches b
        LEFT JOIN checklist_submissions cs
               ON cs.branch_id = b.id
              AND cs.submission_date BETWEEN ? AND ?
        LEFT JOIN scores s ON s.submission_id = cs.id
    '''
    branch_score_params = [filter_from, filter_to]
    if filter_branch:
        branch_score_query += ' WHERE b.id = ?'
        branch_score_params.append(filter_branch)
    branch_score_query += '''
        GROUP BY b.id, b.name
        HAVING COUNT(s.id) > 0
        ORDER BY avg_score DESC, b.name
    '''
    branch_score_rows = db.execute(branch_score_query, branch_score_params).fetchall()
    branch_score_data = [
        {
            'branch_name': r['branch_name'],
            'avg_score': round(r['avg_score'], 1),
            'scored_count': r['scored_count'],
        }
        for r in branch_score_rows
    ]

    return render_template(
        'dashboard/reports.html',
        submissions=submissions,
        branches=branches,
        filter_branch=filter_branch,
        filter_from=filter_from,
        filter_to=filter_to,
        avg_score=avg_score,
        total_submissions=len(submissions),
        branch_score_data=branch_score_data,
    )
