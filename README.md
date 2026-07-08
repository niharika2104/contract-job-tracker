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

## Dashboard

docs/index.html is a live view of jobs.csv - reads it straight off GitHub
(raw.githubusercontent.com) client-side, no build step, refetches every 60s.
Hosted free via GitHub Pages (Settings → Pages → deploy from main /docs).
Search, filter by source, min-score slider, sort. Recruiter email/phone show
as clickable buttons right on the card when present.

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
sites (Recruut) hide addresses that way. Phone numbers pick up extensions
too (ext./x/extension, any format) - the dashboard turns those into a proper
tel:;ext= link and shows "(ext NNN)" on the call button so it's not hidden.

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

SmartRecruiters-based sources (Collabera, Apex, Mastech, Eliassen, ASK,
Diverse Lynx, Aditi) only show a bare title on the listing page - no
description, no skills. Scoring on title alone missed roles like "Senior
Consultant" that were actually GenAI work once you opened the posting. Fixed
by having those scrapers fetch each job's own detail page for full-text
scoring, but only for postings not already in seen_ids.json - otherwise
we'd be re-fetching every open req on every 10-min run for no reason.

Hotlist posts (recruiters advertising their own bench consultants, not an
actual open requirement - e.g. "Hot-List of my consultants || ... || AI
Engineer") get excluded entirely before scoring, via EXCLUDE_REGEX in
scraper.py. Was seeing these show up as false positives since the bench
skill list would trip a high-weight keyword like "AI Engineer" despite it
not being a real req. Add more patterns there if other flavors of this show
up.

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

- Robert Half and Motion Recruitment are returning 0 listings as of the last
  check, despite both having real, confirmed-working listing pages when
  checked outside of GitHub Actions. Best guess is their sites are blocking
  requests coming from GitHub's runner IPs specifically (common anti-bot
  behavior for bigger corporate career sites - cloud/datacenter IP ranges get
  blocklisted a lot). Added debug logging (status code, byte count, page
  title, link-match count) to both scrapers so the next run's log output
  should confirm one way or the other. Check Actions log before assuming
  either site's HTML changed.
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
