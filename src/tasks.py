# src/tasks.py

import os
import re
import json
import html
import requests
from datetime import datetime, timedelta
from random import sample
from urllib.parse import urlparse, unquote

from src.feeds import (
    fetch_rss_news,   # returns list of dicts with: title, link, summary, published(dt), source, source_host, image?, is_breaking?, score
    fetch_images,     # returns list of dicts with: title, url(image), published(dt), source_name, source_link
    DEFAULT_TAGS,     # e.g., ["#Space", "#Mars", "#SpaceX", "#Starship", "#RedHorizon"]
)

# ---------------------------------
# Constants / Files / HTTP headers
# ---------------------------------
DISCUSS_URL = "https://x.com/RedHorizonHub"
HEADERS = {"User-Agent": "Mozilla/5.0 (RedHorizonBot)"}

DATA_DIR = "data"
SEEN_FILE         = os.path.join(DATA_DIR, "seen_links.json")
IMAGE_CACHE_FILE  = os.path.join(DATA_DIR, "image_cache.json")

# Culture data files
BOOKS_FILE  = os.path.join(DATA_DIR, "books.json")
GAMES_FILE  = os.path.join(DATA_DIR, "games.json")
MOVIES_FILE = os.path.join(DATA_DIR, "movies.json")
STATE_FILE  = os.path.join(DATA_DIR, "culture_state.json")

# -------------
# JSON helpers
# -------------
def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        return default

def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# -------------
# Text helpers
# -------------
def _safe(text: str) -> str:
    """Escape for Telegram HTML mode."""
    return html.escape(text or "")

def _first_sentence(text: str, fallback_title: str, max_words: int = 26) -> str:
    """
    Try to return the first clean sentence from text.
    Fallback to first N words from title if text is empty/credits/img tags.
    """
    t = (text or "").strip()
    # Strip rudimentary tags if any slipped through
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()

    parts = re.split(r"(?<=[.!?])\s+", t)
    cand = parts[0].strip() if parts else ""
    bad = ("credit", "image:", "src=", "href=", "figure")
    if not cand or len(cand) < 20 or any(b in cand.lower() for b in bad):
        words = (fallback_title or "").strip().split()
        cand = " ".join(words[:max_words]).strip()
    return cand

def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""

# ------------------------
# Telegram API (HTML mode)
# ------------------------
def _tg_send_message(text, buttons=None, disable_preview=False):
    bot = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHANNEL_ID", "")
    if not bot or not chat:
        print("[TG] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID")
        return False

    url = f"https://api.telegram.org/bot{bot}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [buttons]}
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            print("[TG] sendMessage error:", r.text)
            return False
        return True
    except Exception as e:
        print("[TG] sendMessage exception:", e)
        return False

def _tg_send_photo(photo_url, caption, buttons=None):
    bot = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHANNEL_ID", "")
    if not bot or not chat:
        print("[TG] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID")
        return False

    url = f"https://api.telegram.org/bot{bot}/sendPhoto"
    payload = {
        "chat_id": chat,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [buttons]}
    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code != 200:
            print("[TG] sendPhoto error (fallback to text):", r.text)
            return _tg_send_message(caption, buttons)
        return True
    except Exception as e:
        print("[TG] sendPhoto exception, fallback to text:", e)
        return _tg_send_message(caption, buttons)

def _buttons_open_and_discuss(link: str):
    return [
        {"text": "➡️ Open", "url": link},
        {"text": "💬 Discuss on X", "url": DISCUSS_URL},
    ]

# ------------
# Zapier hook
# ------------
def _zapier_send(data):
    hook = os.getenv("ZAPIER_HOOK_URL", "")
    if not hook:
        return True
    try:
        r = requests.post(hook, json=data, timeout=10)
        if r.status_code not in (200, 201):
            print("[Zapier] error:", r.status_code, r.text)
            return False
        return True
    except Exception as e:
        print("[Zapier] exception:", e)
        return False

# -------------------------------
# Helper: pick hero image for digest
# -------------------------------
def _og_image(url):
    try:
        html_txt = requests.get(url, headers=HEADERS, timeout=8).text
        m = re.search(r'(?:property|name)=["\']og:image["\']\s*content=["\']([^"\']+)["\']', html_txt, re.I)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None

def _pick_hero(items):
    """Return (hero_item, hero_image_url or None)."""
    for it in items:
        if it.get("image"):
            return it, it["image"]
    if items:
        img = _og_image(items[0]["link"])
        if img:
            items[0]["image"] = img
            return items[0], img
        return items[0], None
    return None, None

# -------------
# Breaking news
# -------------
def run_breaking_news():
    """
    Post up to 2 breaking items (<= 15 min old).
    SpaceX/Starship weighted. Bold titles. First-sentence quick read.
    Includes image when present. Inline Open + Discuss buttons.
    """
    seen = _load(SEEN_FILE, {}) or {}
    now = datetime.utcnow()

    items = [a for a in fetch_rss_news()[:60] if a.get("is_breaking")]
    # weight: SpaceX-ish + score + recency
    def _weight(x):
        txt = (x.get("title","") + " " + x.get("summary","")).lower()
        spacexy = any(k in txt for k in ["spacex","starship","falcon","raptor","starbase","elon"])
        return (1 if spacexy else 0, x.get("score", 0), x.get("published", now))
    items.sort(key=_weight, reverse=True)

    posted = 0
    for a in items[:2]:
        if a["link"] in seen:
            continue

        title = _safe(a["title"])
        quick = _safe(_first_sentence(a["summary"], a["title"]))
        src   = _safe(a["source"])
        body = (
            f"🚨 <b>BREAKING</b> — <b>{title}</b>\n\n"
            f"<i>Quick read:</i> {quick}\n\n"
            f"Read more on {src}\n"
            f"#Breaking #SpaceX #Starship #RedHorizon"
        )
        ok = False
        if a.get("image"):
            ok = _tg_send_photo(a["image"], body, _buttons_open_and_discuss(a["link"]))
        if not ok:
            ok = _tg_send_message(body, _buttons_open_and_discuss(a["link"]))
        if ok:
            seen[a["link"]] = True
            posted += 1

    _save(SEEN_FILE, seen)
    return "ok" if posted else "no-post"

# -----------
# Daily digest
# -----------
def run_daily_digest():
    """
    5-item digest, SpaceX-weighted, Reddit capped to 1 total.
    Bold titles, 'Quick read' 1-sentence, hero image for the top story
    (uses article image or falls back to og:image).
    Falls back to 72h window if 24h yields <3 items.
    """
    seen = _load(SEEN_FILE, {}) or {}
    now = datetime.utcnow()

    all_items = fetch_rss_news()[:100]

    def _within(items, hours):
        return [a for a in items if (now - a["published"]) <= timedelta(hours=hours)]

    fresh = _within(all_items, 24)

    def _spacexy(a):
        t = (a["title"] + " " + a["summary"]).lower()
        return any(k in t for k in ["spacex","starship","falcon","raptor","starbase","elon"])

    spacex_items = [a for a in fresh if _spacexy(a)]
    other_items  = [a for a in fresh if not _spacexy(a)]

    spacex_items.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)
    other_items.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)

    final = []
    reddit_count = 0
    def _maybe_add(item):
        nonlocal reddit_count
        if "reddit.com" in item.get("source_host",""):
            if reddit_count >= 1:
                return
            reddit_count += 1
        final.append(item)

    for it in spacex_items:
        if len(final) >= 3:
            break
        _maybe_add(it)
    for it in other_items:
        if len(final) >= 5:
            break
        _maybe_add(it)

    # Fallback to 72h if too few
    if len(final) < 3:
        fresh72 = _within(all_items, 72)
        spacex_items = [a for a in fresh72 if _spacexy(a)]
        other_items  = [a for a in fresh72 if not _spacexy(a)]
        spacex_items.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)
        other_items.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)
        final, reddit_count = [], 0
        for it in spacex_items:
            if len(final) >= 3:
                break
            _maybe_add(it)
        for it in other_items:
            if len(final) >= 5:
                break
            _maybe_add(it)

    if not final:
        print("[DIGEST] no-post")
        return "no-post"

    # Head & blocks
    extra_tags = " ".join(sample(DEFAULT_TAGS, k=min(3, len(DEFAULT_TAGS))))
    head  = f"🚀 <b>Red Horizon Daily Digest — {now.strftime('%b %d, %Y')}</b>"
    blocks = [head, ""]

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

    # Pick a hero with image (or og:image)
    hero, hero_img = _pick_hero(final)
    if hero_img:
        _tg_send_photo(hero_img, head, buttons=_buttons_open_and_discuss(hero["link"]))
        # send list without the duplicate head (skip head + blank)
        _tg_send_message("\n".join(blocks[2:]), disable_preview=True)
    else:
        _tg_send_message(
            full_text,
            buttons=[{"text": "💬 Discuss on X", "url": DISCUSS_URL}],
            disable_preview=True
        )

    for a in final:
        seen[a["link"]] = True
    _save(SEEN_FILE, seen)
    _zapier_send({"text": full_text})
    return "ok"

# -----------
# Daily image
# -----------
def run_daily_image():
    """
    Post newest not-yet-seen image; cache image URLs to avoid repeats.
    """
    seen  = _load(SEEN_FILE, {}) or {}
    cache = _load(IMAGE_CACHE_FILE, {}) or {}  # {url: True}
    imgs = fetch_images()

    for im in imgs:
        if im["url"] in seen or cache.get(im["url"]):
            continue

        title = _safe(im.get("title") or "Space Image")
        date  = im["published"].strftime("%b %d, %Y")
        caption = f"📸 <b>Red Horizon Daily Image</b>\n{title}\n<i>{date}</i>\n#Space #Mars #RedHorizon"

        buttons = []
        if im.get("source_link"):
            buttons.append({"text": "Source", "url": im["source_link"]})
        else:
            buttons.append({"text": "Open", "url": im["url"]})

        if _tg_send_photo(im["url"], caption, buttons=[buttons]):
            seen[im["url"]] = True
            cache[im["url"]] = True
            _save(SEEN_FILE, seen)
            _save(IMAGE_CACHE_FILE, cache)
            return "ok"

    print("[IMAGE] no-post")
    return "no-post"

# ----------------
# Welcome message
# ----------------
def run_welcome_message():
    msg = (
        "👋 <b>Welcome to Red Horizon!</b>\n"
        "Your daily hub for SpaceX, Starship, Mars exploration & culture.\n\n"
        "<b>What to expect</b>\n"
        "• 🚨 Breaking (only when it truly breaks)\n"
        "• 📰 Daily Digest (5 hand-picked stories)\n"
        "• 📸 Daily Image\n"
        "• 🎭 Culture Spotlights (books, games, movies/TV)\n\n"
        "Follow on X: @RedHorizonHub"
    )
    _tg_send_message(msg, disable_preview=True)
    return "ok"

# -------------------------------------------------------
# Culture Spotlight (rotates: book → game → movie/screen)
# -------------------------------------------------------
def _wiki_summary_api(wiki_link: str) -> str:
    """Use Wikipedia REST Summary API to get 1–3 sentence extract."""
    try:
        if not wiki_link:
            return ""
        p = urlparse(wiki_link)
        if "wikipedia.org" not in p.netloc.lower():
            return ""
        if "/wiki/" not in p.path:
            return ""
        title = unquote(p.path.split("/wiki/")[-1])
        api = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
        r = requests.get(api, headers={"Accept": "application/json"}, timeout=8)
        if r.status_code != 200:
            return ""
        data = r.json()
        extract = (data.get("extract") or "").strip()
        if not extract:
            return ""
        parts = re.split(r"(?<=[.!?])\s+", extract)
        return " ".join(parts[:2]).strip()
    except Exception:
        return ""

_PTAG = re.compile(r"<p[^>]*>(.*?)</p>", re.I | re.S)

def _html_first_paragraph(url: str) -> str:
    """Fallback: pull the first paragraph text from a page and return 1–2 sentences."""
    try:
        if not url:
            return ""
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return ""
        m = _PTAG.search(resp.text)
        if not m:
            return ""
        text = re.sub(r"<[^>]+>", " ", m.group(1))
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        parts = re.split(r"(?<=[.!?])\s+", text)
        return " ".join(parts[:2]).strip()
    except Exception:
        return ""

def _blurb_for_item(item, kind):
    # 1) explicit blurb
    if (item.get("blurb") or "").strip():
        return item["blurb"].strip()
    # 2) wiki summary API
    wiki = item.get("wiki_link")
    s = _wiki_summary_api(wiki)
    if s:
        return s
    # 3) HTML first paragraph from review/wiki
    for url in [item.get("review_link"), wiki]:
        s = _html_first_paragraph(url or "")
        if s:
            return s
    # 4) safe fallback
    title = item.get("title", "This work")
    if kind == "book":
        return f"{title} is a noted space-related read that resonates with Red Horizon’s audience."
    if kind == "game":
        return f"{title} offers an engaging space experience for explorers and sim fans."
    return f"{title} captures the awe and peril of space in a way that sticks with you."

def _buttons_for_item(item, kind):
    buttons = []
    if item.get("review_link"):
        buttons.append({"text": "📖 Review", "url": item["review_link"]})
    if item.get("wiki_link"):
        buttons.append({"text": "🌐 Wiki", "url": item["wiki_link"]})
    if kind in ("game", "movie") and item.get("official"):
        buttons.append({"text": "🏷 Official", "url": item["official"]})
    if kind in ("game", "movie") and item.get("trailer"):
        buttons.append({"text": "🎬 Trailer", "url": item["trailer"]})
    return [buttons] if buttons else None

def _culture_state():
    return _load(STATE_FILE, {
        "cycle": "book",
        "book_index": 0,
        "game_index": 0,
        "movie_index": 0,
    })

def _advance_cycle(cyc):
    return {"book": "game", "game": "movie", "movie": "book"}[cyc]

def _hashtags_for(kind):
    base = "#RedHorizon #Space"
    if kind == "book":
        return f"{base} #Books #SciFi"
    if kind == "game":
        return f"{base} #Gaming #SpaceGames"
    return f"{base} #Films #TV #SpaceFilms"

def run_culture_spotlight():
    """
    Rotate Book → Game → Movie (Screen). You can force cycle by setting env CULTURE_FORCE
    to one of: 'book' | 'game' | 'movie'.
    """
    state = _culture_state()
    force = (os.getenv("CULTURE_FORCE") or "").strip().lower()
    cycle = force if force in ("book","game","movie") else state["cycle"]

    if cycle == "book":
        items = _load(BOOKS_FILE, [])
        idx_key = "book_index"
        icon = "📚"; title_line = "Book Spotlight"
        kind = "book"
    elif cycle == "game":
        items = _load(GAMES_FILE, [])
        idx_key = "game_index"
        icon = "🎮"; title_line = "Game Spotlight"
        kind = "game"
    else:
        items = _load(MOVIES_FILE, [])
        idx_key = "movie_index"
        icon = "🎬"; title_line = "Culture Spotlight"
        kind = "movie"

    if not items:
        _tg_send_message("⚠️ Culture list is empty.")
        return "empty"

    i = state[idx_key] % len(items)
    item = items[i]

    title  = _safe(item.get("title"))
    author = _safe(item.get("author") or item.get("creator") or item.get("studio") or "")
    blurb  = _safe(_blurb_for_item(item, kind))
    tags   = _hashtags_for(kind)

    header = f"<b>{icon} {title_line}</b>\n<i>{title}</i>{(' — ' + author) if author else ''}\n\n{blurb}\n\n{tags}"
    buttons = _buttons_for_item(item, kind)

    cover = item.get("cover")
    if cover:
        _tg_send_photo(cover, header, buttons)
    else:
        _tg_send_message(header, buttons)

    # advance state unless forced
    if not force:
        state[idx_key] = (state[idx_key] + 1) % len(items)
        state["cycle"] = _advance_cycle(cycle)
        _save(STATE_FILE, state)

    return "ok"

# Back-compat alias for your workflow name
def run_culture_daily():
    return run_culture_spotlight()
