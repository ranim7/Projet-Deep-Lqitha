"""Evaluation of the board translator (agent 7) with the lexical-overlap metrics BLEU and ROUGE.

Why round-trip: BLEU and ROUGE need a reference text for every output. There is no human reference for Tunisian Derja,
so each translation is translated BACK into English by a different model (qwen3.8-27b, temperature 0, same translator
prompt with English as the target) and compared with the original English description of the item, which is the
reference. A translation that loses or changes details (colour, material, brand, marks) cannot be recovered by the
back-translation, so its scores drop.

Test set: 30 found items of the demo database (fixed random sample), French and Derja.
Systems
  S0  local fallback model gemma3:4b + production prompt   (what users get when the API quota is exhausted)
  S1  gpt-oss-120b, prompt WITHOUT the language style guide (ablation of failures T1 / T3)
  S2  gpt-oss-120b + production prompt (style guide, Derja glossary + examples) = production
Metrics (computed on lower-cased, accent-free word tokens)
  BLEU-4   corpus BLEU: geometric mean of the 1-4-gram precisions x brevity penalty (Papineni et al., 2002)
  ROUGE-1  F1 of unigram overlap         ROUGE-L  F1 of the longest common subsequence (Lin, 2004)
  script   share of outputs written in the right script / language (Derja in Latin letters; French without Derja words)
  BLEU-4 (sacreBLEU)  same score computed by the reference library sacreBLEU on the raw strings (13a tokenizer, punctuation
           kept) - checks the implementation and shows how much the score depends on tokenization
  copied   share of the output words (3+ letters, no numbers) copied unchanged from the English source - untranslated
           words make the round trip trivial, so BLEU / ROUGE reward them (limitation of lexical-overlap metrics)
Outputs reports/results/translation_eval.json + .md
"""
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lqitha import config, store  # noqa: E402
from lqitha.api import LANG_NAMES, STYLE  # noqa: E402
from lqitha.evalutil import cached_prompt, md_table, norm, save_json  # noqa: E402

N_ITEMS = 30
BATCH = 15
BACK_MODEL = config.VISION_MODEL                    # independent back-translator
LOCAL = "local:" + config.LOCAL_TEXT_MODEL
PLAIN_STYLE = {"derja": "Write Tunisian Derja.", "fr": "Write French."}
SYSTEMS = {"S0 local fallback (gemma3:4b)": (LOCAL, STYLE), "S1 gpt-oss-120b, no style guide": (None, PLAIN_STYLE),
           "S2 gpt-oss-120b + style guide (production)": (None, STYLE)}
DERJA_WORDS = re.compile(r"\b(fih|fiha|k7el|ka7la|abyedh|a7mer|7amra|az9e9|a5dher|asfer|dhahbi|fodhi|jeld|7did|9mech|sghir|kbir|"
                         r"mrayet|chams|telifoun|mfete7|mangala|dabbouza|mdhalla|sabbat|kasa|khyata)\b")
ARABIC = re.compile(r"[؀-ۿ]")


def items():
    with store.conn() as c:
        rows = c.execute("SELECT id FROM reports WHERE type='found' AND source='seed' ORDER BY id").fetchall()
    out = []
    for (rid,) in rows:
        d = ((store.get_report(rid) or {}).get("record") or {}).get("canonical_description")
        if d and len(d.split()) >= 4 and d not in STYLE["derja"] + STYLE["fr"]:   # never test on a few-shot example
            out.append((rid, d[:300]))
    return random.Random(7).sample(out, min(N_ITEMS, len(out)))


def translate(tag, pairs, lang_name, style, model):
    out = {}
    for b in range(0, len(pairs), BATCH):
        chunk = pairs[b:b + BATCH]
        r = cached_prompt(tag, "translator", "v1", model_override=model, language_name=lang_name, style=style,
                          items="\n".join(f"{rid}: {d}" for rid, d in chunk))
        tr = ((r or {}).get("parsed") or {}).get("translations") or {}
        out.update({k: v.strip() for k, v in tr.items() if isinstance(v, str) and v.strip()})
    return out


# ---------------------------------------------------------------- metrics (as defined in the course chapter)
def toks(s):
    return norm(s).split()


def ngrams(t, n):
    return Counter(tuple(t[i:i + n]) for i in range(len(t) - n + 1))


def corpus_bleu(hyps, refs, max_n=4):
    match, total = [0] * max_n, [0] * max_n
    hl = rl = 0
    for h, r in zip(hyps, refs):
        hl, rl = hl + len(h), rl + len(r)
        for n in range(1, max_n + 1):
            hn, rn = ngrams(h, n), ngrams(r, n)
            match[n - 1] += sum(min(c, rn[g]) for g, c in hn.items())
            total[n - 1] += max(0, len(h) - n + 1)
    if min(match) == 0:
        return 0.0
    logp = sum(math.log(m / t) for m, t in zip(match, total)) / max_n
    bp = 1.0 if hl > rl else math.exp(1 - rl / max(1, hl))
    return bp * math.exp(logp)


def rouge1(h, r):
    o = sum((Counter(h) & Counter(r)).values())
    if not o:
        return 0.0
    p, rc = o / len(h), o / len(r)
    return 2 * p * rc / (p + rc)


def lcs(a, b):
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def rouge_l(h, r):
    m = lcs(h, r)
    if not m:
        return 0.0
    p, rc = m / len(h), m / len(r)
    return 2 * p * rc / (p + rc)


def copied(text, src):
    s = {t for t in toks(src) if len(t) > 2 and not t.isdigit()}
    t = [w for w in toks(text) if len(w) > 2 and not w.isdigit()]
    return sum(w in s for w in t), len(t)


def sacre(ids, back, ref):
    try:
        import sacrebleu
    except ImportError:
        return "-"
    return round(sacrebleu.corpus_bleu([back.get(r, "") for r in ids], [[ref[r] for r in ids]], lowercase=True).score, 1)


def right_script(text, lang):
    if lang == "derja":
        return not ARABIC.search(text)
    return not ARABIC.search(text) and not DERJA_WORDS.search(norm(text))


if __name__ == "__main__":
    pairs = items()
    ref = dict(pairs)
    print(f"{len(pairs)} items")
    table, details = [], {}
    for lang in ("fr", "derja"):
        for name, (model, styles) in SYSTEMS.items():
            fwd = translate(f"tr_{lang}_{name[:2]}", pairs, LANG_NAMES[lang], styles[lang], model)
            back = translate(f"trback_{lang}_{name[:2]}", list(fwd.items()), LANG_NAMES["en"], STYLE["en"], BACK_MODEL)
            ids = [rid for rid, _ in pairs]
            hyps = [toks(back.get(rid, "")) for rid in ids]
            refs = [toks(ref[rid]) for rid in ids]
            row = {"language": lang, "system": name, "translated": f"{len(fwd)}/{len(ids)}",
                   "BLEU-4": round(100 * corpus_bleu(hyps, refs), 1),
                   "BLEU-4 (sacreBLEU)": sacre(ids, back, ref),
                   "ROUGE-1 F1": round(sum(rouge1(h, r) for h, r in zip(hyps, refs) if h) / len(ids), 3),
                   "ROUGE-L F1": round(sum(rouge_l(h, r) for h, r in zip(hyps, refs) if h) / len(ids), 3),
                   "copied English words": round(sum(copied(fwd.get(rid, ""), ref[rid])[0] for rid in ids)
                                                 / max(1, sum(copied(fwd.get(rid, ""), ref[rid])[1] for rid in ids)), 3),
                   "right script/language": round(sum(right_script(fwd.get(rid, ""), lang) for rid in ids if rid in fwd) / len(ids), 3)}
            table.append(row)
            details[f"{lang} | {name}"] = [{"id": rid, "reference": ref[rid], "translation": fwd.get(rid), "back": back.get(rid)} for rid in ids]
            print(row, flush=True)
    save_json("translation_eval.json", {"n_items": len(pairs), "back_translator": BACK_MODEL, "table": table, "details": details})
    (config.RESULTS / "translation_eval.md").write_text(
        f"# Board translator - BLEU / ROUGE by round-trip translation ({len(pairs)} found items)\n\n"
        "Each translation is translated back into English by an independent model and compared with the original English "
        "description (the reference). Scores on lower-cased, accent-free words; BLEU-4 is a corpus score (x100).\n\n"
        + md_table(table, ["language", "system", "translated", "BLEU-4", "BLEU-4 (sacreBLEU)", "ROUGE-1 F1", "ROUGE-L F1", "copied English words", "right script/language"]) + "\n",
        encoding="utf-8")
