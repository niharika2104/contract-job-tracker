#!/usr/bin/env python3
"""
C2C Job Tracker - GenAI/ML/Data Science focused
Scrapes configured portals, matches against resume keywords, logs new
matches to CSV, and sends a Telegram alert the moment a new match appears.

Run this on a schedule (e.g. GitHub Actions every 5-10 min). Each run:
  1. Fetches each portal's current listing page
  2. Extracts job postings (title, company/loc, link, posted-time if available)
  3. Skips postings we've already seen (tracked in data/seen_ids.json)
  4. Scores new postings against KEYWORDS
  5. Appends matches (score > 0) to data/jobs.csv
  6. Sends a Telegram message for each new match above MIN_SCORE_TO_ALERT
"""

import os
import re
import csv
import json
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CSV_PATH = os.path.join(DATA_DIR, "jobs.csv")
SEEN_PATH = os.path.join(DATA_DIR, "seen_ids.json")

# Minimum relevance score (see KEYWORDS weights below) required to:
#   - write the job to the CSV at all
MIN_SCORE_TO_LOG = 1
#   - fire a Telegram notification
MIN_SCORE_TO_ALERT = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Keywords derived from Niharika's resume (Sr. Data Scientist / Gen AI-ML Engineer)
# Higher weight = stronger, more specific signal. Tune freely.
KEYWORDS = {
    # Core role titles
    "data scientist": 3, "gen ai": 3, "genai": 3, "generative ai": 3,
    "ml engineer": 3, "machine learning engineer": 3, "agentic ai": 3,
    "agentic": 2, "ai engineer": 3, "ai/ml": 2,

    # Specific tech from resume
    "langgraph": 3, "langchain": 2, "rag": 2, "retrieval augmented": 3,
    "bedrock": 2, "sagemaker": 2, "prompt engineering": 2, "llm": 2,
    "xgboost": 2, "tabnet": 2, "pytorch": 2, "tensorflow": 1,
    "mlops": 2, "databricks": 2, "pyspark": 1, "nlp": 1,
    "deep learning": 2, "scikit-learn": 1, "sklearn": 1,

    # General but relevant
    "machine learning": 1, "artificial intelligence": 1, "data science": 1,
    "python": 0.5, "aws": 0.5, "azure": 0.5,

    # C2C / contract signal (not scored for relevance, just informational)
    "c2c": 0, "corp-to-corp": 0, "corp to corp": 0,
}


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def load_seen():
    if os.path.exists(SEEN_PATH):
        with open(SEEN_PATH, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen_ids):
    with open(SEEN_PATH, "w") as f:
        json.dump(sorted(seen_ids), f)


def score_job(title, extra_text=""):
    text = (title + " " + extra_text).lower()
    score = 0
    matched = []
    for kw, weight in KEYWORDS.items():
        # Word-boundary match so short keywords like "rag" or "llm" don't
        # false-positive inside unrelated words ("storage", "snapdragon").
        pattern = r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            score += weight
            if weight > 0:
                matched.append(kw)
    return score, matched


EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# Matches common US phone formats: (123) 456-7890, 123-456-7890,
# 123.456.7890, +1 123 456 7890, etc.
PHONE_PATTERN = re.compile(
    r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
)

# Emails on these domains belong to the portal itself (support/contact
# addresses), not the recruiter who posted the job — always exclude them.
SITE_OWNED_EMAIL_DOMAINS = {"nvoids.com", "onlyc2c.com"}


def _decode_cfemail(hex_string):
    """
    Decodes Cloudflare's email-obfuscation encoding (used by many WordPress
    sites, including Recruut) back into a real email address. The encoding
    XORs each byte against a key stored as the first byte.
    """
    try:
        key = int(hex_string[:2], 16)
        return "".join(
            chr(int(hex_string[i:i + 2], 16) ^ key)
            for i in range(2, len(hex_string), 2)
        )
    except (ValueError, IndexError):
        return None


def extract_recruiter_contacts(detail_url):
    """
    Fetches a job's own detail page and pulls out any recruiter email/phone
    mentioned in the posting body. Returns (email, phone) — either may be
    an empty string if nothing was found or the page couldn't be fetched.
    Only called for jobs that already passed the relevance threshold, to
    keep the extra request volume reasonable.
    """
    try:
        resp = requests.get(detail_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[warn] could not fetch detail page for contacts: {e}")
        return "", ""

    soup = BeautifulSoup(resp.text, "html.parser")

    emails = []
    # Cloudflare-obfuscated emails (common on WordPress sites like Recruut) —
    # these show up as data-cfemail attributes rather than plain text.
    for tag in soup.find_all(attrs={"data-cfemail": True}):
        decoded = _decode_cfemail(tag["data-cfemail"])
        if decoded:
            emails.append(decoded)

    text = soup.get_text(" ", strip=True)
    emails += EMAIL_PATTERN.findall(text)
    emails = [
        e for e in dict.fromkeys(emails)  # dedup, keep order
        if e.split("@")[-1].lower() not in SITE_OWNED_EMAIL_DOMAINS
    ]

    phones = PHONE_PATTERN.findall(text)
    phones = list(dict.fromkeys(phones))  # dedup, keep order

    return (
        "; ".join(emails[:2]),   # cap at 2 in case a posting lists a few
        "; ".join(phones[:2]),
    )


def append_to_csv(rows):
    file_exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["source", "title", "company_or_location", "job_url",
                        "posted", "score", "matched_keywords",
                        "recruiter_email", "recruiter_phone", "found_at"],
        )
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[warn] Telegram not configured, skipping alert:", message[:80])
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            print("[warn] Telegram send failed:", resp.text[:200])
    except Exception as e:
        print("[warn] Telegram exception:", e)


# ---------------------------------------------------------------------------
# SITE SCRAPERS
# Each returns a list of dicts: {id, title, company_or_location, link, posted}
# ---------------------------------------------------------------------------

def scrape_nvoids():
    """
    nvoids.com — plain HTML table: Job Title | Location | Time
    Job detail links look like: /job_details.jsp?id=XXXXXXX&uid=...
    We use the numeric id as the unique key.
    """
    url = "https://nvoids.com/index.jsp"
    out = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"[error] nvoids fetch failed: {e}")
        return out

    soup = BeautifulSoup(resp.text, "html.parser")
    # Every job row has an <a> to job_details.jsp?id=...
    for a in soup.find_all("a", href=re.compile(r"job_details\.jsp\?id=(\d+)")):
        m = re.search(r"id=(\d+)", a["href"])
        if not m:
            continue
        job_id = "nvoids_" + m.group(1)
        title = a.get_text(strip=True)
        link = a["href"]
        if link.startswith("/"):
            link = "https://nvoids.com" + link
        elif not link.startswith("http"):
            link = "https://nvoids.com/" + link

        # Location and time usually sit in sibling <td> cells of the same row
        location, posted = "", ""
        row = a.find_parent("tr")
        if row:
            cells = row.find_all("td")
            texts = [c.get_text(strip=True) for c in cells]
            if len(texts) >= 3:
                location, posted = texts[1], texts[2]

        out.append({
            "id": job_id,
            "title": title,
            "company_or_location": location,
            "link": link,
            "posted": posted,
        })
    return out


# Only alert/log postings at least this fresh. OnlyC2C's homepage mixes
# brand-new listings with ones from many months ago (not chronological),
# so without this filter every old posting looks "new" on first run.
MAX_POSTING_AGE_DAYS = 3


def _parse_relative_age_days(posted_text):
    """
    Turns strings like '36 minutes ago', '11 months ago', '2 days ago',
    or an absolute date like '10/9/2024' into an approximate age in days.
    Returns None if it can't be parsed (caller should then treat cautiously).
    """
    if not posted_text:
        return None
    posted_text = posted_text.strip().lower()

    m = re.match(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", posted_text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        unit_days = {
            "minute": 1 / 1440, "hour": 1 / 24, "day": 1,
            "week": 7, "month": 30, "year": 365,
        }
        return n * unit_days[unit]

    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", posted_text)
    if m:
        try:
            posted_date = datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)),
                                    tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - posted_date).days
        except ValueError:
            return None

    m = re.match(
        r"(january|february|march|april|may|june|july|august|"
        r"september|october|november|december)\s+(\d{1,2}),?\s+(\d{4})",
        posted_text,
    )
    if m:
        try:
            posted_date = datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y"
            ).replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - posted_date).days
        except ValueError:
            return None

    return None


def scrape_onlyc2c():
    """
    onlyc2c.com — card-based listing on the homepage, mixing fresh and very
    old postings on the same page (not chronological).

    Each job has a real, unique permalink like:
        https://onlyc2c.com/c2c-jobid-C2C-41
    We key entirely off that job id (reliable dedup, unlike raw text hashing)
    and skip anything whose "Posted X ago" text is older than
    MAX_POSTING_AGE_DAYS, since old postings aren't useful for real-time
    alerting and previously showed up as false "new" jobs on first run.
    """
    url = "https://onlyc2c.com"
    out = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"[error] onlyc2c fetch failed: {e}")
        return out

    soup = BeautifulSoup(resp.text, "html.parser")

    id_pattern = re.compile(r"c2c-jobid-(C2C-\d+)", re.I)
    seen_ids_this_page = set()

    for a in soup.find_all("a", href=id_pattern):
        m = id_pattern.search(a["href"])
        if not m:
            continue
        job_id = "onlyc2c_" + m.group(1)
        if job_id in seen_ids_this_page:
            continue  # same job card matched at more than one DOM level
        seen_ids_this_page.add(job_id)

        link = a["href"]
        if link.startswith("/"):
            link = "https://onlyc2c.com" + link

        card = a.find_parent(["div", "li", "article"]) or a
        hops = 0
        while card and hops < 3 and len(card.get_text(strip=True)) < 40:
            card = card.find_parent(["div", "li", "article"])
            hops += 1
        full_text = card.get_text(" ", strip=True) if card else a.get_text(" ", strip=True)

        posted_match = re.search(
            r"posted\s+(\d+\s+\w+\s+ago|\d{1,2}/\d{1,2}/\d{4})",
            full_text, re.I,
        )
        posted_text = posted_match.group(1) if posted_match else ""
        age_days = _parse_relative_age_days(posted_text)

        # Skip anything clearly older than the freshness window. If we
        # couldn't parse an age at all, keep it (better to log an
        # unfiltered item than silently drop a real new posting).
        if age_days is not None and age_days > MAX_POSTING_AGE_DAYS:
            continue

        # Title = everything before the "Posted ..." marker, which keeps
        # out the applicant-count/tag noise that follows it.
        if posted_match:
            title = full_text[:posted_match.start()].strip()
        else:
            title = full_text[:180].strip()

        out.append({
            "id": job_id,
            "title": title,
            "company_or_location": "",
            "link": link,
            "posted": posted_text,
        })
    return out


def scrape_recruut():
    """
    recruut.com — WordPress-based C2C job board. Homepage lists recent jobs
    as cards, each with a real permalink like:
        https://www.recruut.com/job/some-job-title-slug/
    We use the URL slug itself as the unique id (stable, human-readable).

    NOTE: this parser is a best-effort heuristic built from the rendered
    page content (my sandbox can't verify raw HTML class names on this
    domain directly). If it returns 0 results or garbled titles on the
    first real run, that's expected-possible and a quick fix — send back
    the log output.
    """
    url = "https://www.recruut.com"
    out = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"[error] recruut fetch failed: {e}")
        return out

    soup = BeautifulSoup(resp.text, "html.parser")
    job_link_pattern = re.compile(r"^https://www\.recruut\.com/job/[a-z0-9\-]+/?$", re.I)

    seen_slugs = set()
    for a in soup.find_all("a", href=job_link_pattern):
        href = a["href"].rstrip("/")
        slug = href.split("/job/")[-1]
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        title = a.get_text(strip=True)
        if not title:
            continue  # some duplicate <a> wrappers (e.g. image links) have no text

        # The posting date usually appears near the card as "Month DD, YYYY"
        posted = ""
        card = a.find_parent(["article", "div"])
        hops = 0
        while card and hops < 4 and len(card.get_text(strip=True)) < 60:
            card = card.find_parent(["article", "div"])
            hops += 1
        if card:
            date_match = re.search(
                r"(January|February|March|April|May|June|July|August|"
                r"September|October|November|December)\s+\d{1,2},\s+\d{4}",
                card.get_text(" ", strip=True),
            )
            if date_match:
                posted = date_match.group(0)

        age_days = _parse_relative_age_days(posted)
        if age_days is not None and age_days > MAX_POSTING_AGE_DAYS:
            continue

        out.append({
            "id": "recruut_" + slug,
            "title": title,
            "company_or_location": "",
            "link": href,
            "posted": posted,
        })
    return out


SCRAPERS = {
    "nvoids": scrape_nvoids,
    "onlyc2c": scrape_onlyc2c,
    "recruut": scrape_recruut,
}


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    seen = load_seen()
    new_seen = set(seen)
    new_csv_rows = []
    alerts_sent = 0

    for source_name, scraper_fn in SCRAPERS.items():
        print(f"[info] scraping {source_name}...")
        jobs = scraper_fn()
        print(f"[info] {source_name}: found {len(jobs)} listings on page")

        for job in jobs:
            if job["id"] in seen:
                continue  # already processed in a previous run
            new_seen.add(job["id"])

            score, matched = score_job(job["title"], job.get("company_or_location", ""))
            if score < MIN_SCORE_TO_LOG:
                continue  # not relevant enough to log at all

            # Only worth the extra request for jobs we're actually keeping.
            recruiter_email, recruiter_phone = extract_recruiter_contacts(job["link"])

            row = {
                "source": source_name,
                "title": job["title"],
                "company_or_location": job.get("company_or_location", ""),
                "job_url": job["link"],
                "posted": job.get("posted", ""),
                "score": score,
                "matched_keywords": ", ".join(matched),
                "recruiter_email": recruiter_email,
                "recruiter_phone": recruiter_phone,
                "found_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            new_csv_rows.append(row)

            if score >= MIN_SCORE_TO_ALERT:
                msg = (
                    f"🎯 New C2C match ({source_name}, score {score})\n"
                    f"{job['title']}\n"
                    f"{job.get('company_or_location', '')}\n"
                    f"{job['link']}"
                )
                if recruiter_email:
                    msg += f"\n📧 {recruiter_email}"
                if recruiter_phone:
                    msg += f"\n📞 {recruiter_phone}"
                send_telegram(msg)
                alerts_sent += 1
                time.sleep(1)  # be gentle with Telegram's rate limits

    if new_csv_rows:
        append_to_csv(new_csv_rows)
        print(f"[info] wrote {len(new_csv_rows)} new rows to {CSV_PATH}")
    else:
        print("[info] no new relevant postings this run")

    save_seen(new_seen)
    print(f"[info] alerts sent this run: {alerts_sent}")


if __name__ == "__main__":
    main()
