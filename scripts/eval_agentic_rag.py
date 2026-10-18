"""Evaluation of the agentic RAG (personalised recovery plan) against the single-query RAG it replaces.

Test set: data/eval/plan_cases.json - 12 situations in Derja, French, English and Arabic. Each case lists the NEEDS a good
plan must cover (e.g. "block the bank card", "ask at the louage station", "declare the loss of the ID card"), the documents
of the knowledge base that answer each need, and key phrases that show the need is covered in the final answer.

Pipelines (same models as in n8n, answers cached in reports/results/cache):
  B   single-query RAG (previous version): intake v3 query rewriting -> 1 dense search (top 4, item filter) -> procedure v2
  A1  agentic retrieval: planner (rag_planner) -> 1 routed search per sub-question (all candidates, max 15)
  A2  A1 + LLM re-ranking (rag_reranker) -> final context given to the writer
  W1 / W2  recovery_plan v1 / v2 on the A2 context, then faithfulness checker (rag_checker) -> unsupported steps removed

Metrics
  need recall@context  share of needs with at least one passage from one of their documents in the context
  context precision    share of context passages that come from a document needed by the case
  need coverage        share of needs whose key phrases appear in the answer delivered to the user
  cited steps          share of steps with at least one valid citation [n]
  faithful steps       share of steps labelled supported or partially by the checker (before removal)
  supported steps      share labelled supported
  letter type          the planner chose the expected letter (police declaration / airline claim / none)
Outputs reports/results/agentic_rag_eval.json + .md
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lqitha import config, rag  # noqa: E402
from lqitha.evalutil import cached_prompt, md_table, norm, save_json  # noqa: E402

TODAY = "2026-10-18"     # fixed so that cached answers stay valid
CASES = json.loads((config.ROOT / "data" / "eval" / "plan_cases.json").read_text(encoding="utf-8"))
SRC = ["administration", "law", "operator", "sop"]
TAGS = ["national_id", "passport", "family_booklet", "driving_licence", "bank_card", "baggage", "phone", "general"]


def need_recall(ctx, case):
    docs = {p["doc_id"] for p in ctx}
    return sum(1 for n in case["needs"] if docs & set(n["docs"])) / len(case["needs"])


def precision(ctx, case):
    needed = {d for n in case["needs"] for d in n["docs"]}
    return sum(1 for p in ctx if p["doc_id"] in needed) / max(1, len(ctx))


def coverage(text, case):
    t = norm(text)
    return sum(1 for n in case["needs"] if any(norm(k) in t for k in n["kw"])) / len(case["needs"])


def planner(case):
    sl = case["slots"]
    r = cached_prompt("arag", "rag_planner", "v1", today=TODAY, message=case["message"], language=case["lang"], intent=case["intent"],
                      item_category=sl.get("item_category") or "unknown", place_type=sl.get("place_type") or "unknown",
                      place_name=sl.get("place_name") or "unknown", date_iso=sl.get("date_iso") or "unknown")
    p = r["parsed"] or {}
    sq = [{"q": str(x["q"])[:200], "sources": [s for s in (x.get("sources") or []) if s in SRC][:2],
           "item_type": x.get("item_type") if x.get("item_type") in TAGS else "general", "need": x.get("need", "")}
          for x in (p.get("sub_questions") or []) if isinstance(x, dict) and x.get("q")][:5]
    return p, sq


def rerank(sq, passages):
    r = cached_prompt("arag", "rag_reranker", "v1", sub_questions="\n".join(f"Q{i + 1}. {x['q']}" for i, x in enumerate(sq)),
                      candidates=rag.candidates_text(passages))
    by = {p["pid"]: p for p in passages}
    picked = []
    sel = (r["parsed"] or {}).get("selection") or []
    for s in sorted([s for s in sel if isinstance(s, dict)], key=lambda s: s.get("sub_question") or 0):
        for x in (s.get("passages") or [])[:2]:
            m = re.search(r"\d+", str(x.get("pid") if isinstance(x, dict) else x))
            if not m:
                continue
            pid = int(m.group(0))
            score = x.get("score", 2) if isinstance(x, dict) else 2
            if pid in by and pid not in picked and score >= 2:
                picked.append(pid)
    if not picked:     # same fallback as the n8n workflow
        for i in range(len(sq)):
            for p in [p for p in passages if i + 1 in p["for"]][:2]:
                if p["pid"] not in picked:
                    picked.append(p["pid"])
    return [by[i] for i in picked[:8]]


def context_text(ctx):
    return "\n\n".join(f"[{i + 1}] ({p['authority']} - {p['url']})\n{p['text']}" for i, p in enumerate(ctx))


def check(tag, steps, ctx):
    """steps: [{"action", "cites"}] -> labels from the checker (no citation = unsupported)."""
    txt = "\n\n".join(f"STEP {i + 1}: {s['action']}\n" + ("\n".join(f"  [{k}] {' '.join(ctx[k - 1]['text'].split())[:550]}" for k in s["cites"])
                                                         if s["cites"] else "  (no citation)") for i, s in enumerate(steps))
    if not steps:
        return []
    r = cached_prompt(tag, "rag_checker", "v1", steps=txt)
    lab = {int(v.get("step", 0)): v.get("label") for v in ((r["parsed"] or {}).get("verdicts") or []) if isinstance(v, dict)}
    return [("unsupported" if not s["cites"] else lab.get(i + 1, "unsupported")) for i, s in enumerate(steps)]


def plan_steps(plan, n):
    out = []
    for s in (plan.get("steps") or [])[:8]:
        if not isinstance(s, dict) or not s.get("action"):
            continue
        cites = {int(c) for c in (s.get("cites") or []) if str(c).isdigit()} | {int(m) for m in re.findall(r"\[(\d+)\]", str(s["action"]))}
        out.append({"action": str(s["action"]), "when": s.get("when"), "cites": sorted(c for c in cites if 1 <= c <= n)})
    return out


def plan_text(plan, steps, labels):
    kept = [s["action"] for s, l in zip(steps, labels) if l != "unsupported"]
    extra = [str(plan.get("summary") or "")] + [str(d) for d in plan.get("documents") or []] + \
            [str(d.get("what", "")) for d in plan.get("deadlines") or [] if isinstance(d, dict)]
    return " ".join(kept + extra)


def baseline_steps(answer, n):
    """The steps of the single-query answer = its numbered lines (the "Documents" bullet list is not a step)."""
    lines = [l.strip() for l in str(answer).splitlines() if re.match(r"^\s*\d+[.)]\s+", l)] or \
            [l.strip() for l in str(answer).splitlines() if re.match(r"^\s*[-*•]\s+", l)]
    return [{"action": l, "cites": sorted({int(m) for m in re.findall(r"\[(\d+)\]", l) if 1 <= int(m) <= n})} for l in lines]


if __name__ == "__main__":
    rows = {k: [] for k in ("B", "A1", "A2", "W1", "W2")}
    details = []
    for case in CASES:
        sl = case["slots"]
        # ---------------- B: single-query RAG (previous production path)
        it = cached_prompt("arag_intake", "intake", "v3", today=TODAY, source="text", message=case["message"], has_photo=False,
                           location="not given", conversation={"turn": 0, "intent_so_far": None, "language": None,
                                                               "slots_so_far": {}, "questions_already_asked": []})
        q = ((it["parsed"] or {}).get("search_query_fr")) or case["message"]
        hits = rag.search(q, "recursive", "dense", top_k=4, item_type=sl.get("item_category"))
        b_ctx = [{**h, "source": rag.source_of(h["doc_id"])} for h in hits]
        pr = cached_prompt("arag", "procedure", "v2", context=rag.format_context(hits), question=case["message"], language=case["lang"],
                           item_category=sl.get("item_category") or "unknown", place_type=sl.get("place_type") or "unknown", intent=case["intent"])
        b_ans = (pr["parsed"] or {}).get("answer") or ""
        b_steps = baseline_steps(b_ans, len(b_ctx))
        b_lab = check("arag_chkB", b_steps, b_ctx)
        # ---------------- A: agentic RAG
        p, sq = planner(case)
        if not sq:
            sq = [{"q": q, "sources": [], "item_type": "general", "need": "what to do"}]
        passages = rag.multi_search(sq, per_question=3)
        ctx = rerank(sq, passages)
        letter = p.get("letter") if p.get("letter") in ("police_declaration", "airline_claim") else "none"
        res = {}
        for ver in ("v1", "v2"):
            w = cached_prompt("arag", "recovery_plan", ver, today=TODAY, situation=p.get("situation") or case["message"],
                              affected_items=p.get("affected_items") or [], urgency=p.get("urgency") or "normal", language=case["lang"],
                              context=context_text(ctx), letter_type=letter, date_iso=sl.get("date_iso") or "unknown",
                              place=", ".join(x for x in (sl.get("place_name"), sl.get("place_type")) if x) or "unknown", message=case["message"])
            plan = w["parsed"] or {}
            st = plan_steps(plan, len(ctx))
            lab = check(f"arag_chk{ver}", st, ctx)
            res[ver] = (plan, st, lab)

        def gen_metrics(steps, labels, text):
            n = max(1, len(steps))
            return {"need coverage": coverage(text, case), "cited steps": sum(1 for s in steps if s["cites"]) / n,
                    "faithful steps": sum(1 for l in labels if l in ("supported", "partially")) / n,
                    "supported steps": sum(1 for l in labels if l == "supported") / n, "steps": len(steps)}
        rows["B"].append({"need recall@context": need_recall(b_ctx, case), "context precision": precision(b_ctx, case),
                          "passages": len(b_ctx), **gen_metrics(b_steps, b_lab, b_ans)})
        rows["A1"].append({"need recall@context": need_recall(passages, case), "context precision": precision(passages, case),
                           "passages": len(passages)})
        rows["A2"].append({"need recall@context": need_recall(ctx, case), "context precision": precision(ctx, case), "passages": len(ctx)})
        for ver, key in (("v1", "W1"), ("v2", "W2")):
            plan, st, lab = res[ver]
            rows[key].append({**gen_metrics(st, lab, plan_text(plan, st, lab)),
                              "letter type": 1.0 if letter == case["letter"] else 0.0,
                              "letter written": 1.0 if (letter == "none") == (not (plan.get("letter") or {}).get("body")) else 0.0})
        details.append({"id": case["id"], "lang": case["lang"], "message": case["message"], "baseline_query": q,
                        "baseline_docs": [h["doc_id"] for h in b_ctx], "sub_questions": sq, "letter": letter,
                        "candidates": [(x["pid"], x["doc_id"], x["for"]) for x in passages], "context": [x["doc_id"] for x in ctx],
                        "plan_v2": res["v2"][0], "labels_v2": res["v2"][2], "labels_v1": res["v1"][2], "baseline_labels": b_lab,
                        "metrics": {k: v[-1] for k, v in rows.items()}})
        print(case["id"], {k: v[-1] for k, v in rows.items()}, flush=True)

    def avg(rs, k):
        vals = [r[k] for r in rs if k in r]
        return round(sum(vals) / len(vals), 3) if vals else "-"
    names = {"B": "Single-query RAG (previous)", "A1": "Agentic: decomposition + routed retrieval", "A2": "Agentic: + LLM re-ranking",
             "W1": "Agentic + writer v1", "W2": "Agentic + writer v2 (production)"}
    cols = ["need recall@context", "context precision", "passages", "need coverage", "cited steps", "faithful steps", "supported steps", "letter type"]
    table = []
    for k in ("B", "A1", "A2", "W1", "W2"):
        row = {"pipeline": names[k]}
        for c in cols:
            row[c] = avg(rows[k], c)
        table.append(row)
    for r in table:
        print(r)
    save_json("agentic_rag_eval.json", {"n_cases": len(CASES), "today": TODAY, "table": table, "details": details})
    (config.RESULTS / "agentic_rag_eval.md").write_text(
        f"# Agentic RAG vs single-query RAG ({len(CASES)} situations, 4 languages)\n\n" + md_table(table, ["pipeline"] + cols) +
        "\n\nRetrieval metrics (need recall, precision, passages) describe the context given to the writer; generation metrics "
        "(coverage, citations, faithfulness) describe the answer delivered to the user (unsupported plan steps removed).\n", encoding="utf-8")
