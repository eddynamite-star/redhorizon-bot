# src/tasks.py
import os
import json
import html
import re
import requests
from datetime import datetime, timedelta
from random import sample

from src.feeds import (
    fetch_rss_news,
    fetch_images,
    extract_image_from_entry,   # used by breaking fallback
    DEFAULT_TAGS,
)

DISCUSS_URL = "https://x.com/RedHorizonHub"

DATA_DIR = "data"
SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
IMAGE_CACHE_FILE = os.path.join(DATA_DIR, "image_cache.json")
CULTURE_STATE_FILE = os.path.join(DATA_DIR, "culture_state.json")
BOOKS_FILE  = os.path.join(DATA_DIR, "books.json")
GAMES_FILE  = os.path.join(DATA_DIR, "games.json")
MOVIES_FILE = os.path.join(DATA_DIR, "movies.json")

# ---------------------------
# JSON helpers
# ---------------------------
def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ---------------------------
# Telegram helpers (HTML mode)
# ---------------------------
HEADERS = {"User-Agent": "RedHorizonBot/1.0"}

def _tg_send_message(text, buttons=None, disable_preview=False):
    bot = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHANNEL_ID", "")
    if not bot or not chat:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID")
    url = f"https://api.telegram.org/bot{bot}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [buttons]}
    r = requests.post(url, json=payload, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram sendMessage failed: {r.text}")
    return True

def _tg_send_photo(photo_url, caption, buttons=None):
    bot = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHANNEL_ID", "")
    if not bot or not chat:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID")
    url = f"https://api.telegram.org/bot{bot}/sendPhoto"
    payload = {
        "chat_id": chat,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [buttons]}
    r = requests.post(url, json=payload, timeout=20)
    if r.status_code != 200:
        # fallback to text if photo fails
        _tg_send_message(caption, buttons)
    return True

def _buttons_open_and_discuss(url):
    return [
        {"text": "🔗 Open", "url": url},
        {"text": "💬 Discuss on X", "url": DISCUSS_URL},
    ]

# ---------------------------
# Format helpers
# ---------------------------
_PTAG = re.compile(r"<p[\s>].*?</p>", re.I | re.S)
_TAGS = re.compile(r"<.*?>", re.S)
_WS   = re.compile(r"\s+")

def _first_sentence(text, fallback_title, max_words=26):
    if not text:
        words = (fallback_title or "").split()
        return " ".join(words[:max_words])
    # already stripped of tags by feeds.py
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    cand = parts[0] if parts else ""
    if len(cand) < 20 or "credit" in cand.lower():
        words = (fallback_title or "").split()
        cand = " ".join(words[:max_words])
    return cand

def _safe(s):  # HTML escape for Telegram
    return html.escape(s or "").replace("&amp;", "&")

# ---------------------------
# Zapier (optional)
# ---------------------------
def _zapier_send(data):
    url = os.getenv("ZAPIER_HOOK_URL")
    if not url:
        return True
    try:
        r = requests.post(url, json=data, timeout=10)
        return r.status_code in (200, 201)
    except Exception:
        return False

# ---------------------------
# Breaking News
# ---------------------------
def run_breaking_news():
    seen = _load(SEEN_FILE, {})
    now = datetime.utcnow()

    # Grab a larger pool, then subselect breaking
    pool = fetch_rss_news()[:60]
    items = [a for a in pool if a.get("is_breaking")]

    # Prefer SpaceX, then score, then recency
    def spacex_bias(a):
        ts = (a["title"] + " " + a["summary"]).lower()
        return any(k in ts for k in ("spacex","starship","falcon","starbase","raptor"))

    items.sort(key=lambda x: (spacex_bias(x), x.get("score",0), x["published"]), reverse=True)

    posted = 0
    for a in items[:2]:
        if a["link"] in seen:
            continue
        title = _safe(a["title"])
        quick = _safe(_first_sentence(a["summary"], a["title"]))
        body = (
            f"🚨 <b>BREAKING</b> — <b>{title}</b>\n\n"
            f"<i>Quick read:</i> {quick}\n\n"
            f"Source: {_safe(a['source'])}\n"
            f"#Breaking #SpaceX #Starship #RedHorizon"
        )
        buttons = _buttons_open_and_discuss(a["link"])
        if a.get("image"):
            _tg_send_photo(a["image"], body, buttons)
        else:
            _tg_send_message(body, buttons)

        seen[a["link"]] = True
        posted += 1

    _save(SEEN_FILE, seen)
    return "ok" if posted else "no-post"

# ---------------------------
# Daily Digest (5 items)
# ---------------------------
def run_daily_digest():
    seen = _load(SEEN_FILE, {})
    now = datetime.utcnow()

    all_items = fetch_rss_news()[:100]
    fresh24 = [a for a in all_items if (now - a["published"]) <= timedelta(hours=24)]

    # SpaceX first
    def is_spacex(a):
        t = (a["title"] + " " + a["summary"]).lower()
        return any(k in t for k in ("spacex","starship","falcon","starbase","raptor","elon"))

    spacexy = [a for a in fresh24 if is_spacex(a)]
    others  = [a for a in fresh24 if not is_spacex(a)]

    spacexy.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)
    others.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)

    # Build final: cap Reddit to 1 globally
    final = []
    reddit_count = 0
    def maybe_add(it):
        nonlocal reddit_count
        if "reddit.com" in it.get("source_host","") and reddit_count >= 1:
            return
        final.append(it)
        if "reddit.com" in it.get("source_host",""):
            reddit_count += 1

    for it in spacexy:
        if len(final) >= 3:
            break
        maybe_add(it)
    for it in others:
        if len(final) >= 5:
            break
        maybe_add(it)

    # Fallback to 72h if quiet
    if len(final) < 3:
        fresh72 = [a for a in all_items if (now - a["published"]) <= timedelta(hours=72)]
        spacexy = [a for a in fresh72 if is_spacex(a)]
        others  = [a for a in fresh72 if not is_spacex(a)]
        spacexy.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)
        others.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)
        final, reddit_count = [], 0
        for it in spacexy:
            if len(final) >= 3:
                break
            maybe_add(it)
        for it in others:
            if len(final) >= 5:
                break
            maybe_add(it)

    if not final:
        return "no-post"

    extra_tags = " ".join(sample(DEFAULT_TAGS, k=3))
    head = f"🚀 <b>Red Horizon Daily Digest — {now.strftime('%b %d, %Y')}</b>"
    blocks = [head, ""]

    # Compose blocks
    for a in final:
        title = _safe(a["title"])
        quick = _safe(_first_sentence(a["summary"], a["title"]))
        clock = a["published"].strftime("%H:%M")
        src   = _safe(a["source"])
        line = (
            f"• <b>{title}</b> — <i>{src}</i> · 🕒 {clock} UTC\n"
            f"  <i>Quick read:</i> {quick}\n"
            f"  ➡️ <a href=\"{a['link']}\">Open</a>\n"
        )
        blocks.append(line)

    blocks.append(f"Follow on X: @RedHorizonHub\n#Daily {extra_tags}")
    full_text = "\n".join(blocks)

    # Post with top-story image if available
    top = final[0]
    if top.get("image"):
        _tg_send_photo(top["image"], head, buttons=_buttons_open_and_discuss(top["link"]))
        # Send the rest without repeating header
        _tg_send_message("\n".join(blocks[2:]), disable_preview=True)
    else:
        _tg_send_message(full_text, buttons=[{"text":"💬 Discuss on X","url":DISCUSS_URL}], disable_preview=True)

    for a in final:
        seen[a["link"]] = True
    _save(SEEN_FILE, seen)
    _zapier_send({"text": full_text})
    return "ok"

# ---------------------------
# Image of the Day
# ---------------------------
def run_daily_image():
    seen  = _load(SEEN_FILE, {})
    cache = _load(IMAGE_CACHE_FILE, {})

    imgs = fetch_images()
    for im in imgs:
        if im["url"] in seen or cache.get(im["url"]):
            continue
        title = _safe(im["title"] or "Space Image")
        date  = im["published"].strftime("%b %d, %Y")
        caption = f"📸 <b>Red Horizon Daily Image</b>\n{title}\n<i>{date}</i>\n#Space #Mars #RedHorizon"
        buttons = [{"text":"Source","url":im.get("source_link") or im["url"]}]
        _tg_send_photo(im["url"], caption, buttons=[buttons])

        seen[im["url"]] = True
        cache[im["url"]] = True
        _save(SEEN_FILE, seen)
        _save(IMAGE_CACHE_FILE, cache)
        return "ok"

    return "no-post"

# ---------------------------
# Welcome
# ---------------------------
def run_welcome_message():
    msg = (
        "👋 <b>Welcome to Red Horizon!</b>\n"
        "Your daily hub for SpaceX, Starship, Mars exploration & culture.\n\n"
        "What to expect:\n"
        "• 🚨 Breaking (only when it truly breaks)\n"
        "• 📰 Daily Digest (5 hand-picked stories)\n"
        "• 📸 Daily Image\n"
        "• 🎭 Culture Spotlights (books, games, movies/TV)\n\n"
        "Follow on X: @RedHorizonHub"
    )
    _tg_send_message(msg, disable_preview=True)
    return "ok"

# ---------------------------
# Culture Spotlight (rotates)
# ---------------------------
def _first_sentences_from_url(url, max_sent=2):
    try:
        if not url:
            return ""
        html_raw = requests.get(url, headers=HEADERS, timeout=12).text
        for m in _PTAG.finditer(html_raw):
            txt = _TAGS.sub(" ", m.group())
            txt = html.unescape(_WS.sub(" ", txt)).strip()
            if len(txt) > 60:
                parts = re.split(r"(?<=[.!?])\s+", txt)
                return " ".join(parts[:max_sent]).strip()
        return ""
    except Exception:
        return ""

def _blurb_from_item(it, kind):
    if it.get("blurb"):
        return it["blurb"]
    for k in ("wiki_link","review_link","official","trailer"):
        s = _first_sentences_from_url(it.get(k))
        if s:
            return s
    # fallback
    title = it.get("title","This work")
    if kind == "book":  return f"{title} is a standout space read with ideas our community loves."
    if kind == "game":  return f"{title} offers an engaging space experience for explorers and sim fans."
    return f"{title} captures the awe and peril of space exploration."

def _buttons_for_item(it, kind):
    row = []
    if it.get("review_link"): row.append({"text":"📖 Review", "url":it["review_link"]})
    if it.get("wiki_link"):   row.append({"text":"🌐 Wiki",    "url":it["wiki_link"]})
    if kind in ("game","movie") and it.get("official"): row.append({"text":"🏷 Official","url":it["official"]})
    if kind in ("game","movie") and it.get("trailer"):  row.append({"text":"🎬 Trailer", "url":it["trailer"]})
    return [row] if row else None

def _hashtags_for(kind):
    base = "#RedHorizon #Space"
    if kind == "book":  return f"{base} #Books #SciFi"
    if kind == "game":  return f"{base} #Gaming #SpaceGames"
    return f"{base} #Films #TV #SpaceFilms"

def run_culture_spotlight():
    st = _load(CULTURE_STATE_FILE, {"cycle":"book","book_index":0,"game_index":0,"movie_index":0})
    force = (os.getenv("CULTURE_FORCE") or "").strip().lower()
    cycle = force if force in ("book","game","movie") else st["cycle"]

    if cycle == "book":
        items = _load(BOOKS_FILE, [])
        idx_key, icon, title_line = "book_index", "📚", "Book Spotlight"
    elif cycle == "game":
        items = _load(GAMES_FILE, [])
        idx_key, icon, title_line = "game_index", "🎮", "Game Spotlight"
    else:
        items = _load(MOVIES_FILE, [])
        idx_key, icon, title_line = "movie_index", "🎬", "Culture Spotlight"

    if not items:
        _tg_send_message("⚠️ Culture list is empty.")
        return "empty"

    i = st[idx_key] % len(items)
    it = items[i]

    title  = _safe(it.get("title"))
    author = _safe(it.get("author") or it.get("creator") or it.get("studio") or "")
    blurb  = _safe(_blurb_from_item(it, cycle))
    tags   = _hashtags_for(cycle)
    header = f"<b>{icon} {title_line}</b>\n<i>{title}</i>{(' — ' + author) if author else ''}\n\n{blurb}\n\n{tags}"

    buttons = _buttons_for_item(it, cycle)
    cover   = it.get("cover")

    if cover:
        _tg_send_photo(cover, header, buttons)
    else:
        _tg_send_message(header, buttons)

    if not force:
        st[idx_key] = (st[idx_key] + 1) % len(items)
        st["cycle"] = {"book":"game","game":"movie","movie":"book"}[cycle]
        _save(CULTURE_STATE_FILE, st)

    return "ok"
