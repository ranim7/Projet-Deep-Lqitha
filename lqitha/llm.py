"""Thin client for the free Groq API (OpenAI-compatible) + prompt-library rendering.

Used by the API tools (speech-to-text) and by the evaluation scripts. At run time the
agents' LLM calls are made by n8n with the SAME prompts (see scripts/build_n8n.py).
"""
import base64
import json
import random
import re
import time
from datetime import date
from pathlib import Path

import requests
import yaml

from . import config

_PROMPTS = None


def prompts():
    global _PROMPTS
    if _PROMPTS is None:
        _PROMPTS = yaml.safe_load(config.PROMPTS_FILE.read_text(encoding="utf-8"))
    return _PROMPTS


def render(template: str, **vars) -> str:
    vars.setdefault("today", date.today().isoformat())

    def rep(m):
        v = vars.get(m.group(1), "")
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return re.sub(r"\{\{(\w+)\}\}", rep, template)


def build_messages(prompt_name: str, version: str | None = None, image_b64: str | None = None, **vars):
    p = prompts()[prompt_name]
    v = p["versions"][version or p["active"]]
    system = render(v["system"], **vars)
    user = render(v["user"], **vars)
    if image_b64:
        content = [{"type": "text", "text": user},
                   {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]
    else:
        content = user
    return [{"role": "system", "content": system}, {"role": "user", "content": content}]


def _headers():
    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY missing in .env (free key at https://console.groq.com/keys)")
    return {"Authorization": f"Bearer {config.GROQ_API_KEY}"}


def reasoning_params(model):
    """gpt-oss: low reasoning effort (saves tokens under the 8k TPM free limit); Qwen: no thinking."""
    if "gpt-oss" in model:
        return {"reasoning_effort": "low"}
    if "qwen" in model:
        return {"reasoning_effort": "none"}
    return {}


def _wait_seconds(r, attempt):
    """Groq tells us how long to wait ("try again in 19.48s"); use it, plus a little jitter."""
    try:
        ra = float(r.headers.get("retry-after", 0) or 0)
    except ValueError:
        ra = 0
    m = re.search(r"try again in ([0-9.]+)s", r.text or "")
    hint = float(m.group(1)) if m else 0
    return max(ra, hint, 2.0) + random.uniform(0.5, 3.0) if (ra or hint) else min(60, 5 * 2 ** min(attempt, 4))


def local_chat(messages, model, temperature=0.0, json_mode=True, max_tokens=1500):
    """Same request on the local Ollama server (OpenAI-compatible endpoint) - free, no quota."""
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(f"{config.OLLAMA_URL}/v1/chat/completions", json=body, timeout=600)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"], r.json().get("usage", {})


def chat(messages, model=None, temperature=0.0, json_mode=True, max_tokens=1500, retries=40):
    model = model or config.TEXT_MODEL
    if model.startswith("local:"):
        return local_chat(messages, model[6:], temperature, json_mode, max_tokens)
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
            **reasoning_params(model)}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    for attempt in range(retries):
        r = requests.post(f"{config.GROQ_BASE}/chat/completions", headers=_headers(), json=body, timeout=120)
        if r.status_code == 429 and "per day" in r.text:
            # daily free quota exhausted -> fall back to the local model (vision if the request has an image)
            has_img = any(isinstance(m.get("content"), list) for m in messages)
            local = config.LOCAL_VISION_MODEL if has_img else config.LOCAL_TEXT_MODEL
            print(f"    [{time.strftime('%H:%M:%S')}] {model}: daily quota exhausted -> local fallback {local}", flush=True)
            return local_chat(messages, local, temperature, json_mode, max_tokens)
        if r.status_code == 429 or r.status_code >= 500:
            w = min(_wait_seconds(r, attempt), 90)      # never sleep more than 90 s at once
            print(f"    [{time.strftime('%H:%M:%S')}] {model}: HTTP {r.status_code}, retry in {w:.0f}s "
                  f"(retry-after={r.headers.get('retry-after')}) {r.text[:160]}", flush=True)
            time.sleep(w)
            continue
        if r.status_code == 400 and json_mode and "json" in r.text.lower():
            # model produced invalid JSON -> retry once without json mode, then parse leniently
            body.pop("response_format", None)
            json_mode = False
            continue
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        usage = r.json().get("usage", {})
        return txt, usage
    raise RuntimeError(f"Groq failed after {retries} retries: {r.status_code} {r.text[:300]}")


def parse_json(txt: str):
    try:
        return json.loads(txt), True
    except Exception:
        m = re.search(r"\{.*\}", txt or "", re.S)
        if m:
            try:
                return json.loads(m.group(0)), False
            except Exception:
                pass
    return None, False


def run_prompt(prompt_name, version=None, image_path: Path | None = None, model_override=None, **vars):
    """Render + call + parse. Returns (parsed_json_or_None, strict_json_ok, raw_text, usage)."""
    p = prompts()[prompt_name]
    img = base64.b64encode(Path(image_path).read_bytes()).decode() if image_path else None
    model = config.MODEL_BY_ENV.get(p.get("model"), config.TEXT_MODEL)
    if img:
        model = config.VISION_MODEL
    elif p.get("text_only_model"):
        model = config.MODEL_BY_ENV.get(p["text_only_model"], model)
    model = model_override or model
    msgs = build_messages(prompt_name, version, image_b64=img, **vars)
    txt, usage = chat(msgs, model=model, temperature=p.get("temperature", 0.0))
    parsed, strict = parse_json(txt)
    return parsed, strict, txt, usage


def transcribe(audio_bytes: bytes, filename="audio.webm", model=None, language=None):
    data = {"model": model or config.STT_MODEL, "response_format": "json", "temperature": "0"}
    if language:
        data["language"] = language
    for attempt in range(5):
        r = requests.post(f"{config.GROQ_BASE}/audio/transcriptions", headers=_headers(),
                          files={"file": (filename, audio_bytes)}, data=data, timeout=180)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("retry-after", 0) or 0) or 10 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json().get("text", "").strip()
    raise RuntimeError(f"Groq STT failed: {r.status_code} {r.text[:200]}")
