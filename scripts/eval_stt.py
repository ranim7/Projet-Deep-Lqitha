"""Speech-to-text evaluation on REAL Tunisian Derja clips (LinTO Tunisian dataset, Hugging Face).

Compares two free Groq Whisper models: whisper-large-v3 vs whisper-large-v3-turbo.
Metrics: WER and CER after normalisation (lowercase, no punctuation, no Arabic diacritics).
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

import jiwer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lqitha import config  # noqa: E402
from lqitha.evalutil import md_table, save_json  # noqa: E402
from lqitha.llm import transcribe  # noqa: E402

MODELS = ["whisper-large-v3", "whisper-large-v3-turbo"]
CACHE = config.RESULTS / "cache"


def normalize(t):
    t = unicodedata.normalize("NFKC", t or "").lower()
    t = re.sub(r"[ً-ْٰـ]", "", t)        # Arabic diacritics + tatweel
    t = re.sub(r"[إأآا]", "ا", t).replace("ة", "ه").replace("ى", "ي")
    t = re.sub(r"[^\w\s؀-ۿ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def normalize_ortho(t):
    """Extra, fair normalisation for Derja (no standard spelling): attach the conjunction 'و' to the next word
    and unify a few frequent spelling variants."""
    t = normalize(t)
    t = re.sub(r"(^|\s)و\s+", r"\1و", t)
    for a, b in [("راجل", "رجل"), ("برشه", "برشا"), ("زادا", "زاده"), ("الي", "اللي")]:
        t = re.sub(rf"(^|\s){a}(?=\s|$)", rf"\g<1>{b}", t)
    return t


if __name__ == "__main__":
    base = config.RAW / "audio_tn"
    clips = json.loads((base / "audio_tn_samples.json").read_text(encoding="utf-8"))
    rows, summary = [], []
    for m in MODELS:
        refs, hyps, refs_o, hyps_o = [], [], [], []
        for c in clips:
            f = CACHE / f"stt_{m}_{Path(c['file']).stem}.txt"
            if f.exists():
                hyp = f.read_text(encoding="utf-8")
            else:
                p = base / c["file"]
                hyp = transcribe(p.read_bytes(), filename=p.name, model=m)
                f.write_text(hyp, encoding="utf-8")
            r, h = normalize(c["reference"]), normalize(hyp)
            if not r:
                continue
            refs.append(r)
            hyps.append(h or "<empty>")
            refs_o.append(normalize_ortho(c["reference"]))
            hyps_o.append(normalize_ortho(hyp) or "<empty>")
            rows.append({"model": m, "file": c["file"], "source": c["source"].split("/data/")[-1].split("/")[0],
                         "reference": c["reference"], "hypothesis": hyp,
                         "wer": round(jiwer.wer(r, h or "<empty>"), 3)})
        summary.append({"model": m, "clips": len(refs), "WER": round(jiwer.wer(refs, hyps), 3),
                        "CER": round(jiwer.cer(refs, hyps), 3),
                        "WER (orthographic normalisation)": round(jiwer.wer(refs_o, hyps_o), 3),
                        "CER (orthographic normalisation)": round(jiwer.cer(refs_o, hyps_o), 3)})
        print(summary[-1])
    save_json("stt_eval.json", {"summary": summary, "clips": rows})
    md = ["# Speech-to-text on real Tunisian Derja clips (LinTO dataset)\n",
          md_table(summary, list(summary[0].keys())), "",
          "Examples (whisper-large-v3):\n"]
    for r in [x for x in rows if x["model"] == MODELS[0]][:8]:
        md.append(f"- REF: {r['reference']}\n  HYP: {r['hypothesis']}  (WER {r['wer']})")
    (config.RESULTS / "stt_eval.md").write_text("\n".join(md), encoding="utf-8")
