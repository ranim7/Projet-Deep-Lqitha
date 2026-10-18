"""Generate the n8n workflows (n8n/*.json) from the prompt library.

Why generate instead of hand-editing in the n8n UI?
- prompts/prompts.yaml stays the single source of truth (versioned, documented, evaluated)
- switching a prompt version = change `active:` in the YAML and re-run this script

Run:  python scripts/build_n8n.py
"""
import json
import sys
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
P = yaml.safe_load((ROOT / "prompts" / "prompts.yaml").read_text(encoding="utf-8"))
OUT = ROOT / "n8n"
OUT.mkdir(exist_ok=True)

MAIN_ID, REMATCH_ID, ERROR_ID = "LqithaMainFlow01", "LqithaRematch001", "LqithaErrorFlow1"
API = "{{ $env.LQITHA_API }}"
# model chosen per agent (prompts.yaml -> model_env), reasoning kept low to respect the free 8k tokens/min limit
MODEL_EXPR = ("={{ (() => { const m = $json.vision ? $env.VISION_MODEL : ($json.model_env === 'FAST_MODEL' ? $env.FAST_MODEL : "
              "($json.model_env === 'VISION_MODEL' ? $env.VISION_MODEL : $env.TEXT_MODEL)); "
              "const r = m.includes('gpt-oss') ? {reasoning_effort: 'low'} : (m.includes('qwen') ? {reasoning_effort: 'none'} : {}); "
              "return JSON.stringify(Object.assign({}, $json.groq_body, {model: m, max_tokens: 1500}, r)); })() }}")
GROQ_URL = "={{ $env.LLM_BASE_URL || 'https://api.groq.com/openai/v1' }}/chat/completions"

JS_HELPERS = r"""
function render(t, v) {
  return t.replace(/\{\{(\w+)\}\}/g, (m, k) => (v[k] === undefined || v[k] === null) ? '' : (typeof v[k] === 'string' ? v[k] : JSON.stringify(v[k])));
}
function parseLLM(resp) {
  try {
    if (!resp || resp.error) return null;
    const txt = resp.choices[0].message.content;
    try { return JSON.parse(txt); } catch (e) { const m = txt.match(/\{[\s\S]*\}/); return m ? JSON.parse(m[0]) : null; }
  } catch (e) { return null; }
}
function today() { return new Date().toISOString().slice(0, 10); }
function L(lang, d) { return d[lang] || d.fr; }
// language of a message, decided without the LLM when it is unambiguous (Arabic script, Derja spellings)
function detectLang(t) {
  if (!t) return null;
  if (/[\u0600-\u06FF]/.test(t)) return 'ar';
  const w = ' ' + t.toLowerCase() + ' ';
  if (/[a-z][379]|[379][a-z]/.test(w)) return 'derja';
  if (/\b(l9it|lqit|dhaya3t|dha3li|mta3|mte3|lyoum|lbera7|chnowa|chnoua|fama|barcha|kifech|kifesh|3andi|7aja|ya3tik|sakoche|nheb|n7eb|winou|wine)\b/.test(w)) return 'derja';
  const en = (w.match(/\b(the|i|my|lost|found|where|what|have|is|at|in|it|bag|phone)\b/g) || []).length;
  const fr = (w.match(/\b(le|la|les|j'ai|je|mon|ma|mes|perdu|trouvé|trouve|à|de|du|que|est|un|une|dans|sac)\b/g) || []).length;
  if (en > fr) return 'en';
  if (fr > 0) return 'fr';
  return null;
}
"""


def prompt_consts(name):
    p = P[name]
    v = p["versions"][p["active"]]
    return (f"// Prompt '{name}' version {p['active']} (generated from prompts/prompts.yaml - do not edit here)\n"
            f"const SYSTEM = {json.dumps(v['system'], ensure_ascii=False)};\n"
            f"const USER = {json.dumps(v['user'], ensure_ascii=False)};\n"
            f"const TEMPERATURE = {p.get('temperature', 0)};\n"
            f"const MODEL_ENV = {json.dumps(p.get('model', 'TEXT_MODEL'))};\n")


class WF:
    def __init__(self, name, wid):
        self.name, self.id, self.nodes, self.conn = name, wid, [], {}

    def add(self, name, ntype, version, params, pos, **extra):
        node = {"parameters": params, "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, self.id + name)), "name": name,
                "type": ntype, "typeVersion": version, "position": pos}
        node.update(extra)
        self.nodes.append(node)
        return name

    def code(self, name, js, pos, each=False, note=None, helpers=True, prompt=None):
        src = (JS_HELPERS if helpers else "") + (prompt_consts(prompt) if prompt else "") + js
        params = {"jsCode": src}
        if each:
            params["mode"] = "runOnceForEachItem"
        extra = {"alwaysOutputData": False}
        if note:
            extra.update(notes=note, notesInFlow=True)
        return self.add(name, "n8n-nodes-base.code", 2, params, pos, **extra)

    def http(self, name, url, pos, method="POST", body=None, headers=None, note=None, retry=True, on_error=None, timeout=120000):
        params = {"method": method, "url": url, "options": {"timeout": timeout}}
        if headers:
            params["sendHeaders"] = True
            params["headerParameters"] = {"parameters": [{"name": k, "value": v} for k, v in headers.items()]}
        if body is not None:
            params.update(sendBody=True, specifyBody="json", jsonBody=body)
        extra = {}
        if retry:
            extra.update(retryOnFail=True, maxTries=3, waitBetweenTries=4000)
        if on_error:
            extra["onError"] = on_error
        if note:
            extra.update(notes=note, notesInFlow=True)
        return self.add(name, "n8n-nodes-base.httpRequest", 4.2, params, pos, **extra)

    def llm(self, name, pos, note):
        return self.http(name, GROQ_URL, pos,
                         body=MODEL_EXPR,
                         headers={"Authorization": "=Bearer {{ $env.GROQ_API_KEY }}"},
                         note=note, on_error="continueRegularOutput", timeout=90000)

    def switch(self, name, expr, n, pos, note=None):
        extra = {"notes": note, "notesInFlow": True} if note else {}
        return self.add(name, "n8n-nodes-base.switch", 3.2,
                        {"mode": "expression", "numberOutputs": n, "output": "=" + expr, "options": {}}, pos, **extra)

    def sticky(self, text, pos, w=520, h=300, color=5):
        self.add(f"Note {len(self.nodes)}", "n8n-nodes-base.stickyNote", 1,
                 {"content": text, "height": h, "width": w, "color": color}, pos)

    def link(self, a, b, out=0, inp=0):
        main = self.conn.setdefault(a, {"main": []})["main"]
        while len(main) <= out:
            main.append([])
        main[out].append({"node": b, "type": "main", "index": inp})

    def chain(self, *names):
        for a, b in zip(names, names[1:]):
            self.link(a, b)

    def add_local_fallbacks(self):
        """Every LLM node gets an error branch: if Groq fails (e.g. free daily quota exhausted), the same request is
        re-sent to the local Ollama model on this PC (free, no quota), and the result continues on the normal path."""
        for n in [n for n in self.nodes if n["type"].endswith("httpRequest") and "(LLM" in n["name"]]:
            n["onError"] = "continueErrorOutput"
            fb = n["name"].split(" (LLM")[0] + " (local fallback)"
            self.add(fb, "n8n-nodes-base.httpRequest", 4.2, {
                "method": "POST", "url": "={{ ($env.OLLAMA_DOCKER_URL || 'http://host.docker.internal:11434') }}/v1/chat/completions",
                "sendBody": True, "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify(Object.assign({}, $json.groq_body, {model: $env.LOCAL_VISION_MODEL || 'gemma3:4b'})) }}",
                "options": {"timeout": 300000}}, [n["position"][0], n["position"][1] + 170],
                onError="continueRegularOutput", notes="local Ollama model - used only if Groq fails", notesInFlow=True)
            targets = self.conn.get(n["name"], {"main": [[]]})["main"][0]
            for t in targets:
                self.link(fb, t["node"], 0, t["index"])
            self.link(n["name"], fb, 1)

    def dump(self, fname, settings=None):
        self.add_local_fallbacks()
        wf = {"id": self.id, "name": self.name, "nodes": self.nodes, "connections": self.conn,
              "settings": {"executionOrder": "v1", **(settings or {})}, "pinData": {}, "active": False,
              "meta": {"templateCredsSetupCompleted": True}, "tags": []}
        (OUT / fname).write_text(json.dumps(wf, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote n8n/{fname}: {len(self.nodes)} nodes")


# =============================================================================== MAIN
def build_main():
    w = WF("Lqitha - Main (multi-agent lost & found)", MAIN_ID)
    X = 0

    w.sticky("## Lqitha - multi-agent lost & found (Tunisia)\nEntry: POST /webhook/lqitha from the web app.\n"
             "**Agents (LLM, Groq):** 1 Intake/Router - 2 Perception (vision) - 4 Verifier - 5 Recovery plan (agentic RAG: 5a planner, "
             "5b re-ranker, 5c writer, 5d checker) - 6 Notifier - 6b Claim check - 8 Found-items search answer\n"
             "**Tools (HTTP, Lqitha API):** speech-to-text, save report, matcher (3), multi-source RAG retrieval, found-items search, claims, notifications.\n"
             "Prompts are generated from prompts/prompts.yaml (single source of truth).", [-420, -420], w=700, h=180, color=4)

    w.add("Webhook", "n8n-nodes-base.webhook", 2,
          {"httpMethod": "POST", "path": "lqitha", "responseMode": "responseNode", "options": {}},
          [X, 0], webhookId="5f2b8a8e-1c1e-4c0a-9d2a-1a2b3c4d5e6f")
    w.code("Normalize input", r"""
const b = $json.body || $json;
const s = b.session || {};
const session = {
  session_id: s.session_id || ('S-' + Math.random().toString(36).slice(2, 10)),
  turn: s.turn || 0, slots_so_far: s.slots_so_far || {}, questions_already_asked: s.questions_already_asked || [],
  intent_so_far: s.intent_so_far || null, language: s.language || null, history: s.history || []
};
const strip = x => (x && x.includes(',')) ? x.split(',').pop() : (x || null);
return [{ json: {
  action: b.action || 'message', session,
  message: (b.message || '').toString().slice(0, 2000),
  image_b64: strip(b.image_b64), audio_b64: strip(b.audio_b64), audio_mime: b.audio_mime || 'audio/webm',
  location: b.location || null, contact: b.contact || null, claim_id: b.claim_id || null, answer: b.answer || null,
  source: 'text'
}}];
""", [X + 220, 0], helpers=False)
    w.switch("Route action", "{{ ({message: 0, claim_answer: 1})[$json.action] ?? 0 }}", 2, [X + 440, 0],
             note="message -> agents pipeline | claim_answer -> ownership check")
    w.chain("Webhook", "Normalize input", "Route action")

    # ---------------- message branch: optional speech-to-text
    w.switch("Voice note?", "{{ $json.audio_b64 ? 0 : 1 }}", 2, [X + 660, -120])
    w.link("Route action", "Voice note?", 0)
    w.http("Tool - Speech-to-text", f"={API}/tools/stt", [X + 880, -240],
           body="={{ JSON.stringify({audio_b64: $json.audio_b64, mime: $json.audio_mime}) }}",
           note="Groq Whisper large-v3 (free) via Lqitha API")
    w.code("Merge transcript", r"""
const ctx = $('Normalize input').first().json;
const t = ($json.text || '').trim();
return [{ json: { ...ctx, message: [ctx.message, t].filter(Boolean).join(' '), source: 'voice_transcript', transcript: t, audio_b64: null } }];
""", [X + 1100, -240], helpers=False)
    w.link("Voice note?", "Tool - Speech-to-text", 0)
    w.link("Tool - Speech-to-text", "Merge transcript")

    # ---------------- Agent 1: intake & router
    w.code("Agent 1 - Intake - build prompt", r"""
const ctx = $json;
const s = ctx.session;
// privacy: when the user answers the hidden-detail question, the secret is NOT sent to the LLM
const lastAsked = s.questions_already_asked[s.questions_already_asked.length - 1];
const secretTurn = lastAsked === 'hidden_detail' && s.intent_so_far === 'found';
const conv = { turn: s.turn, intent_so_far: s.intent_so_far, language: s.language || detectLang(ctx.message),
               slots_so_far: secretTurn ? { ...s.slots_so_far, hidden_detail: '[provided - secret]' } : s.slots_so_far,
               questions_already_asked: s.questions_already_asked };
const vars = { today: today(), source: ctx.source,
               message: secretTurn ? '[the user just gave the secret hidden detail; it is stored securely and not shown to you]' : ctx.message,
               has_photo: !!ctx.image_b64, location: ctx.location || 'not given', conversation: conv };
const groq_body = { messages: [{ role: 'system', content: render(SYSTEM, vars) }, { role: 'user', content: render(USER, vars) }],
                    temperature: TEMPERATURE, max_tokens: 700, response_format: { type: 'json_object' } };
return [{ json: { ctx, groq_body, model_env: MODEL_ENV, vision: false } }];
""", [X + 1320, -120], prompt="intake")
    w.link("Voice note?", "Agent 1 - Intake - build prompt", 1)
    w.link("Merge transcript", "Agent 1 - Intake - build prompt")
    w.llm("Agent 1 - Intake (LLM)", [X + 1540, -120], "Groq - intent, language, slots, next action")
    w.code("Intake - parse & guard", r"""
const ctx = $('Agent 1 - Intake - build prompt').first().json.ctx;
const s = ctx.session;
let out = parseLLM($json);
const llm_ok = !!out;
if (!out) out = { intent: s.intent_so_far || 'other', language: s.language || 'fr', slots: {}, missing: [],
                  next_action: 'reply_only', question_to_user: null,
                  reply_to_user: 'Désolé, je n\'ai pas pu traiter votre message. Pouvez-vous reformuler ? / Sorry, please rephrase.' };
const slots = Object.assign({}, s.slots_so_far);
for (const [k, v] of Object.entries(out.slots || {})) if (v !== null && v !== undefined && v !== '' && v !== false) slots[k] = v;
if (ctx.image_b64) slots.has_photo = true;
if (ctx.location) for (const k of ['governorate', 'place_name', 'place_type']) if (ctx.location[k] && !slots[k]) slots[k] = ctx.location[k];
// privacy guard: the answer to the hidden-detail question is a secret -> never stored in the public history
const lastAsked = s.questions_already_asked[s.questions_already_asked.length - 1];
let history = s.history.slice();
if (lastAsked === 'hidden_detail' && s.intent_so_far === 'found') slots.hidden_detail = ctx.message;
else if (ctx.message) history.push(ctx.message);
// keep the report thread: once a lost/found report is started, follow-up answers stay in it
let intent = out.intent;
if (['lost', 'found'].includes(s.intent_so_far) && !['procedure_question', 'status_check', 'search_found'].includes(intent)) intent = s.intent_so_far;
if (s.intent_so_far === 'search_found' && !['procedure_question', 'status_check', 'lost', 'found'].includes(intent)) intent = 'search_found';
const lang0 = s.language || detectLang(ctx.message) || out.language || 'fr';
let next = out.next_action;
const turn = s.turn + 1;
// deterministic guards: the LLM proposes, the workflow decides
if (['lost', 'found'].includes(intent)) {
  if (!['ask_user', 'to_perception'].includes(next)) next = 'to_perception';
  if (next === 'ask_user' && turn >= 3) next = 'to_perception';
  if (!slots.item_category && turn < 3) next = 'ask_user';
  if (intent === 'found' && !slots.hidden_detail && turn < 3 && !s.questions_already_asked.includes('hidden_detail')) {
    next = 'ask_user'; out.missing = ['hidden_detail'];
    out.question_to_user = out.question_to_user && /secret|cach|سر|khfi/i.test(out.question_to_user) ? out.question_to_user :
      L(out.language, { fr: 'Dernière question : donnez un détail que seul le vrai propriétaire peut connaître (fond d\'écran, contenu, inscription). Il restera secret et servira à vérifier le propriétaire.',
                        en: 'Last question: give one detail only the real owner can know (wallpaper, contents, inscription). It stays secret and is used to verify the owner.',
                        ar: 'سؤال أخير: اذكر تفصيلاً لا يعرفه إلا المالك الحقيقي (خلفية الشاشة، المحتوى...). سيبقى سرياً ويستعمل للتحقق من المالك.',
                        derja: 'Akher sou2el: a3tini detail ken moula el 7aja ya3rfou (fond d\'écran, chnowa fih...). Yab9a sirri w nesta3mlouh bech nthabtou fi moulah.' });
  }
  // once the secret is stored, do not ask again; go on if only optional slots are missing
  if (intent === 'found' && slots.hidden_detail) {
    out.missing = (out.missing || []).filter(m => m !== 'hidden_detail');
    if (next === 'ask_user' && out.missing.every(m => ['photo', 'distinctive_detail', 'date', 'date_iso'].includes(m))) next = 'to_perception';
  }
} else if (intent === 'search_found') {
  // found-items search: a photo or a category is enough; never more than one question
  if (ctx.image_b64 && !slots.item_category) slots.item_category = 'other';
  if (slots.item_category || turn >= 2) next = 'to_search';
  else {
    next = 'ask_user'; out.missing = ['item_category'];
    out.question_to_user = out.question_to_user || L(lang0, { fr: 'Quel objet cherchez-vous (sac, téléphone, portefeuille…) ?', en: 'Which item are you looking for (bag, phone, wallet…)?',
      ar: 'ما هو الغرض الذي تبحث عنه؟', derja: 'Chnowa el 7aja elli tlawej 3liha (sac, telifoun, portefeuille…)?' });
  }
} else if (next === 'to_perception' || next === 'ask_user' || next === 'to_search') next = (intent === 'procedure_question') ? 'to_procedure' : 'reply_only';
const asked = s.questions_already_asked.slice();
if (next === 'ask_user' && out.missing && out.missing[0]) asked.push(out.missing[0]);
const session = { ...s, turn, slots_so_far: slots, questions_already_asked: asked, history,
                  intent_so_far: ['lost', 'found', 'search_found'].includes(intent) ? intent : s.intent_so_far,
                  // the conversation language is fixed by the first message and kept for the whole session
                  language: s.language || detectLang(ctx.message) || out.language || 'fr' };
return [{ json: { ctx, session, llm_ok, intake: { ...out, intent, next_action: next, slots } } }];
""", [X + 1760, -120], note="Deterministic guards on top of the LLM decision")
    w.chain("Agent 1 - Intake - build prompt", "Agent 1 - Intake (LLM)", "Intake - parse & guard")
    w.switch("Route next_action",
             "{{ ({ask_user: 0, reply_only: 0, to_perception: 1, to_procedure: 2, to_status: 3, to_search: 4})[$json.intake.next_action] ?? 0 }}",
             5, [X + 1980, -120], note="0 ask/reply | 1 perception | 2 recovery plan | 3 status | 4 search")
    w.link("Intake - parse & guard", "Route next_action")

    w.code("Reply - ask user", r"""
const r = $json;
const lang = r.session.language;
const text = r.intake.question_to_user || r.intake.reply_to_user ||
  L(lang, { fr: 'Pouvez-vous donner plus de détails ?', en: 'Can you give more details?', ar: 'هل يمكنك إعطاء مزيد من التفاصيل؟', derja: 'Tnajem ta3tini details akther?' });
const done = r.intake.next_action === 'reply_only';
const session = done ? { session_id: r.session.session_id, turn: 0, slots_so_far: {}, questions_already_asked: [], intent_so_far: null, language: lang, history: [] } : r.session;
return [{ json: { reply: text, kind: done ? 'info' : 'question', session, intake: { intent: r.intake.intent, missing: r.intake.missing || [], slots: r.intake.slots } } }];
""", [X + 2200, -420])
    w.link("Route next_action", "Reply - ask user", 0)

    w.http("Tool - Session reports", f"={API}/tools/sessions/{{{{ $json.session.session_id }}}}/reports", [X + 2200, 520], method="GET")
    w.code("Reply - status", r"""
const r = $('Intake - parse & guard').first().json;
const reps = $json.reports || [];
const lang = r.session.language;
let text;
if (!reps.length) text = L(lang, { fr: 'Vous n\'avez aucune déclaration en cours.', en: 'You have no open report.', ar: 'ليس لديك أي تصريح مفتوح.', derja: 'Ma 3andek 7atta tasri7 ma7loul.' });
else text = reps.map(x => `- ${x.id} (${x.type}, ${x.category}, ${x.place_name || ''} ${x.date_iso || ''}) : ${x.status}`).join('\n');
return [{ json: { reply: text, kind: 'status', session: r.session } }];
""", [X + 2420, 520])
    w.link("Route next_action", "Tool - Session reports", 3)
    w.link("Tool - Session reports", "Reply - status")

    # ---------------- Agent 2: perception (multimodal)
    Y = 140
    w.code("Agent 2 - Perception - build prompt", r"""
const r = $json;
const slots = r.intake.slots;
const vars = { report_type: r.intake.intent, text: (r.session.history || []).join(' | ') || r.ctx.message,
               slots: Object.fromEntries(Object.entries(slots).filter(([k]) => k !== 'hidden_detail')),
               has_photo: !!r.ctx.image_b64 };
const user = render(USER, vars);
const content = r.ctx.image_b64 ? [{ type: 'text', text: user }, { type: 'image_url', image_url: { url: 'data:image/jpeg;base64,' + r.ctx.image_b64 } }] : user;
const groq_body = { messages: [{ role: 'system', content: render(SYSTEM, vars) }, { role: 'user', content }],
                    temperature: TEMPERATURE, max_tokens: 700, response_format: { type: 'json_object' } };
return [{ json: { groq_body, model_env: r.ctx.image_b64 ? 'VISION_MODEL' : 'FAST_MODEL', vision: !!r.ctx.image_b64 } }];   // text-only -> cheaper model
""", [X + 2200, Y], prompt="perception", note="Image + text -> normalized English record")
    w.link("Route next_action", "Agent 2 - Perception - build prompt", 1)
    w.llm("Agent 2 - Perception (LLM, vision)", [X + 2420, Y], "Groq Qwen3.8-27B vision (free); error output -> local model")

    w.code("Perception - parse & privacy", r"""
const r = $('Intake - parse & guard').first().json;
const CATS = ['phone','wallet','bag','luggage','keys','id_document','bank_card','glasses','watch','headphones','laptop','electronics','clothing','jewelry','umbrella','shoes','bottle','other'];
const COLORS = ['black','white','grey','silver','gold','brown','beige','red','pink','orange','yellow','green','blue','purple','multicolor','transparent'];
let rec = parseLLM($json) || {};
const slots = r.intake.slots;
if (!CATS.includes(rec.category)) rec.category = CATS.includes(slots.item_category) ? slots.item_category : 'other';
if (rec.color_family === 'gray') rec.color_family = 'grey';
if (!COLORS.includes(rec.color_family)) rec.color_family = null;
// privacy: mask any long digit sequence (ID / card / phone numbers) in every string field
const mask = v => typeof v === 'string' ? v.replace(/(?:\d[\s\-.]?){6,}/g, '[masked]') : Array.isArray(v) ? v.map(mask) : v;
for (const k of Object.keys(rec)) rec[k] = mask(rec[k]);
if (!rec.canonical_description) rec.canonical_description = [rec.color_family, rec.material, rec.brand, rec.category, ...(rec.distinctive_marks || [])].filter(Boolean).join(' ');
return [{ json: {
  type: r.intake.intent, record: rec, slots: Object.fromEntries(Object.entries(slots).filter(([k]) => k !== 'hidden_detail')),
  session_id: r.session.session_id, language: r.session.language, hidden_detail: slots.hidden_detail || null,
  contact: r.ctx.contact, image_b64: r.ctx.image_b64, perception_ok: !!parseLLM($json)
}}];
""", [X + 2640, Y], note="Enum validation + digit masking (privacy)")
    w.http("Tool - Save report", f"={API}/tools/reports", [X + 2860, Y],
           body="={{ JSON.stringify({type: $json.type, record: $json.record, slots: $json.slots, session_id: $json.session_id, language: $json.language, hidden_detail: $json.hidden_detail, contact: $json.contact, image_b64: $json.image_b64}) }}",
           note="SQLite + vector store (bge-m3)")
    w.http("Agent 3 - Matcher (tool)", f"={API}/tools/match", [X + 3080, Y],
           body="={{ JSON.stringify({report_id: $json.report_id, top_k: 3}) }}",
           note="filters + bge-m3 similarity + attribute re-scoring")
    w.chain("Agent 2 - Perception - build prompt", "Agent 2 - Perception (LLM, vision)", "Perception - parse & privacy",
            "Tool - Save report", "Agent 3 - Matcher (tool)")

    w.code("Prepare verification", r"""
const m = $json;
const cands = (m.candidates || []).filter(c => c.score >= 0.45).slice(0, 2);
if (!cands.length) return [{ json: { no_candidates: true, report_id: m.report_id } }];
return cands.map(c => ({ json: { report_id: m.report_id, mine: m.report, cand: c } }));
""", [X + 3300, Y], helpers=False, note="top-2 candidates with score >= 0.45 (token budget)")
    w.link("Agent 3 - Matcher (tool)", "Prepare verification")
    w.switch("Any candidate?", "{{ $json.no_candidates ? 1 : 0 }}", 2, [X + 3520, Y])
    w.link("Prepare verification", "Any candidate?")

    # ---------------- Agent 4: verifier (runs once per candidate)
    w.code("Agent 4 - Verifier - build prompt", r"""
const it = $json;
const side = (rep) => ({ ...rep.record, place: rep.place_name || (rep.slots || {}).place_name, governorate: rep.governorate || (rep.slots || {}).governorate, date: rep.date_iso || (rep.slots || {}).date_iso });
const mine = side(it.mine);
const cand = { ...it.cand.record, place: it.cand.place_name, governorate: it.cand.governorate, date: it.cand.date_iso };
const lost = it.mine.type === 'lost' ? mine : cand;
const found = it.mine.type === 'lost' ? cand : mine;
const vars = { lost, found, score: it.cand.score };
const groq_body = { messages: [{ role: 'system', content: render(SYSTEM, vars) }, { role: 'user', content: render(USER, vars) }],
                    temperature: TEMPERATURE, max_tokens: 700, response_format: { type: 'json_object' } };
return { json: { groq_body, model_env: MODEL_ENV, vision: false } };
""", [X + 3740, Y - 100], each=True, prompt="verifier", note="independent judge, per candidate")
    w.link("Any candidate?", "Agent 4 - Verifier - build prompt", 0)
    w.llm("Agent 4 - Verifier (LLM)", [X + 3960, Y - 100], "Groq - rubric, contradictions, confidence")
    w.code("Verifier - pick best", r"""
const reqs = $('Prepare verification').all();
const rankOf = { match: 2, possible_match: 1, no_match: 0 };
let best = null;
$input.all().forEach((it, i) => {
  const v = parseLLM(it.json) || { decision: 'no_match', confidence: 0, contradictions: ['verifier unavailable'] };
  let rank = rankOf[v.decision] ?? 0;
  if (rank === 2 && (v.confidence || 0) < 0.6) rank = 1;   // calibration guard
  const c = reqs[i].json.cand;
  const cand = { ...c, verdict: v, rank };
  if (!best || rank > best.rank || (rank === best.rank && (v.confidence || 0) > (best.verdict.confidence || 0))) best = cand;
});
const mine = reqs[0].json.mine;
const lost_id = mine.type === 'lost' ? mine.id : best.id;
const found_id = mine.type === 'lost' ? best.id : mine.id;
return [{ json: { best, mine, lost_id, found_id, report_id: mine.id } }];
""", [X + 4180, Y - 100])
    w.chain("Agent 4 - Verifier - build prompt", "Agent 4 - Verifier (LLM)", "Verifier - pick best")
    w.switch("Match or possible match?", "{{ $json.best && $json.best.rank >= 1 ? 0 : 1 }}", 2, [X + 4400, Y - 100])
    w.link("Verifier - pick best", "Match or possible match?")

    # ---------------- Agent 6: notifier + secure return
    w.http("Tool - Read pair (internal)", f"={API}/internal/pairs/{{{{ $json.lost_id }}}}/{{{{ $json.found_id }}}}", [X + 4620, Y - 240],
           method="GET", headers={"X-Internal-Token": "={{ $env.INTERNAL_TOKEN }}"}, note="only n8n can read the finder's secret")
    w.link("Match or possible match?", "Tool - Read pair (internal)", 0)
    w.code("Agent 6 - Notifier - build prompt", r"""
const v = $('Verifier - pick best').first().json;
const sec = $json;
const r = $('Intake - parse & guard').first().json;
const ownerLang = v.mine.type === 'lost' ? r.session.language : (sec.lost.language || 'fr');
const finderLang = v.mine.type === 'found' ? r.session.language : (sec.found.language || 'fr');
const lostRec = sec.lost.record;
const foundPublic = { ...sec.found.record, kept_at: sec.found.place_name };
const vars = { lost: lostRec, found: foundPublic, hidden_detail: sec.hidden_detail || '(none given)',
               decision: v.best.verdict.decision === 'match' ? 'very likely match' : 'possible match',
               language: ownerLang, finder_language: finderLang };
const groq_body = { messages: [{ role: 'system', content: render(SYSTEM, vars) }, { role: 'user', content: render(USER, vars) }],
                    temperature: TEMPERATURE, max_tokens: 600, response_format: { type: 'json_object' } };
return [{ json: { groq_body, model_env: MODEL_ENV, vision: false, hidden_detail: sec.hidden_detail || '', owner_language: ownerLang } }];
""", [X + 4840, Y - 240], prompt="notifier")
    w.link("Tool - Read pair (internal)", "Agent 6 - Notifier - build prompt")
    w.llm("Agent 6 - Notifier (LLM)", [X + 5060, Y - 240], "Groq - messages + challenge question")
    w.code("Notifier - leak guard", r"""
const prev = $('Agent 6 - Notifier - build prompt').first().json;
const v = $('Verifier - pick best').first().json;
let n = parseLLM($json) || {};
const lang = prev.owner_language;
const generic = L(lang, { fr: 'Décrivez un détail que seul le propriétaire peut connaître (contenu, inscription, fond d\'écran...).',
  en: 'Describe a detail only the owner can know (contents, inscription, wallpaper...).',
  ar: 'صف تفصيلاً لا يعرفه إلا المالك (المحتوى، كتابة، خلفية الشاشة...).',
  derja: 'Ousef detail ken enti ta3rfou (chnowa fih, mektoub 3lih, fond d\'écran...).' });
// deterministic leak check: no word (>=4 letters) of the secret may appear in the question; no yes/no question
const STOP = new Set(['dans','avec','pour','sont','cette','there','with','this','that','have','from','une','fih','3lih','mte3','fama','elle','il','your','votre','sur','under','inside']);
const secretWords = ((prev.hidden_detail || '').toLowerCase().match(/[\p{L}\d]{4,}/gu) || []).filter(w => !STOP.has(w));
const q = (n.challenge_question || '').toLowerCase();
const leaked = secretWords.some(wd => q.includes(wd)) || /^(is|est-ce|are|does|do|hal|هل|wa9ilek)\b/i.test(q.trim());
if (!n.challenge_question || leaked || !prev.hidden_detail) n.challenge_question = generic;
n.owner_message = n.owner_message || L(lang, { fr: 'Bonne nouvelle : un objet correspondant à votre déclaration a peut-être été trouvé.', en: 'Good news: an item matching your report may have been found.', ar: 'خبر سار: ربما تم العثور على غرض يطابق تصريحك.', derja: 'Khbar behi: fama 7aja tchabah lel 7aja elli dhay3etha.' });
n.finder_message = n.finder_message || 'Merci ! Un propriétaire possible a été identifié. Ses coordonnées seront partagées après vérification.';
return [{ json: { lost_id: v.lost_id, found_id: v.found_id, decision: v.best.verdict.decision, confidence: v.best.verdict.confidence || 0.5,
  challenge_question: n.challenge_question, owner_message: n.owner_message, finder_message: n.finder_message, leak_blocked: leaked } }];
""", [X + 5280, Y - 240], note="Blocks questions that would reveal the secret")
    w.http("Tool - Create claim & notify", f"={API}/tools/claims", [X + 5500, Y - 240],
           body="={{ JSON.stringify($json) }}")
    w.chain("Agent 6 - Notifier (LLM)", "Notifier - leak guard", "Tool - Create claim & notify")
    w.link("Agent 6 - Notifier - build prompt", "Agent 6 - Notifier (LLM)")
    w.code("Reply - match", r"""
const r = $('Intake - parse & guard').first().json;
const v = $('Verifier - pick best').first().json;
const n = $('Notifier - leak guard').first().json;
const lang = r.session.language;
const reset = { session_id: r.session.session_id, turn: 0, slots_so_far: {}, questions_already_asked: [], intent_so_far: null, language: lang, history: [] };
if (v.mine.type === 'lost') {
  return [{ json: { kind: 'challenge', claim_id: $json.claim_id, report_id: v.lost_id, session: reset,
    reply: n.owner_message + '\n\n' + n.challenge_question,
    match: { found_id: v.found_id, description: v.best.canonical_description, place: v.best.place_name, date: v.best.date_iso,
             score: v.best.score, decision: v.best.verdict.decision, confidence: v.best.verdict.confidence, agreements: v.best.verdict.agreements } } }];
}
return [{ json: { kind: 'registered', report_id: v.found_id, session: reset,
  reply: L(lang, { fr: `Merci ! Objet trouvé enregistré (${v.found_id}). `, en: `Thank you! Found item registered (${v.found_id}). `, ar: `شكراً! تم تسجيل الغرض (${v.found_id}). `, derja: `Ya3tik saha! Tsajlet el 7aja (${v.found_id}). ` }) + n.finder_message,
  match: { lost_id: v.lost_id, decision: v.best.verdict.decision } } }];
""", [X + 5720, Y - 240])
    w.link("Tool - Create claim & notify", "Reply - match")

    # ---------------- Agent 5: personalised recovery plan (agentic RAG) - reached from 3 places
    # 5a planner (decompose + route) -> multi-source retrieval -> 5b re-ranker -> 5c writer -> citation guard -> 5d checker
    Z = 560
    w.sticky("## Agent 5 - Recovery plan (agentic RAG)\n**5a Planner** splits the situation into 2-5 French sub-questions, each routed "
             "to a source family (administration / law / operator / sop) and an item type. **Tool** runs one search per sub-question. "
             "**5b Re-ranker** keeps max 2 useful passages per sub-question. **5c Writer** builds the timed plan + letter with [n] "
             "citations. **5d Checker** (another model) labels each step; unsupported steps are removed.", [X + 4600, Z + 300], w=900, h=170, color=3)
    w.code("Agent 5a - Planner - build prompt", r"""
const r = $('Intake - parse & guard').first().json;
let rec = null, saved = null;
try { rec = $('Perception - parse & privacy').first().json; } catch (e) {}
try { saved = $('Tool - Save report').first().json; } catch (e) {}
const it = r.intake, sl = it.slots || {};
const situation = it.intent === 'procedure_question' ? r.ctx.message : ((r.session.history || []).join(' | ') || r.ctx.message);
const vars = { today: today(), message: situation, language: r.session.language || 'fr', intent: it.intent,
               item_category: (rec && rec.record.category) || sl.item_category || 'unknown', place_type: sl.place_type || 'unknown',
               place_name: sl.place_name || 'unknown', date_iso: sl.date_iso || 'unknown' };
const groq_body = { messages: [{ role: 'system', content: render(SYSTEM, vars) }, { role: 'user', content: render(USER, vars) }],
                    temperature: TEMPERATURE, max_tokens: 700, response_format: { type: 'json_object' } };
return [{ json: { groq_body, model_env: MODEL_ENV, vision: false, report_id: saved ? saved.report_id : null, situation } }];
""", [X + 3740, Z], prompt="rag_planner", note="decomposition + source routing")
    w.link("Route next_action", "Agent 5a - Planner - build prompt", 2)
    w.link("Any candidate?", "Agent 5a - Planner - build prompt", 1)
    w.link("Match or possible match?", "Agent 5a - Planner - build prompt", 1)
    w.llm("Agent 5a - Planner (LLM)", [X + 3960, Z], "Groq gpt-oss-20b - sub-questions, sources, letter")
    w.code("Planner - parse & guard", r"""
const r = $('Intake - parse & guard').first().json;
const prev = $('Agent 5a - Planner - build prompt').first().json;
const SRC = ['administration', 'law', 'operator', 'sop'];
const TAGS = ['national_id', 'passport', 'family_booklet', 'driving_licence', 'bank_card', 'baggage', 'phone', 'general'];
let p = parseLLM($json) || {};
let sq = (Array.isArray(p.sub_questions) ? p.sub_questions : []).filter(x => x && x.q).slice(0, 5)
  .map(x => ({ q: String(x.q).slice(0, 200), sources: (x.sources || []).filter(s => SRC.includes(s)).slice(0, 2),
               item_type: TAGS.includes(x.item_type) ? x.item_type : 'general', need: x.need || '' }));
// fallback: planner unavailable -> single query (rewritten by the intake agent)
if (!sq.length) sq = [{ q: r.intake.search_query_fr || prev.situation, sources: [], item_type: 'general', need: 'what to do' }];
const plan = { situation: p.situation || prev.situation, affected_items: p.affected_items || [r.intake.slots.item_category || 'item'],
               urgency: p.urgency === 'high' ? 'high' : 'normal', letter: ['police_declaration', 'airline_claim'].includes(p.letter) ? p.letter : 'none',
               sub_questions: sq, planner_ok: !!parseLLM($json) };
return [{ json: plan }];
""", [X + 4180, Z], note="enum validation + single-query fallback")
    w.http("Tool - Multi-source retrieval", f"={API}/tools/rag/multi", [X + 4400, Z],
           body="={{ JSON.stringify({sub_questions: $json.sub_questions, per_question: 3}) }}",
           note="1 routed dense search per sub-question (bge-m3), max 15 passages")
    w.code("Agent 5b - Re-ranker - build prompt", r"""
const plan = $('Planner - parse & guard').first().json;
const vars = { sub_questions: plan.sub_questions.map((x, i) => `Q${i + 1}. ${x.q}`).join('\n'), candidates: $json.candidates || '(none)' };
const groq_body = { messages: [{ role: 'system', content: render(SYSTEM, vars) }, { role: 'user', content: render(USER, vars) }],
                    temperature: TEMPERATURE, max_tokens: 600, response_format: { type: 'json_object' } };
return [{ json: { groq_body, model_env: MODEL_ENV, vision: false, passages: $json.passages || [] } }];
""", [X + 4620, Z], prompt="rag_reranker")
    w.llm("Agent 5b - Re-ranker (LLM)", [X + 4840, Z], "Groq Qwen3.8-27B - keeps max 2 useful passages per sub-question")
    w.code("Re-ranker - select context", r"""
const plan = $('Planner - parse & guard').first().json;
const passages = $('Agent 5b - Re-ranker - build prompt').first().json.passages;
const byPid = Object.fromEntries(passages.map(p => [p.pid, p]));
const sel = parseLLM($json);
let picked = [];
if (sel && Array.isArray(sel.selection)) {
  sel.selection.slice().sort((a, b) => (a.sub_question || 0) - (b.sub_question || 0)).forEach(s =>
    (s.passages || []).slice(0, 2).forEach(x => { const pid = Number(String(x.pid ?? x).replace(/\D/g, '')); if (byPid[pid] && !picked.includes(pid) && (x.score ?? 2) >= 2) picked.push(pid); }));
}
const reranked = picked.length > 0;
// fallback (re-ranker unavailable or nothing kept): the 2 best retrieved passages of each sub-question
if (!picked.length) plan.sub_questions.forEach((q, i) => passages.filter(p => p.for.includes(i + 1)).slice(0, 2).forEach(p => { if (!picked.includes(p.pid)) picked.push(p.pid); }));
picked = picked.slice(0, 8);
const ctx = picked.map(pid => byPid[pid]);
const context = ctx.map((p, i) => `[${i + 1}] (${p.authority} - ${p.url})\n${p.text}`).join('\n\n');
return [{ json: { ...plan, context, ctx, reranked, n_candidates: passages.length } }];
""", [X + 5060, Z], note="selected passages -> numbered context [1..n]")
    w.code("Agent 5c - Plan writer - build prompt", r"""
const r = $('Intake - parse & guard').first().json;
const c = $json, sl = r.intake.slots || {};
const vars = { today: today(), situation: c.situation, affected_items: c.affected_items, urgency: c.urgency, language: r.session.language || 'fr',
               context: c.context || '(no sources found)', letter_type: c.letter, date_iso: sl.date_iso || 'unknown',
               place: [sl.place_name, sl.place_type, sl.governorate].filter(Boolean).join(', ') || 'unknown',
               message: r.intake.intent === 'procedure_question' ? r.ctx.message : ((r.session.history || []).join(' | ') || r.ctx.message) };
const groq_body = { messages: [{ role: 'system', content: render(SYSTEM, vars) }, { role: 'user', content: render(USER, vars) }],
                    temperature: TEMPERATURE, max_tokens: 1400, response_format: { type: 'json_object' } };
return [{ json: { groq_body, model_env: MODEL_ENV, vision: false } }];
""", [X + 5280, Z], prompt="recovery_plan")
    w.llm("Agent 5c - Plan writer (LLM)", [X + 5500, Z], "Groq gpt-oss-120b - timed plan, deadlines, letter, [n] citations")
    w.code("Plan - citation guard", r"""
const c = $('Re-ranker - select context').first().json;
let plan = parseLLM($json) || { summary: '', steps: [] };
const n = c.ctx.length;
// deterministic guard: keep only citations that exist; a step without a valid citation cannot be supported
plan.steps = (plan.steps || []).slice(0, 8).map(s => {
  const fromText = [...String(s.action || '').matchAll(/\[(\d+)\]/g)].map(m => Number(m[1]));
  const cites = [...new Set([...(s.cites || []), ...fromText].map(Number))].filter(k => k >= 1 && k <= n);
  return { when: ['now', 'today', 'this_week', 'later'].includes(s.when) ? s.when : 'later', action: String(s.action || ''), cites };
}).filter(s => s.action);
const steps = plan.steps.map((s, i) => `STEP ${i + 1}: ${s.action}\n` + (s.cites.length
  ? s.cites.map(k => `  [${k}] ${c.ctx[k - 1].text.replace(/\s+/g, ' ').slice(0, 550)}`).join('\n') : '  (no citation)')).join('\n\n');
const groq_body = { messages: [{ role: 'system', content: render(SYSTEM, { steps }) }, { role: 'user', content: render(USER, { steps }) }],
                    temperature: TEMPERATURE, max_tokens: 700, response_format: { type: 'json_object' } };
return [{ json: { plan, groq_body, model_env: MODEL_ENV, vision: false } }];
""", [X + 5720, Z], prompt="rag_checker", note="drops invalid citations, prepares the checker")
    w.llm("Agent 5d - Checker (LLM)", [X + 5940, Z], "Groq Qwen3.8-27B - faithfulness of each step")
    w.code("Reply - plan", r"""
const r = $('Intake - parse & guard').first().json;
const c = $('Re-ranker - select context').first().json;
const g = $('Plan - citation guard').first().json;
const reportId = $('Agent 5a - Planner - build prompt').first().json.report_id;
const lang = r.session.language || 'fr';
const plan = g.plan;
const ver = parseLLM($json);
const labels = {};
((ver && ver.verdicts) || []).forEach(v => { labels[Number(v.step)] = v.label; });
// self-verification: unsupported steps are removed, partially supported ones are flagged
const kept = [], removed = [];
plan.steps.forEach((s, i) => {
  const label = !s.cites.length ? 'unsupported' : (labels[i + 1] || (ver ? 'unsupported' : 'unchecked'));
  (label === 'unsupported' ? removed : kept).push({ ...s, label });
});
const T = { now: L(lang, { fr: 'Maintenant', en: 'Now', ar: 'الآن', derja: 'Taw' }), today: L(lang, { fr: "Aujourd'hui", en: 'Today', ar: 'اليوم', derja: 'Lyoum' }),
            this_week: L(lang, { fr: 'Cette semaine', en: 'This week', ar: 'هذا الأسبوع', derja: 'Had jem3a' }), later: L(lang, { fr: 'Ensuite', en: 'Later', ar: 'لاحقاً', derja: 'Ba3d' }) };
const check = L(lang, { fr: '(à confirmer auprès du bureau)', en: '(to confirm with the office)', ar: '(للتأكد لدى المصلحة)', derja: '(a confirmer auprès du bureau)' });
let text = (plan.summary || '') + '\n';
for (const k of ['now', 'today', 'this_week', 'later']) {
  const st = kept.filter(s => s.when === k);
  if (st.length) text += `\n**${T[k]}**\n` + st.map(s => `- ${s.action}${s.label === 'partially' ? ' ' + check : ''}`).join('\n') + '\n';
}
if ((plan.documents || []).length) text += `\n**${L(lang, { fr: 'Documents', en: 'Documents', ar: 'الوثائق', derja: 'Lwra9' })}** : ` + plan.documents.join(' · ') + '\n';
(plan.deadlines || []).filter(d => d && d.what).forEach(d => { text += `\n⏱ ${d.what}${d.date_iso ? ' → ' + d.date_iso : ''}`; });
let prefix = '';
if (reportId) prefix = r.intake.intent === 'found'
  ? L(lang, { fr: `Merci ! Objet trouvé enregistré (${reportId}). Aucun propriétaire identifié pour l'instant.\n\n`, en: `Thank you! Found item registered (${reportId}). No owner identified yet.\n\n`, ar: `شكراً! تم تسجيل الغرض (${reportId}).\n\n`, derja: `Ya3tik saha! Tsajlet (${reportId}). Mazelna ma l9ina moulha.\n\n` })
  : L(lang, { fr: `Votre déclaration est enregistrée (${reportId}). Aucune correspondance pour l'instant : la recherche est relancée chaque jour.\n\nVoici votre plan de récupération :\n`, en: `Your report is registered (${reportId}). No match yet: we re-check every day.\n\nHere is your recovery plan:\n`, ar: `تم تسجيل تصريحك (${reportId}). لا يوجد تطابق حالياً.\n\nهذه خطتك:\n`, derja: `Tsajel tasri7ek (${reportId}). Mazel ma fama 7atta 7aja tchabha, nfarksou kol nhar.\n\nHedha plan mte3ek:\n` });
const cited = [...new Set(kept.flatMap(s => s.cites))].sort((a, b) => a - b);
const sources = c.ctx.map((p, i) => ({ n: i + 1, id: p.id, doc_id: p.doc_id, source: p.source, authority: p.authority, url: p.url, cited: cited.includes(i + 1) }));
const reset = { session_id: r.session.session_id, turn: 0, slots_so_far: {}, questions_already_asked: [], intent_so_far: null, language: lang, history: [] };
// letter: fill the date placeholders the writer may leave (loss date, then today's date for the signature)
let letter = c.letter !== 'none' ? (plan.letter || null) : null;
if (letter && letter.body) {
  const fr = d => d ? d.split('-').reverse().join('/') : '';
  const lossDate = fr((r.intake.slots || {}).date_iso) || '[date]';
  let k = 0;
  letter = { ...letter, body: String(letter.body).replace(/\[(date_iso|date de la perte|date)\]/gi, () => (k++ === 0 ? lossDate : fr(today()))) };
}
const stats = { sub_questions: c.sub_questions.length, candidates: c.n_candidates, context: c.ctx.length, reranked: c.reranked,
                steps_written: plan.steps.length, steps_kept: kept.length, removed: removed.length,
                supported: kept.filter(s => s.label === 'supported').length, partially: kept.filter(s => s.label === 'partially').length };
return [{ json: { kind: reportId ? 'registered' : 'plan', report_id: reportId, session: reset,
  reply: (prefix + (plan.summary || '')).trim(), reply_text: prefix + text.trim(), sources,
  plan: { summary: plan.summary, urgency: c.urgency, affected_items: c.affected_items, sub_questions: c.sub_questions.map(q => q.q),
          steps: kept, documents: plan.documents || [], deadlines: plan.deadlines || [], coverage: plan.coverage || [],
          letter, stats } } }];
""", [X + 6160, Z], note="removes unsupported steps, renders the plan")
    w.chain("Agent 5a - Planner - build prompt", "Agent 5a - Planner (LLM)", "Planner - parse & guard", "Tool - Multi-source retrieval",
            "Agent 5b - Re-ranker - build prompt", "Agent 5b - Re-ranker (LLM)", "Re-ranker - select context",
            "Agent 5c - Plan writer - build prompt", "Agent 5c - Plan writer (LLM)", "Plan - citation guard",
            "Agent 5d - Checker (LLM)", "Reply - plan")

    # ---------------- found-items search (multimodal RAG over the live database)
    S = 1000
    w.sticky("## Found-items search (multimodal RAG)\nThe user's text OR photo is normalised by the Perception agent into the same "
             "English record as the indexed found items. **Tool**: dense retrieval + soft constraints (same category, place and "
             "plausible date add a bonus, never exclude). **Agent 8** re-ranks the candidates and answers with item references.",
             [X + 2160, S + 330], w=820, h=150, color=6)
    w.code("Agent 2 - Perception for search - build prompt", r"""
const r = $json;
const slots = r.intake.slots;
const vars = { report_type: 'lost', text: (r.session.history || []).join(' | ') || r.ctx.message,
               slots: Object.fromEntries(Object.entries(slots).filter(([k]) => k !== 'hidden_detail')), has_photo: !!r.ctx.image_b64 };
const user = render(USER, vars);
const content = r.ctx.image_b64 ? [{ type: 'text', text: user }, { type: 'image_url', image_url: { url: 'data:image/jpeg;base64,' + r.ctx.image_b64 } }] : user;
const groq_body = { messages: [{ role: 'system', content: render(SYSTEM, vars) }, { role: 'user', content }],
                    temperature: TEMPERATURE, max_tokens: 700, response_format: { type: 'json_object' } };
return [{ json: { groq_body, model_env: r.ctx.image_b64 ? 'VISION_MODEL' : 'FAST_MODEL', vision: !!r.ctx.image_b64 } }];
""", [X + 2200, S], prompt="perception", note="text or photo -> normalised English record (the query)")
    w.link("Route next_action", "Agent 2 - Perception for search - build prompt", 4)
    w.llm("Agent 2 - Perception for search (LLM, vision)", [X + 2420, S], "Groq - Qwen vision for a photo, gpt-oss-20b for text")
    w.code("Search - query record", r"""
const r = $('Intake - parse & guard').first().json;
const CATS = ['phone','wallet','bag','luggage','keys','id_document','bank_card','glasses','watch','headphones','laptop','electronics','clothing','jewelry','umbrella','shoes','bottle','other'];
const COLORS = ['black','white','grey','silver','gold','brown','beige','red','pink','orange','yellow','green','blue','purple','multicolor','transparent'];
let rec = parseLLM($json) || {};
const sl = r.intake.slots || {};
if (!CATS.includes(rec.category)) rec.category = CATS.includes(sl.item_category) ? sl.item_category : 'other';
if (rec.color_family === 'gray') rec.color_family = 'grey';
if (!COLORS.includes(rec.color_family)) rec.color_family = null;
if (!rec.canonical_description) rec.canonical_description = [rec.color_family, rec.brand, rec.category, sl.distinctive_detail].filter(Boolean).join(' ');
const slots = { governorate: sl.governorate || null, place_name: sl.place_name || null, place_type: sl.place_type || null, date_iso: sl.date_iso || null };
return [{ json: { record: rec, slots, text: r.ctx.message, top_k: 8 } }];
""", [X + 2640, S])
    w.http("Tool - Search found items", f"={API}/tools/search/found", [X + 2860, S],
           body="={{ JSON.stringify($json) }}", note="filters with relaxation + dense/BM25/attribute score")
    w.switch("Any found item?", "{{ ($json.results || []).length ? 0 : 1 }}", 2, [X + 3080, S])
    w.code("Agent 8 - Search answer - build prompt", r"""
const r = $('Intake - parse & guard').first().json;
const q = $('Search - query record').first().json;
const res = $json;
const vars = { message: r.ctx.message || '(photo of the lost item)', record: q.record, language: r.session.language || 'fr',
               place: q.slots.place_name || q.slots.governorate || 'not given', date: q.slots.date_iso || 'not given',
               relaxed: (res.relaxed || []).length ? res.relaxed.join(', ') : 'none', candidates: res.candidates };
const groq_body = { messages: [{ role: 'system', content: render(SYSTEM, vars) }, { role: 'user', content: render(USER, vars) }],
                    temperature: TEMPERATURE, max_tokens: 900, response_format: { type: 'json_object' } };
return [{ json: { groq_body, model_env: MODEL_ENV, vision: false } }];
""", [X + 3300, S - 80], prompt="search_answer")
    w.llm("Agent 8 - Search answer (LLM)", [X + 3520, S - 80], "Groq gpt-oss-120b - re-ranking + answer with references")
    w.code("Reply - search results", r"""
const r = $('Intake - parse & guard').first().json;
const res = $('Tool - Search found items').first().json;
const lang = r.session.language || 'fr';
const out = parseLLM($json);
const byId = Object.fromEntries((res.results || []).map(x => [x.id, x]));
const RANK = { high: 0, medium: 1, low: 2 };
let items;
if (out && Array.isArray(out.results)) {
  items = out.results.filter(x => byId[x.id]).map(x => ({ ...byId[x.id], relevance: RANK[x.relevance] !== undefined ? x.relevance : 'low', reason: x.reason || '' }))
    .sort((a, b) => RANK[a.relevance] - RANK[b.relevance]);
} else items = (res.results || []).slice(0, 3).map(x => ({ ...x, relevance: 'medium', reason: '' }));   // LLM unavailable: retrieval order
const shown = items.filter(x => x.relevance !== 'low').slice(0, 5);
const answer = (out && out.answer) || L(lang, { fr: `${shown.length} objet(s) trouvé(s) pourraient correspondre.`, en: `${shown.length} found item(s) may match.`, ar: `${shown.length} غرض قد يطابق.`, derja: `Fama ${shown.length} 7aja tnajem tkoun mte3ek.` });
const reset = { session_id: r.session.session_id, turn: 0, slots_so_far: {}, questions_already_asked: [], intent_so_far: null, language: lang, history: [] };
return [{ json: { kind: 'search', reply: answer, session: reset, found_items: shown, search: { applied: res.applied, relaxed: res.relaxed, candidates: (res.results || []).length } } }];
""", [X + 3740, S - 80])
    w.chain("Agent 2 - Perception for search - build prompt", "Agent 2 - Perception for search (LLM, vision)", "Search - query record",
            "Tool - Search found items", "Any found item?")
    w.link("Any found item?", "Agent 8 - Search answer - build prompt", 0)
    w.chain("Agent 8 - Search answer - build prompt", "Agent 8 - Search answer (LLM)", "Reply - search results")
    w.code("Reply - no search result", r"""
const r = $('Intake - parse & guard').first().json;
const lang = r.session.language || 'fr';
const reset = { session_id: r.session.session_id, turn: 0, slots_so_far: {}, questions_already_asked: [], intent_so_far: null, language: lang, history: [] };
return [{ json: { kind: 'search', found_items: [], session: reset, reply: L(lang, {
  fr: "Aucun objet correspondant n'a été déposé pour l'instant. Déclarez la perte : la recherche sera relancée chaque jour et vous serez prévenu.",
  en: 'No matching item has been handed in yet. Report the loss: we search again every day and will notify you.',
  ar: 'لم يتم تسليم أي غرض مطابق حتى الآن. صرّح بالضياع وسنبحث يومياً ونعلمك.',
  derja: "Mazel 7atta 7aja tchabha ma tsajlet. Sajel tasri7 bel 7aja elli dhay3etha w nfarksou kol nhar w nkhabrouk." }) } }];
""", [X + 3300, S + 100])
    w.link("Any found item?", "Reply - no search result", 1)

    # ---------------- claim branch: ownership verification
    C = -700
    w.http("Tool - Read claim secret (internal)", f"={API}/internal/claims/{{{{ $json.claim_id }}}}/secret", [X + 660, C],
           method="GET", headers={"X-Internal-Token": "={{ $env.INTERNAL_TOKEN }}"})
    w.link("Route action", "Tool - Read claim secret (internal)", 1)
    w.code("Agent 6b - Claim check - build prompt", r"""
const ctx = $('Normalize input').first().json;
const vars = { hidden_detail: $json.hidden_detail || '', answer: ctx.answer || ctx.message || '' };
const groq_body = { messages: [{ role: 'system', content: render(SYSTEM, vars) }, { role: 'user', content: render(USER, vars) }],
                    temperature: TEMPERATURE, max_tokens: 200, response_format: { type: 'json_object' } };
return [{ json: { groq_body, model_env: MODEL_ENV, vision: false } }];
""", [X + 880, C], prompt="claim_check")
    w.llm("Agent 6b - Claim check (LLM)", [X + 1100, C], "Groq - semantic, cross-language comparison")
    w.code("Claim check - parse", r"""
const v = parseLLM($json) || { verified: false, reason: 'checker unavailable' };
return [{ json: { verified: v.verified === true, reason: v.reason || '' } }];
""", [X + 1320, C])
    w.http("Tool - Claim result (internal)", f"={API}/internal/claims/{{{{ $('Normalize input').first().json.claim_id }}}}/result", [X + 1540, C],
           body="={{ JSON.stringify($json) }}", headers={"X-Internal-Token": "={{ $env.INTERNAL_TOKEN }}"})
    w.code("Reply - claim outcome", r"""
const ctx = $('Normalize input').first().json;
const lang = ctx.session.language || 'fr';
const res = $json;
let reply;
if (res.status === 'verified') {
  const c = res.finder_contact || {};
  reply = L(lang, { fr: `Vérification réussie ✅. L'objet est disponible à : ${res.pickup_place || 'lieu indiqué par la personne qui l\'a trouvé'}.` + (c.name || c.phone || c.email ? `\nContact : ${[c.name, c.phone, c.email].filter(Boolean).join(' - ')}` : ''),
                    en: `Verified ✅. The item is available at: ${res.pickup_place || 'the place given by the finder'}.` + (c.name || c.phone || c.email ? `\nContact: ${[c.name, c.phone, c.email].filter(Boolean).join(' - ')}` : ''),
                    ar: `تم التحقق ✅. الغرض متوفر في: ${res.pickup_place || ''}`, derja: `Tthabetna ✅. El 7aja mawjouda fi: ${res.pickup_place || ''}` });
} else if (res.status === 'retry') {
  reply = L(lang, { fr: 'La réponse ne correspond pas. Il vous reste 1 essai.', en: 'The answer does not match. 1 attempt left.', ar: 'الإجابة غير مطابقة. بقيت لك محاولة واحدة.', derja: 'El jweb moch s7i7. 3andek mouhawla wa7da.' });
} else {
  reply = L(lang, { fr: 'Vérification bloquée après 2 réponses incorrectes. Contactez le lieu de dépôt avec une preuve de propriété.', en: 'Verification locked after 2 wrong answers. Contact the deposit place with proof of ownership.', ar: 'تم إيقاف التحقق بعد إجابتين خاطئتين.', derja: 'Tsakret el verification ba3d 2 ajwiba ghaltin.' });
}
return [{ json: { kind: 'claim_' + res.status, claim_id: ctx.claim_id, reply, session: ctx.session } }];
""", [X + 1760, C])
    w.chain("Tool - Read claim secret (internal)", "Agent 6b - Claim check - build prompt", "Agent 6b - Claim check (LLM)",
            "Claim check - parse", "Tool - Claim result (internal)", "Reply - claim outcome")

    # ---------------- single response node
    w.add("Respond to web app", "n8n-nodes-base.respondToWebhook", 1.1,
          {"respondWith": "firstIncomingItem", "options": {}}, [X + 6600, 0])
    for n in ["Reply - ask user", "Reply - status", "Reply - match", "Reply - plan", "Reply - claim outcome",
              "Reply - search results", "Reply - no search result"]:
        w.link(n, "Respond to web app")
    w.dump("lqitha_main.json", {"errorWorkflow": ERROR_ID, "saveDataSuccessExecution": "all", "saveManualExecutions": True})


# =============================================================================== REMATCH (daily)
def build_rematch():
    w = WF("Lqitha - Daily re-matching", REMATCH_ID)
    w.sticky("## Daily re-matching\nA phone lost today may only be handed in tomorrow. Every day at 08:00 the matcher re-scores "
             "all open lost reports; strong new candidates go through the same Verifier and Notifier agents, and the owner "
             "is notified in the web app.", [-300, -300], w=600, h=160, color=6)
    w.add("Every day 08:00", "n8n-nodes-base.scheduleTrigger", 1.2,
          {"rule": {"interval": [{"field": "days", "triggerAtHour": 8}]}}, [0, 0])
    w.add("Run now (manual)", "n8n-nodes-base.manualTrigger", 1, {}, [0, 200])
    w.http("Tool - Rematch candidates", f"={API}/tools/rematch/candidates?min_score=0.62", [220, 100], method="GET")
    w.link("Every day 08:00", "Tool - Rematch candidates")
    w.link("Run now (manual)", "Tool - Rematch candidates")
    w.code("Split pairs", "return ($json.pairs || []).map(p => ({ json: p }));", [440, 100], helpers=False)
    w.http("Tool - Read pair (internal)", f"={API}/internal/pairs/{{{{ $json.lost_id }}}}/{{{{ $json.found_id }}}}", [660, 100],
           method="GET", headers={"X-Internal-Token": "={{ $env.INTERNAL_TOKEN }}"})
    w.code("Agent 4 - Verifier - build prompt", r"""
const p = $json;
const side = rep => ({ ...rep.record, place: rep.place_name, governorate: rep.governorate, date: rep.date_iso });
const vars = { lost: side(p.lost), found: side(p.found), score: p.score };
const groq_body = { messages: [{ role: 'system', content: render(SYSTEM, vars) }, { role: 'user', content: render(USER, vars) }],
                    temperature: TEMPERATURE, max_tokens: 700, response_format: { type: 'json_object' } };
return { json: { groq_body, model_env: MODEL_ENV, vision: false } };
""", [880, 100], each=True, prompt="verifier")
    w.llm("Agent 4 - Verifier (LLM)", [1100, 100], "Groq")
    w.code("Keep confident matches", r"""
const pairs = $('Tool - Read pair (internal)').all();
const out = [];
$input.all().forEach((it, i) => {
  const v = parseLLM(it.json);
  if (v && ['match', 'possible_match'].includes(v.decision)) out.push({ json: { pair: pairs[i].json, verdict: v } });
});
return out;
""", [1320, 100])
    w.code("Agent 6 - Notifier - build prompt", r"""
const p = $json.pair;
const vars = { lost: p.lost.record, found: { ...p.found.record, kept_at: p.found.place_name }, hidden_detail: p.hidden_detail || '(none given)',
               decision: $json.verdict.decision === 'match' ? 'very likely match' : 'possible match',
               language: p.lost.language || 'fr', finder_language: p.found.language || 'fr' };
const groq_body = { messages: [{ role: 'system', content: render(SYSTEM, vars) }, { role: 'user', content: render(USER, vars) }],
                    temperature: TEMPERATURE, max_tokens: 600, response_format: { type: 'json_object' } };
return { json: { groq_body, model_env: MODEL_ENV, vision: false } };
""", [1540, 100], each=True, prompt="notifier")
    w.llm("Agent 6 - Notifier (LLM)", [1760, 100], "Groq")
    w.code("Leak guard + claim body", r"""
const prev = $('Keep confident matches').all();
return $input.all().map((it, i) => {
  const p = prev[i].json.pair, v = prev[i].json.verdict;
  const n = parseLLM(it.json) || {};
  const STOP = new Set(['dans','avec','pour','sont','cette','there','with','this','that','have','from','fama','your','votre','inside']);
  const words = ((p.hidden_detail || '').toLowerCase().match(/[\p{L}\d]{4,}/gu) || []).filter(w => !STOP.has(w));
  const q = (n.challenge_question || '').toLowerCase();
  if (!n.challenge_question || words.some(w => q.includes(w)) || !p.hidden_detail)
    n.challenge_question = 'Décrivez un détail que seul le propriétaire peut connaître (contenu, inscription, fond d\'écran...).';
  return { json: { lost_id: p.lost.id, found_id: p.found.id, decision: v.decision, confidence: v.confidence || 0.5,
    challenge_question: n.challenge_question, owner_message: n.owner_message || 'Un objet correspondant à votre déclaration a peut-être été trouvé.',
    finder_message: n.finder_message || 'Un propriétaire possible a été identifié.' } };
});
""", [1980, 100])
    w.http("Tool - Create claim & notify", f"={API}/tools/claims", [2200, 100], body="={{ JSON.stringify($json) }}")
    w.chain("Tool - Rematch candidates", "Split pairs", "Tool - Read pair (internal)", "Agent 4 - Verifier - build prompt",
            "Agent 4 - Verifier (LLM)", "Keep confident matches", "Agent 6 - Notifier - build prompt",
            "Agent 6 - Notifier (LLM)", "Leak guard + claim body", "Tool - Create claim & notify")
    w.dump("lqitha_rematch_daily.json", {"errorWorkflow": ERROR_ID})


# =============================================================================== ERROR HANDLER
def build_error():
    w = WF("Lqitha - Error handler", ERROR_ID)
    w.add("Error Trigger", "n8n-nodes-base.errorTrigger", 1, {}, [0, 0])
    w.http("Tool - Log error", f"={API}/tools/log_error", [220, 0], retry=False,
           body="={{ JSON.stringify({workflow: $json.workflow.name, node: $json.execution.lastNodeExecuted, message: $json.execution.error.message, execution_id: $json.execution.id}) }}")
    w.link("Error Trigger", "Tool - Log error")
    w.dump("lqitha_error_handler.json")


if __name__ == "__main__":
    build_main()
    build_rematch()
    build_error()
