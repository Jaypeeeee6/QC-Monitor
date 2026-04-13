"""
Resolve checklist definitions for a branch: legacy per-branch, brand-wide, or global + inheritance chain.
"""
from __future__ import annotations


def _row_get(row, key, default=None):
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def get_root_template(db, template_id):
    """Walk parent_template_id up to the root row."""
    row = db.execute('SELECT * FROM checklist_templates WHERE id = ?', (template_id,)).fetchone()
    if not row:
        return None
    while _row_get(row, 'parent_template_id'):
        row = db.execute(
            'SELECT * FROM checklist_templates WHERE id = ?',
            (_row_get(row, 'parent_template_id'),),
        ).fetchone()
        if not row:
            return None
    return row


def template_scope(root):
    return (_row_get(root, 'template_scope') or 'legacy').lower()


def uses_inheritance_chain(root):
    """Global base template that may have brand / branch child layers."""
    return template_scope(root) == 'global'


def is_brand_root(root):
    return template_scope(root) == 'brand'


def list_branch_manager_templates(db, branch_id):
    """Root templates visible to a branch manager at this branch."""
    return db.execute(
        '''
        SELECT ct.*
        FROM checklist_templates ct
        WHERE ct.is_active = 1
          AND ct.parent_template_id IS NULL
          AND COALESCE(ct.template_status, 'active') = 'active'
          AND (
                ct.template_scope IS NULL
             OR ct.template_scope = ''
             OR ct.template_scope = 'legacy'
             OR ct.template_scope = 'global'
             OR (
                  ct.template_scope = 'brand'
                  AND ct.brand_id IS NOT NULL
                  AND ct.brand_id = (SELECT brand_id FROM branches WHERE id = ?)
                )
          )
        ORDER BY ct.id
        ''',
        (branch_id,),
    ).fetchall()


def resolve_template_chain(db, root_id, branch_id):
    """
    Ordered layers for inheritance / editing:
    - global root: [global, brand_child?, branch_child?]
    - brand root: [brand, branch_child?] when a per-branch layer exists
    - otherwise: [root]
    """
    root = db.execute('SELECT * FROM checklist_templates WHERE id = ?', (root_id,)).fetchone()
    if not root:
        return []
    if not uses_inheritance_chain(root):
        if is_brand_root(root):
            chain = [root]
            br_layer = db.execute(
                '''
                SELECT * FROM checklist_templates
                WHERE parent_template_id = ?
                  AND template_scope = 'branch'
                  AND branch_id = ?
                  AND COALESCE(template_status, 'active') = 'active'
                  AND COALESCE(is_active, 1) = 1
                LIMIT 1
                ''',
                (root['id'], branch_id),
            ).fetchone()
            if br_layer:
                chain.append(br_layer)
            return chain
        return [root]

    chain = [root]
    branch = db.execute('SELECT * FROM branches WHERE id = ?', (branch_id,)).fetchone()
    brand_id = _row_get(branch, 'brand_id') if branch else None

    if brand_id:
        child = db.execute(
            '''
            SELECT * FROM checklist_templates
            WHERE parent_template_id = ?
              AND template_scope = 'brand'
              AND brand_id = ?
              AND COALESCE(template_status, 'active') = 'active'
              AND COALESCE(is_active, 1) = 1
            LIMIT 1
            ''',
            (root['id'], brand_id),
        ).fetchone()
        if child:
            chain.append(child)

    parent_for_branch = chain[-1]['id']
    br_layer = db.execute(
        '''
        SELECT * FROM checklist_templates
        WHERE parent_template_id = ?
          AND template_scope = 'branch'
          AND branch_id = ?
          AND COALESCE(template_status, 'active') = 'active'
          AND COALESCE(is_active, 1) = 1
        LIMIT 1
        ''',
        (parent_for_branch, branch_id),
    ).fetchone()
    if br_layer:
        chain.append(br_layer)

    return chain


def _sections_for_layer(db, layer, branch_id):
    tid = layer['id']
    scope = (_row_get(layer, 'template_scope') or 'legacy').lower()
    if scope == 'branch':
        return db.execute(
            '''
            SELECT * FROM checklist_sections
            WHERE template_id = ? AND (branch_id IS NULL OR branch_id = ?)
            ORDER BY display_order, id
            ''',
            (tid, branch_id),
        ).fetchall()
    if scope in ('global', 'brand'):
        return db.execute(
            '''
            SELECT * FROM checklist_sections
            WHERE template_id = ? AND branch_id IS NULL
            ORDER BY display_order, id
            ''',
            (tid,),
        ).fetchall()
    # legacy: per-branch sections
    return db.execute(
        '''
        SELECT * FROM checklist_sections
        WHERE template_id = ? AND branch_id = ?
        ORDER BY display_order, id
        ''',
        (tid, branch_id),
    ).fetchall()


def _merge_sections_across_layers(db, chain, branch_id):
    merged = []

    def index_by_section_id(sid):
        for i, s in enumerate(merged):
            if s['id'] == sid:
                return i
        return None

    for layer in chain:
        for sec in _sections_for_layer(db, layer, branch_id):
            oid = _row_get(sec, 'overrides_section_id')
            if oid:
                idx = index_by_section_id(oid)
                if idx is not None:
                    merged[idx] = sec
                else:
                    merged.append(sec)
            else:
                merged.append(sec)
    return merged


def _item_allowed_for_branch(db, item, branch_id):
    rows = db.execute(
        'SELECT 1 FROM checklist_item_branches WHERE item_id = ? LIMIT 1',
        (item['id'],),
    ).fetchone()
    if not rows:
        return True
    ok = db.execute(
        'SELECT 1 FROM checklist_item_branches WHERE item_id = ? AND branch_id = ? LIMIT 1',
        (item['id'], branch_id),
    ).fetchone()
    return ok is not None


def _items_for_section(db, section_id, branch_id):
    rows = db.execute(
        '''
        SELECT ci.* FROM checklist_items ci
        WHERE ci.section_id = ? AND ci.is_active = 1
        ORDER BY ci.display_order, ci.id
        ''',
        (section_id,),
    ).fetchall()
    out = []
    for item in rows:
        if _item_allowed_for_branch(db, item, branch_id):
            out.append(item)
    return out


def get_effective_flat_items(db, root_template_id, branch_id):
    """
    Flat rows for submit flows: each row has checklist_items columns plus
    section_name, sec_order, sec_id (section id).
    """
    root = get_root_template(db, root_template_id)
    if not root:
        return []

    root_id = root['id']

    if uses_inheritance_chain(root):
        chain = resolve_template_chain(db, root_id, branch_id)
        sections = _merge_sections_across_layers(db, chain, branch_id)
        items_out = []
        for sec in sections:
            for item in _items_for_section(db, sec['id'], branch_id):
                d = dict(item)
                d['section_name'] = sec['name']
                d['sec_order'] = sec['display_order']
                d['sec_id'] = sec['id']
                items_out.append(d)
        return items_out

    if is_brand_root(root):
        chain = resolve_template_chain(db, root_id, branch_id)
        sections = _merge_sections_across_layers(db, chain, branch_id)
        items_out = []
        for sec in sections:
            for item in _items_for_section(db, sec['id'], branch_id):
                d = dict(item)
                d['section_name'] = sec['name']
                d['sec_order'] = sec['display_order']
                d['sec_id'] = sec['id']
                items_out.append(d)
        return items_out

    # legacy: sections for this branch + root template
    sections = db.execute(
        '''
        SELECT * FROM checklist_sections
        WHERE template_id = ? AND branch_id = ?
        ORDER BY display_order, id
        ''',
        (root_id, branch_id),
    ).fetchall()
    items_out = []
    for sec in sections:
        for item in _items_for_section(db, sec['id'], branch_id):
            d = dict(item)
            d['section_name'] = sec['name']
            d['sec_order'] = sec['display_order']
            d['sec_id'] = sec['id']
            items_out.append(d)
    return items_out


def group_items_into_sections(flat_items):
    """Turn flat item rows into ordered section dicts for templates (submit_all)."""
    seen = {}
    ordered = []
    for item in flat_items:
        sec_key = item.get('sec_id') or 0
        if sec_key not in seen:
            seen[sec_key] = {
                'id': item.get('sec_id'),
                'name': item.get('section_name') or 'General',
                'items': [],
            }
            ordered.append(seen[sec_key])
        seen[sec_key]['items'].append(item)
    return ordered


def sections_data_merged_for_manage(db, root_id, branch_id):
    """
    Merged section order for a branch, each with all checklist_items (active and inactive).

    Unlike get_effective_flat_items, includes sections that have zero items, so QC admins
    see newly added empty sections and can add questions there.
    """
    root = get_root_template(db, root_id)
    if not root:
        return []
    rid = root['id']
    if not (uses_inheritance_chain(root) or is_brand_root(root)):
        return []
    chain = resolve_template_chain(db, rid, branch_id)
    merged_secs = _merge_sections_across_layers(db, chain, branch_id)
    out = []
    for sec in merged_secs:
        items = db.execute(
            'SELECT * FROM checklist_items WHERE section_id = ? ORDER BY display_order, id',
            (sec['id'],),
        ).fetchall()
        out.append({'section': sec, 'items': list(items)})
    return out


def default_editing_layer_id(db, root_id, branch_id):
    """Prefer the most specific layer in the chain for QC editing."""
    root = get_root_template(db, root_id)
    if not root:
        return root_id
    if uses_inheritance_chain(root) or is_brand_root(root):
        chain = resolve_template_chain(db, root_id, branch_id)
        return chain[-1]['id']
    return root_id


def layer_chain_for_manage(db, root_id, branch_id):
    """Layers to show as tabs (global inheritance or brand-wide + branch)."""
    root = get_root_template(db, root_id)
    if not root:
        return []
    if uses_inheritance_chain(root) or is_brand_root(root):
        return resolve_template_chain(db, root_id, branch_id)
    return [root]


def branch_layer_template(db, root_id, branch_id):
    """The per-branch checklist_templates row for this branch under root, if it exists."""
    chain = resolve_template_chain(db, root_id, branch_id)
    if not chain:
        return None
    last = chain[-1]
    if (_row_get(last, 'template_scope') or '').lower() == 'branch':
        return last
    return None


def ensure_brand_branch_layer(db, root_row, branch_id):
    """
    For a brand-scoped root template, ensure a per-branch child layer exists so
    branch-sidebar edits do not write to the shared brand-wide root.
    """
    if not root_row or not branch_id or not is_brand_root(root_row):
        return
    root_id = root_row['id']
    exists = db.execute(
        '''
        SELECT id FROM checklist_templates
        WHERE parent_template_id = ?
          AND template_scope = 'branch'
          AND branch_id = ?
          AND COALESCE(is_active, 1) = 1
        LIMIT 1
        ''',
        (root_id, branch_id),
    ).fetchone()
    if exists:
        return
    br = db.execute('SELECT name FROM branches WHERE id = ?', (branch_id,)).fetchone()
    bname = (br['name'] if br else None) or 'Branch'
    cur = db.execute(
        '''INSERT INTO checklist_templates
           (name, description, is_active, parent_template_id, template_scope,
            brand_id, branch_id, template_status, version)
           VALUES (?, NULL, 1, ?, 'branch', NULL, ?, 'active', 1)''',
        (f'{root_row["name"]} — {bname}', root_id, branch_id),
    )
    new_id = cur.lastrowid
    db.execute('UPDATE checklist_templates SET root_template_id = ? WHERE id = ?', (root_id, new_id))
    db.commit()


def ensure_inheritance_branch_layer(db, root_row, branch_id):
    """
    For a global inheritance root, ensure a per-branch child layer exists (parent is the
    deepest layer in the chain for this branch — global and/or brand — same as resolve_template_chain).
    Lets QC pick a branch in the sidebar and default to branch-only edits like brand-scoped templates.
    """
    if not root_row or not branch_id or not uses_inheritance_chain(root_row):
        return
    root_id = root_row['id']
    chain = resolve_template_chain(db, root_id, branch_id)
    if not chain:
        return
    last = chain[-1]
    if (_row_get(last, 'template_scope') or '').lower() == 'branch':
        return
    parent_id = last['id']
    exists = db.execute(
        '''
        SELECT id FROM checklist_templates
        WHERE parent_template_id = ?
          AND template_scope = 'branch'
          AND branch_id = ?
          AND COALESCE(is_active, 1) = 1
        LIMIT 1
        ''',
        (parent_id, branch_id),
    ).fetchone()
    if exists:
        return
    br = db.execute('SELECT name FROM branches WHERE id = ?', (branch_id,)).fetchone()
    bname = (br['name'] if br else None) or 'Branch'
    cur = db.execute(
        '''INSERT INTO checklist_templates
           (name, description, is_active, parent_template_id, template_scope,
            brand_id, branch_id, template_status, version)
           VALUES (?, NULL, 1, ?, 'branch', NULL, ?, 'active', 1)''',
        (f'{root_row["name"]} — {bname}', parent_id, branch_id),
    )
    new_id = cur.lastrowid
    db.execute('UPDATE checklist_templates SET root_template_id = ? WHERE id = ?', (root_id, new_id))
    db.commit()


def fetch_sections_for_template_layer(db, layer_id, branch_id):
    layer = db.execute('SELECT * FROM checklist_templates WHERE id = ?', (layer_id,)).fetchone()
    if not layer:
        return []
    return list(_sections_for_layer(db, layer, branch_id))


def clone_template_structure(db, source_template_id, target_template_id, section_branch_id, source_branch_id=None):
    """
    Copy sections and items from source_template_id onto target_template_id.
    New sections use section_branch_id (NULL for shared global/brand layers).
    If source_branch_id is set, only sections for that branch are copied (legacy → branch).
    """
    q = 'SELECT * FROM checklist_sections WHERE template_id = ?'
    params = [source_template_id]
    if source_branch_id is not None:
        q += ' AND branch_id = ?'
        params.append(source_branch_id)
    q += ' ORDER BY display_order, id'
    secs = db.execute(q, params).fetchall()
    for sec in secs:
        cur = db.execute(
            '''INSERT INTO checklist_sections (template_id, branch_id, name, display_order, is_active)
               VALUES (?, ?, ?, ?, ?)''',
            (
                target_template_id,
                section_branch_id,
                sec['name'],
                sec['display_order'],
                sec['is_active'],
            ),
        )
        new_sid = cur.lastrowid
        items = db.execute(
            '''SELECT * FROM checklist_items WHERE section_id = ?
               ORDER BY display_order, id''',
            (sec['id'],),
        ).fetchall()
        for it in items:
            cur_it = db.execute(
                '''INSERT INTO checklist_items
                   (template_id, section_id, item_text, display_order, is_active, created_by)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (
                    target_template_id,
                    new_sid,
                    it['item_text'],
                    it['display_order'],
                    it['is_active'],
                    it['created_by'],
                ),
            )
            new_iid = cur_it.lastrowid
            for cib in db.execute(
                'SELECT branch_id FROM checklist_item_branches WHERE item_id = ?', (it['id'],)
            ):
                db.execute(
                    'INSERT INTO checklist_item_branches (item_id, branch_id) VALUES (?, ?)',
                    (new_iid, cib['branch_id']),
                )


def fallback_branch_id(db, layer_template_id, preferred_branch_id=None):
    """Branch context for redirects when a section has branch_id NULL (global/brand layers)."""
    if preferred_branch_id:
        return preferred_branch_id
    layer = db.execute('SELECT * FROM checklist_templates WHERE id = ?', (layer_template_id,)).fetchone()
    if not layer:
        return None
    if layer['branch_id']:
        return layer['branch_id']
    if layer['brand_id']:
        b = db.execute(
            'SELECT id FROM branches WHERE brand_id = ? ORDER BY id LIMIT 1',
            (layer['brand_id'],),
        ).fetchone()
        if b:
            return b['id']
    b = db.execute('SELECT id FROM branches ORDER BY id LIMIT 1').fetchone()
    return b['id'] if b else None


def _child_layer_inapplicable_under_root(db, child, root):
    """Whether a non-root layer row cannot apply under the root's current scope (for reconciliation)."""
    rs = (_row_get(root, 'template_scope') or 'legacy').lower()
    ch_scope = (_row_get(child, 'template_scope') or '').lower()

    if rs == 'legacy':
        return True

    if rs == 'global':
        return False

    if rs == 'brand':
        rb = _row_get(root, 'brand_id')
        if rb is None:
            return False
        if ch_scope == 'brand':
            cb = _row_get(child, 'brand_id')
            return cb is None or int(cb) != int(rb)
        if ch_scope == 'branch':
            bid = _row_get(child, 'branch_id')
            if not bid:
                return True
            row = db.execute('SELECT brand_id FROM branches WHERE id = ?', (bid,)).fetchone()
            cb = _row_get(row, 'brand_id') if row else None
            return cb is None or int(cb) != int(rb)
        return True

    if rs == 'branch':
        rbranch = _row_get(root, 'branch_id')
        br_brand = None
        if rbranch:
            row = db.execute('SELECT brand_id FROM branches WHERE id = ?', (rbranch,)).fetchone()
            br_brand = _row_get(row, 'brand_id') if row else None
        if ch_scope == 'branch':
            cid = _row_get(child, 'branch_id')
            if rbranch and cid and int(cid) != int(rbranch):
                return True
            return False
        if ch_scope == 'brand':
            cb = _row_get(child, 'brand_id')
            if br_brand and cb and int(cb) != int(br_brand):
                return True
            return False
        return True

    return False


def reconcile_child_layers_after_root_scope_change(db, root_id):
    """
    Set is_active=0 on child template rows that no longer fit the root's scope/brand/branch.
    Never deletes rows — submissions and checklist_responses keep working.
    Returns the number of template rows deactivated.
    """
    root = db.execute('SELECT * FROM checklist_templates WHERE id = ?', (root_id,)).fetchone()
    if not root:
        return 0
    children = db.execute(
        '''SELECT * FROM checklist_templates
           WHERE root_template_id = ? AND id != ?''',
        (root_id, root_id),
    ).fetchall()
    if not children:
        return 0

    to_deactivate = set()
    for ch in children:
        if _child_layer_inapplicable_under_root(db, ch, root):
            to_deactivate.add(ch['id'])

    changed = True
    while changed:
        changed = False
        for ch in children:
            cid = ch['id']
            if cid in to_deactivate:
                continue
            pid = _row_get(ch, 'parent_template_id')
            if pid and pid in to_deactivate:
                to_deactivate.add(cid)
                changed = True

    n = 0
    for cid in to_deactivate:
        cur = db.execute(
            'UPDATE checklist_templates SET is_active = 0 WHERE id = ? AND COALESCE(is_active, 1) != 0',
            (cid,),
        )
        n += cur.rowcount or 0
    return n
