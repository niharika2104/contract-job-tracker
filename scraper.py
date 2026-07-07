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
        if kw in text:
            score += weight
            if weight > 0:
                matched.append(kw)
    return score, matched


def append_to_csv(rows):
    file_exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["source", "title", "company_or_location", "link",
                        "posted", "score", "matched_keywords", "found_at"],
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


def scrape_onlyc2c():
    """
    onlyc2c.com — card-based listing on the homepage.
    NOTE: this site mixes recently-posted jobs with much older ones on the
    same page (not strictly chronological), so we rely entirely on the
    seen_ids tracker rather than position/order. The exact CSS classes could
    not be inspected directly (sandbox has no live access to this domain),
    so this parser uses a best-effort structural heuristic. If it comes back
    with zero/garbled results on first real run, send back the HTML
    (view-source) for one listing card and it'll be corrected quickly.
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

    # Heuristic: look for "Apply Now" links/buttons, which appear once per
    # job card on this site, and walk up to the enclosing card container.
    apply_links = soup.find_all(string=re.compile(r"Apply Now", re.I))
    seen_cards = set()
    for node in apply_links:
        card = node.find_parent(["div", "li", "article"])
        # Walk up a couple levels to get the full card if needed
        hops = 0
        while card and hops < 3 and len(card.get_text(strip=True)) < 40:
            card = card.find_parent(["div", "li", "article"])
            hops += 1
        if not card:
            continue
        card_id = id(card)
        if card_id in seen_cards:
            continue
        seen_cards.add(card_id)

        text = card.get_text(" ", strip=True)
        link_tag = card.find("a", href=True)
        link = link_tag["href"] if link_tag else url
        if link.startswith("/"):
            link = "https://onlyc2c.com" + link

        # Use a hash of the text as a stable-ish dedup id since this site
        # doesn't expose clean numeric job ids in the extracted text.
        import hashlib
        job_id = "onlyc2c_" + hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

        out.append({
            "id": job_id,
            "title": text[:180],  # full card text as a fallback "title"
            "company_or_location": "",
            "link": link,
            "posted": "",
        })
    return out


SCRAPERS = {
    "nvoids": scrape_nvoids,
    "onlyc2c": scrape_onlyc2c,
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

            row = {
                "source": source_name,
                "title": job["title"],
                "company_or_location": job.get("company_or_location", ""),
                "link": job["link"],
                "posted": job.get("posted", ""),
                "score": score,
                "matched_keywords": ", ".join(matched),
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
