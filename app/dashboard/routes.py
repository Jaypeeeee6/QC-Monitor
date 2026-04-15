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

    filter_day = request.args.get('date', today)

    branches = db.execute(
        '''SELECT b.id, b.name, b.brand_id, br.name as brand_name
           FROM branches b
           LEFT JOIN brands br ON br.id = b.brand_id
           ORDER BY br.name NULLS LAST, b.name'''
    ).fetchall()

    valid_branch_ids = {b['id'] for b in branches}
    filter_branches = []
    for raw in request.args.getlist('branch_id'):
        try:
            bid = int(raw)
        except (TypeError, ValueError):
            continue
        if bid in valid_branch_ids and bid not in filter_branches:
            filter_branches.append(bid)

    branch_order = {b['id']: i for i, b in enumerate(branches)}
    filter_branches.sort(key=lambda bid: branch_order.get(bid, 0))

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
        WHERE cs.submission_date = ?
    '''
    params = [filter_day]

    if filter_branches:
        placeholders = ','.join('?' * len(filter_branches))
        query += f' AND cs.branch_id IN ({placeholders})'
        params.extend(filter_branches)

    query += ' GROUP BY cs.id ORDER BY cs.submission_date DESC, b.name'

    submissions = db.execute(query, params).fetchall()

    scored = [s['score'] for s in submissions if s['score'] is not None]
    avg_score = round(sum(scored) / len(scored), 1) if scored else None

    # Selected-day branch comparison (one day only)
    daily_branch_query = '''
        SELECT b.name AS branch_name,
               AVG(s.score) AS avg_score,
               COUNT(s.id) AS scored_count
        FROM checklist_submissions cs
        JOIN branches b ON b.id = cs.branch_id
        JOIN scores s ON s.submission_id = cs.id
        WHERE cs.submission_date = ?
    '''
    daily_branch_params = [filter_day]
    if filter_branches:
        ph = ','.join('?' * len(filter_branches))
        daily_branch_query += f' AND cs.branch_id IN ({ph})'
        daily_branch_params.extend(filter_branches)
    daily_branch_query += '''
        GROUP BY b.id, b.name
        ORDER BY avg_score DESC, b.name
    '''
    daily_branch_rows = db.execute(daily_branch_query, daily_branch_params).fetchall()
    daily_branch_score_data = [
        {
            'branch_name': r['branch_name'],
            'avg_score': round(r['avg_score'], 1),
            'scored_count': r['scored_count'],
        }
        for r in daily_branch_rows
    ]

    # All-time branch comparison (average scored checklists across all dates)
    all_time_branch_query = '''
        SELECT b.name AS branch_name,
               AVG(s.score) AS avg_score,
               COUNT(s.id) AS scored_count
        FROM branches b
        LEFT JOIN checklist_submissions cs ON cs.branch_id = b.id
        LEFT JOIN scores s ON s.submission_id = cs.id
    '''
    all_time_branch_params = []
    if filter_branches:
        ph = ','.join('?' * len(filter_branches))
        all_time_branch_query += f' WHERE b.id IN ({ph})'
        all_time_branch_params.extend(filter_branches)
    all_time_branch_query += '''
        GROUP BY b.id, b.name
        HAVING COUNT(s.id) > 0
        ORDER BY avg_score DESC, b.name
    '''
    all_time_branch_rows = db.execute(all_time_branch_query, all_time_branch_params).fetchall()
    all_time_branch_score_data = [
        {
            'branch_name': r['branch_name'],
            'avg_score': round(r['avg_score'], 1),
            'scored_count': r['scored_count'],
        }
        for r in all_time_branch_rows
    ]

    return render_template(
        'dashboard/reports.html',
        submissions=submissions,
        branches=branches,
        filter_branches=filter_branches,
        filter_day=filter_day,
        avg_score=avg_score,
        total_submissions=len(submissions),
        daily_branch_score_data=daily_branch_score_data,
        all_time_branch_score_data=all_time_branch_score_data,
    )
