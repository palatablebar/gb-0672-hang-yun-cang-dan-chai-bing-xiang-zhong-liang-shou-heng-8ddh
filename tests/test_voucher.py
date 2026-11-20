from app.modules.hazard.voucher import build_voucher, HOLDING_ZONE, GENERAL_ZONE
from app.modules.hazard.rules import inspect
from app import main, store
from app.engine import evaluate
from fastapi.testclient import TestClient
from copy import deepcopy
import json, zipfile, io
import pytest

def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.sqlite'))
    return TestClient(main.app)

def entry(voucher, container):
    return next(e for e in voucher['entries'] if e['container'] == container)

def zone_of(voucher, container):
    return [z['zone'] for z in voucher['zones'] if container in z['boxes']]

def make_state(rows, quarantine=None, context=None):
    # 最小手工状态：一条主单一个箱，可叠加危险品与父级制造差异。
    records = []
    for line, row in enumerate(rows, 2):
        base = {'id': f'N{line}', 'parent': '', 'container': 'C1', 'hazard': 'none', 'un_code': '',
                'pieces': 1, 'net_kg': '1', 'gross_kg': '2', 'revision': 1, '_line': line}
        base.update(row)
        records.append(base)
    children = {}
    for r in records:
        if r['parent']:
            children.setdefault(r['parent'], []).append(r['id'])
    state = {'records': records, 'graph': {'children': children, 'roots': [], 'errors': []},
             'quarantine': quarantine or [],
             'context': context or {'hazards': ['3', '5.1', '8'], 'incompatible': [['3', '5.1']]}}
    state['hazard_issues'] = inspect(records, state['context'])
    return state

def test_dirty_partial_hold(sample):
    voucher = sample['voucher']
    assert voucher['decision'] == '部分扣留'
    assert voucher['unclosed'] == ['C001']
    assert set(voucher['held']) == {'C001', 'X', 'Y'}
    assert voucher['released'] == ['C002', 'C003', 'C004', 'C005', 'C006']

def test_dirty_box_evidence(sample):
    box = entry(sample['voucher'], 'C001')
    assert box['zone'] == HOLDING_ZONE and box['category'] == '混类'
    assert box['legs'] == {'category': False, 'weight': False, 'seal': True}
    assert set(box['findings']) == {'INCOMPATIBLE', 'MISSING_UN'}
    assert (box['pieces'], box['net_kg'], box['gross_kg']) == (40, '400', '441')
    assert {'id': 'M001', 'line': 2} in box['sources']

def test_box_counted_once(sample):
    voucher = sample['voucher']
    boxes = [b for z in voucher['zones'] for b in z['boxes']]
    assert sorted(boxes) == sorted({e['container'] for e in voucher['entries']})
    assert len(boxes) == len(set(boxes))
    assert zone_of(voucher, 'C001') == [HOLDING_ZONE]

def test_seal_leg_broken_by_quarantine(sample):
    assert entry(sample['voucher'], 'X')['legs']['seal'] is False
    assert entry(sample['voucher'], 'Y')['legs']['seal'] is False
    assert entry(sample['voucher'], 'Y')['quarantined_lines']

def test_clean_release_all(clean):
    voucher = clean['voucher']
    assert voucher['decision'] == '整区放行'
    assert voucher['held'] == [] and voucher['unclosed'] == []
    assert all(e['handover'] for e in voucher['entries'])
    assert {z['zone'] for z in voucher['zones']} == {GENERAL_ZONE}

def test_seal_deterministic(sample):
    assert build_voucher(sample)['seal'] == sample['voucher']['seal']

def test_freeze_when_closed_box_incompatible():
    # 资料已闭合但同箱混装3与5.1：剩余箱无法共同证明隔离结论，整区冻结。
    state = make_state([
        {'id': 'A', 'container': 'C1', 'hazard': '3', 'un_code': 'UN1203'},
        {'id': 'B', 'container': 'C1', 'hazard': '5.1', 'un_code': 'UN1479'},
        {'id': 'C', 'container': 'C2'}])
    voucher = build_voucher(state)
    assert voucher['decision'] == '整区冻结'
    assert voucher['released'] == [] and set(voucher['held']) == {'C1', 'C2'}
    assert zone_of(voucher, 'C1') == [HOLDING_ZONE]

def test_partial_hold_when_unclosed_documents():
    # 分类资料未闭合（缺UN），其余箱可共同证明隔离结论：只扣留未闭合箱。
    state = make_state([
        {'id': 'A', 'container': 'C1', 'hazard': '3'},
        {'id': 'B', 'container': 'C2'}])
    voucher = build_voucher(state)
    assert voucher['decision'] == '部分扣留'
    assert voucher['unclosed'] == ['C1'] and voucher['released'] == ['C2']
    assert entry(voucher, 'C1')['legs']['category'] is False

def test_unknown_hazard_unclosed():
    state = make_state([{'id': 'A', 'container': 'C1', 'hazard': '9', 'un_code': 'UN9999'}, {'id': 'B', 'container': 'C2'}])
    voucher = build_voucher(state)
    assert voucher['unclosed'] == ['C1'] and voucher['decision'] == '部分扣留'
    assert entry(voucher, 'C1')['category'] == '未闭合'

def test_mixed_compatible_held_but_no_freeze():
    # 3与8可相容但无法采用单一类别：该箱扣留，不触发整区冻结。
    state = make_state([
        {'id': 'A', 'container': 'C1', 'hazard': '3', 'un_code': 'UN1203'},
        {'id': 'B', 'container': 'C1', 'hazard': '8', 'un_code': 'UN1789'},
        {'id': 'C', 'container': 'C2'}])
    voucher = build_voucher(state)
    assert voucher['decision'] == '部分扣留'
    box = entry(voucher, 'C1')
    assert box['category'] == '混类' and not box['unclosed'] and not box['isolation_broken']
    assert voucher['released'] == ['C2']

def test_weight_leg_blocks_handover():
    state = make_state([
        {'id': 'P', 'container': 'C1', 'pieces': 5, 'gross_kg': '9'},
        {'id': 'CH', 'parent': 'P', 'container': 'C1', 'pieces': 4}])
    box = entry(build_voucher(state), 'C1')
    assert box['legs']['weight'] is False and not box['handover']

def test_seal_leg_mixed_revisions():
    state = make_state([
        {'id': 'A', 'container': 'C1', 'revision': 1},
        {'id': 'B', 'container': 'C1', 'revision': 2}])
    assert entry(build_voucher(state), 'C1')['legs']['seal'] is False

def test_seal_leg_quarantined_row():
    quarantine = [{'line': 9, 'id': 'BAD', 'code': 'MISSING_VALUE', 'raw': {'id': 'BAD', 'container': 'C1'}}]
    state = make_state([{'id': 'A', 'container': 'C1'}], quarantine=quarantine)
    box = entry(build_voucher(state), 'C1')
    assert box['legs']['seal'] is False and box['quarantined_lines'] == [9]

def test_voucher_confirm_flow(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    run = c.post('/api/runs', json={}).json()
    rid = run['manifest']['run_id']
    assert run['voucher']['status'] == '待确认' and run['voucher']['revision'] == 1
    url = f'/api/runs/{rid}/voucher/confirm'
    ok = c.post(url, json={'revision': 1})
    assert ok.status_code == 200
    assert ok.json()['voucher']['status'] == '已确认' and ok.json()['voucher']['revision'] == 2

def test_voucher_confirm_failure_restores_old(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={}).json()['manifest']['run_id']
    url = f'/api/runs/{rid}/voucher/confirm'
    assert c.post(url, json={'revision': 1}).status_code == 200
    stale = c.post(url, json={'revision': 1})
    assert stale.status_code == 409 and stale.json()['code'] == 'STALE_VERSION'
    # 确认失败后旧版凭证保持不动，原舱单证据（隔离行）完整保留。
    current = c.get(f'/api/runs/{rid}').json()
    assert current['voucher']['revision'] == 2 and current['voucher']['status'] == '已确认'
    assert len(current['quarantine']) == 4 and current['voucher']['decision'] == '部分扣留'

def test_voucher_rerun_no_second_set(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={}).json()['manifest']['run_id']
    assert c.post(f'/api/runs/{rid}/voucher/confirm', json={'revision': 1}).status_code == 200
    again = c.post(f'/api/runs/{rid}/rerun', json={})
    assert again.status_code == 200 and again.json()['manifest']['run_id'] == rid
    voucher = c.get(f'/api/runs/{rid}').json()['voucher']
    assert voucher['revision'] == 2 and voucher['status'] == '已确认'
    with store.connect(main.DB_PATH) as con:
        assert con.execute('SELECT COUNT(*) FROM vouchers WHERE run_id=?', (rid,)).fetchone()[0] == 1

def test_voucher_confirm_errors(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    assert c.post('/api/runs/none/voucher/confirm', json={'revision': 1}).status_code == 404
    rid = c.post('/api/runs', json={}).json()['manifest']['run_id']
    assert c.post(f'/api/runs/{rid}/voucher/confirm', json={'revision': 0}).status_code == 422

def test_voucher_in_export_and_hazard_api(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={}).json()['manifest']['run_id']
    hazard = c.post('/api/hazard', json={}).json()
    assert hazard['voucher']['decision'] == '部分扣留'
    z = zipfile.ZipFile(io.BytesIO(c.get(f'/api/export/{rid}').content))
    voucher = json.loads(z.read('voucher.json'))
    assert voucher['seal'] == hazard['voucher']['seal'] and voucher['entries']

def test_voucher_page_renders(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={}).json()['manifest']['run_id']
    html = c.get('/hazard?run_id=' + rid).text
    assert '隔离区配载放行凭证' in html and '部分扣留' in html and '扣留区' in html
