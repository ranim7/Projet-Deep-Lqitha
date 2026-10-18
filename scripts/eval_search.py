"""Evaluation of the found-items search (multimodal RAG over the live database of found reports).

Test set (built once, cached): 18 found items of the demo database (12 with a photo - their record was produced by the
Perception agent from the photo - and 6 real Delhi Metro text records). For each item, gpt-oss-120b writes the message of
a person who LOST it and asks whether someone found it: from memory (1-3 details, never the full description), with the
city / place and a relative date ("lbera7", "il y a 3 jours", "last Tuesday"), in Derja, French or English.
The full production chain is then run on each message: intake v3 (intent + slots) -> Perception (text -> English record)
-> retrieval -> answer agent. The gold item must be ranked first among ~200 open found items.

Retrieval / generation configurations
  R0 dense only (no filter)            R1 dense + hard filters (category, governorate, date)
  R2 hard filters with relaxation      R3 R2 + hybrid score (BM25 + colour/brand/marks)
  R4 dense + SOFT constraints          R5 R4 + colour/brand/marks agreement
  G1 / G2  R3 candidates re-ranked by answer agent v1 / v2 (prompt comparison)
  G3       R4 + answer agent v2 = production
Metrics: Hit@1, Hit@3, MRR, recall@8 (gold among the 8 candidates), empty-result rate; intent accuracy of intake v3.
Outputs reports/results/search_eval.json + .md
"""
import json
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lqitha import config, search, store  # noqa: E402
from lqitha.evalutil import cached_chat, cached_prompt, hit_at, md_table, mrr, save_json  # noqa: E402

TODAY = "2026-10-18"
LANGS = ["derja", "fr", "en"]
GEN = """You write realistic test messages for a lost-and-found search engine in Tunisia.
For each item below, write the message of the person who LOST it and now asks whether someone has found it.
Rules: from memory, mention only 1 to 3 details of the item (never copy the full description, no brand if it is obscure);
mention the city or place where it was lost (the place given, or its governorate); say WHEN it was lost with a relative
expression computed from today's date {today} and the loss date given; write in the requested language:
"derja" = Tunisian Arabic in Latin letters (e.g. "fama chkoun l9a ...", "dhaya3t ... lbera7"), "fr" = French, "en" = English.
Max 30 words. Return JSON {{"queries": [{{"id": "...", "message": "..."}}]}}.
ITEMS:
{items}"""


def targets():
    with store.conn() as c:
        rows = c.execute("SELECT id, image_file FROM reports WHERE type='found' AND status='open' AND source='seed' ORDER BY id").fetchall()
    photo = [r[0] for r in rows if r[1]]
    text = [r[0] for r in rows if not r[1]]
    rnd = random.Random(11)
    return rnd.sample(photo, min(12, len(photo))) + rnd.sample(text, min(6, len(text)))


def gen_queries(ids):
    out = {}
    for b in range(0, len(ids), 9):
        lines = []
        for k, rid in enumerate(ids[b:b + 9]):
            r = store.get_report(rid)
            rec = r["record"] or {}
            lost = r["date_iso"]
            lines.append(json.dumps({"id": rid, "language": LANGS[(b + k) % 3], "description": rec.get("canonical_description"),
                                     "colour": rec.get("color_family"), "brand": rec.get("brand"), "marks": rec.get("distinctive_marks"),
                                     "place": r["place_name"], "governorate": r["governorate"], "loss_date": lost}, ensure_ascii=False))
        res = cached_chat("search_gen", [{"role": "user", "content": GEN.format(today=TODAY, items="\n".join(lines))}],
                          model=config.DATA_GEN_MODEL, temperature=0.7)
        for q in (res["parsed"] or {}).get("queries", []):
            if q.get("id") in ids and q.get("message"):
                out[q["id"]] = q["message"]
    return out


def rank_of(gold, ids):
    return ids.index(gold) if gold in ids else None


def llm_order(res, ver, message, record, slots, language):
    cands = res["results"]
    if not cands:
        return [], None
    r = cached_prompt("srch", "search_answer", ver, message=message, record=record, language=language,
                      place=slots.get("place_name") or slots.get("governorate") or "not given", date=slots.get("date_iso") or "not given",
                      relaxed=", ".join(res["relaxed"]) or "none", candidates=search.candidates_text(cands))
    out = (r["parsed"] or {}).get("results") or []
    rk = {"high": 0, "medium": 1, "low": 2}
    seen, order = set(), []
    pos = {c["id"]: i for i, c in enumerate(cands)}
    for x in sorted([x for x in out if isinstance(x, dict) and x.get("id") in pos], key=lambda x: (rk.get(x.get("relevance"), 2), pos[x["id"]])):
        if x["id"] not in seen:
            seen.add(x["id"])
            order.append((x["id"], x.get("relevance")))
    order += [(c["id"], "unranked") for c in cands if c["id"] not in seen]
    return order, r


if __name__ == "__main__":
    ids = targets()
    queries = gen_queries(ids)
    print(f"{len(queries)} test queries")
    CONFIGS = {"R0 dense only": dict(use_filters=False, hybrid=False), "R1 dense + strict filters": dict(relax=False, hybrid=False),
               "R2 dense + filters with relaxation": dict(hybrid=False), "R3 relaxation + hybrid score": dict(),
               "R4 dense + soft constraints (production)": "soft", "R5 soft + attribute agreement": "soft_attr"}
    ranks = {k: [] for k in CONFIGS}
    ranks.update({"G1 R3 + answer agent v1": [], "G2 R3 + answer agent v2": [], "G3 R4 + answer agent v2 (production)": []})
    empty = {k: 0 for k in CONFIGS}
    recall8, intents, high_prec, details = [], [], [], []
    for gold, msg in queries.items():
        lang = "derja" if any(w in msg.lower() for w in ("fama", "l9a", "dhaya3", "lbera7", "chkoun", "mta3", "mte3")) else None
        it = cached_prompt("srch_intake", "intake", "v3", today=TODAY, source="text", message=msg, has_photo=False, location="not given",
                           conversation={"turn": 0, "intent_so_far": None, "language": None, "slots_so_far": {}, "questions_already_asked": []})
        ip = it["parsed"] or {}
        intents.append(ip.get("intent") == "search_found")
        sl = ip.get("slots") or {}
        slots = {k: sl.get(k) for k in ("governorate", "place_name", "place_type", "date_iso") if sl.get(k)}
        pc = cached_prompt("srch_perc", "perception", "v2", report_type="lost", text=msg,
                           slots={k: v for k, v in sl.items() if k != "hidden_detail"}, has_photo=False)
        rec = pc["parsed"] or {}
        if rec.get("color_family") == "gray":
            rec["color_family"] = "grey"
        if not rec.get("category") or rec.get("category") not in config.CATEGORIES:
            rec["category"] = sl.get("item_category") if sl.get("item_category") in config.CATEGORIES else "other"
        row = {"gold": gold, "message": msg, "intent": ip.get("intent"), "slots": slots, "record": rec}
        prod = None
        for name, kw in CONFIGS.items():
            if kw == "soft":
                res = search.search_soft(rec, slots, top_k=8)
            elif kw == "soft_attr":
                res = search.search_soft(rec, slots, top_k=8, attributes=True)
            else:
                res = search.search_found(rec, slots, top_k=8, **kw)
            got = [x["id"] for x in res["results"]]
            if not got:
                empty[name] += 1
            ranks[name].append(rank_of(gold, got))
            row[name] = {"rank": rank_of(gold, got), "relaxed": res["relaxed"]}
            if name.startswith("R3"):
                r3 = res
            if name.startswith("R4"):
                prod = res
        recall8.append(gold in [x["id"] for x in prod["results"]])
        for ver, name, cand in (("v1", "G1 R3 + answer agent v1", r3), ("v2", "G2 R3 + answer agent v2", r3),
                                ("v2", "G3 R4 + answer agent v2 (production)", prod)):
            order, _ = llm_order(cand, ver, msg, rec, slots, ip.get("language") or lang or "fr")
            ranks[name].append(rank_of(gold, [o[0] for o in order]))
            row[name] = {"rank": ranks[name][-1], "labels": order[:4]}
            if name.startswith("G3"):
                high_prec += [o[0] == gold for o in order if o[1] == "high"]
        details.append(row)
        print(gold, {k: v[-1] for k, v in ranks.items()}, flush=True)
    n = len(details)
    table = [{"configuration": k, "Hit@1": hit_at(v, 1), "Hit@3": hit_at(v, 3), "MRR": mrr(v),
              "empty results": round(empty.get(k, 0) / max(1, n), 3) if k in empty else "-"} for k, v in ranks.items()]
    summary = {"n_queries": n, "pool": len(store.list_reports("found", "open", limit=5000)), "intent_accuracy": round(sum(intents) / max(1, n), 3),
               "recall@8 (production retrieval)": round(sum(recall8) / max(1, n), 3),
               "precision of 'high' labels (v2)": round(sum(high_prec) / max(1, len(high_prec)), 3) if high_prec else None}
    for r in table:
        print(r)
    print(summary)
    save_json("search_eval.json", {"summary": summary, "table": table, "details": details})
    (config.RESULTS / "search_eval.md").write_text(
        f"# Found-items search ({n} queries, Derja / French / English, pool of {summary['pool']} open found items)\n\n"
        + md_table(table, ["configuration", "Hit@1", "Hit@3", "MRR", "empty results"])
        + "\n\n" + "\n".join(f"- {k}: {v}" for k, v in summary.items()) + "\n", encoding="utf-8")
