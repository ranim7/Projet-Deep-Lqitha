"""Lqitha tools API (FastAPI, port 8000).

This service holds the deterministic TOOLS that the n8n agents call over HTTP
(speech-to-text, storage, matcher, RAG retrieval, claims, notifications) and serves
the demo web app. All LLM reasoning (the agents) happens in n8n.

Run:  python -m uvicorn lqitha.api:app --port 8000
"""
import base64
import json
import os
import time
import uuid
from pathlib import Path

import requests
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, matching, rag, store
from .llm import run_prompt, transcribe

app = FastAPI(title="Lqitha tools API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
WEB = config.ROOT / "webapp"
N8N_WEBHOOK = os.getenv("N8N_WEBHOOK", "http://localhost:5678/webhook/lqitha")
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "lqitha-internal-dev-token")
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
app.mount("/uploads", StaticFiles(directory=str(config.UPLOADS)), name="uploads")
app.mount("/abo", StaticFiles(directory=str(config.RAW / "abo")), name="abo") if (config.RAW / "abo").exists() else None


def _check(token):
    if token != INTERNAL_TOKEN:
        raise HTTPException(403, "internal endpoint")


# ------------------------------------------------------------------ web app + chat proxy
@app.get("/")
def home():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})   # always serve the latest UI


@app.get("/health")
def health():
    ok = {"api": True}
    try:
        ok["ollama"] = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=3).ok
    except Exception:
        ok["ollama"] = False
    try:
        ok["n8n"] = requests.get(N8N_WEBHOOK.split("/webhook")[0] + "/healthz", timeout=3).ok
    except Exception:
        ok["n8n"] = False
    ok["groq_key"] = bool(config.GROQ_API_KEY)
    return ok


@app.post("/chat")
def chat(body: dict):
    """Web app -> n8n main workflow (keeps the browser away from CORS / internal URLs)."""
    t0 = time.time()
    try:
        r = requests.post(N8N_WEBHOOK, json=body, timeout=180)
    except Exception as e:
        raise HTTPException(502, f"n8n not reachable at {N8N_WEBHOOK}: {e}")
    try:
        data = r.json()
    except Exception:
        data = {"reply": r.text}
    if isinstance(data, list):
        data = data[0] if data else {}
    data["_latency_s"] = round(time.time() - t0, 1)
    return JSONResponse(data, status_code=200 if r.ok else 502)


@app.get("/places")
def places():
    p = config.PROCESSED / "places_tn.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


# ------------------------------------------------------------------ tools for the agents
class STTIn(BaseModel):
    audio_b64: str
    mime: str = "audio/webm"


@app.post("/tools/stt")
def stt(x: STTIn):
    ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/wav": "wav",
           "audio/mp4": "m4a", "audio/x-m4a": "m4a"}.get(x.mime.split(";")[0], "webm")
    text = transcribe(base64.b64decode(x.audio_b64), filename=f"voice.{ext}")
    return {"text": text, "model": config.STT_MODEL}


class ReportIn(BaseModel):
    type: str
    record: dict
    slots: dict = {}
    session_id: str | None = None
    language: str | None = None
    hidden_detail: str | None = None
    contact: dict | None = None
    image_b64: str | None = None


@app.post("/tools/reports")
def create_report(x: ReportIn):
    image_file = None
    if x.image_b64:
        image_file = f"{uuid.uuid4().hex[:10]}.jpg"
        (config.UPLOADS / image_file).write_bytes(base64.b64decode(x.image_b64.split(",")[-1]))
    rid = store.add_report(x.type, x.record, x.slots, x.session_id, x.language, x.hidden_detail,
                           x.contact, image_file)
    return {"report_id": rid, "image_url": f"/uploads/{image_file}" if image_file else None}


@app.get("/tools/reports/{rid}")
def read_report(rid: str):
    r = store.get_report(rid)
    if not r:
        raise HTTPException(404)
    return r


@app.get("/tools/reports")
def all_reports(type: str | None = None, status: str | None = "open"):
    return store.list_reports(type, status)


class MatchIn(BaseModel):
    report_id: str
    top_k: int = 3


@app.post("/tools/match")
def match(x: MatchIn):
    cands = matching.find_matches(report_id=x.report_id, top_k=x.top_k)
    rep = store.get_report(x.report_id)
    return {"report_id": x.report_id, "report": rep, "candidates": cands, "n": len(cands)}


class RagIn(BaseModel):
    query: str
    item_type: str | None = None
    top_k: int = 5
    strategy: str = "recursive"   # best configuration in scripts/eval_rag.py
    mode: str = "dense"


@app.post("/tools/rag/search")
def rag_search(x: RagIn):
    hits = rag.search(x.query, x.strategy, x.mode, x.top_k, x.item_type)
    for h in hits:
        h.pop("dense", None)
    return {"hits": hits, "context": rag.format_context(hits)}


class ClaimIn(BaseModel):
    lost_id: str
    found_id: str
    decision: str
    confidence: float
    challenge_question: str
    owner_message: str
    finder_message: str


@app.post("/tools/claims")
def create_claim(x: ClaimIn):
    cid = store.create_claim(x.lost_id, x.found_id, x.decision, x.confidence, x.challenge_question)
    lost, found = store.get_report(x.lost_id), store.get_report(x.found_id)
    store.notify(lost["session_id"], x.lost_id, "possible_match", x.owner_message,
                 {"claim_id": cid, "question": x.challenge_question, "found_id": x.found_id,
                  "image_url": f"/uploads/{found['image_file']}" if found.get("image_file") else None})
    if found.get("session_id"):
        store.notify(found["session_id"], x.found_id, "owner_identified", x.finder_message, {"claim_id": cid})
    return {"claim_id": cid, "owner_session": lost["session_id"]}


@app.get("/tools/claims/{cid}")
def read_claim(cid: str):
    c = store.get_claim(cid)
    if not c:
        raise HTTPException(404)
    return c


@app.get("/internal/claims/{cid}/secret")
def claim_secret(cid: str, x_internal_token: str = Header(None)):
    """Only n8n (claim-check agent) can read the finder's hidden detail."""
    _check(x_internal_token)
    c = store.get_claim(cid)
    if not c:
        raise HTTPException(404)
    s = store.secret(c["found_id"])
    return {"claim": c, "hidden_detail": s["hidden_detail"], "attempts": c["attempts"]}


class ClaimResult(BaseModel):
    verified: bool
    reason: str = ""


@app.post("/internal/claims/{cid}/result")
def claim_result(cid: str, x: ClaimResult, x_internal_token: str = Header(None)):
    _check(x_internal_token)
    c = store.get_claim(cid)
    if not c:
        raise HTTPException(404)
    if c["status"] in ("verified", "locked"):
        return {"status": c["status"]}
    if x.verified:
        store.update_claim(cid, "verified")
        store.set_status(c["lost_id"], "resolved")
        store.set_status(c["found_id"], "resolved")
        s = store.secret(c["found_id"])
        finder = json.loads(s["contact"] or "{}")
        lost = store.get_report(c["lost_id"])
        found = store.get_report(c["found_id"])
        if found.get("session_id"):
            store.notify(found["session_id"], c["found_id"], "owner_verified",
                         "The owner answered the verification question correctly. They will contact you to collect the item.",
                         {"owner_contact": store.get_report(c["lost_id"], public=False).get("contact", {})})
        return {"status": "verified", "pickup_place": s["place_name"], "finder_contact": finder,
                "lost_session": lost["session_id"]}
    attempts = c["attempts"] + 1
    store.update_claim(cid, "locked" if attempts >= 2 else "pending")
    return {"status": "locked" if attempts >= 2 else "retry", "attempts_left": max(0, 2 - attempts)}


@app.get("/tools/notifications")
def get_notifications(session_id: str, since: int = 0):
    return store.notifications(session_id, since)


class NotifyIn(BaseModel):
    session_id: str
    report_id: str | None = None
    kind: str = "info"
    message: str
    payload: dict | None = None


@app.post("/tools/notifications")
def post_notification(x: NotifyIn):
    store.notify(x.session_id, x.report_id, x.kind, x.message, x.payload)
    return {"ok": True}


@app.get("/tools/sessions/{session_id}/reports")
def session_reports(session_id: str):
    with store.conn() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM reports WHERE session_id=? ORDER BY created_at DESC",
                                       (session_id,)).fetchall()]
    out = []
    for i in ids:
        r = store.get_report(i)
        with store.conn() as c:
            cl = c.execute("SELECT status, decision FROM claims WHERE lost_id=? OR found_id=? ORDER BY created_at DESC LIMIT 1",
                           (i, i)).fetchone()
        r["claim"] = {"status": cl[0], "decision": cl[1]} if cl else None
        out.append(r)
    return {"reports": out}


@app.get("/tools/stats")
def stats():
    """Public counters for the web app (no personal data)."""
    with store.conn() as c:
        q = lambda sql, *a: c.execute(sql, a).fetchall()
        found_open = q("SELECT COUNT(*) FROM reports WHERE type='found' AND status='open'")[0][0]
        lost_open = q("SELECT COUNT(*) FROM reports WHERE type='lost' AND status='open'")[0][0]
        returned = q("SELECT COUNT(*) FROM claims WHERE status='verified'")[0][0]
        by_cat = dict(q("SELECT category, COUNT(*) FROM reports WHERE type='found' AND status='open' GROUP BY category ORDER BY 2 DESC"))
        by_gov = dict(q("SELECT governorate, COUNT(*) FROM reports WHERE type='found' AND status='open' AND governorate IS NOT NULL GROUP BY governorate ORDER BY 2 DESC"))
    return {"found_open": found_open, "lost_open": lost_open, "returned": returned, "by_category": by_cat, "by_governorate": by_gov}


@app.get("/internal/pairs/{lost_id}/{found_id}")
def read_pair(lost_id: str, found_id: str, x_internal_token: str = Header(None)):
    """Lost + found public records and the finder's secret (only for the n8n agents)."""
    _check(x_internal_token)
    lost, found, s = store.get_report(lost_id), store.get_report(found_id), store.secret(found_id)
    if not (lost and found and s):
        raise HTTPException(404)
    return {"lost": lost, "found": found, "hidden_detail": s["hidden_detail"]}


@app.post("/tools/log_error")
def log_error(body: dict):
    (config.ROOT / "logs").mkdir(exist_ok=True)
    with open(config.ROOT / "logs" / "n8n_errors.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), **body}, ensure_ascii=False) + "\n")
    return {"ok": True}


@app.get("/tools/rematch/candidates")
def rematch_candidates(min_score: float = 0.62):
    """Used by the daily n8n schedule: open lost reports that now have a strong candidate."""
    out = []
    with store.conn() as c:
        done = {(r[0], r[1]) for r in c.execute("SELECT lost_id, found_id FROM claims").fetchall()}
    for rep in store.list_reports("lost", "open"):
        cands = matching.find_matches(report_id=rep["id"], top_k=1)
        if cands and cands[0]["score"] >= min_score and (rep["id"], cands[0]["id"]) not in done:
            out.append({"lost_id": rep["id"], "found_id": cands[0]["id"], "score": cands[0]["score"]})
    return {"pairs": out}


# ------------------------------------------------------------------ board translation (Derja / French / English)
LANG_NAMES = {"derja": "Tunisian Derja (Tunisian Arabic in LATIN letters, arabizi)", "fr": "French", "en": "English",
              "ar": "Modern Standard Arabic (Arabic script)"}


STYLE = {
    "derja": ('"Tunisian Derja" = Tunisian Arabic written in LATIN letters with the digits 3, 7, 9, 5 (arabizi), as Tunisians '
              'write on Facebook - never Arabic script. French loanwords are normal (sac, portefeuille, écouteurs, pochette).\n'
              'Glossary: black k7el, white abyedh, red a7mer, blue az9e9, green a5dher, yellow asfer, brown marron, grey gris, '
              'pink rose, gold dhahbi, silver fodhi, leather jeld, plastic plastique, metal 7did, fabric 9mech, with fih / b, '
              'and w, small sghir, big kbir, bag sac, backpack sac à dos, wallet portefeuille, phone telifoun, keys mfete7, '
              'glasses mrayet, sunglasses mrayet chams, watch mangala, bottle dabbouza, umbrella mdhalla, shoes sabbat, case kasa.\n'
              'Examples: "Black leather bifold wallet with red stitching." -> "Portefeuille k7el jeld, fih khyata 7amra." | '
              '"Pink plastic phone case with a lion face design." -> "Kasa telifoun rose plastique, fiha wjeh sbou3." | '
              '"Brown tortoiseshell sunglasses with a metal frame." -> "Mrayet chams marron écaille, cadre 7did."'),
    "fr": ("Write correct, natural French (standard spelling, accents). Never use Derja or arabizi words. "
           'Example: "Black leather bifold wallet with red stitching." -> "Portefeuille noir en cuir à deux volets, coutures rouges."'),
    "ar": "Write Modern Standard Arabic in Arabic script.",
    "en": "Write natural English.",
}


class TranslateIn(BaseModel):
    ids: list[str]
    lang: str = "derja"


@app.post("/tools/translate")
def translate(x: TranslateIn):
    """Public descriptions of found items in the board language. English = the canonical record. Cached in SQLite."""
    lang = x.lang if x.lang in LANG_NAMES else "fr"
    out, todo = {}, []
    with store.conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS translations_v2(report_id TEXT, lang TEXT, text TEXT, PRIMARY KEY(report_id, lang))")
        for rid in x.ids[:40]:
            row = c.execute("SELECT text FROM translations_v2 WHERE report_id=? AND lang=?", (rid, lang)).fetchone()
            if row:
                out[rid] = row[0]
                continue
            r = store.get_report(rid)
            if not r:
                continue
            desc = (r["record"] or {}).get("canonical_description") or ""
            if lang == "en" or not desc:
                out[rid] = desc
            else:
                todo.append((rid, desc[:300]))
    if todo:
        try:
            parsed, *_ = run_prompt("translator", language_name=LANG_NAMES[lang], style=STYLE[lang],
                                    items="\n".join(f"{rid}: {d}" for rid, d in todo))
            tr = (parsed or {}).get("translations", {}) or {}
        except Exception:
            tr = {}
        with store.conn() as c:
            for rid, _ in todo:
                t = tr.get(rid)
                if isinstance(t, str) and t.strip():
                    out[rid] = t.strip()
                    c.execute("INSERT OR REPLACE INTO translations_v2 VALUES (?,?,?)", (rid, lang, t.strip()))
    return out
