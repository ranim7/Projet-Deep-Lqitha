"""Matcher (agent 3 tool). Deterministic and explainable:

1. hard filters  : opposite report type, same category, status open,
                   found date in [lost date - 1 day, lost date + 60 days]
2. dense recall  : bge-m3 cosine between the two canonical English descriptions (top fetch_k)
3. re-scoring    : final = 0.60 * semantic + 0.25 * attributes + 0.15 * context
                   attributes = color family, brand, distinctive marks/contents overlap
                   context    = same governorate / same place type
"""
import json
import re

from . import store
from .embeddings import embed_one

CLOSE_COLORS = [{"grey", "silver"}, {"beige", "brown"}, {"white", "grey"}, {"gold", "yellow"},
                {"pink", "red"}, {"blue", "purple"}, {"black", "grey"}]
W_SEM, W_ATTR, W_CTX = 0.60, 0.25, 0.15


def color_score(a, b):
    if not a or not b:
        return 0.5
    if a == b:
        return 1.0
    if {a, b} in CLOSE_COLORS or "multicolor" in (a, b):
        return 0.6
    return 0.0


def brand_score(a, b):
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    if not a or not b:
        return 0.5
    return 1.0 if (a in b or b in a) else 0.0


def _tokens(lst):
    return set(w for x in (lst or []) for w in re.findall(r"[a-z0-9]{3,}", str(x).lower()))


def marks_score(ra, rb):
    a = _tokens(ra.get("distinctive_marks")) | _tokens(ra.get("contents"))
    b = _tokens(rb.get("distinctive_marks")) | _tokens(rb.get("contents"))
    if not a or not b:
        return 0.5
    return min(1.0, 0.5 + len(a & b) / max(3, min(len(a), len(b))))


def attr_score(ra, rb):
    return round(0.45 * color_score(ra.get("color_family"), rb.get("color_family"))
                 + 0.30 * brand_score(ra.get("brand"), rb.get("brand"))
                 + 0.25 * marks_score(ra, rb), 4)


def context_score(sa, sb):
    s = 0.5
    if sa.get("governorate") and sb.get("governorate"):
        s = 1.0 if sa["governorate"].lower() == sb["governorate"].lower() else 0.2
    if sa.get("place_type") and sa.get("place_type") == sb.get("place_type"):
        s = min(1.0, s + 0.2)
    return s


def find_matches(report_id=None, record=None, slots=None, rtype=None, top_k=5, fetch_k=25,
                 use_filters=True, use_rescoring=True, collection=None):
    """Return ranked candidates of the opposite type. Either report_id or (record, slots, rtype)."""
    if report_id:
        rep = store.get_report(report_id)
        record, slots, rtype = rep["record"], rep["slots"], rep["type"]
        date_iso = rep["date_iso"]
    else:
        date_iso = (slots or {}).get("date_iso")
    slots = slots or {}
    col = collection or store.reports_col()
    target = "found" if rtype == "lost" else "lost"
    where = [{"type": target}, {"status": "open"}]
    if use_filters:
        if record.get("category"):
            where.append({"category": record["category"]})
        d = store._ord(date_iso) if date_iso else 0
        if d:
            if target == "found":
                where += [{"date_ord": {"$gte": d - 1}}, {"date_ord": {"$lte": d + 60}}]
            else:
                where += [{"date_ord": {"$lte": d + 1}}, {"date_ord": {"$gte": d - 60}}]
    text = record.get("canonical_description") or json.dumps(record)
    res = col.query(query_embeddings=[embed_one(text)], n_results=fetch_k,
                    where={"$and": where} if len(where) > 1 else where[0],
                    include=["metadatas", "distances", "documents"])
    out = []
    for cid, meta, dist, doc in zip(res["ids"][0], res["metadatas"][0], res["distances"][0], res["documents"][0]):
        sem = 1 - dist
        cand = store.get_report(cid) if collection is None else None
        if cand is not None and cand.get("status") != "open":   # SQLite is the source of truth for the status
            continue
        crec = cand["record"] if cand else json.loads(meta.get("record_json", "{}") or "{}")
        cslots = cand["slots"] if cand else json.loads(meta.get("slots_json", "{}") or "{}")
        a = attr_score(record, crec) if use_rescoring else 0
        cx = context_score(slots, cslots) if use_rescoring else 0
        final = W_SEM * sem + W_ATTR * a + W_CTX * cx if use_rescoring else sem
        out.append({"id": cid, "score": round(final, 4), "semantic": round(sem, 4), "attributes": a,
                    "context": cx, "canonical_description": doc,
                    "record": crec, "place_name": (cand or {}).get("place_name"),
                    "governorate": (cand or {}).get("governorate"), "date_iso": (cand or {}).get("date_iso")})
    out.sort(key=lambda x: -x["score"])
    return out[:top_k]
