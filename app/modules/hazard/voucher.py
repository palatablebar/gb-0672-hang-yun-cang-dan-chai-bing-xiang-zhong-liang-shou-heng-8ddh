from collections import defaultdict
from decimal import Decimal
import hashlib, json
from app.modules.weight.rules import check_weights

GENERAL_ZONE = '普通区'
HOLDING_ZONE = '扣留区'
# 类别未闭合：危险品类目未知或UN编号缺失，资料仍在分批到达途中。
UNCLOSED_FINDINGS = ('UNKNOWN_HAZARD', 'MISSING_UN')
# 隔离结论被破坏：同箱混装不相容类别。
ISOLATION_FINDINGS = ('INCOMPATIBLE',)

def zone_name(category):
    return GENERAL_ZONE if category == 'none' else f'隔离区-{category}'

def _box_roots(rows, nodes, container):
    # 只取本箱局部根，避免上下级重复加总（与 DATA-DICTIONARY 一致）。
    roots = []
    for row in rows:
        parent = row['parent'].strip()
        if not parent or parent not in nodes or nodes[parent]['container'] != container:
            roots.append(row)
    return roots

def _entry(container, rows, state, nodes, findings, bad_weight, held_rows):
    context = state['context']
    observed = sorted({r['hazard'].strip() for r in rows if r['hazard'].strip() != 'none'})
    known = [c for c in observed if c in context['hazards']]
    codes = findings.get(container, [])
    if len(observed) > 1 and 'INCOMPATIBLE' not in codes:
        codes = codes + ['MIXED_CLASSES']
    adoptable = len(observed) == 1 and not set(observed) - set(known)
    if not observed:
        category = 'none'
    elif adoptable:
        category = observed[0]
    elif len(observed) > 1:
        category = '混类'
    else:
        category = '未闭合'
    roots = _box_roots(rows, nodes, container)
    net = sum((Decimal(r['net_kg']) for r in roots), Decimal(0))
    gross = sum((Decimal(r['gross_kg']) for r in roots), Decimal(0))
    quarantined = sorted(q['line'] for q in held_rows.get(container, []))
    legs = {
        'category': not observed or (adoptable and not codes),
        'weight': container not in bad_weight,
        'seal': not quarantined and len({r['revision'] for r in rows}) <= 1,
    }
    return {
        'container': container, 'zone': zone_name(category) if category not in ('未闭合', '混类') else HOLDING_ZONE,
        'category': category, 'observed': observed,
        'sources': [{'id': r['id'], 'line': r['_line']} for r in sorted(rows, key=lambda r: r['_line'])],
        'pieces': sum(r['pieces'] for r in roots), 'net_kg': str(net), 'gross_kg': str(gross),
        'legs': legs, 'findings': sorted(set(codes)), 'quarantined_lines': quarantined,
        'unclosed': any(c in UNCLOSED_FINDINGS for c in codes),
        'isolation_broken': any(c in ISOLATION_FINDINGS for c in codes),
        'handover': all(legs.values()),
    }

def build_voucher(state):
    nodes = {r['id']: r for r in state['records']}
    by_container = defaultdict(list)
    for row in state['records']:
        by_container[row['container']].append(row)
    held_rows = defaultdict(list)
    for q in state['quarantine']:
        held_rows[str(q['raw'].get('container', '')).strip()].append(q)
    findings = defaultdict(list)
    for issue in state['hazard_issues']:
        if issue['code'] == 'INCOMPATIBLE':
            findings[issue['id']].append('INCOMPATIBLE')
        elif issue['id'] in nodes:
            findings[nodes[issue['id']]['container']].append(issue['code'])
    bad_weight = {}
    for check in check_weights(state['records'], state['graph']):
        if check['code'] != 'BALANCED' and check['id'] in nodes:
            bad_weight.setdefault(nodes[check['id']]['container'], []).append(check)
    containers = sorted(set(by_container) | (set(held_rows) - {''}))
    entries = [_entry(c, by_container.get(c, []), state, nodes, findings, bad_weight, held_rows) for c in containers]
    zones = defaultdict(list)
    for entry in entries:
        zones[entry['zone']].append(entry['container'])
    order = {GENERAL_ZONE: 0, HOLDING_ZONE: 2}
    zone_rows = [{'zone': name, 'boxes': sorted(boxes)} for name, boxes in zones.items()]
    zone_rows.sort(key=lambda z: (order.get(z['zone'], 1), z['zone']))
    held = sorted(e['container'] for e in entries if not e['handover'])
    unclosed = sorted(e['container'] for e in entries if e['unclosed'])
    # 裁决：剔除未闭合箱后，剩余箱若仍能共同证明隔离结论，则只扣留未闭合箱；否则整区冻结。
    remaining = [e for e in entries if not e['unclosed']]
    isolation_proven = all(not e['isolation_broken'] for e in remaining)
    if not isolation_proven:
        decision, held, released = '整区冻结', [e['container'] for e in entries], []
    elif held:
        decision, released = '部分扣留', sorted(e['container'] for e in entries if e['handover'])
    else:
        decision, released = '整区放行', [e['container'] for e in entries]
    voucher = {
        'zones': zone_rows, 'entries': entries, 'decision': decision,
        'held': held, 'released': released, 'unclosed': unclosed,
    }
    voucher['seal'] = hashlib.sha256(json.dumps(voucher, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return voucher
