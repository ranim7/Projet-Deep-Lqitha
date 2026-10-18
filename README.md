# Lqitha (لقيتها) - multi-agent lost & found assistant for Tunisia

Esprit 5DS - Advanced Deep Learning final project (AI Automation).
People who **lose** or **find** an item anywhere in Tunisia (airports, train/metro/bus stations, louages,
streets) talk to Lqitha in **Derja, French, Arabic or English**, by **text, photo or voice note**.
A team of agents orchestrated by **n8n** understands the report, normalises it, finds matching reports,
verifies the match, organises a **secure return** (ownership question) and uses two RAG pipelines:

* **Agentic RAG - personalised recovery plan**: a planner splits the user's situation into sub-questions routed to
  4 source families, one search per sub-question, LLM re-ranking, a writer that builds a timed plan (now / today /
  this week), deadlines and a pre-filled letter with citations, and a checker (another model) that removes unsupported steps.
* **Multimodal RAG - search in the found items**: a text or a photo of the lost item becomes the same English record
  as the indexed found items; dense retrieval with soft constraints (category, place, date), then LLM re-ranking and
  an answer with item references.

Stack: Groq API (LLMs + Whisper), Ollama (local embeddings and fallback model), a NumPy vector store, n8n self-hosted, FastAPI.

---

## 1. Install and run (Windows 10/11)

### Prerequisites
| Software | Link |
|---|---|
| Python 3.10+ (tick "Add python.exe to PATH") | https://www.python.org/downloads/ |
| Docker Desktop (runs n8n) | https://www.docker.com/products/docker-desktop/ |
| Ollama (local embeddings + local fallback model) | https://ollama.com/download |
| Groq API key (LLMs, vision model, Whisper) | https://console.groq.com/keys |
| Kaggle API token - optional, only to re-download the Kaggle datasets | https://www.kaggle.com/settings |

Start Docker Desktop and Ollama once so they are running.

### Steps
```bat
git clone https://github.com/ranim7/Projet-Deep-Lqitha.git
cd Projet-Deep-Lqitha
copy .env.example .env
notepad .env        &:: paste your Groq key after GROQ_API_KEY= and set any random INTERNAL_TOKEN
```

Then double-click, in this order:

| Step | Double-click | What it does | Time |
|---|---|---|---|
| 1 | `run_01_download_data.bat` | Downloads the real datasets into `data/raw/` (see section 4) | 5-10 min |
| 2 | `run_02_setup.bat` | Python environment, pulls `bge-m3` and `gemma3:4b` (Ollama), checks Groq, prepares the data, builds the knowledge base, generates and imports the n8n workflows (Docker), seeds the demo database | 10-20 min |
| 3 | `run_03_start.bat` | Starts the tools API and opens the web app on **http://localhost:8000** | 1 min |
| 4 | `run_04_evaluate.bat` (optional) | Re-runs all evaluations (RAG, agentic RAG, found-items search, prompts, matching, speech-to-text, translation BLEU / ROUGE) -> `reports/results/` | 30-60 min |

Logs of every step: `logs/`.

### Use it
* **Web app - http://localhost:8000**: write in Derja, French, Arabic or English, attach a photo or record a voice note,
  browse the found-items board (Derja / French / English) and follow your reports. Try:
  `dhaya3li portefeuille fih carte d'identité w carte bancaire fi louage Sousse lbera7, chnowa na3mel?` (recovery plan)
  and `fama chkoun l9a sac à dos k7el fi Sousse?` (found-items search).
* **n8n - http://localhost:5678**: on the first visit n8n asks you to create a local owner account. Workflows:
  `Lqitha - Main (multi-agent lost & found)`, `Lqitha - Daily re-matching`, `Lqitha - Error handler`.
  Open **Executions** to see the input and output of every agent for each message.
* **API docs - http://localhost:8000/docs** (the tools called by the agents).
* Demo scenario, manual import of the workflows and troubleshooting: **[INSTALL.md](INSTALL.md)**.
* Change a prompt: edit `prompts/prompts.yaml` -> `.venv\Scripts\python scripts\build_n8n.py` -> re-run `run_02_setup.bat`.

## 2. Architecture

```
Web app (text / photo / voice / place)            n8n  (Docker, port 5678)                               Lqitha tools API (FastAPI, port 8000)
        |  POST /chat                              Webhook -> Normalize -> Route action                    /tools/stt        Groq Whisper large-v3
        +---------------------------------------->   [voice?] -> Tool STT --------------------------------> /tools/reports    SQLite + vectors (bge-m3)
                                                     Agent 1  Intake & Router (gpt-oss-20b) + guards        /tools/match      matcher (agent 3)
                                                       |- ask_user / reply ------------> reply             /tools/rag/search recursive chunks + bge-m3 + filter
                                                       |- to_procedure -> Agent 5 agentic RAG           /tools/claims     claims + notifications
                                                       |     (5a planner -> /tools/rag/multi -> 5b re-ranker -> 5c plan writer -> 5d checker)
                                                       |- to_search ---> Agent 2 Perception -> /tools/search/found -> Agent 8 re-rank + answer
                                                       |- to_status --------------------> Tool session reports
                                                       '- to_perception -> Agent 2 Perception (Qwen3.8-27B vision; text-only -> gpt-oss-20b)
                                                                          -> Tool save report -> Agent 3 Matcher (tool)
                                                                          -> Agent 4 Verifier x top-2 (gpt-oss-120b)
                                                                          -> match?  yes -> Agent 6 Notifier (gpt-oss-120b) + leak guard -> claim + notifications
                                                                                     no  -> Agent 5 recovery plan (agentic RAG) -> reply
                                                     claim_answer -> Agent 6b Claim check (gpt-oss-20b) -> release pickup place / lock after 2 tries
                                                     Schedule 08:00 -> Daily re-matching (Verifier -> Notifier)
```

Why a **router + sequential pipeline with an independent verifier** (and not one autonomous orchestrator agent):
the process is mostly linear, each step has a testable JSON contract, each agent needs a different model
(vision vs reasoning vs fast), and the agent that proposes a match (matcher) is not the one that judges it.
Deterministic guards in n8n (turn limit, hidden-detail privacy, digit masking, leak check) have the last word.

## 3. Main results (reports/results/)

| Component | Test set | Result |
|---|---|---|
| Agentic RAG vs single-query RAG | 12 situations, 4 languages | needs found in context 80% -> 97%, context precision 71% -> 82%, steps supported by sources 52% -> 86% |
| Found-items search | 13 requests, 209 found items | Hit@1 0.85, Hit@3 0.92 (hard filters: 0.62); 90% of "high" labels correct |
| RAG retrieval configuration | 32 questions | Hit@1 0.875, Hit@3 1.00, MRR 0.94 |
| Intake v1 -> v3 | 30 messages | correct intent 7% -> 100% |
| Matching (photo vs text) | 40 vs 40 | Hit@1 0.33 -> 0.68 |

## 4. Repository

```
lqitha/            Python package: config, Groq client, embeddings, RAG engine, matcher, storage, FastAPI app
prompts/           prompts.yaml = versioned prompt library (v1/v2, techniques, failures, fixes) + PROMPT_LIBRARY.md
n8n/               exported workflows (generated by scripts/build_n8n.py)
webapp/            web app (single HTML file: assistant, found-items board in Derja/FR/EN, tracking)
scripts/           data pipeline (download_data, prepare_data, build_kb, seed_demo), workflow generation (build_n8n,
                   build_prompt_library), evaluations (eval_rag, eval_agentic_rag, eval_search, eval_prompts, eval_matching,
                   eval_stt, eval_translation), checks (check_groq, smoke_test, smoke_rag)
data/knowledge_base/  normalised corpus (docs/), team SOPs (sops/), chunk files
data/eval/         retrieval test queries (gold documents + key phrases), recovery-plan situations (plan_cases.json)
data/processed/    cleaned datasets (Tunisian places, item catalogue, Delhi Metro records, statistics)
reports/results/   all evaluation outputs (JSON + Markdown) + cache/ of model answers (re-running the evaluations is free)
```

## 5. Datasets

| Dataset | Source | Used for |
|---|---|---|
| Amazon Berkeley Objects (536 product photos + attributes, 13 categories) | AWS open data | Perception agent evaluation, cross-modal matching benchmark, demo seed |
| Delhi Metro Lost & Found (13,713 real found-item records) | Kaggle | category taxonomy, text matching benchmark with 1,500 real distractors, demo seed |
| TSA Claims Database (204,267 airport property claims) | Kaggle | what is lost/damaged at airports (taxonomy, report) |
| OpenStreetMap - 661 Tunisian airports, stations, louage stations, malls | Overpass API | places list in the app, realistic locations |
| LinTO Tunisian Derja speech (34 clips + transcripts) | Hugging Face | speech-to-text evaluation |
| Tunisian procedures: idaraty.tn (passport, family booklet), Loi organique 2024-22 (CIN), Tunisair passenger protection 2026, Montreal Convention 1999, ICAO guide, bank-card guide, embassy guide + 7 team SOPs | official websites | RAG knowledge base |

What did not work (documented in the technical report): the SNCF found-objects dataset was removed from SNCF open data;
idaraty.tn was down (HTTP 523) so pages came from the Internet Archive; Arabic idaraty pages had no archive copy.
