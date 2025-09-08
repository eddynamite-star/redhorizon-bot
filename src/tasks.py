# src/tasks.py
import os
import re
import json
import html
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse, unquote

from src.feeds import (
    fetch_rss_news,
    fetch_images,
    DEFAULT_TAGS,
)

# ───────────────────────────────────────────────────────────────────────────────
# JSON helpers
# ───────────────────────────────────────────────────────────────────────────────

def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ───────────────────────────────────────────────────────────────────────────────
# Small text helpers
# ───────────────────────────────────────────────────────────────────────────────

def md_escape(text: str) -> str:
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
    t = (text or "").strip()
    parts = re.split(r"(?<=[\.!?])\s+", t)
    cand = parts[0].strip() if parts else ""
    if (not cand) or len(cand) < 20 or "credit" in cand.lower() or "image:" in cand.lower():
        words = (fallback_title or "").strip().split()
        cand = " ".join(words[:max_words]).strip()
    return cand

# ───────────────────────────────────────────────────────────────────────────────
# Telegram helpers (ALWAYS build proper InlineKeyboardButton objects)
# ───────────────────────────────────────────────────────────────────────────────

def _btns(*rows):
    """
    Accepts rows like:
      _btns([("Open", "https://..."), ("Discuss", "https://...")])
    Returns Telegram-ready reply_markup dict.
    """
    if not rows:
        return None
    keyboard = []
    for row in rows:
        line = []
        for item in row:
            if isinstance(item, dict) and "text" in item and "url" in item:
                line.append(item)
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                line.append({"text": str(item[0]), "url": str(item[1])})
            else:
                # ignore malformed
                continue
        if line:
            keyboard.append(line)
    return {"inline_keyboard": keyboard} if keyboard else None

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
        "parse_mode": "Markdown",
        "disable_web_page_preview": disable_preview,
    }
    if buttons:
        payload["reply_markup"] = _btns(*buttons) if isinstance(buttons[0], (list, tuple, dict)) else _btns(buttons)
    r = requests.post(url, json=payload, timeout=15)
    ok = (r.status_code == 200)
    if not ok:
        print("[TG] sendMessage error:", r.text)
    return ok

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
        "parse_mode": "Markdown",
    }
    if buttons:
        payload["reply_markup"] = _btns(*buttons) if isinstance(buttons[0], (list, tuple, dict)) else _btns(buttons)
    r = requests.post(url, json=payload, timeout=20)
    ok = (r.status_code == 200)
    if not ok:
        print("[TG] sendPhoto error:", r.text)
    return ok

def _send_text_or_photo(image_url, caption, buttons=None):
    if image_url:
        if _tg_send_photo(image_url, caption, buttons):
            return True
    return _tg_send_message(caption, buttons)

def _zap(data):
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

DISCUSS_URL = "https://x.com/RedHorizonHub"

# ───────────────────────────────────────────────────────────────────────────────
# Breaking news
# ───────────────────────────────────────────────────────────────────────────────

def run_breaking_news():
    """
    Post up to 2 truly breaking items (<= 15m old), preferring SpaceX-centric
    stories and higher scores. Bold title, quick read, buttons, include image if any.
    """
    seen = _load_json("data/seen_links.json", {})
    now = datetime.utcnow()

    items = [a for a in fetch_rss_news()[:60] if a.get("is_breaking")]
    # Prefer SpaceXish + score + recency
    def sx_bias(a):
        blob = (a.get("title","") + a.get("summary","")).lower()
        sx = any(k in blob for k in ["spacex","starship","falcon","raptor","starbase"])
        return (1 if sx else 0, a.get("score",0), a.get("published", now))
    items.sort(key=sx_bias, reverse=True)

    posted = 0
    for a in items[:4]:  # allow 4 candidates, still cap actual posts to 2
        if posted >= 2:
            break
        if a["link"] in seen:
            continue

        title = md_escape(a["title"])
        quick = md_escape(first_sentence(a["summary"], a["title"]))
        body = (
            f"🚨 *BREAKING* — {title}\n\n"
            f"_Quick read:_ {quick}\n"
            f"Source: {md_escape(a['source'])}\n"
            f"#Breaking #SpaceX #Starship #RedHorizon"
        )
        buttons = [[("Open", a["link"]), ("Discuss on X", DISCUSS_URL)]]

        ok = _send_text_or_photo(a.get("image"), body, buttons)
        if ok:
            seen[a["link"]] = True
            posted += 1

    _save_json("data/seen_links.json", seen)
    return "ok" if posted else "no-post"

# ───────────────────────────────────────────────────────────────────────────────
# Daily digest (5 stories, top story image)
# ───────────────────────────────────────────────────────────────────────────────

def run_daily_digest():
    seen = _load_json("data/seen_links.json", {})
    now = datetime.utcnow()

    all_items = fetch_rss_news()[:120]
    fresh24 = [a for a in all_items if (now - a["published"]) <= timedelta(hours=24)]

    # Bias to SpaceX first
    def is_sx(a):
        blob = (a["title"] + a["summary"]).lower()
        return any(k in blob for k in ["spacex","starship","falcon","elon","raptor","starbase"])
    spacexy = [a for a in fresh24 if is_sx(a)]
    others  = [a for a in fresh24 if not is_sx(a)]

    spacexy.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)
    others.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)

    # Build 5 with reddit cap=1 total
    final, reddit_count = [], 0
    def maybe_add(it):
        nonlocal reddit_count
        if "reddit.com" in it.get("source_host",""):
            if reddit_count >= 1:
                return
            reddit_count += 1
        final.append(it)

    for it in spacexy:
        if len(final) >= 3:
            break
        maybe_add(it)
    for it in others:
        if len(final) >= 5:
            break
        maybe_add(it)

    # Fallback to 72h if too few
    if len(final) < 3:
        fresh72 = [a for a in all_items if (now - a["published"]) <= timedelta(hours=72)]
        spacexy = [a for a in fresh72 if is_sx(a)]
        others  = [a for a in fresh72 if not is_sx(a)]
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

    head = f"🚀 *Red Horizon Daily Digest — {now.strftime('%b %d, %Y')}*"
    blocks = [head, ""]
    for a in final:
        title = md_escape(a["title"])
        quick = md_escape(first_sentence(a["summary"], a["title"]))
        clock = a["published"].strftime("%H:%M")
        blocks.append(
            f"• *{title}* — _{md_escape(a['source'])}_ · 🕒 {clock} UTC\n"
            f"  _Quick read:_ {quick}\n"
            f"  ➡️ [Open]({a['link']})\n"
        )
    extra_tags = " ".join(DEFAULT_TAGS[:3])
    blocks.append(f"Follow on X: @RedHorizonHub\n#Daily {extra_tags}")

    top = final[0]
    head_only = head
    list_without_head = "\n".join(blocks[2:])  # skip header (already on image caption)

    # Post: top image (if any) with header + buttons, then the list
    top_buttons = [[("Open top story", top["link"]), ("Discuss on X", DISCUSS_URL)]]
    if top.get("image"):
        _tg_send_photo(top["image"], head_only, top_buttons)
        _tg_send_message(list_without_head, buttons=[[("Discuss on X", DISCUSS_URL)]], disable_preview=True)
    else:
        _tg_send_message("\n".join(blocks), buttons=[[("Discuss on X", DISCUSS_URL)]], disable_preview=True)

    for a in final:
        seen[a["link"]] = True
    _save_json("data/seen_links.json", seen)
    _zap({"text": "\n".join(blocks)})
    return "ok"

# ───────────────────────────────────────────────────────────────────────────────
# Daily image (with cache & no-repeat)
# ───────────────────────────────────────────────────────────────────────────────

def run_daily_image():
    seen = _load_json("data/seen_links.json", {})
    cache = _load_json("data/image_cache.json", {})  # {url: true}
    imgs = fetch_images()

    for im in imgs:
        if im["url"] in seen or cache.get(im["url"]):
            continue

        title = md_escape(im["title"] or "Space Image")
        date  = im["published"].strftime("%b %d, %Y")
        caption = f"📸 *Sky Highlight*\n{title}\n_{date}_\n#Space #Mars #RedHorizon"

        if _tg_send_photo(im["url"], caption, buttons=[[("Source", im.get("source_link") or im["url"])]]):
            seen[im["url"]] = True
            cache[im["url"]] = True
            _save_json("data/seen_links.json", seen)
            _save_json("data/image_cache.json", cache)
            return "ok"

    print("[IMAGE] no-post")
    return "no-post"

# ───────────────────────────────────────────────────────────────────────────────
# Welcome
# ───────────────────────────────────────────────────────────────────────────────

def run_welcome_message():
    msg = (
        "👋 *Welcome to Red Horizon!*\n"
        "Your daily hub for SpaceX, Starship, Mars exploration & culture.\n\n"
        "What to expect:\n"
        "• 🚨 Breaking (only when it truly breaks)\n"
        "• 📰 Daily Digest (5 hand-picked stories)\n"
        "• 📸 Sky Highlight (image of the day)\n"
        "• 🎭 Culture Spotlights (books, games, movies/TV)\n\n"
        "Follow on X: @RedHorizonHub"
    )
    _tg_send_message(msg, disable_preview=True)
    return "ok"

# ───────────────────────────────────────────────────────────────────────────────
# Culture rotation (book → game → movie) with AUTO state-initialisation
# ───────────────────────────────────────────────────────────────────────────────

DATA_DIR    = "data"
BOOKS_FILE  = os.path.join(DATA_DIR, "books.json")
GAMES_FILE  = os.path.join(DATA_DIR, "games.json")
MOVIES_FILE = os.path.join(DATA_DIR, "movies.json")
STATE_FILE  = os.path.join(DATA_DIR, "culture_state.json")

def _ensure_culture_state():
    st = _load_json(STATE_FILE, None)
    if not isinstance(st, dict) or not all(k in (st or {}) for k in ("cycle","book_index","game_index","movie_index")):
        st = {"cycle":"book","book_index":0,"game_index":0,"movie_index":0}
        _save_json(STATE_FILE, st)
    return st

def _advance_cycle(cycle):
    return {"book":"game","game":"movie","movie":"book"}.get(cycle, "book")

# very small OG:image finder
_OG_IMG = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I)

def _find_image_from_page(url):
    try:
        if not url:
            return None
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        if r.status_code != 200:
            return None
        m = _OG_IMG.search(r.text)
        return m.group(1) if m else None
    except Exception:
        return None

def _best_cover(item):
    # 1) explicit cover
    if item.get("cover"):
        return item["cover"]
    # 2) wiki / review / official / trailer OG
    for k in ("wiki_link","review_link","official","trailer"):
        u = item.get(k)
        img = _find_image_from_page(u) if u else None
        if img:
            return img
    # 3) last resort NASA placeholder (won't fail sendPhoto)
    return "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e7/NGC_1309.jpg/640px-NGC_1309.jpg"

_P_TAG = re.compile(r"<p[^>]*>(.*?)</p>", re.I | re.S)
_STRIP = re.compile(r"<[^>]+>")
_SENT  = re.compile(r"(?<=[.!?])\s+")

def _blurb_from_wiki(url, max_sent=2):
    try:
        if not url or "wikipedia.org" not in url:
            return ""
        path = urlparse(url).path
        if "/wiki/" not in path:
            return ""
        title = unquote(path.split("/wiki/")[-1])
        api = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
        r = requests.get(api, headers={"Accept":"application/json"}, timeout=8)
        if r.status_code != 200:
            return ""
        extract = (r.json().get("extract") or "").strip()
        if not extract:
            return ""
        return " ".join(_SENT.split(extract)[:max_sent]).strip()
    except Exception:
        return ""

def _blurb_for_item(item, fallback_kind="screen"):
    if item.get("blurb"):
        return item["blurb"]
    b = _blurb_from_wiki(item.get("wiki_link"))
    if b:
        return b
    for k in ("review_link","official"):
        u = item.get(k)
        if not u: 
            continue
        try:
            r = requests.get(u, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
            if r.status_code == 200:
                m = _P_TAG.search(r.text)
                if m:
                    text = _STRIP.sub(" ", m.group(1))
                    text = re.sub(r"\s+", " ", text).strip()
                    parts = _SENT.split(text)
                    if parts:
                        return " ".join(parts[:2]).strip()
        except Exception:
            pass
    title = item.get("title","This work")
    if fallback_kind == "book":
        return f"{title} is a celebrated space read."
    if fallback_kind == "game":
        return f"{title} delivers an engaging space experience."
    return f"{title} captures the wonder of space."

def _hash_for(kind):
    base = "#RedHorizon #Space"
    if kind == "book":
        return f"{base} #Books #SciFi"
    if kind == "game":
        return f"{base} #Gaming #SpaceGames"
    return f"{base} #Films #TV #SpaceFilms"

def run_culture_daily():
    """
    Rotates book -> game -> movie automatically. If CULTURE_FORCE is set
    to 'book'|'game'|'movie', uses that for this run (state still advanced).
    """
    st = _ensure_culture_state()
    force = (os.getenv("CULTURE_FORCE") or "").strip().lower()
    cycle = force if force in ("book","game","movie") else st.get("cycle","book")

    if cycle == "book":
        items = _load_json(BOOKS_FILE, [])
        idx_key, icon, title_line, kind = "book_index", "📚", "Book Spotlight", "book"
    elif cycle == "game":
        items = _load_json(GAMES_FILE, [])
        idx_key, icon, title_line, kind = "game_index", "🎮", "Game Spotlight", "game"
    else:
        items = _load_json(MOVIES_FILE, [])
        idx_key, icon, title_line, kind = "movie_index", "🎬", "Screen Spotlight", "screen"

    if not items:
        _tg_send_message("⚠️ Culture list is empty.")
        return "empty"

    i = st.get(idx_key, 0) % len(items)
    item = items[i]

    title  = md_escape(item.get("title",""))
    author = md_escape(item.get("author") or item.get("creator") or item.get("studio") or "")
    blurb  = md_escape(_blurb_for_item(item, fallback_kind=kind))
    tags   = _hash_for(kind)
    header = f"*{icon} {title_line}*\n_{title}_{(' — ' + author) if author else ''}\n\n{blurb}\n\n{tags}"

    # buttons row
    row = []
    if item.get("review_link"): row.append(("📖 Review", item["review_link"]))
    if item.get("wiki_link"):   row.append(("🌐 Wiki", item["wiki_link"]))
    if item.get("official"):    row.append(("🏷 Official", item["official"]))
    if item.get("trailer"):     row.append(("🎬 Trailer", item["trailer"]))
    buttons = [row] if row else None

    # image (always ensure something valid)
    cover = _best_cover(item)
    _send_text_or_photo(cover, header, buttons)

    # advance and persist
    st[idx_key] = (i + 1) % len(items)
    st["cycle"] = _advance_cycle(cycle)
    _save_json(STATE_FILE, st)
    return "ok"
