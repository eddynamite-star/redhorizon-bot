# src/tasks.py — polished output + launch scan/reminders

import os
import json
import random
import re
import requests
from typing import Dict, Any, List
from datetime import datetime, timedelta

from src.feeds import fetch_rss_news, fetch_images
from src.formatter import (
    fmt_breaking, fmt_priority, fmt_digest, fmt_image_post,
    fmt_starbase_fact, fmt_book_spotlight, fmt_welcome
)

# ---------- paths ----------
DATA_DIR = "data"
SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
BOOK_IDX_FILE = os.path.join(DATA_DIR, "book_index.json")
FACT_IDX_FILE = os.path.join(DATA_DIR, "fact_index.json")
BOOK_LIST_FILE = os.path.join(DATA_DIR, "book_list.json")
LAUNCH_CACHE_FILE = os.path.join(DATA_DIR, "launch_cache.json")

def _ensure_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    for path, default in [
        (SEEN_FILE, {}),
        (BOOK_IDX_FILE, {"index": 0}),
        (FACT_IDX_FILE, {"index": 0}),
        (LAUNCH_CACHE_FILE, []),
    ]:
        if not os.path.exists(path):
            with open(path, "w") as f:
                json.dump(default, f)

def load_json(file_path: str):
    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except Exception:
        # recover from missing/corrupt
        if file_path.endswith("seen_links.json"):
            return {}
        if file_path.endswith("launch_cache.json"):
            return []
        return {"index": 0}

def save_json(file_path: str, data: Any):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)

# ---------- Telegram / Zapier ----------
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
        r = requests.post(url, json=payload, timeout=20)
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
        r = requests.post(url, json=payload, timeout=30)
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
        return True
    try:
        r = requests.post(hook, json=data, timeout=15)
        if r.status_code in (200, 201, 202, 204):
            print("ok [Zapier]")
            return True
        print(f"Zapier error: {r.status_code} {r.text}")
    except Exception as e:
        print(f"Zapier exception: {e}")
    return True  # non-fatal

# ---------- Copy helpers ----------
BREAKING_INTROS = [
    "🚨 <b>BREAKING</b> — {title}",
    "⚡ <b>Just in</b> — {title}",
    "🔥 <b>Hot off the press</b> — {title}",
]
LIVE_INTROS = [
    "🟢 <b>Live/Now</b> — {title}",
    "🟢 <b>Happening now</b> — {title}",
    "🟢 <b>Live update</b> — {title}",
]
WHY_HINTS = [
    (r"\bstarship\b", "Progress on Starship directly affects Mars architecture & payload cadence."),
    (r"\bstatic fire|hotfire|engine test\b", "Firing milestones validate engines and clear the path to flight."),
    (r"\brollout|stack|destack\b", "Vehicle movement hints at imminent testing or launch flow."),
    (r"\blaunch\b", "Launch cadence and reliability drive reusability economics."),
    (r"\bmars\b", "Anything Mars-related informs long-term habitation & ISRU strategy."),
    (r"\bnasa|esa|jpl\b", "Agency collaboration & science shape mission windows and funding."),
]
def pick_intro(intros: list[str], title: str) -> str:
    t = random.choice(intros)
    return t.format(title=title)
def why_it_matters(title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    for pat, reason in WHY_HINTS:
        if re.search(pat, text):
            return f"• <i>Why it matters:</i> {reason}"
    return ""

# ---------- Tasks ----------
def run_breaking_news():
    """
    Post ONLY when there is breaking content (no empty posts).
    Super-breaking (<=5m): Live/Now style, Breaking (<=15m): Breaking style.
    """
    _ensure_files()
    seen = load_json(SEEN_FILE)
    articles = fetch_rss_news()
    now = datetime.utcnow()
    posted = 0

    for art in articles[:12]:
        if art["link"] in seen:
            continue

        if art.get("is_super_breaking"):
            header = pick_intro(LIVE_INTROS, art["title"])
            base = fmt_priority(title=art["title"], url=art["link"], reason="Super-priority", tags=["Breaking","Live"])
            msg = base.replace("🟢 <b>Live/Now</b>", header.split(" — ")[0], 1)
        elif art.get("is_breaking"):
            header = pick_intro(BREAKING_INTROS, art["title"])
            base = fmt_breaking(
                title=art["title"],
                url=art["link"],
                summary=art.get("summary",""),
                tags=["Breaking"],
                source_hint=art.get("source","")
            )
            msg = base.replace("🚨 <b>BREAKING</b>", header.split(" — ")[0], 1)
            why = why_it_matters(art["title"], art.get("summary",""))
            if why:
                msg = msg.replace("Read more", f"{why}\n\nRead more", 1)
        else:
            continue

        if send_telegram_message(msg):
            seen[art["link"]] = True
            posted += 1
            send_to_zapier({"text": strip_html_for_x(msg), "url": art["link"], "kind": "breaking"})
        if posted >= 2:
            break

    save_json(SEEN_FILE, seen)
    return "ok" if posted else "no-post"

def run_daily_digest():
    _ensure_files()
    seen = load_json(SEEN_FILE)
    articles = fetch_rss_news()
    now = datetime.utcnow()
    recent = [a for a in articles if (now - a["published"]) <= timedelta(hours=24)]
    top = recent[:7]

    items = [{"title": a["title"], "url": a["link"], "source": a.get("source")} for a in top]
    date_label = now.strftime("%b %d, %Y")
    msg = fmt_digest(date_label=date_label, items=items, tags=["Daily"], footer_x="https://x.com/RedHorizonHub")

    if top and send_telegram_message(msg):
        for a in top:
            seen[a["link"]] = True
        save_json(SEEN_FILE, seen)
        send_to_zapier({"text": strip_html_for_x(msg), "kind": "digest"})
        return "ok"
    return "no-post"

def run_daily_image():
    _ensure_files()
    seen = load_json(SEEN_FILE)
    fact_idx = load_json(FACT_IDX_FILE)
    images = fetch_images()

    for img in images:
        url = img.get("url")
        if not url or url in seen:
            continue
        caption = fmt_image_post(
            title=img.get("title","Space image"),
            url=img.get("source_link", url),
            credit=img.get("source_name",""),
            tags=["Image"]
        )
        if send_telegram_image(url, caption):
            seen[url] = True
            fact_idx["index"] = int(fact_idx.get("index", 0)) + 1
            save_json(SEEN_FILE, seen)
            save_json(FACT_IDX_FILE, fact_idx)
            send_to_zapier({"image_url": url, "caption": strip_html_for_x(caption), "kind": "image"})
            return "ok"
    return "no-post"

def run_starbase_highlight():
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
    return "no-post"

def run_book_spotlight():
    """
    Expects data/book_list.json entries:
    [{"title":"Red Mars","author":"Kim Stanley Robinson","blurb":"...","wiki_link":"https://en.wikipedia.org/wiki/Red_Mars"}]
    """
    _ensure_files()
    books = load_json(BOOK_LIST_FILE)
    if not isinstance(books, list) or not books:
        print("No books.json list found")
        return "no-post"
    book_idx = load_json(BOOK_IDX_FILE)
    i = int(book_idx.get("index", 0)) % len(books)
    b = books[i]
    title = b.get("title", "Recommended book")
    author = b.get("author", "")
    blurb = b.get("blurb", "A standout pick for space & sci-fi fans.")
    url = b.get("wiki_link") or b.get("link") or ""
    msg = fmt_book_spotlight(title=title, author=author, blurb=blurb, url=url, tags=["Books","SciFi"])
    if send_telegram_message(msg):
        book_idx["index"] = i + 1
        save_json(BOOK_IDX_FILE, book_idx)
        send_to_zapier({"text": strip_html_for_x(msg), "url": url, "kind": "book"})
        return "ok"
    return "no-post"

def run_welcome_message():
    msg = fmt_welcome("https://x.com/RedHorizonHub")
    if send_telegram_message(msg):
        send_to_zapier({"text": strip_html_for_x(msg), "kind": "welcome"})
        return "ok"
    return "no-post"

# ---------- Launch scan & reminders ----------
# Simple heuristic: detect likely launch items, store with timestamp if found, remind at T-24h and T-1h.
LAUNCH_PATTERNS = [
    r"\blaunch\b", r"\bcountdown\b", r"\bliftoff\b", r"\blive\b", r"\bpremiere\b",
]
TIME_HINTS = [
    r"\bT[-\s]?(\d+)\s*min\b", r"\bT[-\s]?(\d+)\s*hr\b",
    r"\b(\d{1,2}:\d{2})\s*(UTC|GMT)\b",
    r"\b(\d{1,2})\s*(UTC|GMT)\b",
]

def is_launchy(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    return any(re.search(p, text) for p in LAUNCH_PATTERNS)

def run_scan_launches():
    """
    Scan feeds; cache potential launch items with rough schedule if present.
    """
    _ensure_files()
    cache = load_json(LAUNCH_CACHE_FILE)
    seen_links = set(x.get("link") for x in cache)

    arts = fetch_rss_news()
    new = 0
    for a in arts[:40]:
        if a["link"] in seen_links:
            continue
        if not is_launchy(a["title"], a.get("summary","")):
            continue
        # naive: store published time as reference; better if title has UTC time
        when = a.get("published")
        cache.append({
            "title": a["title"],
            "link": a["link"],
            "source": a.get("source",""),
            "published": (when.isoformat() if when else datetime.utcnow().isoformat()),
            "t24_posted": False,
            "t1_posted": False
        })
        new += 1

    # trim cache to last 200
    cache = sorted(cache, key=lambda x: x.get("published",""), reverse=True)[:200]
    save_json(LAUNCH_CACHE_FILE, cache)
    return f"ok (+{new})"

def run_launch_reminders():
    """
    Post reminders for cached launch items roughly at T-24h and T-1h,
    using the item's published time as a proxy (best-effort).
    """
    _ensure_files()
    cache = load_json(LAUNCH_CACHE_FILE)
    now = datetime.utcnow()
    updated = False
    posted = 0

    for it in cache:
        try:
            pub = datetime.fromisoformat(it.get("published"))
        except Exception:
            continue
        age = now - pub
        # windows
        t24 = timedelta(hours=24)
        t1  = timedelta(hours=1)
        within_10m = timedelta(minutes=10)

        # T-24h (approx: when age ~ 24h)
        if not it.get("t24_posted") and abs(age - t24) <= within_10m:
            msg = f"⏰ <b>Launch in ~24 hours</b>\n{it['title']}\n<a href=\"{it['link']}\">Details / stream</a>\n#Launch #RedHorizon"
            if send_telegram_message(msg):
                it["t24_posted"] = True
                posted += 1
                updated = True

        # T-1h
        if not it.get("t1_posted") and abs(age - t1) <= within_10m:
            msg = f"🚀 <b>Launch in ~1 hour</b>\n{it['title']}\n<a href=\"{it['link']}\">Watch live</a>\n#Launch #RedHorizon"
            if send_telegram_message(msg):
                it["t1_posted"] = True
                posted += 1
                updated = True

    if updated:
        save_json(LAUNCH_CACHE_FILE, cache)
    return f"ok (posted {posted})"

# ---------- helper for Zapier/X ----------
TAG_RE = re.compile(r"<[^>]+>")
def strip_html_for_x(s: str) -> str:
    return TAG_RE.sub("", s).strip()
