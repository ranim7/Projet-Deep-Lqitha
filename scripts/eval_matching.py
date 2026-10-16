"""Matching + verifier evaluation on REAL items.

Set A (cross-modal):  found side = PHOTO of a real product (Amazon Berkeley Objects) -> Perception agent (vision)
                      lost side  = owner message in Derja / French / Arabic / English written by an LLM from the
                                   product metadata (no photo), -> Perception agent (text)
Set B (text, real):   found side = REAL found-item records from Delhi Metro (item + free-text description)
                      lost side  = owner message (Derja/FR/AR/EN) paraphrasing one record; distractors = 1,500 other records

Compared matchers (ablation): raw text similarity -> + Perception normalisation -> + hard filters -> + attribute re-scoring.
Verifier: v1 vs v2 on the top-2 candidates of the full matcher (true pairs + hard negatives).
Usage: python scripts/eval_matching.py [A] [B] [verifier]
"""
import csv
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lqitha import config, matching, rag  # noqa: E402
from lqitha.embeddings import cosine, embed  # noqa: E402
from lqitha.vectorstore import VectorStore  # noqa: E402
from lqitha.evalutil import cached_chat, cached_prompt, hit_at, md_table, mrr, save_json  # noqa: E402

random.seed(11)
N_A, N_B, N_VERIF, N_DISTRACT = 40, 40, 15, 1500
LANGS = ["derja"] * 35 + ["fr"] * 35 + ["ar"] * 15 + ["en"] * 15
PLACES = json.loads((config.PROCESSED / "places_tn.json").read_text(encoding="utf-8"))
PLACES = [p for p in PLACES if p["place_type"] in ("airport", "train_station", "metro", "bus_station", "louage", "street", "mall")]
GEN_SYS = """You simulate ordinary people in Tunisia who LOST an item and describe it to a lost-and-found chatbot.
Write ONE short message (12-40 words) in the requested language/style:
- derja: Tunisian Arabic written in Latin letters with numbers (3, 7, 9, 5), mixing French words, e.g. "dha3li sac ak7el fih..."
- fr: everyday French; ar: Modern Standard / Tunisian Arabic in Arabic script; en: simple English.
Rules: describe the object like an owner from memory (colour, type, material, a distinctive detail or content),
NOT like a product listing. Never copy the product title. Detail level: {level}. If level is "vague", do NOT mention the
brand or model. Include the place "{place}" and when it was lost ({when}).
Return JSON: {{"message": "..."}}"""
WHEN = {"derja": ["lbera7", "lyoum", "awel lbera7"], "fr": ["hier", "ce matin", "avant-hier"],
        "ar": ["أمس", "اليوم", "أول أمس"], "en": ["yesterday", "this morning", "two days ago"]}


def gen_lost_message(tag, facts, lang, place, level):
    msgs = [{"role": "system", "content": GEN_SYS.format(level=level, place=place, when=random.choice(WHEN[lang]))},
            {"role": "user", "content": f"Language/style: {lang}\nObject facts: {json.dumps(facts, ensure_ascii=False)}"}]
    r = cached_chat(tag, msgs, config.DATA_GEN_MODEL, temperature=0.8)
    return (r["parsed"] or {}).get("message") or r["raw"]


def rand_slots():
    p = random.choice(PLACES)
    d = date(2026, 9, 1) + timedelta(days=random.randint(0, 40))
    return {"place_name": p["name"], "place_type": p["place_type"], "governorate": p["governorate"], "date_iso": d.isoformat()}


def lost_slots_for(found_slots):
    d = date.fromisoformat(found_slots["date_iso"]) - timedelta(days=random.randint(0, 2))
    same = random.random() < 0.7
    return {**(found_slots if same else rand_slots()), "date_iso": d.isoformat()}


def collection(name, found):
    col = VectorStore(None)    # in-memory, isolated from the app database
    vecs = embed([f["record"].get("canonical_description") or f["text"] for f in found])
    for i in range(0, len(found), 200):
        part = found[i:i + 200]
        col.upsert(ids=[f["id"] for f in part], embeddings=vecs[i:i + 200],
                documents=[f["record"].get("canonical_description") or f["text"] for f in part],
                metadatas=[{"type": "found", "status": "open", "category": f["record"].get("category") or "other",
                            "date_ord": date.fromisoformat(f["slots"]["date_iso"]).toordinal(),
                            "record_json": json.dumps(f["record"], ensure_ascii=False),
                            "slots_json": json.dumps(f["slots"], ensure_ascii=False)} for f in part])
    return col, vecs


def rank_of(true_id, ranked_ids):
    return ranked_ids.index(true_id) if true_id in ranked_ids else None


def evaluate(name, lost, found, col, found_vecs):
    raw_vecs = embed([l["message"] for l in lost])
    ids = [f["id"] for f in found]
    res = {k: [] for k in ["M0 raw message, dense", "M1 + perception normalisation", "M2 + hard filters (category, date)",
                           "M3 full matcher (+ attribute re-scoring)"]}
    per_lang = {}
    for l, rv in zip(lost, raw_vecs):
        sims = sorted(range(len(found)), key=lambda j: -cosine(rv, found_vecs[j]))
        r0 = rank_of(l["true_id"], [ids[j] for j in sims[:25]])
        c1 = matching.find_matches(record=l["record"], slots=l["slots"], rtype="lost", top_k=25, fetch_k=25,
                                   use_filters=False, use_rescoring=False, collection=col)
        c2 = matching.find_matches(record=l["record"], slots=l["slots"], rtype="lost", top_k=25, fetch_k=25,
                                   use_filters=True, use_rescoring=False, collection=col)
        c3 = matching.find_matches(record=l["record"], slots=l["slots"], rtype="lost", top_k=25, fetch_k=25,
                                   use_filters=True, use_rescoring=True, collection=col)
        rs = [r0, rank_of(l["true_id"], [c["id"] for c in c1]), rank_of(l["true_id"], [c["id"] for c in c2]),
              rank_of(l["true_id"], [c["id"] for c in c3])]
        for k, r in zip(res, rs):
            res[k].append(r)
        per_lang.setdefault(l["lang"], []).append(rs[3])
        l["top_candidates"] = c3[:2]
    table = [{"matcher": k, "Hit@1": hit_at(v, 1), "Hit@3": hit_at(v, 3), "Hit@5": hit_at(v, 5), "MRR": mrr(v)}
             for k, v in res.items()]
    lang_table = [{"language": k, "n": len(v), "Hit@1": hit_at(v, 1), "Hit@3": hit_at(v, 3), "MRR": mrr(v)}
                  for k, v in sorted(per_lang.items())]
    print(name, json.dumps(table, indent=1), json.dumps(lang_table))
    return {"n_lost": len(lost), "n_found_pool": len(found), "ablation": table, "by_language": lang_table}


# ----------------------------------------------------------------------------- Set A
def set_a():
    items = list(csv.DictReader(open(config.PROCESSED / "items_catalog.csv", encoding="utf-8")))
    random.shuffle(items)
    by_cat = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it)
    pick = []
    while len(pick) < N_A:
        for c in sorted(by_cat):
            if by_cat[c] and len(pick) < N_A:
                pick.append(by_cat[c].pop())
    found, lost = [], []
    for k, it in enumerate(pick):
        fs = rand_slots()
        # Groq's free DAILY quota for Qwen-VL was exhausted by the other evaluations, so the photo side of this
        # benchmark uses the free LOCAL vision model (Ollama) with the same Perception v2 prompt.
        r = cached_prompt("perclocal", "perception", "v2", image_path=config.ROOT / it["image_path"], report_type="found",
                          text="(photo only)", slots={}, has_photo=True, model_override="local:" + config.LOCAL_VISION_MODEL)
        frec = r["parsed"] or {"category": it["category"], "canonical_description": it["item_name"]}
        found.append({"id": f"A-F{k}", "record": frec, "slots": fs, "text": it["item_name"], "item": it})
        lang = LANGS[k % len(LANGS)]
        facts = {"type": it["product_type"].lower().replace("_", " "), "colour": it["color"], "brand": it["brand"],
                 "material": it["material"], "keywords": it["keywords"][:120]}
        ls = lost_slots_for(fs)
        msg = gen_lost_message("genA", facts, lang, ls["place_name"], "vague" if k % 2 else "detailed")
        lr = cached_prompt("perclost", "perception", "v2", report_type="lost", text=msg, slots={}, has_photo=False)
        lrec = lr["parsed"] or {"canonical_description": msg}
        lost.append({"id": f"A-L{k}", "true_id": f"A-F{k}", "message": msg, "lang": lang, "record": lrec, "slots": ls})
        print(f"  set A: {k + 1}/{N_A}", flush=True)
    col, fv = collection("setA", found)
    out = evaluate("Set A (photo vs text)", lost, found, col, fv)
    save_json("matching_setA_cases.json", [{"lost_message": l["message"], "lang": l["lang"], "lost_record": l["record"],
                                            "true_found": next(f["record"] for f in found if f["id"] == l["true_id"]),
                                            "top_candidates": [{"id": c["id"], "score": c["score"]} for c in l["top_candidates"]],
                                            "correct_top1": bool(l["top_candidates"]) and l["top_candidates"][0]["id"] == l["true_id"]}
                                           for l in lost])
    return out, lost, found


# ----------------------------------------------------------------------------- Set B
def set_b():
    rows = list(csv.DictReader(open(config.PROCESSED / "delhi_metro_found_items.csv", encoding="utf-8")))
    rows = [r for r in rows if len(r["description"].split()) >= 2 and r["category"] != "other"]
    random.shuffle(rows)
    targets, distract = rows[:N_B], rows[N_B:N_B + N_DISTRACT]
    found, lost = [], []
    for k, r in enumerate(targets + distract):
        txt = f"{r['item_name'].title()}: {r['description'].lower()}"
        found.append({"id": f"B-F{k}", "record": {"category": r["category"], "canonical_description": txt},
                      "slots": rand_slots(), "text": txt})
    for k, r in enumerate(targets):
        lang = LANGS[(k * 7) % len(LANGS)]
        fs = found[k]["slots"]
        ls = lost_slots_for(fs)
        msg = gen_lost_message("genB", {"item": r["item_name"], "description": r["description"]}, lang, ls["place_name"], "detailed")
        lr = cached_prompt("perclost", "perception", "v2", report_type="lost", text=msg, slots={}, has_photo=False)
        lost.append({"id": f"B-L{k}", "true_id": f"B-F{k}", "message": msg, "lang": lang,
                     "record": lr["parsed"] or {"canonical_description": msg}, "slots": ls})
        print(f"  set B: {k + 1}/{N_B}", flush=True)
    col, fv = collection("setB", found)
    return evaluate("Set B (real Delhi Metro records)", lost, found, col, fv)


# ----------------------------------------------------------------------------- verifier
def verifier(lost, found):
    fmap = {f["id"]: f for f in found}
    out = []
    for v in ("v1", "v2"):
        tp = fp = fn = tn = poss_true = poss_false = 0
        for l in lost[:N_VERIF]:
            for c in l["top_candidates"][:2]:
                f = fmap[c["id"]]
                lrec = {**l["record"], "place": l["slots"]["place_name"], "date": l["slots"]["date_iso"]}
                frec = {**f["record"], "place": f["slots"]["place_name"], "date": f["slots"]["date_iso"]}
                r = cached_prompt("verif", "verifier", v, lost=lrec, found=frec, score=c["score"])
                d = (r["parsed"] or {}).get("decision", "no_match")
                truth = c["id"] == l["true_id"]
                if d == "match":
                    tp += truth; fp += not truth
                elif d == "possible_match":
                    poss_true += truth; poss_false += not truth
                else:
                    fn += truth; tn += not truth
        n_true = tp + fn + poss_true
        out.append({"version": v, "pairs": tp + fp + fn + tn + poss_true + poss_false, "true_pairs": n_true,
                    "match_precision": round(tp / (tp + fp), 3) if tp + fp else None,
                    "true_pairs_kept (match or possible)": round((tp + poss_true) / n_true, 3) if n_true else None,
                    "wrong_pairs_called_match": fp, "wrong_pairs_rejected": tn, "wrong_pairs_possible": poss_false})
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    steps = sys.argv[1:] or ["A", "B", "verifier"]
    prev = config.RESULTS / "matching_eval.json"
    R = json.loads(prev.read_text(encoding="utf-8")) if prev.exists() else {}
    lost_a = found_a = None
    if "A" in steps or "verifier" in steps:
        R["set_A"], lost_a, found_a = set_a()
        save_json("matching_eval.json", R)
    if "B" in steps:
        R["set_B"] = set_b()
        save_json("matching_eval.json", R)
    if "verifier" in steps:
        R["verifier"] = verifier(lost_a, found_a)
        save_json("matching_eval.json", R)
    md = ["# Matching & verification evaluation\n"]
    for k in ("set_A", "set_B"):
        if k in R:
            md += [f"\n## {k} - {R[k]['n_lost']} lost reports vs {R[k]['n_found_pool']} found reports\n",
                   md_table(R[k]["ablation"], ["matcher", "Hit@1", "Hit@3", "Hit@5", "MRR"]), "",
                   md_table(R[k]["by_language"], ["language", "n", "Hit@1", "Hit@3", "MRR"]), ""]
    if "verifier" in R:
        md += ["\n## Verifier v1 vs v2 (top-2 candidates of the full matcher, Set A)\n",
               md_table(R["verifier"], list(R["verifier"][0].keys()))]
    (config.RESULTS / "matching_eval.md").write_text("\n".join(md), encoding="utf-8")
    print("wrote reports/results/matching_eval.md")
