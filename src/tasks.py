# src/tasks.py
import os
import re
import json
import html
import hashlib
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse, unquote

from src.feeds import (
    fetch_rss_news,
    fetch_images,
    DEFAULT_TAGS,
    extract_image_from_entry,  # used by breaking / extra fallbacks
)

# =============================================================================
# Small utilities: JSON I/O, HTML helpers, hashing, first sentence
# =============================================================================

DATA_DIR = "data"

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

def _esc(text: str) -> str:
    """Escape to safe HTML for Telegram parse_mode=HTML."""
    return html.escape(text or "").replace("&amp;", "&")

def _first_sentence(text: str, fallback_title: str, max_words: int = 26) -> str:
    t = (text or "").strip()
    parts = re.split(r"(?<=[\.!?])\s+", t)
    cand = parts[0].strip() if parts else ""
    bad = ("credit", "image:", "photo:", "caption:")
    if (not cand) or (len(cand) < 20) or any(b in cand.lower() for b in bad):
        words = (fallback_title or "").strip().split()
        cand = " ".join(words[:max_words]).strip()
    return cand

def _sha1_key(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()[:16]

# =============================================================================
# Telegram senders
# =============================================================================

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
    r = requests.post(url, json=payload, timeout=15)
    if r.status_code != 200:
        print("[TG] sendMessage error:", r.text)
        return False
    return True

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
    r = requests.post(url, json=payload, timeout=20)
    if r.status_code != 200:
        print("[TG] sendPhoto error:", r.text)
        return False
    return True

def _zapier_send(data):
    hook = os.getenv("ZAPIER_HOOK_URL")
    if not hook:
        return True
    try:
        r = requests.post(hook, json=data, timeout=10)
        ok = r.status_code in (200, 201)
        if not ok:
            print("[Zapier] error:", r.status_code, r.text)
        return ok
    except Exception as e:
        print("[Zapier] exception:", e)
        return False

DISCUSS_URL = "https://x.com/RedHorizonHub"

# =============================================================================
# BREAKING NEWS (unchanged logic, still solid)
# =============================================================================

def run_breaking_news():
    """
    Post up to 2 breaking items (<= 15 min old), with bold title, first-sentence quick read,
    image (if any), inline buttons. Prefers SpaceX-ish + higher score.
    """
    seen = _load_json(os.path.join(DATA_DIR, "seen_links.json"), {}) or {}
    now = datetime.utcnow()

    items = [a for a in fetch_rss_news()[:40] if a.get("is_breaking")]
    # prefer SpaceX + score + recency
    items.sort(
        key=lambda x: (
            any(k in (x.get("title","") + x.get("summary","")).lower()
                for k in ["spacex","starship","falcon","raptor","starbase"]),
            x.get("score", 0),
            x.get("published", now),
        ),
        reverse=True,
    )

    posted = 0
    for a in items[:2]:
        if a["link"] in seen:
            continue

        title = _esc(a["title"])
        quick = _esc(_first_sentence(a["summary"], a["title"]))
        body = (
            f"🚨 <b>BREAKING</b> — <b>{title}</b>\n\n"
            f"<i>Quick read:</i> {quick}\n\n"
            f"Read more on {_esc(a['source'])}\n"
            f"#Breaking #SpaceX #Starship #RedHorizon"
        )
        buttons = [{"text": "Open", "url": a["link"]}, {"text": "Discuss on X", "url": DISCUSS_URL}]

        ok = False
        if a.get("image"):
            ok = _tg_send_photo(a["image"], body, [buttons])
        if not ok:
            ok = _tg_send_message(body, [buttons])

        if ok:
            seen[a["link"]] = True
            posted += 1

    _save_json(os.path.join(DATA_DIR, "seen_links.json"), seen)
    return "ok" if posted else "no-post"

# =============================================================================
# DAILY DIGEST (unchanged from your good version)
# =============================================================================

def run_daily_digest():
    seen = _load_json(os.path.join(DATA_DIR, "seen_links.json"), {}) or {}
    now = datetime.utcnow()

    all_items = fetch_rss_news()[:80]
    fresh = [a for a in all_items if (now - a["published"]) <= timedelta(hours=24)]

    spacexy = [a for a in fresh if any(k in (a["title"] + a["summary"]).lower()
                                       for k in ["spacex","starship","falcon","elon","raptor","starbase"])]
    others = [a for a in fresh if a not in spacexy]

    spacexy.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)
    others.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)

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

    for it in spacexy:
        if len(final) >= 3:
            break
        maybe_add(it)
    for it in others:
        if len(final) >= 5:
            break
        maybe_add(it)

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

    head = f"🚀 <b>Red Horizon Daily Digest — {now.strftime('%b %d, %Y')}</b>"
    blocks = [head, ""]

    top = final[0]
    for a in final:
        title = _esc(a["title"])
        quick = _esc(_first_sentence(a["summary"], a["title"]))
        clock = a["published"].strftime("%H:%M")
        blocks.append(
            f"• <b>{title}</b> — <i>{_esc(a['source'])}</i> · 🕒 {clock} UTC\n"
            f"  <i>Quick read:</i> {quick}\n"
            f"  ➡️ <a href=\"{a['link']}\">Open</a>\n"
        )

    tags = " ".join(DEFAULT_TAGS[:3])
    blocks.append(f"Follow on X: @RedHorizonHub\n#Daily {tags}")
    list_text = "\n".join(blocks)

    # Send with top image if available
    buttons = [{"text": "Open top story", "url": top["link"]}, {"text": "Discuss on X", "url": DISCUSS_URL}]
    if top.get("image"):
        _tg_send_photo(top["image"], head, [buttons])
        _tg_send_message("\n".join(blocks[2:]))  # skip duplicate header
    else:
        _tg_send_message(list_text, [buttons])

    for a in final:
        seen[a["link"]] = True
    _save_json(os.path.join(DATA_DIR, "seen_links.json"), seen)
    _zapier_send({"text": list_text})
    return "ok"

# =============================================================================
# DAILY IMAGE — de-dup by URL and title-hash, persist cache
# =============================================================================

def run_daily_image():
    seen = _load_json(os.path.join(DATA_DIR, "seen_links.json"), {}) or {}
    cache = _load_json(os.path.join(DATA_DIR, "image_cache.json"), {}) or {}  # { "url:1": true, "th:xxxx": true }
    imgs = fetch_images()

    for im in imgs:
        url = im["url"]
        title = im.get("title") or ""
        key_url = f"url:{url}"
        key_th = f"th:{_sha1_key(title, im.get('source_link') or '')}"

        if key_url in cache or key_th in cache:
            continue

        title_s = _esc(title or "Space Image")
        date = im["published"].strftime("%b %d, %Y")
        caption = f"📸 <b>Sky Highlight</b>\n{title_s}\n<i>{date}</i>\n#Space #Mars #RedHorizon"

        buttons = []
        if im.get("source_link"):
            buttons.append({"text": "Source", "url": im["source_link"]})
        else:
            buttons.append({"text": "Open", "url": im["url"]})

        if _tg_send_photo(url, caption, [buttons]):
            seen[url] = True
            cache[key_url] = True
            cache[key_th] = True
            _save_json(os.path.join(DATA_DIR, "seen_links.json"), seen)
            _save_json(os.path.join(DATA_DIR, "image_cache.json"), cache)
            return "ok"

    print("[IMAGE] no-post")
    return "no-post"

# =============================================================================
# WELCOME
# =============================================================================

def run_welcome_message():
    msg = (
        "👋 <b>Welcome to Red Horizon!</b>\n"
        "Your daily hub for SpaceX, Starship, Mars exploration & culture.\n\n"
        "What to expect:\n"
        "• 🚨 Breaking (only when it truly breaks)\n"
        "• 📰 Daily Digest (5 hand-picked stories)\n"
        "• 📸 Sky Highlight (image)\n"
        "• 🎭 Culture Spotlights (books, games, movies/TV)\n\n"
        "Follow on X: @RedHorizonHub"
    )
    _tg_send_message(msg, disable_preview=True)
    return "ok"

# =============================================================================
# CULTURE: Book → Game → Screen (always with an image)
# =============================================================================

BOOKS_FILE   = os.path.join(DATA_DIR, "books.json")
GAMES_FILE   = os.path.join(DATA_DIR, "games.json")
MOVIES_FILE  = os.path.join(DATA_DIR, "movies.json")
STATE_FILE   = os.path.join(DATA_DIR, "media_cycle.json")
DEFAULT_CULTURE_IMAGE = "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e7/NGC_1309.jpg/1024px-NGC_1309.jpg"

# --- tiny scrapers ---

_PTAG = re.compile(r"<p[\s>].*?</p>", re.I | re.S)
_TAGS = re.compile(r"<.*?>", re.S)
_WS   = re.compile(r"\s+")

def _first_paragraph(html_text: str) -> str:
    for m in _PTAG.finditer(html_text or ""):
        txt = _TAGS.sub(" ", m.group())
        txt = html.unescape(_WS.sub(" ", txt)).strip()
        if len(txt) > 60:
            parts = re.split(r"(?<=[.!?])\s+", txt)
            return " ".join(parts[:2]).strip()
    return ""

def _wiki_summary_extract(wiki_url: str) -> (str, str):
    """
    Returns (two_sentence_blurb, thumbnail_url) from Wikipedia REST Summary API,
    or ("", "") if not available.
    """
    try:
        if not wiki_url:
            return "", ""
        p = urlparse(wiki_url)
        if "wikipedia.org" not in p.netloc.lower():
            return "", ""
        if "/wiki/" not in p.path:
            return "", ""
        title = unquote(p.path.split("/wiki/")[-1])
        api = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
        r = requests.get(api, headers={"Accept": "application/json"}, timeout=10)
        if r.status_code != 200:
            return "", ""
        data = r.json()
        extract = (data.get("extract") or "").strip()
        thumb = (data.get("thumbnail") or {}).get("source") or ""
        if extract:
            parts = re.split(r"(?<=[.!?])\s+", extract)
            text2 = " ".join(parts[:2]).strip()
        else:
            text2 = ""
        return text2, thumb
    except Exception:
        return "", ""

def _opengraph_image(url: str) -> str:
    try:
        if not url:
            return ""
        html_text = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10).text
        m = re.search(r'property=["\']og:image["\']\s*content=["\']([^"\']+)["\']', html_text, re.I)
        if m:
            return m.group(1)
        m2 = re.search(r'content=["\']([^"\']+)["\']\s*property=["\']og:image["\']', html_text, re.I)
        return m2.group(1) if m2 else ""
    except Exception:
        return ""

def _build_buttons(item, kind):
    row = []
    if item.get("review_link"):
        row.append({"text": "📖 Review", "url": item["review_link"]})
    if item.get("wiki_link"):
        row.append({"text": "🌐 Wiki", "url": item["wiki_link"]})
    if kind in ("game", "movie") and item.get("official"):
        row.append({"text": "🏷 Official", "url": item["official"]})
    if kind in ("game", "movie") and item.get("trailer"):
        row.append({"text": "🎬 Trailer", "url": item["trailer"]})
    return [row] if row else None

def _blurb_for_item(item, kind):
    if item.get("blurb"):
        return item["blurb"]
    # Wikipedia first (cleanest)
    b, _ = _wiki_summary_extract(item.get("wiki_link", ""))
    if b:
        return b
    # Try first <p> of review/wiki as fallback
    for url in [item.get("review_link"), item.get("wiki_link")]:
        if not url:
            continue
        try:
            t = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=10).text
            p = _first_paragraph(t)
            if p:
                return p
        except Exception:
            pass
    # last fallback
    title = item.get("title", "This work")
    if kind == "book":
        return f"{title} is a landmark space read often recommended to Red Horizon readers."
    if kind == "game":
        return f"{title} offers a compelling space experience for explorers and sim fans."
    return f"{title} captures the wonder and challenge of space on screen."

def _image_for_item(item, kind) -> str:
    # 1) JSON cover if provided
    if item.get("cover"):
        return item["cover"]
    # 2) Wikipedia thumbnail
    _, thumb = _wiki_summary_extract(item.get("wiki_link", ""))
    if thumb:
        return thumb
    # 3) OpenGraph from review/official/wiki order
    for url in (item.get("review_link"), item.get("official"), item.get("wiki_link")):
        img = _opengraph_image(url)
        if img:
            return img
    # 4) Hard fallback
    return DEFAULT_CULTURE_IMAGE

def _ensure_cycle():
    st = _load_json(STATE_FILE, {
        "cycle": "book",   # book -> game -> movie -> book ...
        "book_index": 0,
        "game_index": 0,
        "movie_index": 0
    })
    return st

def _advance(cycle):
    return {"book":"game","game":"movie","movie":"book"}[cycle]

def _hashtags_for(kind):
    base = "#RedHorizon #Space"
    if kind == "book":
        return f"{base} #Books #SciFi"
    if kind == "game":
        return f"{base} #Gaming #SpaceGames"
    return f"{base} #Films #TV #SpaceFilms"

def run_culture_daily():
    """
    Rotates book → game → movie (persisted in data/media_cycle.json).
    Always posts a photo (guaranteed via fallbacks).
    """
    state = _ensure_cycle()
    force = (os.getenv("CULTURE_FORCE") or "").strip().lower()
    cycle = force if force in ("book","game","movie") else state["cycle"]

    if cycle == "book":
        items = _load_json(BOOKS_FILE, [])
        idx_key = "book_index"; icon = "📚"; title_line = "Book Spotlight"
    elif cycle == "game":
        items = _load_json(GAMES_FILE, [])
        idx_key = "game_index"; icon = "🎮"; title_line = "Game Spotlight"
    else:
        items = _load_json(MOVIES_FILE, [])
        idx_key = "movie_index"; icon = "🎬"; title_line = "Screen Spotlight"

    if not items:
        _tg_send_message("⚠️ Culture list is empty.", disable_preview=True)
        return "empty"

    i = state[idx_key] % len(items)
    item = items[i]
    kind = "book" if cycle == "book" else ("game" if cycle == "game" else "movie")

    title = _esc(item.get("title"))
    author = _esc(item.get("author") or item.get("creator") or item.get("studio") or "")
    blurb = _esc(_blurb_for_item(item, kind))
    hashtags = _hashtags_for(kind)

    header = f"<b>{icon} {title_line}</b>\n<i>{title}</i>{(' — ' + author) if author else ''}\n\n{blurb}\n\n{hashtags}"
    buttons = _build_buttons(item, kind)
    photo = _image_for_item(item, kind)

    # Always send as photo (we have guaranteed fallback)
    ok = _tg_send_photo(photo, header, buttons)
    if not ok:
        # rare cases where Telegram rejects an image URL; fallback to default image then plain text
        if photo != DEFAULT_CULTURE_IMAGE:
            _tg_send_photo(DEFAULT_CULTURE_IMAGE, header, buttons)
        else:
            _tg_send_message(header, buttons)

    # advance & persist (unless forced)
    if not force:
        state[idx_key] = (state[idx_key] + 1) % len(items)
        state["cycle"] = _advance(cycle)
        _save_json(STATE_FILE, state)

    return "ok"
