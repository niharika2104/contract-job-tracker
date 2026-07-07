# C2C Job Tracker — GenAI / ML / Data Science

Scrapes C2C/contract job portals every ~10 minutes, scores each new posting
against your resume keywords, logs matches to `data/jobs.csv`, and sends you
a Telegram message the moment a relevant job appears.

**Currently covers:** Nvoids, OnlyC2C
**Cost:** $0/month (GitHub Actions free tier + free Telegram bot)

## One-time setup (10 minutes)

1. **Create a GitHub account** if you don't have one (free): https://github.com/signup

2. **Create a new repository**
   - Click "+" → "New repository"
   - Name it anything, e.g. `c2c-job-tracker`
   - Set it to **Private** (keeps your job search private)
   - Click "Create repository"

3. **Upload these files** to the repo
   - Easiest way: on the repo page, click "Add file" → "Upload files", then drag
     in everything from this folder (keep the folder structure — the
     `.github/workflows/job_scraper.yml` path matters)

4. **Create your Telegram bot** (if you haven't already)
   - Message **@BotFather** on Telegram → `/newbot` → follow the prompts
   - Save the **bot token** it gives you
   - Send your new bot any message (e.g. "hi") so it's allowed to reply to you
   - Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
     to find your **chat ID** (the number in `"chat":{"id":...}`)

5. **Add your bot token + chat ID as GitHub Secrets**
   - In your repo: Settings → Secrets and variables → Actions → "New repository secret"
   - Add `TELEGRAM_BOT_TOKEN` = your bot token
   - Add `TELEGRAM_CHAT_ID` = your chat ID

6. **Turn on Actions** (if prompted)
   - Go to the "Actions" tab in your repo, click "I understand my workflows, go ahead and enable them"

7. **Test it manually** before waiting for the schedule
   - Actions tab → "C2C Job Scraper" (left sidebar) → "Run workflow" → Run workflow
   - Check the run logs, and check `data/jobs.csv` in the repo afterward
   - You should also get a Telegram message if any relevant job was found

That's it — after this, it runs automatically every ~10 minutes, forever,
for free.

## How matching works

`scraper.py` has a `KEYWORDS` dict with weighted terms pulled from your
resume (GenAI, LangGraph, RAG, Bedrock, SageMaker, MLOps, Data Scientist,
etc.). Every new posting gets scored; postings scoring ≥1 go into the CSV,
postings scoring ≥2 also trigger a Telegram alert. Tune the weights or
`MIN_SCORE_TO_LOG` / `MIN_SCORE_TO_ALERT` in `scraper.py` any time.

## Adding more portals

Each site needs its own small scraper function in `scraper.py` (see
`scrape_nvoids` for a clean example, `scrape_onlyc2c` for a heuristic
example). Send me the portal name and I'll write and test the next one —
we're doing this incrementally since each site's HTML structure is different.

## Known limitations / honesty notes

- **OnlyC2C's parser is a best-effort heuristic**, not verified against live
  HTML (its listings are card-based, not a clean table like Nvoids). If it
  returns 0 results or garbled titles on the first real run, check the
  Action's log output and share it back — it's a quick fix.
- **GitHub's cron scheduling is "best effort," not exact** — under high load
  across GitHub's infrastructure, a `*/10` cron job can occasionally run late
  by a few minutes. This is still effectively "near real-time" for job
  alerts, just not to-the-second.
- **If a portal starts blocking requests** (some sites tighten anti-bot
  measures after being scraped repeatedly), the fix usually requires either
  slowing down the polling interval or routing through a proxy service —
  let me know if a run starts failing and we'll adjust.
- This scrapes each site respectfully (low frequency, standard headers, no
  login bypass). Most job boards' Terms of Service technically prohibit
  automated access even at this pace — worth being aware of, even though
  personal-use job-search scraping at this volume is low-risk in practice.
