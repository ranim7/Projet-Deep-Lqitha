"""Found-items search: retrieval over the LIVE database of found reports (multimodal RAG, retrieval part).

The query is the normalised English record produced by the Perception agent from the user's text OR photo, so a
Derja sentence, a French sentence or a photo of the lost item all become the same kind of query as the indexed
found items (cross-modal).

Production mode "soft" (chosen by scripts/eval_search.py):
1. dense recall      : bge-m3 cosine over all open found items (top 60)
2. soft constraints  : score = 0.70 dense + 0.10 category + 0.10 place + 0.10 date. A wrong category (Perception error) or a
                       wrong governorate (Intake error) lowers an item instead of excluding it. Constraints that none of the
                       top-3 items satisfies are reported as "relaxed" so the answer agent can say the search was widened.
3. the generation step (LLM re-ranking + answer with item references) is done by the n8n agent "search_answer".
Evaluated alternatives (kept for the evaluation): hard filters, hard filters with progressive relaxation
(date -> place -> category), and a hybrid score (+ BM25 + colour/brand/marks agreement).
"""
import json

from . import store
from .embeddings import embed_one
from .matching import attr_score
from .rag import BM25

W_DENSE, W_LEX, W_ATTR = 0.60, 0.15, 0.25
LADDER = [("category", "governorate", "date"), ("category", "governorate"), ("category",), ()]


def query_text(record, fallback=""):
    rec = record or {}
    parts = [rec.get("canonical_description"), rec.get("brand"), rec.get("color_family"), rec.get("material"),
             " ".join(rec.get("distinctive_marks") or []), " ".join(rec.get("contents") or [])]
    return " ".join(str(p) for p in parts if p) or fallback


def _where(level, record, slots):
    conds = [{"type": "found"}, {"status": "open"}]
    cat = (record or {}).get("category")
    if "category" in level and cat and cat != "other":
        conds.append({"category": cat})
    gov = (slots or {}).get("governorate")
    if "governorate" in level and gov:
        conds.append({"governorate": gov})
    d = store._ord((slots or {}).get("date_iso") or "")
    if "date" in level and d:
        conds += [{"date_ord": {"$gte": d - 1}}, {"date_ord": {"$lte": d + 60}}]
    return {"$and": conds}


def search_found(record, slots=None, top_k=8, min_results=3, min_dense=0.35, use_filters=True, relax=True,
                 hybrid=True, fetch_k=40, text=""):
    """Returns {"results": [...], "applied": [...filters kept...], "relaxed": [...filters dropped...]}"""
    slots = slots or {}
    q = query_text(record, text)
    qv = embed_one(q)
    col = store.reports_col()
    ladder = LADDER if use_filters else [()]
    if use_filters and not relax:
        ladder = LADDER[:1]
    wanted = [f for f in LADDER[0] if (f == "category" and (record or {}).get("category") not in (None, "other"))
              or (f == "governorate" and slots.get("governorate")) or (f == "date" and slots.get("date_iso"))]
    used, pool, seen = (), [], set()
    for level in ladder:
        eff = tuple(f for f in level if f in wanted)
        if eff in seen:
            continue
        seen.add(eff)
        used = eff
        res = col.query(query_embeddings=[qv], n_results=fetch_k, where=_where(level, record, slots),
                        include=["metadatas", "distances", "documents"])
        pool = []
        for cid, dist, doc in zip(res["ids"][0], res["distances"][0], res["documents"][0]):
            rep = store.get_report(cid)
            if rep is None or rep.get("status") != "open":     # SQLite is the source of truth for the status
                continue
            pool.append({"rep": rep, "dense": 1 - dist, "doc": doc})
        if sum(1 for p in pool if p["dense"] >= min_dense) >= min_results:
            break
    if not pool:
        return {"results": [], "applied": list(used), "relaxed": [f for f in wanted if f not in used], "query": q}
    lex = BM25([p["doc"] for p in pool]).scores(q) if hybrid else [0] * len(pool)
    mx = max(lex) or 1.0
    out = []
    for p, l in zip(pool, lex):
        rep = p["rep"]
        a = attr_score(record or {}, rep["record"] or {}) if hybrid else 0.0
        score = W_DENSE * p["dense"] + W_LEX * (l / mx) + W_ATTR * a if hybrid else p["dense"]
        rec = rep["record"] or {}
        out.append({"id": rep["id"], "score": round(score, 4), "dense": round(p["dense"], 4), "lexical": round(l / mx, 4),
                    "attributes": round(a, 4), "category": rep["category"], "description": rec.get("canonical_description") or p["doc"],
                    "color_family": rec.get("color_family"), "brand": rec.get("brand"),
                    "distinctive_marks": rec.get("distinctive_marks") or [],
                    "place_name": rep.get("place_name"), "place_type": rep.get("place_type"), "governorate": rep.get("governorate"),
                    "date_iso": rep.get("date_iso"), "image_url": f"/uploads/{rep['image_file']}" if rep.get("image_file") else None})
    out.sort(key=lambda x: -x["score"])
    applied = list(used)
    return {"results": out[:top_k], "applied": applied, "relaxed": [f for f in wanted if f not in applied], "query": q}


def _fold(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t or "").lower())
    return "".join(ch for ch in t if not unicodedata.combining(ch))


def _place_ok(slots, rep):
    want = [_fold(slots.get("governorate")), _fold(slots.get("place_name"))]
    want = [w for w in want if w]
    if not want:
        return 0.5
    have = _fold(" ".join(str(x or "") for x in (rep.get("governorate"), rep.get("place_name"))))
    toks = {t for w in want for t in w.replace("-", " ").split() if len(t) >= 4}
    return 1.0 if any(w and w in have for w in want) or any(t in have for t in toks) else 0.0


def search_soft(record, slots=None, top_k=8, attributes=False, fetch_k=60, text=""):
    slots = slots or {}
    q = query_text(record, text)
    res = store.reports_col().query(query_embeddings=[embed_one(q)], n_results=fetch_k,
                                    where={"$and": [{"type": "found"}, {"status": "open"}]}, include=["distances", "documents"])
    cat = (record or {}).get("category")
    d = store._ord(slots.get("date_iso") or "")
    out = []
    for cid, dist, doc in zip(res["ids"][0], res["distances"][0], res["documents"][0]):
        rep = store.get_report(cid)
        if rep is None or rep.get("status") != "open":
            continue
        rec = rep["record"] or {}
        c_ok = 0.5 if cat in (None, "other") else (1.0 if rep["category"] == cat else 0.0)
        p_ok = _place_ok(slots, rep)
        rd = store._ord(rep.get("date_iso") or "")
        d_ok = 0.5 if not (d and rd) else (1.0 if d - 1 <= rd <= d + 60 else 0.0)
        dense = 1 - dist
        a = attr_score(record or {}, rec) if attributes else 0.0
        score = (0.60 * dense + 0.10 * a if attributes else 0.70 * dense) + 0.10 * c_ok + 0.10 * p_ok + 0.10 * d_ok
        out.append({"id": rep["id"], "score": round(score, 4), "dense": round(dense, 4), "category_ok": c_ok, "place_ok": p_ok,
                    "date_ok": d_ok, "attributes": round(a, 4), "category": rep["category"],
                    "description": rec.get("canonical_description") or doc, "color_family": rec.get("color_family"),
                    "brand": rec.get("brand"), "distinctive_marks": rec.get("distinctive_marks") or [],
                    "place_name": rep.get("place_name"), "place_type": rep.get("place_type"), "governorate": rep.get("governorate"),
                    "date_iso": rep.get("date_iso"), "image_url": f"/uploads/{rep['image_file']}" if rep.get("image_file") else None})
    out.sort(key=lambda x: -x["score"])
    top = out[:3]
    given = {"category": cat not in (None, "other"), "place": bool(slots.get("governorate") or slots.get("place_name")),
             "date": bool(d)}
    key = {"category": "category_ok", "place": "place_ok", "date": "date_ok"}
    relaxed = [k for k, g in given.items() if g and top and not any(x[key[k]] == 1.0 for x in top)]
    return {"results": out[:top_k], "applied": [k for k, g in given.items() if g and k not in relaxed], "relaxed": relaxed,
            "query": q, "mode": "soft"}


def candidates_text(results):
    """Compact list for the answer / re-ranking agent (public fields only - never the hidden detail)."""
    lines = []
    for r in results:
        bits = [r["category"], r.get("color_family"), r.get("brand"), ", ".join(r.get("distinctive_marks") or [])]
        lines.append(f"{r['id']}: {r['description']} | {' / '.join(b for b in bits if b)} | found at {r.get('place_name') or '?'} "
                     f"({r.get('governorate') or '?'}) on {r.get('date_iso') or '?'}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    rec = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {"category": "bag", "color_family": "black", "canonical_description": "black backpack"}
    print(json.dumps(search_found(rec, {}), ensure_ascii=False, indent=1)[:3000])
