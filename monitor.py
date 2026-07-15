#!/usr/bin/env python3
"""
EU Institutions Watch
----------------------
Polls RSS feeds from EUR-Lex, CJEU (Curia), Council of the EU, and the
European Commission press corner. Sends a Telegram alert whenever a new
item matches one of the configured keywords.

Designed to run unattended (e.g. every 15-30 min via GitHub Actions cron).
State (which item links/ids have already been alerted on) is persisted to
a local JSON file so the same item is never sent twice.
"""

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import feedparser
import requests
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("eu-watch")

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
STATE_PATH = ROOT / "state" / "seen.json"

# ---- Networking / retry settings -------------------------------------------------
REQUEST_TIMEOUT = 20          # seconds
MAX_RETRIES = 3
BASE_BACKOFF = 5              # seconds, doubles each retry
DELAY_BETWEEN_FEEDS = 2        # seconds, be polite to the source servers


@dataclass
class Item:
    source: str
    title: str
    link: str
    published: str
    summary: str
    matched_keywords: list


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state() -> set:
    if not STATE_PATH.exists():
        return set()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("seen_ids", []))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not read state file (%s), starting fresh", e)
        return set()


def save_state(seen_ids: set) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Cap the stored history so the file doesn't grow forever.
    trimmed = list(seen_ids)[-5000:]
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"seen_ids": trimmed}, f, ensure_ascii=False, indent=2)


def fetch_feed_with_retry(url: str) -> Optional[feedparser.FeedParserDict]:
    """Fetch + parse an RSS/Atom feed, retrying on transient failures."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; eu-institutions-watch/1.0; "
                      "+https://github.com/)"
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                wait = BASE_BACKOFF * (2 ** (attempt - 1))
                log.warning("Rate limited (429) on %s, waiting %ss", url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            if parsed.bozo and not parsed.entries:
                raise ValueError(f"Unparseable feed: {parsed.bozo_exception}")
            return parsed
        except (requests.RequestException, ValueError) as e:
            wait = BASE_BACKOFF * (2 ** (attempt - 1))
            log.warning(
                "Attempt %d/%d failed for %s (%s), retrying in %ss",
                attempt, MAX_RETRIES, url, e, wait,
            )
            time.sleep(wait)
    log.error("Giving up on feed after %d attempts: %s", MAX_RETRIES, url)
    return None


def entry_id(entry) -> str:
    """Stable unique id for a feed entry, across feed formats."""
    return entry.get("id") or entry.get("link") or entry.get("title", "")


def entry_text_blob(entry) -> str:
    """All text worth keyword-matching against, lowercased."""
    parts = [
        entry.get("title", ""),
        entry.get("summary", ""),
        entry.get("description", ""),
    ]
    return " ".join(p for p in parts if p).lower()


def match_keywords(text: str, keywords: list) -> list:
    return [kw for kw in keywords if kw.lower() in text]


def send_telegram_alert(bot_token: str, chat_id: str, item: Item) -> bool:
    text = (
        f"🔔 <b>{item.source}</b>\n"
        f"Mots-clés : {', '.join(item.matched_keywords)}\n\n"
        f"<b>{item.title}</b>\n"
        f"{item.published}\n"
        f"{item.link}"
    )
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", BASE_BACKOFF)
                log.warning("Telegram rate limited, waiting %ss", retry_after)
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            wait = BASE_BACKOFF * (2 ** (attempt - 1))
            log.warning("Telegram send failed (attempt %d/%d): %s", attempt, MAX_RETRIES, e)
            time.sleep(wait)
    log.error("Failed to send Telegram alert for: %s", item.title)
    return False


def main() -> int:
    config = load_config()
    feeds = config.get("feeds", [])
    keywords = config.get("keywords", [])

    if not feeds:
        log.error("No feeds configured in config.yaml")
        return 1
    if not keywords:
        log.error("No keywords configured in config.yaml")
        return 1

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    dry_run = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

    if not dry_run and (not bot_token or not chat_id):
        log.error(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set. "
            "Set them as GitHub Actions secrets, or set DRY_RUN=1 to test locally."
        )
        return 1

    seen_ids = load_state()
    new_seen = set(seen_ids)
    total_matches = 0

    for feed_cfg in feeds:
        name = feed_cfg["name"]
        url = feed_cfg["url"]
        log.info("Checking feed: %s", name)

        parsed = fetch_feed_with_retry(url)
        time.sleep(DELAY_BETWEEN_FEEDS)
        if parsed is None:
            continue

        for entry in parsed.entries:
            uid = f"{name}:{entry_id(entry)}"
            if uid in seen_ids:
                continue

            text_blob = entry_text_blob(entry)
            matched = match_keywords(text_blob, keywords)

            # Mark as seen regardless of match, so we don't re-scan it forever.
            new_seen.add(uid)

            if not matched:
                continue

            item = Item(
                source=name,
                title=entry.get("title", "(no title)"),
                link=entry.get("link", ""),
                published=entry.get("published", entry.get("updated", "")),
                summary=entry.get("summary", "")[:300],
                matched_keywords=matched,
            )

            total_matches += 1
            log.info("Match [%s] %s -> %s", name, item.matched_keywords, item.title)

            if dry_run:
                log.info("[DRY RUN] Would send Telegram alert: %s", item.title)
            else:
                send_telegram_alert(bot_token, chat_id, item)
                time.sleep(1)  # avoid hammering the Telegram API

    save_state(new_seen)
    log.info("Done. %d new matching item(s) this run.", total_matches)
    return 0


if __name__ == "__main__":
    sys.exit(main())
