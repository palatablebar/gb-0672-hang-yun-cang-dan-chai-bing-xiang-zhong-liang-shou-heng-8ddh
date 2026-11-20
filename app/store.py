import sqlite3, json
from pathlib import Path
from app.errors import BusinessError

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')")
    con.execute("CREATE TABLE IF NOT EXISTS vouchers (run_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待确认', seal TEXT NOT NULL DEFAULT '')")
    return con

def save(path, result):
    run_id=result['manifest']['run_id']
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result) VALUES(?,?,?)', (run_id,result['manifest']['sha256'],json.dumps(result,ensure_ascii=False)))
        if result.get('voucher'):
            con.execute('INSERT OR IGNORE INTO vouchers(run_id,seal) VALUES(?,?)', (run_id,result['voucher']['seal']))
    return get(path,run_id)

def get(path, run_id):
    with connect(path) as con:
        row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
        result=json.loads(row['result']);result.update(revision=row['revision'],status=row['status'])
        if 'voucher' in result:
            voucher=con.execute('SELECT revision,status FROM vouchers WHERE run_id=?',(run_id,)).fetchone()
            if voucher: result['voucher'].update(revision=voucher['revision'],status=voucher['status'])
    return result

def history(path):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT id,revision,status FROM runs ORDER BY rowid DESC LIMIT 100')]

def review(path,run_id,revision):
    with connect(path) as con:
        cur=con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',('已复核',run_id,revision))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise BusinessError('NOT_FOUND','批次不存在',404)
            raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
    return get(path,run_id)

def confirm_voucher(path,run_id,revision):
    with connect(path) as con:
        cur=con.execute("UPDATE vouchers SET revision=revision+1,status='已确认' WHERE run_id=? AND revision=?",(run_id,revision))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise BusinessError('NOT_FOUND','批次不存在',404)
            if not con.execute('SELECT 1 FROM vouchers WHERE run_id=?',(run_id,)).fetchone():
                raise BusinessError('VOUCHER_MISSING','该批无放行凭证',404)
            raise BusinessError('STALE_VERSION','凭证版本已更新，请刷新',409)
    return get(path,run_id)
