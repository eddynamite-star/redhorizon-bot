# src/tasks.py

import os
import json
import requests
from datetime import datetime, timedelta
from random import sample
import re
from urllib.parse import urlparse, unquote

from src.feeds import fetch_rss_news, fetch_images, DEFAULT_TAGS

# ---------------------------
# Small utils: JSON + Markdown
# ---------------------------
def load_json(path, fallback=None):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return fallback

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def md_escape(text: str) -> str:
    """Escape Telegram Markdown special chars we use."""
    if not text:
        return ""
    return (
        text.replace("\\", "\\\\")
            .replace("_", "\\_")
            .replace("*", "\\*")
            .replace("[", "\\[")
            .replace("`", "\\`")
    )

def first_sentence(text: str, fallback_title: str, max_words: int = 26) -> str:
    """
    Return the first clean sentence from text.
    Fallback: first N words from title if text is empty/garbage.
    """
    t = (text or "").strip()
    parts = re.split(r"(?<=[\.!?])\s+", t)
    cand = parts[0].strip() if parts else ""
    if not cand or len(cand) < 20 or "credit" in cand.lower() or "image:" in cand.lower():
        words = (fallback_title or "").strip().split()
        cand = " ".join(words[:max_words]).strip()
    return cand

# ---------------------------
# Telegram senders (with buttons)
# ---------------------------
def send_telegram_message(text, buttons=None, retry=True):
    bot = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHANNEL_ID")
    if not bot or not chat:
        print("[TG] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID")
        return False

    url = f"https://api.telegram.org/bot{bot}/sendMessage"
    payload = {"chat_id": chat, "text": text, "parse_mode": "Markdown"}
    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": label, "url": href}] for (label, href) in buttons]
        }

    try:
        r = requests.post(url, json=payload, timeout=15)
        ok = (r.status_code == 200)
        if not ok:
            print("[TG] sendMessage error:", r.text)
            if retry:
                return send_telegram_message(text, buttons, retry=False)
        return ok
    except Exception as e:
        print("[TG] sendMessage exception:", e)
        return False

def send_telegram_image(image_url, caption, buttons=None, retry=True):
    bot = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHANNEL_ID")
    if not bot or not chat:
        print("[TG] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID")
        return False

    url = f"https://api.telegram.org/bot{bot}/sendPhoto"
    payload = {"chat_id": chat, "photo": image_url, "caption": caption, "parse_mode": "Markdown"}
    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": label, "url": href}] for (label, href) in buttons]
        }

    try:
        r = requests.post(url, json=payload, timeout=20)
        ok = (r.status_code == 200)
        if not ok:
            print("[TG] sendPhoto error:", r.text)
            if retry:
                return send_telegram_image(image_url, caption, buttons, retry=False)
        return ok
    except Exception as e:
        print("[TG] sendPhoto exception:", e)
        return False

def send_to_zapier(data):
    hook = os.getenv("ZAPIER_HOOK_URL")
    if not hook:
        return True
    try:
        r = requests.post(hook, json=data, timeout=10)
        if r.status_code not in (200, 201):
            print("[Zapier] error:", r.status_code, r.text)
        return r.status_code in (200, 201)
    except Exception as e:
        print("[Zapier] exception:", e)
        return False

# ---------------------------
# News Tasks
# ---------------------------
DISCUSS_URL = "https://x.com/RedHorizonHub"

def run_breaking_news():
    """
    Post up to 2 breaking items (<= 15 min old), with bold title, first-sentence quick read,
    image (if any), inline buttons. Prefers SpaceX-ish + higher score.
    """
    seen = load_json("data/seen_links.json", {}) or {}
    now = datetime.utcnow()

    items = [a for a in fetch_rss_news()[:40] if a.get("is_breaking")]
    # prefer SpaceX + score + recency
    items.sort(
        key=lambda x: (
            any(k in (x.get("title","") + x.get("summary","")).lower()
                for k in ["spacex", "starship", "falcon", "raptor", "starbase"]),
            x.get("score",0),
            x.get("published", now)
        ),
        reverse=True
    )

    posted = 0
    for a in items[:2]:
        if a["link"] in seen:
            continue

        title = md_escape(a["title"])
        quick = md_escape(first_sentence(a["summary"], a["title"]))
        body = (
            f"🚨 *BREAKING* — {title}\n\n"
            f"_Quick read:_ {quick}\n\n"
            f"Read more on {md_escape(a['source'])}\n"
            f"#Breaking #SpaceX #Starship #RedHorizon"
        )
        buttons = [("Open", a["link"]), ("Discuss on X", DISCUSS_URL)]

        ok = False
        if a.get("image"):
            ok = send_telegram_image(a["image"], body, buttons)
        if not ok:
            ok = send_telegram_message(body, buttons)

        if ok:
            seen[a["link"]] = True
            posted += 1

    save_json("data/seen_links.json", seen)
    return "ok" if posted else "no-post"

def run_daily_digest():
    """
    5-item digest, SpaceX-biased, Reddit capped to 1 across the whole list,
    bold titles, first-sentence quick read, top story image + buttons.
    Fallback to 72h window if 24h yields <3 items.
    """
    seen = load_json("data/seen_links.json", {}) or {}
    now = datetime.utcnow()

    # Try 24h window
    all_items = fetch_rss_news()[:80]
    fresh = [a for a in all_items if (now - a["published"]) <= timedelta(hours=24)]

    # Rank within two buckets
    spacexy = [a for a in fresh if any(k in (a["title"] + a["summary"]).lower()
                                       for k in ["spacex","starship","falcon","elon","raptor","starbase"])]
    others = [a for a in fresh if a not in spacexy]

    # Sort each bucket by score + recency
    spacexy.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)
    others.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)

    # Build final list with Reddit cap = 1 globally
    final = []
    reddit_count = 0

    def maybe_add(item):
        nonlocal reddit_count
        is_reddit = "reddit.com" in item.get("source_host", "")
        if is_reddit and reddit_count >= 1:
            return
        final.append(item)
        if is_reddit:
            reddit_count += 1

    # Prefer up to 3 SpaceX-heavy first, then others
    for it in spacexy:
        if len(final) >= 3:
            break
        maybe_add(it)
    for it in others:
        if len(final) >= 5:
            break
        maybe_add(it)

    # If too few, fallback to 72h
    if len(final) < 3:
        fresh72 = [a for a in all_items if (now - a["published"]) <= timedelta(hours=72)]
        spacexy = [a for a in fresh72 if any(k in (a["title"] + a["summary"]).lower()
                                             for k in ["spacex","starship","falcon","elon","raptor","starbase"])]
        others = [a for a in fresh72 if a not in spacexy]
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
        print("[DIGEST] no-post")
        return "no-post"

    extra_tags = " ".join(sample(DEFAULT_TAGS, k=3))
    top = final[0]
    head = f"🚀 *Red Horizon Daily Digest — {now.strftime('%b %d, %Y')}*"
    blocks = [head, ""]

    for a in final:
        title = md_escape(a["title"])
        quick = md_escape(first_sentence(a["summary"], a["title"]))
        clock = a["published"].strftime("%H:%M")
        block = (
            f"• *{title}* — _{md_escape(a['source'])}_ · 🕒 {clock} UTC\n"
            f"  _Quick read:_ {quick}\n"
            f"  ➡️ [Open]({a['link']})\n"
        )
        blocks.append(block)

    blocks.append(f"Follow on X: @RedHorizonHub\n#Daily {extra_tags}")
    full_text = "\n".join(blocks)

    # Send with top image if present; then send the list (without header duplication)
    buttons = [("Open top story", top["link"]), ("Discuss on X", DISCUSS_URL)]
    if top.get("image"):
        send_telegram_image(top["image"], head, buttons)
        send_telegram_message("\n".join(blocks[2:]))  # skip header already shown
    else:
        send_telegram_message(full_text, buttons=[("Discuss on X", DISCUSS_URL)])

    for a in final:
        seen[a["link"]] = True
    save_json("data/seen_links.json", seen)
    send_to_zapier({"text": full_text})
    return "ok"

def run_daily_image():
    """
    Post newest not-yet-seen image; cache image URLs to avoid repeats.
    """
    seen = load_json("data/seen_links.json", {}) or {}
    cache = load_json("data/image_cache.json", {}) or {}  # {url: true}
    imgs = fetch_images()

    for im in imgs:
        if im["url"] in seen or cache.get(im["url"]):
            continue

        title = md_escape(im["title"] or "Space Image")
        date = im["published"].strftime("%b %d, %Y")
        caption = f"📸 *Red Horizon Daily Image*\n{title}\n_{date}_\n#Space #Mars #RedHorizon"

        buttons = []
        if im.get("source_link"):
            buttons.append(("Source", im["source_link"]))
        else:
            buttons.append(("Open", im["url"]))

        if send_telegram_image(im["url"], caption, buttons):
            seen[im["url"]] = True
            cache[im["url"]] = True
            save_json("data/seen_links.json", seen)
            save_json("data/image_cache.json", cache)
            return "ok"

    print("[IMAGE] no-post")
    return "no-post"

def run_welcome_message():
    msg = (
        "👋 *Welcome to Red Horizon!*\n"
        "Your daily hub for SpaceX, Starship, Mars exploration & culture.\n\n"
        "What to expect:\n"
        "• 🚨 Breaking (only when it truly breaks)\n"
        "• 📰 Daily Digest (5 hand-picked stories)\n"
        "• 📸 Daily Image\n"
        "• 🎭 Culture Spotlights (books, games, movies/TV)\n\n"
        "Follow on X: @RedHorizonHub"
    )
    send_telegram_message(msg)
    return "ok"

# ---------------------------
# Culture blurbs (custom from review/wiki)
# ---------------------------

DEFAULT_CULTURE_IMAGE = "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e7/NGC_1309.jpg/640px-NGC_1309.jpg"

def _wiki_summary_from_link(wiki_link: str) -> str:
    """
    Use Wikipedia REST Summary API for clean 1–3 sentence extracts.
    """
    try:
        p = urlparse(wiki_link)
        if "wikipedia.org" not in p.netloc.lower():
            return ""
        # Title after /wiki/
        path = p.path
        if "/wiki/" not in path:
            return ""
        title = path.split("/wiki/")[-1]
        title = unquote(title)
        api = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
        hdrs = {"Accept": "application/json"}
        r = requests.get(api, headers=hdrs, timeout=8)
        if r.status_code != 200:
            return ""
        data = r.json()
        extract = data.get("extract", "").strip()
        if not extract:
            return ""
        # Return first two sentences
        parts = re.split(r"(?<=[\.!?])\s+", extract)
        return " ".join(parts[:2]).strip()
    except Exception:
        return ""

def _html_first_paragraph(url: str) -> str:
    """
    Fallback: fetch HTML and try to extract first paragraph text,
    then return first two sentences.
    """
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code != 200:
            return ""
        html = r.text
        # naive extract first <p> ... </p>
        m = re.search(r"<p[^>]*>(.*?)</p>", html, re.I | re.S)
        if not m:
            return ""
        text = re.sub(r"<[^>]+>", " ", m.group(1))  # strip tags
        text = re.sub(r"\s+", " ", text).strip()
        parts = re.split(r"(?<=[\.!?])\s+", text)
        return " ".join(parts[:2]).strip()
    except Exception:
        return ""

def fetch_custom_blurb(meta: dict, kind: str) -> str:
    """
    Try, in order:
      1) Explicit 'blurb' in JSON item
      2) Wikipedia Summary API (if wiki_link present)
      3) First paragraph from review_link / wiki_link HTML
    Returns a 2-sentence string (best effort).
    """
    # 1) explicit blurb
    b = (meta.get("blurb") or "").strip()
    if b:
        return b

    # 2) Wikipedia summary API
    wiki = meta.get("wiki_link")
    if wiki:
        s = _wiki_summary_from_link(wiki)
        if s:
            return s

    # 3) First paragraph from review/wiki HTML
    for url in [meta.get("review_link"), wiki]:
        if not url:
            continue
        s = _html_first_paragraph(url)
        if s:
            return s

    # last-resort short line
    title = meta.get("title", "this work")
    if kind == "book":
        return f"{title} is a noted space-related read that resonates with Red Horizon’s audience."
    if kind == "game":
        return f"{title} offers an engaging space experience for explorers and sim fans."
    # screen
    return f"{title} captures the awe and peril of space in a way that sticks with you."

# ---------------------------
# Culture: Books, Games, Screen (Movies/TV/Docs)
# ---------------------------

# src/tasks.py
import os
import re
import json
import html
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse

from src.feeds import (
    fetch_rss_news,                   # unchanged
    fetch_images,                     # unchanged
    extract_image_from_entry          # used in breaking (unchanged)
)

# ---------- Simple JSON helpers ----------

def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        return default

def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ---------- Telegram helpers ----------

def _tg_send_message(text, buttons=None, disable_preview=False):
    bot = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHANNEL_ID", "")
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

# ---------- Tiny HTML -> first sentences scraper (Wiki-safe) ----------

_PTAG = re.compile(r"<p[\s>].*?</p>", re.I | re.S)
_TAGS = re.compile(r"<.*?>", re.S)
_WS = re.compile(r"\s+")

def _first_sentences_from_wiki(url, max_sentences=2):
    try:
        if not url:
            return ""
        # Only attempt on wiki-ish domains
        host = urlparse(url).netloc.lower()
        if "wikipedia" not in host and "fandom" not in host and "wikidata" not in host and "wiki" not in host:
            return ""
        html_raw = requests.get(url, timeout=12).text
        # Grab first <p> that has decent text
        for m in _PTAG.finditer(html_raw):
            txt = _TAGS.sub(" ", m.group())
            txt = html.unescape(_WS.sub(" ", txt)).strip()
            if len(txt) > 60:
                # sentence split (naive)
                parts = re.split(r"(?<=[.!?])\s+", txt)
                out = " ".join(parts[:max_sentences]).strip()
                return out
        return ""
    except Exception:
        return ""

# ---------- CULTURE SPOTLIGHT ----------

DATA_DIR = "data"
BOOKS_FILE  = os.path.join(DATA_DIR, "books.json")
GAMES_FILE  = os.path.join(DATA_DIR, "games.json")
MOVIES_FILE = os.path.join(DATA_DIR, "movies.json")
STATE_FILE  = os.path.join(DATA_DIR, "culture_state.json")

def _ensure_culture_state():
    st = _load_json(STATE_FILE, {
        "cycle": "book",   # book -> game -> movie -> book ...
        "book_index": 0,
        "game_index": 0,
        "movie_index": 0
    })
    return st

def _advance_cycle(cycle):
    return {"book":"game", "game":"movie", "movie":"book"}[cycle]

def _sanitize(text):
    return html.escape(text or "").replace("&amp;", "&")

def _blurb_for_item(item):
    """
    Priority:
      1) explicit blurb in JSON
      2) wiki_link first paragraph (1–2 sentences)
      3) summary in JSON
      4) safe fallback
    """
    if item.get("blurb"):
        return item["blurb"]
    wiki = item.get("wiki_link") or item.get("review_link")
    wiki_blurb = _first_sentences_from_wiki(wiki, max_sentences=2)
    if wiki_blurb:
        return wiki_blurb
    if item.get("summary"):
        return item["summary"]
    # fallback
    title = item.get("title", "This work")
    return f"{title} is a standout entry in space culture—highly recommended for Red Horizon readers."

def _buttons_for_item(item, kind):
    buttons = []
    # prioritize 'review' + 'wiki' + 'official' + 'trailer'
    if item.get("review_link"):
        buttons.append({"text": "📖 Review", "url": item["review_link"]})
    if item.get("wiki_link"):
        buttons.append({"text": "🌐 Wiki", "url": item["wiki_link"]})
    if kind in ("game", "movie") and item.get("official"):
        buttons.append({"text": "🏷 Official", "url": item["official"]})
    if kind in ("game", "movie") and item.get("trailer"):
        buttons.append({"text": "🎬 Trailer", "url": item["trailer"]})
    # format as Telegram inline row
    return [[b for b in buttons]] if buttons else None

def _hashtags_for(kind):
    base = "#RedHorizon #Space"
    if kind == "book":
        return f"{base} #Books #SciFi"
    if kind == "game":
        return f"{base} #Gaming #SpaceGames"
    return f"{base} #Films #TV #SpaceFilms"

def run_culture_spotlight():
    """
    Rotates book → game → movie (persisted). You can override in the
    workflow by setting env CULTURE_FORCE to 'book'|'game'|'movie'.
    """
    state = _ensure_culture_state()
    force = (os.getenv("CULTURE_FORCE") or "").strip().lower()
    cycle = force if force in ("book","game","movie") else state["cycle"]

    if cycle == "book":
        items = _load_json(BOOKS_FILE, [])
        idx_key = "book_index"
        icon = "📚"
        title_line = "Book Spotlight"
    elif cycle == "game":
        items = _load_json(GAMES_FILE, [])
        idx_key = "game_index"
        icon = "🎮"
        title_line = "Game Spotlight"
    else:
        items = _load_json(MOVIES_FILE, [])
        idx_key = "movie_index"
        icon = "🎬"
        title_line = "Culture Spotlight"

    if not items:
        _tg_send_message("⚠️ Culture list is empty.")
        return "empty"

    i = state[idx_key] % len(items)
    item = items[i]

    title = _sanitize(item.get("title"))
    author = _sanitize(item.get("author") or item.get("creator") or item.get("studio") or "")
    blurb = _sanitize(_blurb_for_item(item))
    hashtags = _hashtags_for("book" if cycle=="book" else ("game" if cycle=="game" else "movie"))
    header = f"<b>{icon} {title_line}</b>\n<i>{title}</i>{(' — ' + author) if author else ''}\n\n{blurb}\n\n{hashtags}"

    buttons = _buttons_for_item(item, cycle)

    # Prefer cover image
    cover = item.get("cover")
    if cover:
        _tg_send_photo(cover, header, buttons)
    else:
        _tg_send_message(header, buttons)

    # advance & persist (unless forced)
    if not force:
        state[idx_key] = (state[idx_key] + 1) % len(items)
        state["cycle"] = _advance_cycle(cycle)
        _save_json(STATE_FILE, state)

    return "ok"


