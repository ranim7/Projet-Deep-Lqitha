# Lqitha - installation and run guide

This guide installs Lqitha on a new Windows 10/11 machine. Everything used is **free**.
Time needed: about 30 minutes, mostly downloads.

---

## 1. Prerequisites (install once)

| Software | Why | Where |
|---|---|---|
| **Python 3.10+** (or Anaconda) | tools API, RAG, evaluations | https://www.python.org/downloads/ (tick "Add python.exe to PATH") |
| **Docker Desktop** | runs n8n | https://www.docker.com/products/docker-desktop/ |
| **Ollama** | local embeddings (bge-m3) and local fallback model (Gemma 3 4B) | https://ollama.com/download |
| **Groq API key** (free) | LLMs, vision model, Whisper | https://console.groq.com/keys (sign up, "Create API key") |
| Kaggle API token (free, optional) | only to re-download the Kaggle datasets | https://www.kaggle.com/settings -> "Create New Token" -> save `kaggle.json` in `%USERPROFILE%\.kaggle\` |

Start **Docker Desktop** and **Ollama** once so they are running.

## 2. Configure

1. Copy `.env.example` to `.env` (same folder).
2. Open `.env` in Notepad and paste your Groq key after `GROQ_API_KEY=`.
3. Replace `INTERNAL_TOKEN=change-me-to-a-random-string` by any random string.
   Do not share `.env` - it is excluded from the repository by `.gitignore`.

## 3. Install and run (double-click, in this order)

| Step | Double-click | What happens | Duration |
|---|---|---|---|
| 1 | `run_01_download_data.bat` | downloads the real datasets into `data/raw/` (Amazon Berkeley Objects, Delhi Metro, TSA, OpenStreetMap, LinTO speech, official Tunisian documents) | 5-10 min |
| 2 | `run_02_setup.bat` | creates the Python environment, pulls `bge-m3` and `gemma3:4b`, checks Groq, prepares the data, builds the knowledge base, generates the n8n workflows, seeds the demo database, starts n8n in Docker and imports + publishes the 3 workflows | 10-20 min |
| 3 | `run_03_start.bat` | starts the tools API and opens the web app | 1 min |
| 4 | `run_04_evaluate.bat` (optional) | re-runs every evaluation -> `reports/results/` | 30-60 min (free-tier rate limits) |

Logs of every step are written to `logs/`.

## 4. Use it

* **Web app:** http://localhost:8000 - write (Derja, French, Arabic, English), attach a photo, record a voice note, pick a place.
* **n8n:** http://localhost:5678 - on the first visit n8n asks you to create a local owner account (stays on your machine).
  Workflows: `Lqitha - Main (multi-agent lost & found)`, `Lqitha - Daily re-matching`, `Lqitha - Error handler`.
  Open **Executions** to see the input and output of every agent for each message.
* **API docs** (tools used by the agents): http://localhost:8000/docs

### Demo scenario (3 minutes)

1. *Finder:* choose `Sousse` + a louage station, attach a photo of a backpack, write `l9it sac à dos fi louage Sousse lyoum`,
   then give the secret detail, e.g. `fih kteb mta3 math w stylo a7mer`.
2. *Owner:* open the app in a private window (new session) and write `dhaya3li sac à dos k7el fih khtout turquoise fi louage mta3 Sousse lyoum`.
   Lqitha finds the match and asks an ownership question without revealing the secret.
3. Answer wrongly (`fih laptop`) -> rejected; answer correctly (`kteb math w stylo a7mer`) -> the pickup place is released.
4. Ask `Que faire si j'ai perdu ma carte d'identité ?` -> answer with citations from the knowledge base.

## 5. Importing the workflows manually (without the .bat files)

The exported workflows are in `n8n/`. In n8n: **Workflows -> Import from file**, choose the three JSON files, then **Publish** the main and daily workflows.
The workflows read their configuration from environment variables (see `docker-compose.yml`):
`GROQ_API_KEY`, `TEXT_MODEL`, `FAST_MODEL`, `VISION_MODEL`, `LQITHA_API`, `INTERNAL_TOKEN`, `OLLAMA_DOCKER_URL`, `LOCAL_VISION_MODEL`, and `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| a status dot is red at the top of the web app | `api`: run `run_03_start.bat`; `n8n`: start Docker Desktop; `ollama`: start Ollama; `groq_key`: check `.env` |
| answers are slow (10-30 s) | normal on the Groq free tier (8,000 tokens/minute per model); the workflow waits and retries |
| `429 ... tokens per day` in the logs | a free daily quota is used up; n8n automatically falls back to the local Gemma 3 4B model; quotas reset within 24 h |
| port 8000 or 5678 already used | close the other application, or change the port in `scripts/start_api.cmd` / `docker-compose.yml` |
| change a prompt | edit `prompts/prompts.yaml`, run `.venv\Scripts\python scripts\build_n8n.py`, then step 7 of `run_02_setup.bat` (re-import) |
