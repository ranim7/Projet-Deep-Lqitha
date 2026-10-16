"""Report storage: SQLite (records, secrets, claims, notifications) + vector store (report embeddings)."""
import json
import sqlite3
import time
import uuid
from datetime import date

from . import config
from .embeddings import embed_one
from .vectorstore import VectorStore

DB = config.STORE / "lqitha.sqlite"
_chroma = None


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS reports(
        id TEXT PRIMARY KEY, type TEXT, status TEXT, session_id TEXT, language TEXT,
        category TEXT, governorate TEXT, place_type TEXT, place_name TEXT, date_iso TEXT,
        record_json TEXT, slots_json TEXT, hidden_detail TEXT, contact TEXT, image_file TEXT,
        source TEXT, created_at REAL);
    CREATE TABLE IF NOT EXISTS claims(
        id TEXT PRIMARY KEY, lost_id TEXT, found_id TEXT, decision TEXT, confidence REAL,
        challenge_question TEXT, status TEXT, attempts INTEGER DEFAULT 0, created_at REAL);
    CREATE TABLE IF NOT EXISTS notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, report_id TEXT, kind TEXT,
        message TEXT, payload TEXT, created_at REAL);
    """)
    return c


def reports_col():
    global _chroma
    if _chroma is None:
        _chroma = VectorStore(config.STORE / "vectors" / "reports")
    return _chroma


def _ord(d):
    try:
        return date.fromisoformat(d).toordinal()
    except Exception:
        return 0


def add_report(rtype, record, slots=None, session_id=None, language=None, hidden_detail=None,
               contact=None, image_file=None, source="app", report_id=None):
    slots = slots or {}
    rid = report_id or ("L-" if rtype == "lost" else "F-") + uuid.uuid4().hex[:8]
    category = record.get("category") or slots.get("item_category") or "other"
    date_iso = slots.get("date_iso") or date.today().isoformat()
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (rid, rtype, "open", session_id, language, category, slots.get("governorate"),
                   slots.get("place_type"), slots.get("place_name"), date_iso,
                   json.dumps(record, ensure_ascii=False), json.dumps(slots, ensure_ascii=False),
                   hidden_detail, json.dumps(contact or {}, ensure_ascii=False), image_file, source, time.time()))
    text = record.get("canonical_description") or json.dumps(record, ensure_ascii=False)
    reports_col().upsert(ids=[rid], embeddings=[embed_one(text)], documents=[text], metadatas=[{
        "type": rtype, "category": category, "status": "open", "date_ord": _ord(date_iso),
        "governorate": slots.get("governorate") or "", "color_family": record.get("color_family") or "",
        "brand": (record.get("brand") or "").lower()}])
    return rid


def get_report(rid, public=True):
    with conn() as c:
        r = c.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["record"] = json.loads(d.pop("record_json"))
    d["slots"] = json.loads(d.pop("slots_json") or "{}")
    d["contact"] = json.loads(d.get("contact") or "{}")
    if public:
        d.pop("hidden_detail", None)
        d.pop("contact", None)
        d["has_hidden_detail"] = bool(r["hidden_detail"])
    return d


def list_reports(rtype=None, status="open", limit=200):
    q, args = "SELECT id FROM reports WHERE 1=1", []
    if rtype:
        q += " AND type=?"; args.append(rtype)
    if status:
        q += " AND status=?"; args.append(status)
    q += " ORDER BY created_at DESC LIMIT ?"; args.append(limit)
    with conn() as c:
        ids = [r[0] for r in c.execute(q, args).fetchall()]
    return [get_report(i) for i in ids]


def set_status(rid, status):
    with conn() as c:
        c.execute("UPDATE reports SET status=? WHERE id=?", (status, rid))
    try:
        reports_col().update(ids=[rid], metadatas=[{"status": status}])
    except Exception:
        pass


def notify(session_id, report_id, kind, message, payload=None):
    with conn() as c:
        c.execute("INSERT INTO notifications(session_id, report_id, kind, message, payload, created_at) VALUES (?,?,?,?,?,?)",
                  (session_id, report_id, kind, message, json.dumps(payload or {}, ensure_ascii=False), time.time()))


def notifications(session_id, since=0):
    with conn() as c:
        rows = c.execute("SELECT * FROM notifications WHERE session_id=? AND id>? ORDER BY id", (session_id, since)).fetchall()
    return [dict(r) | {"payload": json.loads(r["payload"] or "{}")} for r in rows]


def create_claim(lost_id, found_id, decision, confidence, question):
    cid = "C-" + uuid.uuid4().hex[:8]
    with conn() as c:
        c.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?)",
                  (cid, lost_id, found_id, decision, confidence, question, "pending", 0, time.time()))
    return cid


def get_claim(cid):
    with conn() as c:
        r = c.execute("SELECT * FROM claims WHERE id=?", (cid,)).fetchone()
    return dict(r) if r else None


def update_claim(cid, status, inc_attempt=True):
    with conn() as c:
        c.execute("UPDATE claims SET status=?, attempts=attempts+? WHERE id=?", (status, 1 if inc_attempt else 0, cid))


def secret(found_id):
    with conn() as c:
        r = c.execute("SELECT hidden_detail, contact, place_name, place_type FROM reports WHERE id=?", (found_id,)).fetchone()
    return dict(r) if r else None
