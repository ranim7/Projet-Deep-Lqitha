"""Retrieval evaluation: 2 chunking strategies x 2 retrieval modes x metadata filter on/off.

A retrieved chunk is RELEVANT if it comes from one of the gold documents of the query AND contains all the
"must" key phrases of that gold entry (accent/case-insensitive). Metrics: Hit@1, Hit@3, Hit@5, MRR.
Outputs reports/results/rag_eval.json + rag_eval.md
"""
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lqitha import config, rag  # noqa: E402
from lqitha.evalutil import hit_at, md_table, mrr, norm, save_json  # noqa: E402

QUERIES = json.loads((config.ROOT / "data" / "eval" / "rag_queries.json").read_text(encoding="utf-8"))


def relevant(hit, gold):
    t = norm(hit["text"])
    return any(hit["doc_id"] == g["doc"] and all(norm(m) in t for m in g["must"]) for g in gold)


if __name__ == "__main__":
    available = {p.stem for p in (config.KB_DIR / "docs").glob("*.md")}
    queries = []
    for q in QUERIES:
        gold = [g for g in q["gold"] if g["doc"] in available]
        if gold:
            queries.append({**q, "gold": gold})
    print(f"{len(queries)}/{len(QUERIES)} queries have a gold document in the corpus")
    stats = json.loads((config.KB_DIR / "kb_build_stats.json").read_text(encoding="utf-8"))
    configs, details = [], []
    for strategy in ("structure", "recursive"):
        for mode in ("dense", "hybrid"):
            for use_filter in (False, True):
                ranks, by_lang, t0 = [], defaultdict(list), time.time()
                for q in queries:
                    hits = rag.search(q["query"], strategy, mode, top_k=5, item_type=q["item_type"] if use_filter else None)
                    r = next((i for i, h in enumerate(hits) if relevant(h, q["gold"])), None)
                    ranks.append(r)
                    by_lang[q["lang"]].append(r)
                    details.append({"config": f"{strategy}/{mode}/{'filter' if use_filter else 'nofilter'}", "id": q["id"],
                                    "query": q["query"], "rank": r, "top_doc": hits[0]["doc_id"] if hits else None,
                                    "top_text": hits[0]["text"][:200] if hits else None})
                configs.append({"chunking": strategy, "retrieval": mode, "item_type filter": "yes" if use_filter else "no",
                                "Hit@1": hit_at(ranks, 1), "Hit@3": hit_at(ranks, 3), "Hit@5": hit_at(ranks, 5), "MRR": mrr(ranks),
                                "ms/query": round(1000 * (time.time() - t0) / len(queries)),
                                **{f"Hit@3 {k}": hit_at(v, 3) for k, v in sorted(by_lang.items())}})
                print(configs[-1])
    best = max(configs, key=lambda c: (c["Hit@3"], c["MRR"]))
    # ---- experiment: query rewriting by the intake agent (search_query_fr), best chunking/retrieval, filter off/on
    try:
        from lqitha.evalutil import cached_prompt
        for use_filter in (False, True):
            ranks, by_lang, rewrites = [], defaultdict(list), []
            for q in queries:
                r = cached_prompt("intake_rw", "intake", "v2", today="2026-10-14", source="text", message=q["query"],
                                  has_photo=False, location="not given",
                                  conversation={"turn": 0, "intent_so_far": None, "slots_so_far": {}, "questions_already_asked": []})
                rq = ((r["parsed"] or {}).get("search_query_fr") or q["query"])
                hits = rag.search(rq, best["chunking"], best["retrieval"], top_k=5,
                                  item_type=q["item_type"] if use_filter else None)
                rank = next((i for i, h in enumerate(hits) if relevant(h, q["gold"])), None)
                ranks.append(rank)
                by_lang[q["lang"]].append(rank)
                rewrites.append({"id": q["id"], "query": q["query"], "rewritten": rq, "rank": rank})
            configs.append({"chunking": best["chunking"], "retrieval": best["retrieval"] + " + query rewriting",
                            "item_type filter": "yes" if use_filter else "no", "Hit@1": hit_at(ranks, 1),
                            "Hit@3": hit_at(ranks, 3), "Hit@5": hit_at(ranks, 5), "MRR": mrr(ranks), "ms/query": None,
                            **{f"Hit@3 {k}": hit_at(v, 3) for k, v in sorted(by_lang.items())}})
            save_json("rag_query_rewrites.json", rewrites)
            print(configs[-1])
    except Exception as e:
        print("query rewriting experiment skipped:", e)
    save_json("rag_eval.json", {"n_queries": len(queries), "kb": stats, "configs": configs, "best": best, "details": details})
    cols = ["chunking", "retrieval", "item_type filter", "Hit@1", "Hit@3", "Hit@5", "MRR", "ms/query"] + \
           [k for k in configs[0] if k.startswith("Hit@3 ")]
    misses = [d for d in details if d["config"] == f"{best['chunking']}/{best['retrieval']}/{'filter' if best['item_type filter'] == 'yes' else 'nofilter'}" and (d["rank"] is None or d["rank"] > 2)]
    md = ["# Retrieval evaluation\n",
          f"{len(queries)} test queries (French, Arabic, Derja, English) with gold documents + key phrases.",
          f"Knowledge base: {stats['documents']} documents, {stats['chars']:,} characters, embeddings {stats['embed_model']} (Ollama, local).\n",
          md_table(stats["strategies"], ["strategy", "n_chunks", "avg_chars", "min_chars", "max_chars"]), "",
          md_table(configs, cols), "",
          f"**Best configuration:** {best['chunking']} chunking + {best['retrieval']} retrieval, filter={best['item_type filter']} "
          f"(Hit@3 {best['Hit@3']}, MRR {best['MRR']}).\n", "## Failures of the best configuration (relevant chunk not in top-3)\n"]
    for d in misses:
        md.append(f"- {d['id']} `{d['query']}` -> rank {d['rank']}; top-1 from **{d['top_doc']}**")
    (config.RESULTS / "rag_eval.md").write_text("\n".join(md), encoding="utf-8")
    print("best:", best)
