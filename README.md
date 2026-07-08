# C2C Job Tracker

Checks a bunch of job portals every 10 minutes for data science / ML / GenAI
contract roles, scores them against my resume keywords, and pings me on
Telegram when something good shows up. Everything also gets logged to
data/jobs.csv.

Runs on GitHub Actions, triggered by cron-job.org. Free.

## Sources currently monitored

C2C boards:
- Nvoids
- OnlyC2C
- Recruut
- TechFetch (corp-to-corp page)
- BenchZero

Prime vendors:
- Collabera
- Apex Systems
- Robert Half
- Motion Recruitment
- Mastech Digital
- Eliassen Group
- ASK Staffing
- Diverse Lynx
- Aditi Consulting

A bunch of others (Kforce, TEKsystems, Randstad, Insight Global, Judge Group,
Dice, Monster, Genesis10, etc.) were checked and skipped because their job
search only works through JavaScript - nothing to scrape without a headless
browser. Dice/Indeed/ZipRecruiter can still be searched on demand through
Claude directly, just not on the automated schedule.

## Setup

1. Repo is public (needed for free unlimited GitHub Actions minutes - keep it
   private only if you don't mind paying ~$13-14/mo in Actions usage at this
   frequency).

2. Secrets needed in Settings → Secrets and variables → Actions:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

3. Triggering: GitHub's own cron scheduler turned out to be unreliable (would
   just stop firing for no clear reason). Working setup now is cron-job.org
   hitting the workflow_dispatch API endpoint every 10 min, using Basic Auth
   (username = github username, password = a fine-grained PAT with Actions
   read/write on this repo only). If cron-job.org executions start failing,
   check that the PAT hasn't expired.

4. To test manually: Actions tab → C2C Job Scraper → Run workflow.

## jobs.csv columns

source, title, company_or_location, job_url, posted, score, matched_keywords,
recruiter_email, recruiter_phone, found_at

Recruiter email/phone only get filled in when the job's own detail page
actually lists contact info - a lot of postings won't have it, that's normal,
not a bug. Emails run through a Cloudflare-obfuscation decoder since a few
sites (Recruut) hide addresses that way.

data/jobs_archive_before_recruiter_columns.csv is old data from before the
job_url/recruiter columns existed, kept around instead of deleted.

## Scoring

Keyword weights live in the KEYWORDS dict in scraper.py, pulled from my
resume (GenAI, LangGraph, RAG, Bedrock, SageMaker, MLOps, Databricks, etc).
Matching is word-boundary based, not plain substring - learned this the hard
way after "rag" was matching inside "storage" and "snapdragon."

MIN_SCORE_TO_LOG = 1 (goes in the CSV)
MIN_SCORE_TO_ALERT = 2 (also pings Telegram)

Postings older than MAX_POSTING_AGE_DAYS (3) get skipped on sites that mix
old and new listings on the same page (OnlyC2C, Recruut, Robert Half).

## Adding a new source

If it's on SmartRecruiters (careers.smartrecruiters.com/{slug}), it's one
line using make_smartrecruiters_scraper() - already covers half the prime
vendors added so far since it's such a common platform for staffing firms.

Anything else needs its own scrape_xxx() function. Check first whether the
job search actually renders without JavaScript (view page source, see if the
listings are there) before spending time on it - most big staffing firms
(Workday/Taleo/Phenom-based sites) don't and aren't worth the effort without
a headless browser.

## Known rough edges

- OnlyC2C and Motion Recruitment's title fields are the raw scraped text
  (title+location+description all mashed together) rather than cleanly split
  out - there's no reliable delimiter in the HTML to split on, so I gave up
  trying to parse it prettier and just left it as-is. Still scores/matches
  fine, just not pretty in the CSV.
- Robert Half only pulls from a fixed list of category pages (data-scientist,
  data-engineer, etc.) rather than a full search - if a role doesn't fall
  into one of those categories it won't show up.
- Mastech Digital had zero open postings when added - scraper's still there
  and will pick jobs up automatically whenever they post something.
