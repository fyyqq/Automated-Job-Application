# 🤖 Automated Job Application Engine

A self-hosted pipeline for the **Malaysian job market**. Every morning at **08:00 MYT** it collects fresh vacancies, drops the ones that don't fit, skips anything already in your tracker, scores the rest against your resume with an LLM, and logs every job to **Google Sheets**.

Three hard rules shape the design:

- 💸 **Budget:** Anthropic API spend stays **under RM 10/month** (estimate: RM 2.50 to RM 5.00).
- 🧢 **Volume:** **5 to 10 applications per day, max.** Each one is targeted and unique.
- 📒 **Logging:** every job found, skipped, queued or applied is written to the Google Sheets tracker.

> ⚠️ **Read [Terms of Service and ban risk](#️-terms-of-service-and-ban-risk) before turning on auto-submit.**

---

## 🔀 How it works

```text
[⏰ n8n Schedule Trigger · 08:00 Asia/Kuala_Lumpur]
            │
            ▼
[📨 Collect postings]        LinkedIn job-alert emails → worker /parse-alerts
            │
            ▼
[🧹 Keyword pre-filter]      worker /filter: title + KL/Selangor location rules (free, no LLM)
            │
            ▼
[🔁 Dedupe vs Google Sheet]  worker /dedupe: skip if company|title key OR job URL already logged
            │
            ▼
[🧠 LLM match score 0-100]   Claude Haiku 5.5 (prod) · free OpenRouter model (staging)
            │
            ▼
[🎯 Keep score ≥ 75, top DAILY_CAP minus today's count]
            │
            ▼
[✍️ Tailored cover note per job]
            │
            ▼
[🔀 AUTO_SUBMIT?]
   no (default) ──► 🔔 Telegram: link + cover note, you tap Apply yourself
   yes (opt-in) ──► 🤖 Playwright submitter with human-like delays
            │
            ▼
[📒 Append row to Google Sheet] → [🔔 Telegram daily summary]
```

One workflow runs end to end. The submit step only starts after scoring finishes, so there is no fragile second trigger at 08:05.

---

## 🧱 Stack

| Layer | Tool | Cost/month |
|---|---|---|
| ⚙️ Orchestration | **n8n Community Edition** in Docker | RM 0 |
| 🕷️ Worker | **Python + FastAPI + Playwright** (separate container, private network) | RM 0 |
| 📒 Dedupe DB + tracker | **Google Sheets API** | RM 0 |
| 🔔 Notifications | **Telegram Bot API** | RM 0 |
| 🧠 LLM (production) | **Claude Haiku 5.5** (`claude-haiku-5-5`, effort `low`) | ≈ RM 2.50 to 5.00 |
| 🧪 LLM (staging) | **Free OpenRouter model** (e.g. `nvidia/nemotron-3.5-super-vl:free`) | RM 0 |

Playwright runs in its own `worker` container, not inside n8n. The n8n image has no Python or browser dependencies, so n8n calls the worker over HTTP instead.

### 🧠 LLM provider switch

One env var picks the model:

| `LLM_PROVIDER` | Model | Use for |
|---|---|---|
| `anthropic` | Claude Haiku 5.5 | **Production** |
| `openrouter` | the free model in `OPENROUTER_MODEL` | **Staging / testing only** |

Free OpenRouter models come with real limits:

- 🚦 **20 requests/minute** and **50 requests/day** (1,000/day once you've bought 10+ credits). A full run is about 50 calls, so stage with **10 to 15 postings**.
- 📉 Some free models ignore strict JSON. The worker validates the reply and **skips the posting** if it can't parse it. It never guesses a score.
- 🕵️ Free providers may log prompts. Use a **trimmed resume** with no IC number, phone, email or address.
- 🔄 Free models get removed or throttled. A `429` or `404` in staging is expected.

Never go live on the free model. Re-run the same test postings on `LLM_PROVIDER=anthropic` first.

---

## ⏰ Schedule: 08:00 Malaysia time

n8n's Schedule Trigger uses the server timezone, and most VPS images run in UTC. `docker-compose.yml` pins both containers to Malaysia time:

```yaml
GENERIC_TIMEZONE=Asia/Kuala_Lumpur
TZ=Asia/Kuala_Lumpur
```

So **Every Day, Hour 8, Minute 0** fires at **08:00 MYT**.

If a tool forces UTC, use cron `0 0 * * *`. Malaysia is UTC+8 with no daylight saving, so **00:00 UTC = 08:00 MYT**.

---

## 🔁 Deduplication

A posting is skipped if **either** of these is already in the sheet:

1. 🔑 **Dedupe key:** `lower(company) + "|" + lower(title)`, e.g. `grab|junior backend engineer`
2. 🔗 **Job URL** (for LinkedIn, the numeric job ID, so tracking params don't matter)

Deduping on title alone would block every future "Junior Software Engineer" role at any company, so the key is company **plus** title.

---

## 🛡️ Terms of Service and ban risk

**LinkedIn's User Agreement and JobStreet/SEEK's terms both ban scraping and automated applying.** Running your session cookie in a headless browser is exactly what their bot detection looks for. The realistic outcome is a restricted or permanently banned account.

That's why submission is split into two modes:

- 🟢 **Default: `AUTO_SUBMIT=false` ("Review & Click").** The engine finds, scores and writes the cover note, then sends it to you on Telegram. You press Apply yourself. Most of the automation, near-zero ban risk.
- 🟡 **Opt-in: `AUTO_SUBMIT=true`.** The Playwright submitter applies for you. Turn it on knowingly, ideally on a **secondary account you can afford to lose**, never your main LinkedIn.

When auto-submit is on, these anti-ban rules apply. They **lower** the risk; they do **not** make it allowed.

1. ⏳ **Random delays:** 4 to 15 min between applications, 1.5 to 6 s between page actions, 50 to 180 ms per keystroke, start time jittered between 08:05 and 08:40.
2. 🎯 **Quality only:** score ≥ 75, one unique cover note per job, and any form question the bot can't answer honestly (salary, notice period, visa) goes to Telegram instead.
3. 🧢 **Hard caps:** `DAILY_CAP` (default 10, start at 5 for the first two weeks), max 5 per platform per day, no auto-submits on Sundays. The cap is counted from the Google Sheet, so a crash or re-run can't exceed it.
4. 🛑 **Hard stop:** any CAPTCHA, "unusual activity" page, login wall or unknown field stops the whole run, marks it `failed` and alerts you on Telegram. No automatic retries.

---

## 📁 Repository layout

```text
.
├── docker-compose.yml        # n8n + worker on a private network (jobnet)
├── .env.example              # copy to .env and fill in
└── worker/
    ├── Dockerfile            # Playwright Python image + FastAPI
    ├── requirements.txt
    ├── api.py                # HTTP endpoints n8n calls
    ├── alerts.py             # LinkedIn job-alert email parser
    ├── filters.py            # free keyword + location pre-filter
    ├── dedupe.py             # company|title + URL dedupe, Sheet row builder
    └── resume.example.txt    # copy to resume.txt (git-ignored)
```

### 🔌 Worker endpoints

Every endpoint needs `Authorization: Bearer <WORKER_TOKEN>`. Errors come back with `error_message`, `causes` and `solution` so a failed n8n run explains itself.

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Shows `llm_provider`, `auto_submit` and `daily_cap` |
| POST | `/parse-alerts` | `{text, email_id}` → jobs parsed from a LinkedIn alert email |
| POST | `/filter` | `{jobs}` → `kept` / `dropped` by title and KL/Selangor location |
| POST | `/dedupe` | `{jobs, existing}` → `new` jobs plus ready-to-append Sheet `rows` |
| POST | `/llm-test` | Sends "Reply with OK" to OpenRouter (needs `LLM_PROVIDER=openrouter`) |

### 🚧 Status

Built so far: alert parsing, the keyword pre-filter, dedupe, and the OpenRouter connection test.

Still to build: LLM match scoring (both providers), cover notes, the Telegram review flow, the n8n workflow export, and the opt-in Playwright submitter.

---

## 🚀 Setup

Tested target: Ubuntu 22.04 / 24.04 or Debian 12 VPS. A laptop with Docker works for staging.

### 1. 🐳 Install Docker Engine + Compose

Follow [Docker's official install guide](https://docs.docker.com/engine/install/), then:

```bash
sudo usermod -aG docker $USER   # log out and back in afterwards
docker --version && docker compose version
```

### 2. 🕗 Set the server clock to Malaysia time

```bash
sudo timedatectl set-timezone Asia/Kuala_Lumpur
```

### 3. 📥 Clone and create `.env`

```bash
git clone https://github.com/fyyqq/Automated-Job-Application.git automated-job-application-engine
cd automated-job-application-engine
cp .env.example .env
chmod 600 .env
```

Fill in `.env`:

| Variable | Value |
|---|---|
| `N8N_ENCRYPTION_KEY` | `openssl rand -hex 32` (back this up, n8n can't decrypt credentials without it) |
| `WORKER_TOKEN` | `openssl rand -hex 24` |
| `LLM_PROVIDER` | `openrouter` for staging, `anthropic` for production |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | staging only |
| `ANTHROPIC_API_KEY` | production only |
| `DAILY_CAP` | `5` to `10` |
| `AUTO_SUBMIT` | keep `false` unless you've read the ToS section |

Type API keys into the file with an editor, not with `echo`, so they stay out of your shell history.

### 4. 📄 Add your trimmed resume

```bash
mkdir -p worker/secrets
cp worker/resume.example.txt worker/resume.txt
```

Fill it in. `resume.txt`, `worker/secrets/` and `.env` are all git-ignored.

### 5. ▶️ Start it

```bash
docker compose up -d --build
docker compose logs -f n8n   # wait for "Editor is now accessible"
```

n8n listens on `127.0.0.1:5678` only. From your laptop, open an SSH tunnel and browse to http://localhost:5678:

```bash
ssh -L 5678:localhost:5678 user@YOUR_VPS_IP
```

### 6. ✅ Smoke test the worker

```bash
docker compose exec worker python -c "import urllib.request,os;r=urllib.request.Request('http://localhost:8000/health',headers={'Authorization':'Bearer '+os.environ['WORKER_TOKEN']});print(urllib.request.urlopen(r).read().decode())"
```

You should see `"status":"ok"` and your `llm_provider`.

### 7. 🔑 Accounts and credentials

- 🧠 **Anthropic Console:** add prepaid credits, set a **monthly spend limit** (e.g. US$2) under Settings → Limits, create a workspace and API key named `automated-job-application-engine`.
- 🧪 **OpenRouter (staging):** create a key, pick a model ID ending in `:free` at openrouter.ai/models.
- 📒 **Google Sheets:** create a sheet named **`Automated Job Application Engine Tracker`** with a tab **`Applications`** and these headers in row 1:

  `Date | Company | Position | URL | Dedupe Key | Source | Match Score | Status`

  Then enable the Google Sheets API in Google Cloud, create an OAuth client, and add a **Google Sheets OAuth2** credential in n8n.
- 🔔 **Telegram:** create a bot with **@BotFather** (`/newbot`), add a Telegram credential in n8n, and note your chat ID.

### 8. 🔒 Harden the VPS

- `ufw allow OpenSSH && ufw enable` (port 5678 stays closed to the internet)
- SSH keys only, password login off
- Back up `.env` and the `n8n_data` volume
- Monthly: `docker compose pull && docker compose up -d`

---

## 🗺️ Rollout plan

0. 🧪 **Staging:** `LLM_PROVIDER=openrouter`, 10 to 15 postings, check scrape → dedupe → score → Sheets → Telegram works at RM 0.
1. 🧱 **Week 1:** production on Claude Haiku 5.5. Filter and log only, **no submissions**. Review the scores by hand.
2. 🔔 **Week 2:** cover notes + Telegram "Review & Click". Apply from your phone.
3. 🟡 **Week 3+ (optional):** `AUTO_SUBMIT=true` with `DAILY_CAP=5` on a secondary account, only if you accept the ToS risk.
4. 📊 **Monthly:** check Anthropic Usage and the sheet's response rate, tune the score threshold.

---

## 📎 Quick reference

| Item | Value |
|---|---|
| Production model | `claude-haiku-5-5` · effort `low` |
| Staging model | free OpenRouter model via `LLM_PROVIDER=openrouter` |
| Run time | 08:00 MYT daily (= cron `0 0 * * *` in UTC) |
| Daily cap | 5 to 10 (`DAILY_CAP`) |
| Match threshold | score ≥ 75 |
| Dedupe | `lower(company)|lower(title)` or same URL |
| Budget | < RM 10/month (expected RM 2.50 to 5.00) |
| n8n UI | http://localhost:5678 via SSH tunnel |
