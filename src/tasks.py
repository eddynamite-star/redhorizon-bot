# src/tasks.py — polished Telegram output (HTML), same core logic

import os
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, List

from src.feeds import fetch_rss_news, fetch_images
from src.formatter import (
    fmt_breaking, fmt_priority, fmt_digest, fmt_image_post,
    fmt_starbase_fact, fmt_book_spotlight, fmt_welcome
)

# --------- JSON state helpers ---------
DATA_DIR = "data"
SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
BOOK_IDX_FILE = os.path.join(DATA_DIR, "book_index.json")
FACT_IDX_FILE = os.path.join(DATA_DIR, "fact_index.json")
BOOK_LIST_FILE = os.path.join(DATA_DIR, "book_list.json")

def _ensure_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(SEEN_FILE):
        save_json(SEEN_FILE, {})
    if not os.path.exists(BOOK_IDX_FILE):
        save_json(BOOK_IDX_FILE, {"index": 0})
    if not os.path.exists(FACT_IDX_FILE):
        save_json(FACT_IDX_FILE, {"index": 0})

def load_json(file_path: str):
    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        # sensible defaults
        if file_path.endswith("seen_links.json"):
            return {}
        return {"index": 0}
    except json.JSONDecodeError:
        # recover from a bad write
        if file_path.endswith("seen_links.json"):
            return {}
        return {"index": 0}

def save_json(file_path: str, data: Any):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)

# --------- Telegram / Zapier IO ---------
def send_telegram_message(html_text: str, retry=True, disable_preview=False):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
    if not bot_token or not channel_id:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID missing")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": channel_id,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 200:
            print("ok")
            return True
        print(f"Telegram sendMessage error: {r.status_code} {r.text}")
        if retry:
            return send_telegram_message(html_text, retry=False, disable_preview=disable_preview)
    except Exception as e:
        print(f"Telegram sendMessage exception: {e}")
    return False

def send_telegram_image(image_url: str, caption_html: str, retry=True):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
    if not bot_token or not channel_id:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID missing")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    payload = {
        "chat_id": channel_id,
        "photo": image_url,
        "caption": caption_html,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code == 200:
            print("ok")
            return True
        print(f"Telegram sendPhoto error: {r.status_code} {r.text}")
        if retry:
            return send_telegram_image(image_url, caption_html, retry=False)
    except Exception as e:
        print(f"Telegram sendPhoto exception: {e}")
    return False

def send_to_zapier(data: Dict[str, Any]):
    hook = os.getenv("ZAPIER_HOOK_URL")
    if not hook:
        return True  # optional
    try:
        r = requests.post(hook, json=data, timeout=15)
        if r.status_code in (200, 201, 202, 204):
            print("ok [Zapier]")
            return True
        print(f"Zapier error: {r.status_code} {r.text}")
    except Exception as e:
        print(f"Zapier exception: {e}")
    return True  # non-fatal

# --------- Posting tasks ---------
def run_breaking_news():
    """
    Posts recent breaking items with polished formatting.
    Super-priority (<=5 min) uses a 'Live/Now' style.
    Breaking (<=15 min) uses 'BREAKING' style.
    """
    _ensure_files()
    seen = load_json(SEEN_FILE)
    articles = fetch_rss_news()
    now = datetime.utcnow()
    posted = 0

    # throttle: top 2 only
    for art in articles[:10]:
        if art["link"] in seen:
            continue
        age = now - art["published"]
        if art.get("is_super_breaking"):
            msg = fmt_priority(
                title=art["title"],
                url=art["link"],
                reason="Super-priority",
                tags=["Breaking", "Live"]
            )
        elif art.get("is_breaking"):
            msg = fmt_breaking(
                title=art["title"],
                url=art["link"],
                summary=art.get("summary", ""),
                tags=["Breaking"],
                source_hint=art.get("source", "")
            )
        else:
            continue  # skip non-breaking

        if send_telegram_message(msg):
            seen[art["link"]] = True
            send_to_zapier({"text": strip_html_for_x(msg), "url": art["link"], "kind": "breaking"})
            posted += 1
        if posted >= 2:
            break

    save_json(SEEN_FILE, seen)
    if posted == 0:
        # only message when truly nothing urgent (optional)
        send_telegram_message("⏳ No breaking items right now. More soon. #RedHorizon", disable_preview=True)
        return "No new breaking news"
    return "ok"

def run_daily_digest():
    """
    Posts a daily digest with top 5–7 items from the last 24h.
    """
    _ensure_files()
    seen = load_json(SEEN_FILE)
    articles = fetch_rss_news()
    now = datetime.utcnow()
    recent = [a for a in articles if (now - a["published"]) <= timedelta(hours=24)]
    top = recent[:7]

    digest_items: List[Dict[str, str]] = []
    for a in top:
        digest_items.append({"title": a["title"], "url": a["link"], "source": a.get("source")})

    date_label = now.strftime("%b %d, %Y")
    msg = fmt_digest(date_label=date_label, items=digest_items, tags=["Daily"], footer_x="https://x.com/RedHorizonHub")

    if send_telegram_message(msg):
        for a in top:
            seen[a["link"]] = True
        save_json(SEEN_FILE, seen)
        send_to_zapier({"text": strip_html_for_x(msg), "kind": "digest"})
        return "ok"
    return "Failed"

def run_daily_image():
    """
    Pulls from IMAGE_FEEDS and posts a single fresh image with a clean caption.
    """
    _ensure_files()
    seen = load_json(SEEN_FILE)
    fact_idx = load_json(FACT_IDX_FILE)
    images = fetch_images()

    for img in images:
        url = img.get("url")
        if not url or url in seen:
            continue
        title = img.get("title", "Space image")
        caption = fmt_image_post(
            title=title,
            url=img.get("source_link", url),
            credit=img.get("source_name", ""),
            tags=["Image"]
        )
        if send_telegram_image(url, caption):
            seen[url] = True
            fact_idx["index"] = int(fact_idx.get("index", 0)) + 1
            save_json(SEEN_FILE, seen)
            save_json(FACT_IDX_FILE, fact_idx)
            send_to_zapier({"image_url": url, "caption": strip_html_for_x(caption), "kind": "image"})
            return "ok"

    # fallback
    send_telegram_message("📸 No new images found just now. Check back later. #RedHorizon", disable_preview=True)
    return "No new images"

def run_starbase_highlight():
    """
    Rotates through a fixed list of Starbase facts (can externalize to JSON later).
    """
    _ensure_files()
    facts = [
        ("High Bay", "Massive tower where Starship sections are stacked before rollout.", "https://www.nasaspaceflight.com/tag/starbase/"),
        ("Mega/Wide Bay", "Larger, taller stacking facility supporting Ship and Booster flow.", "https://www.nasaspaceflight.com/tag/starbase/"),
        ("Launch Integration Tower", "The tower that supports stacking and potential catching operations.", "https://www.nasaspaceflight.com/tag/starbase/"),
        ("Chopsticks", "Mechazilla catch arms for lift and (eventually) catch operations.", "https://www.nasaspaceflight.com/tag/starbase/"),
        ("Orbital Launch Mount", "Holds the Booster, with hold-down clamps and water deluge hardware.", "https://www.nasaspaceflight.com/tag/starbase/"),
        ("Propellant Farm", "Cryogenic storage for methane/oxygen used in tests and launches.", "https://www.nasaspaceflight.com/tag/starbase/"),
        ("Suborbital Pads", "Legacy pads used for Starship prototype tests.", "https://www.nasaspaceflight.com/tag/starbase/"),
    ]
    fact_idx = load_json(FACT_IDX_FILE)
    idx = int(fact_idx.get("index", 0)) % len(facts)
    title, body, ref = facts[idx]

    msg = fmt_starbase_fact(title=title, body=body, ref_url=ref, tags=["Starbase"])
    if send_telegram_message(msg):
        fact_idx["index"] = idx + 1
        save_json(FACT_IDX_FILE, fact_idx)
        send_to_zapier({"text": strip_html_for_x(msg), "url": ref, "kind": "starbase"})
        return "ok"
    return "Failed"

def run_book_spotlight():
    """
    Rotates a curated book list; uses your affiliate links from JSON.
    """
    _ensure_files()
    books = load_json(BOOK_LIST_FILE)
    if not isinstance(books, list) or not books:
        print("No books.json list found")
        return "Failed"

    book_idx = load_json(BOOK_IDX_FILE)
    i = int(book_idx.get("index", 0)) % len(books)
    b = books[i]
    title = b.get("title", "Recommended book")
    author = b.get("author", "")
    blurb = b.get("blurb", "A standout pick for space & sci-fi fans.")
    url = b.get("affiliate_link", "")

    msg = fmt_book_spotlight(title=title, author=author, blurb=blurb, url=url, tags=["Mars"])
    if send_telegram_message(msg):
        book_idx["index"] = i + 1
        save_json(BOOK_IDX_FILE, book_idx)
        send_to_zapier({"text": strip_html_for_x(msg), "url": url, "kind": "book"})
        return "ok"
    return "Failed"

def run_welcome_message():
    """
    Weekly welcome / onboarding message.
    """
    msg = fmt_welcome("https://x.com/RedHorizonHub")
    if send_telegram_message(msg):
        send_to_zapier({"text": strip_html_for_x(msg), "kind": "welcome"})
        return "ok"
    return "Failed"

def run_terraforming_post():
    """
    Weekly terraforming roundup (uses same formatter as digest but with custom tag).
    """
    _ensure_files()
    seen = load_json(SEEN_FILE)
    arts = fetch_rss_news(is_terraforming=True)[:5]
    now = datetime.utcnow()

    if not arts:
        send_telegram_message("🪐 No new terraforming items this week. More soon. #RedHorizon", disable_preview=True)
        return "No terraforming items"

    items = [{"title": a["title"], "url": a["link"], "source": a.get("source")} for a in arts]
    msg = fmt_digest(
        date_label=now.strftime("%b %d, %Y"),
        items=items,
        tags=["Terraforming"],
        footer_x="https://x.com/RedHorizonHub"
    )
    if send_telegram_message(msg):
        for a in arts:
            seen[a["link"]] = True
        save_json(SEEN_FILE, seen)
        send_to_zapier({"text": strip_html_for_x(msg), "kind": "terraforming"})
        return "ok"
    return "Failed"

# --------- helper for Zapier/X (strip HTML quickly) ---------
import re
TAG_RE = re.compile(r"<[^>]+>")

def strip_html_for_x(s: str) -> str:
    # remove tags; Telegram-style hashtags remain (we add them as plain text)
    return TAG_RE.sub("", s).strip()
