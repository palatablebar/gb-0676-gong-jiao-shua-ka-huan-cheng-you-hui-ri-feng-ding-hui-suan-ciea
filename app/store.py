import sqlite3,json,hashlib
from datetime import datetime,timezone
from pathlib import Path
from app.config import DATA_DIR
from app.errors import DomainError
def connect():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA_DIR/"records.db",timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    db.execute("CREATE TABLE IF NOT EXISTS corrections (id TEXT PRIMARY KEY,run_id TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,created TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS ledger (run_id TEXT NOT NULL,card TEXT NOT NULL,business_day TEXT NOT NULL,correction_id TEXT NOT NULL,receivable TEXT NOT NULL,delta TEXT NOT NULL,booked_at TEXT NOT NULL,PRIMARY KEY (run_id,card,business_day))")
    db.execute("CREATE TABLE IF NOT EXISTS seals (run_id TEXT PRIMARY KEY,correction_id TEXT NOT NULL,digest TEXT NOT NULL,sealed_at TEXT NOT NULL)")
    return db
def save(run_id,payload):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)",(run_id,json.dumps(payload,ensure_ascii=False)))
    return get(run_id)
def get(run_id):
    with connect() as db: row=db.execute("SELECT payload,version,decision FROM runs WHERE id=?",(run_id,)).fetchone()
    if not row: raise DomainError("NOT_FOUND","批次不存在",404)
    return {**json.loads(row[0]),"version":row[1],"decision":row[2]}
def latest():
    with connect() as db: row=db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None
def history():
    with connect() as db: return [{"run_id":i,"version":v,"decision":d} for i,v,d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")]
def review(run_id,version,decision):
    if decision not in {"已复核","退回复核"}: raise DomainError("BAD_DECISION","复核状态无效")
    with connect() as db:
        cursor=db.execute("UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?",(decision,run_id,version))
        if cursor.rowcount!=1:
            if not db.execute("SELECT 1 FROM runs WHERE id=?",(run_id,)).fetchone(): raise DomainError("NOT_FOUND","批次不存在",404)
            raise DomainError("STALE_VERSION","版本已更新，请刷新",409)
    return get(run_id)
def _now(): return datetime.now(timezone.utc).isoformat()
def _book(db,correction_id,run_id,adjustments,now):
    for item in adjustments:
        db.execute("INSERT INTO ledger(run_id,card,business_day,correction_id,receivable,delta,booked_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(run_id,card,business_day) DO UPDATE SET correction_id=excluded.correction_id,receivable=excluded.receivable,delta=excluded.delta",(run_id,item["card"],item["business_day"],correction_id,item["new_receivable"],item["delta"],now))
def save_correction(correction_id,run_id,payload):
    now=_now()
    with connect() as db:
        cursor=db.execute("INSERT OR IGNORE INTO corrections(id,run_id,status,payload,created) VALUES (?,?,?,?,?)",(correction_id,run_id,payload["status"],json.dumps(payload,ensure_ascii=False),now))
        if cursor.rowcount==1 and payload["status"]=="已核清": _book(db,correction_id,run_id,payload["adjustments"],now)
    return get_correction(correction_id)
def get_correction(correction_id):
    with connect() as db: row=db.execute("SELECT id,run_id,status,payload,created FROM corrections WHERE id=?",(correction_id,)).fetchone()
    if not row: raise DomainError("NOT_FOUND","校时批次不存在",404)
    return {**json.loads(row[3]),"correction_id":row[0],"run_id":row[1],"status":row[2],"created_at":row[4]}
def corrections_for(run_id):
    with connect() as db: rows=db.execute("SELECT id,status,payload,created FROM corrections WHERE run_id=? ORDER BY rowid DESC",(run_id,)).fetchall()
    return [{**json.loads(p),"correction_id":i,"status":s,"created_at":c} for i,s,p,c in rows]
def latest_correction(run_id):
    items=corrections_for(run_id)
    return items[0] if items else None
def latest_verified(run_id):
    with connect() as db: row=db.execute("SELECT id FROM corrections WHERE run_id=? AND status='已核清' ORDER BY rowid DESC LIMIT 1",(run_id,)).fetchone()
    return get_correction(row[0]) if row else None
def ledger_for(run_id):
    with connect() as db: rows=db.execute("SELECT card,business_day,correction_id,receivable,delta,booked_at FROM ledger WHERE run_id=? ORDER BY card,business_day",(run_id,)).fetchall()
    return [{"card":c,"business_day":d,"correction_id":i,"receivable":r,"delta":x,"booked_at":b} for c,d,i,r,x,b in rows]
def reissue(correction_id):
    correction=get_correction(correction_id)
    if correction["status"]!="已核清": raise DomainError("NOT_VERIFIED","校时未核清，无账可补",409)
    run_id=correction["run_id"];latest=latest_verified(run_id)
    if not latest or latest["correction_id"]!=correction_id: raise DomainError("SUPERSEDED","已有更新校时入账，补发以最新为准",409)
    with connect() as db: _book(db,correction_id,run_id,correction["adjustments"],_now())
    return {"correction_id":correction_id,"reissued":len(correction["adjustments"]),"ledger":ledger_for(run_id)}
def seal_for(run_id):
    with connect() as db: row=db.execute("SELECT correction_id,digest,sealed_at FROM seals WHERE run_id=?",(run_id,)).fetchone()
    return {"run_id":run_id,"correction_id":row[0],"digest":row[1],"sealed_at":row[2]} if row else None
def seal(run_id):
    get(run_id)
    existing=seal_for(run_id)
    if existing: return {**existing,"already":True}
    latest=latest_correction(run_id)
    if latest and latest["status"]!="已核清": raise DomainError("SEAL_BLOCKED","校时未核清，不得封存日结",409)
    correction_id=latest["correction_id"] if latest else "—"
    digest=hashlib.sha256(json.dumps({"run":run_id,"correction":correction_id,"ledger":ledger_for(run_id)},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with connect() as db: db.execute("INSERT OR IGNORE INTO seals(run_id,correction_id,digest,sealed_at) VALUES (?,?,?,?)",(run_id,correction_id,digest,_now()))
    return {**seal_for(run_id),"already":False}
