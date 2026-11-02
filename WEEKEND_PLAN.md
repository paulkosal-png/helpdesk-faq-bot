# Weekend plan: ship helpdesk-faq-bot (Sat Oct 31 – Sun Nov 1, 2026)

Goal: by Sunday night there's a **public GitHub repo** with green CI, a working Slack demo, a Grafana
screenshot, and two resume bullets. Budget: **about 4–6 hours each day**. Tick boxes as you go.

> **Golden rule:** use a **personal** Slack workspace, personal Google account, and personal GCP project.
> Never connect this to your work Slack, work Google Workspace, or work data.

---

## Saturday: run it, understand it, put it in Slack (~5 h)

### Block 1: local setup and tests (1 h)
- [ ] Unzip `helpdesk-faq-bot.zip`, open the folder in VS Code.
- [ ] Create a venv and install:
  ```powershell
  python -m venv .venv
  .\.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
  pip install -r requirements-dev.txt
  $env:PYTHONPATH="src"               # macOS/Linux: export PYTHONPATH=src
  ```
- [ ] `pytest -q` → expect **58 passed**.
- [ ] `ruff check .` → expect **All checks passed**.
- [ ] `python -m helpdesk_faq_bot check` and `python -m helpdesk_faq_bot demo`.

### Block 2: read the code (1 h). You'll be asked about it in interviews
- [ ] `matcher.py`: how a rule like `(wifi OR "wi-fi") AND NOT guest` becomes a tree (`parse_rule`), and the exact → phrase → keyword tiers.
- [ ] `service.py`: the match → RAG → escalate flow and where metrics are recorded.
- [ ] `rag.py`: chunking, embedding, cosine search, the prompt in `SYSTEM_PROMPT`.
- [ ] Make it yours: add **3 new FAQ rows** to `data/sample_faq.csv` (e.g. "How do I book a meeting room?") with alternate phrases and a keyword rule. Test with `python -m helpdesk_faq_bot match "..."`, and add one pytest case in `tests/test_matcher.py`.

### Block 3: Slack (1.5 h)
- [ ] Create a free personal workspace at <https://slack.com/get-started> (e.g. "paul-sandbox").
- [ ] <https://api.slack.com/apps> → **Create New App → From a manifest** → pick the sandbox workspace → paste `slack/manifest.yaml`.
- [ ] **Install to Workspace** → copy the **Bot User OAuth Token** (`xoxb-…`).
- [ ] **Basic Information → App-Level Tokens → Generate** with scope `connections:write` → copy (`xapp-…`).
- [ ] `copy .env.example .env` (macOS/Linux: `cp`), then paste both tokens. Confirm `.env` is **not** shown in `git status` later.
- [ ] `python -m helpdesk_faq_bot slack` → DM the bot: "how do I reset my password?"
- [ ] Try the clarification flow: "how do I connect the printer to wifi" → click a button, then try **None of these**.
- [ ] Create `#it-help`, `/invite @helpdesk-bot`, and mention it. The reply should land in a thread.
- [ ] Check `logs/unanswered.jsonl` for the escalated questions.

### Block 4: RAG (1–1.5 h)
- [ ] Install Ollama (<https://ollama.com>), then `ollama pull llama3.2`. Free, runs locally.
- [ ] In `.env`: `LLM_BASE_URL=http://localhost:11434/v1` and `LLM_MODEL=llama3.2`.
- [ ] Ask: "my laptop fan is really loud and it's overheating" → expect a `[rag]` answer with sources.
- [ ] Ask something off-topic ("can I bring my dog?") → it should escalate, not make something up.
- [ ] Optional: `pip install -r requirements-rag.txt`, then `EMBEDDING_BACKEND=sentence-transformers` and `RAG_MIN_SCORE=0.35`. Compare which questions now get answered. Note the difference for your README.
- [ ] Write one new KB article in `docs/sample_kb/` (e.g. `conference-room-av.md`) and confirm RAG uses it.

---

## Sunday: observability, GitHub, (optional) cloud, resume (~5 h)

### Block 5: docker compose + Grafana (1.5 h)
- [ ] Install/start Docker Desktop.
- [ ] `docker compose up --build` (uses your `.env`, so the Slack bot runs in a container). No tokens handy? Use `BOT_MODE=simulate docker compose up --build` (PowerShell: `$env:BOT_MODE="simulate"; docker compose up --build`).
- [ ] Ollama from inside Docker: set `LLM_BASE_URL=http://host.docker.internal:11434/v1`.
- [ ] Open <http://localhost:9090/targets> → `helpdesk-faq-bot` should be **UP**.
- [ ] Open <http://localhost:3000> (admin/admin, then change it) → **Helpdesk FAQ Bot** dashboard.
- [ ] Ask the bot 15–20 questions in Slack (mix of known, ambiguous, RAG, nonsense), or let `simulate` run for 10 min.
- [ ] Screenshot the dashboard → save as `docs/images/grafana-dashboard.png` (the README already points there). Optional: also a Slack conversation screenshot → `docs/images/slack-demo.png`.

### Block 6: GitHub (1 h)
- [ ] Final scrub: search the repo for your employer's name, real hostnames, real emails, and tokens:
  `git grep -n -i -E "xoxb-[0-9]|xapp-[0-9]|sk-[A-Za-z0-9]{10}|BEGIN PRIVATE KEY"` should find nothing.
- [ ] Replace `<your-github-username>` in `README.md`.
- [ ] Re-read the "Credits" line in the README and adjust it so it's accurate in your own words.
- [ ] ```bash
  git init && git add . && git status    # make sure .env, secrets/, logs/ are NOT listed
  git commit -m "Initial commit: Slack helpdesk FAQ bot with keyword rules, RAG fallback, Prometheus/Grafana"
  ```
- [ ] Create a **public** repo `helpdesk-faq-bot` on GitHub (no README/license, since we have them), then `git branch -M main`, `git remote add origin …`, and `git push -u origin main`.
- [ ] Watch **Actions** → CI should go green (lint, tests, docker build, container smoke test).
- [ ] Add repo topics: `slack-bot`, `rag`, `llm`, `prometheus`, `grafana`, `terraform`, `python`, `mlops`.
- [ ] Pin the repo on your GitHub profile.

### Block 7 (optional, 1.5 h): Terraform deploy to Cloud Run
Only if Blocks 5–6 are done. It's fine to leave this for a weeknight.
- [ ] Personal GCP project with billing + a **budget alert** ($5).
- [ ] Follow `terraform/README.md`: targeted apply → add secret versions → build/push image → `terraform apply`.
- [ ] Check logs: `gcloud run services logs read helpdesk-faq-bot --region us-west1`. DM the bot in Slack while your laptop bot is **stopped** (otherwise both connections share the events).
- [ ] Screenshot the Cloud Run service + Secret Manager for the README, then **`terraform destroy`** (min instances = 1 bills 24/7).

### Block 8: resume and LinkedIn (30 min)
Pick and edit the bullets (keep them true to what you actually ran):

- **Built and open-sourced a Slack IT helpdesk bot (Python, Slack Bolt/Socket Mode) that answers employee FAQs from a Google Sheet using a custom boolean keyword-rule engine, with a RAG fallback (embeddings + vector search + OpenAI-compatible LLM) that cites sources and escalates unanswered questions.**
- **Made it production-ready with Docker, GitHub Actions CI (ruff, 58 pytest tests, image build), Prometheus metrics and a provisioned Grafana dashboard (hit rate, RAG fallback, latency), and Terraform for Cloud Run with Secret Manager and a least-privilege service account.**

- [ ] Add the repo link to the Projects section of your resume and to LinkedIn **Featured**.
- [ ] Optional LinkedIn post: one paragraph on why keyword rules come first and the LLM is only the fallback (cost, predictability, no hallucinated IT advice), plus the dashboard screenshot.

---

## If you get stuck
| Symptom | Fix |
|---|---|
| Can't type in the bot's DM | App Home → Messages tab on, "Allow users to send messages" checked (the manifest sets this), then reload Slack |
| Bot ignores channel messages | Invite it to the channel and actually @mention it |
| Buttons do nothing | Interactivity on (manifest), bot running, and you're the person who asked, within 15 min |
| `invalid_auth` / `not_allowed_token_type` | `SLACK_BOT_TOKEN` must start `xoxb-`, `SLACK_APP_TOKEN` must start `xapp-` |
| RAG always escalates | Check `LLM_BASE_URL` / `LLM_MODEL`, Ollama running; lower `RAG_MIN_SCORE` a little; run with `LOG_LEVEL=DEBUG` |
| Prometheus target DOWN | The bot container crashed. Run `docker compose logs bot` (usually missing tokens; use `BOT_MODE=simulate`) |
