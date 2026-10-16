"""Helpers for the evaluation scripts: cached LLM calls (resume after rate limits) and metrics."""
import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path

from . import config, llm

CACHE = config.RESULTS / "cache"
CACHE.mkdir(parents=True, exist_ok=True)


def cached_prompt(tag, prompt_name, version=None, image_path=None, only_cache=False, model_override=None, **vars):
    """run_prompt with an on-disk cache keyed by (prompt, version, inputs)."""
    p = llm.prompts()[prompt_name]
    version = version or p["active"]
    key_src = json.dumps({"p": prompt_name, "v": version, "img": str(image_path or ""), "vars": vars,
                          "sys": p["versions"][version]["system"], "m": model_override or (config.VISION_MODEL if image_path else config.MODEL_BY_ENV.get(p.get("text_only_model") or p.get("model"), config.TEXT_MODEL))},
                         sort_keys=True, ensure_ascii=False)
    key = hashlib.sha1(key_src.encode()).hexdigest()[:16]
    f = CACHE / f"{tag}_{prompt_name}_{version}_{key}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    if only_cache:
        return None
    t0 = time.time()
    parsed, strict, raw, usage = llm.run_prompt(prompt_name, version, image_path=image_path, model_override=model_override, **vars)
    rec = {"parsed": parsed, "strict_json": strict, "raw": raw, "usage": usage, "latency_s": round(time.time() - t0, 2)}
    f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def cached_chat(tag, messages, model, temperature=0.7, json_mode=True):
    key = hashlib.sha1(json.dumps([messages, model, temperature], ensure_ascii=False).encode()).hexdigest()[:16]
    f = CACHE / f"{tag}_{key}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    txt, usage = llm.chat(messages, model=model, temperature=temperature, json_mode=json_mode, max_tokens=500)
    parsed, _ = llm.parse_json(txt) if json_mode else (txt, True)
    rec = {"parsed": parsed, "raw": txt, "usage": usage}
    f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s؀-ۿ]", " ", s)).strip()


def hit_at(ranks, k):
    return round(sum(1 for r in ranks if r is not None and r < k) / max(1, len(ranks)), 4)


def mrr(ranks):
    return round(sum(1 / (r + 1) for r in ranks if r is not None) / max(1, len(ranks)), 4)


def save_json(name, obj):
    (config.RESULTS / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def md_table(rows, cols):
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(out)
