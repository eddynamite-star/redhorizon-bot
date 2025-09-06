# src/tasks.py

import os
import re
import json
import html
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse, unquote

from src.feeds import fetch_rss_news, fetch_images, DEFAULT_TAGS

# =========================
# JSON helpers
# =========================
def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# =========================
# Markdown helpers (Telegram)
# =========================
def md_escape(text: str) -> str:
    """Escape chars for Telegram Markdown (basic) we actually use."""
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
    """Pick a clean first sentence; fallback to a trimmed title."""
    t = (text or "").strip()
    # strip obvious HTML
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    parts = re.split(r"(?<=[.!?])\s+", t)
    cand = parts[0].strip() if parts else ""
    badbits = ("credit", "image:", "photo:", "subscribe", "sign up")
    if not cand or len(cand) < 20 or any(b in cand.lower() for b in badbits):
        words = (fallback_title or "").strip().split()
        cand = " ".join(words[:max_words]).strip()
    return cand

# =========================
# Telegram senders (Markdown + inline buttons)
# =========================
def send_telegram_message(text, buttons=None, disable_preview=False, retry=True):
    bot = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHANNEL_ID")
    if not bot or not chat:
        print("[TG] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID")
        return False

    url = f"https://api.telegram.org/bot{bot}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": bool(disable_preview),
    }
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
                return send_telegram_message(text, buttons, disable_preview, retry=False)
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
    payload = {
        "chat_id": chat,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "Markdown",
    }
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

# =========================
# NEWS TASKS
# =========================
DISCUSS_URL = "https://x.com/RedHorizonHub"

def run_breaking_news():
    """
    Post up to 2 breaking items (<=15 min). Bold title, first-sentence quick read,
    image (if any), buttons. Biased to SpaceX + higher score + recency.
    """
    seen = load_json("data/seen_links.json", {}) or {}
    now = datetime.utcnow()

    items = [a for a in fetch_rss_news()[:60] if a.get("is_breaking")]
    items.sort(
        key=lambda x: (
            any(k in (x.get("title","") + x.get("summary","")).lower()
                for k in ["spacex","starship","falcon","raptor","starbase"]),
            x.get("score", 0),
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
            f"🚨 *BREAKING* — *{title}*\n\n"
            f"_Quick read:_ {quick}\n\n"
            f"Read more on {md_escape(a['source'])}\n"
            f"#Breaking #Space #SpaceX #Starship #RedHorizon"
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
    5-item digest, SpaceX-biased, Reddit capped to 1 across the whole list.
    Bold titles, first-sentence quick read. Sends a top-story image (if present),
    then the list. Falls back to 72h if <3 in 24h.
    """
    seen = load_json("data/seen_links.json", {}) or {}
    now = datetime.utcnow()

    all_items = fetch_rss_news()[:120]
    fresh = [a for a in all_items if (now - a["published"]) <= timedelta(hours=24)]

    spacexy = [a for a in fresh if any(
        k in (a["title"] + a["summary"]).lower()
        for k in ["spacex","starship","falcon","elon","raptor","starbase"]
    )]
    others = [a for a in fresh if a not in spacexy]

    spacexy.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)
    others.sort(key=lambda x: (x.get("score",0), x["published"]), reverse=True)

    final, reddit_count = [], 0

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
        spacexy = [a for a in fresh72 if any(
            k in (a["title"] + a["summary"]).lower()
            for k in ["spacex","starship","falcon","elon","raptor","starbase"]
        )]
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

    extra_tags = " ".join(DEFAULT_TAGS[:3])
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

    # Prefer sending the header as a photo caption if the top story has an image
    buttons = [("Open top story", top["link"]), ("Discuss on X", DISCUSS_URL)]
    if top.get("image"):
        send_telegram_image(top["image"], head, buttons)
        send_telegram_message("\n".join(blocks[2:]))  # skip header we just sent
    else:
        send_telegram_message(full_text, buttons=[("Discuss on X", DISCUSS_URL)])

    for a in final:
        seen[a["link"]] = True
    save_json("data/seen_links.json", seen)
    send_to_zapier({"text": full_text})
    return "ok"

def run_daily_image():
    """
    Post newest not-yet-seen image. Cache posted URLs to avoid repeats.
    """
    seen = load_json("data/seen_links.json", {}) or {}
    cache = load_json("data/image_cache.json", {}) or {}
    imgs = fetch_images()

    for im in imgs:
        if im["url"] in seen or cache.get(im["url"]):
            continue

        title = md_escape(im["title"] or "Space Image")
        date = im["published"].strftime("%b %d, %Y")
        hashtags = " ".join(["#Space", "#Mars", "#RedHorizon"])
        caption = f"📸 *Red Horizon Daily Image*\n{title}\n_{date}_\n{hashtags}"

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
    send_telegram_message(msg, disable_preview=True)
    return "ok"

# =========================
# Culture (Books / Games / Screen)
# =========================

DATA_DIR   = "data"
BOOKS_FILE = os.path.join(DATA_DIR, "books.json")
GAMES_FILE = os.path.join(DATA_DIR, "games.json")
MOVIE_FILE = os.path.join(DATA_DIR, "movies.json")
STATE_FILE = os.path.join(DATA_DIR, "culture_state.json")

# --- Wiki summary (REST API) or first <p> fallback ---
_P = re.compile(r"(?<=[.!?])\s+")
_PT = re.compile(r"<p[^>]*>(.*?)</p>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

def _wiki_summary(wiki_link: str) -> str:
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
        r = requests.get(api, headers={"Accept":"application/json"}, timeout=8)
        if r.status_code != 200:
            return ""
        extract = (r.json() or {}).get("extract", "").strip()
        if not extract:
            return ""
        parts = _P.split(extract)
        return " ".join(parts[:2]).strip()
    except Exception:
        return ""

def _html_first_para(url: str) -> str:
    try:
        if not url:
            return ""
        h = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        if h.status_code != 200:
            return ""
        m = _PT.search(h.text)
        if not m:
            return ""
        txt = _TAGS.sub(" ", m.group(1))
        txt = _WS.sub(" ", html.unescape(txt)).strip()
        parts = _P.split(txt)
        return " ".join(parts[:2]).strip()
    except Exception:
        return ""

def _culture_blurb(item: dict) -> str:
    if item.get("blurb"):
        return item["blurb"]
    if item.get("wiki_link"):
        s = _wiki_summary(item["wiki_link"])
        if s:
            return s
    for url in (item.get("review_link"), item.get("wiki_link"), item.get("official"), item.get("trailer")):
        s = _html_first_para(url or "")
        if s:
            return s
    return "A standout space pick for the Red Horizon community."

def _mk_buttons(item: dict, kind: str):
    btns = []
    if item.get("review_link"):
        btns.append(("📖 Review", item["review_link"]))
    if item.get("wiki_link"):
        btns.append(("🌐 Wiki", item["wiki_link"]))
    if kind in ("game","movie") and item.get("official"):
        btns.append(("🏷 Official", item["official"]))
    if kind in ("game","movie") and item.get("trailer"):
        btns.append(("🎬 Trailer", item["trailer"]))
    return btns or None

def _next_cycle(cur: str) -> str:
    return {"book": "game", "game": "movie", "movie": "book"}[cur]

def run_culture_spotlight():
    """
    Rotates book → game → movie. Persisted in data/culture_state.json.
    Override with env CULTURE_FORCE = book|game|movie (no index advance).
    """
    state = load_json(STATE_FILE, {"cycle":"book","book_index":0,"game_index":0,"movie_index":0})
    force = (os.getenv("CULTURE_FORCE") or "").strip().lower()
    cycle = force if force in ("book","game","movie") else state["cycle"]

    if cycle == "book":
        items = load_json(BOOKS_FILE, [])
        idx_key = "book_index"; icon = "📚"; title_line = "Book Spotlight"
        kind = "book"
    elif cycle == "game":
        items = load_json(GAMES_FILE, [])
        idx_key = "game_index"; icon = "🎮"; title_line = "Game Spotlight"
        kind = "game"
    else:
        items = load_json(MOVIE_FILE, [])
        idx_key = "movie_index"; icon = "🎬"; title_line = "Screen Spotlight"
        kind = "movie"

    if not items:
        print("[CULTURE] list is empty for", cycle)
        return "empty"

    i = state[idx_key] % len(items)
    item = items[i]

    title  = md_escape(item.get("title",""))
    author = item.get("author") or item.get("creator") or item.get("studio") or ""
    author = md_escape(author)
    blurb  = md_escape(_culture_blurb(item))

    header = f"*{icon} {title_line}*\n*{title}*{(' — ' + author) if author else ''}\n\n{blurb}\n\n"
    tags = {
        "book":  "#RedHorizon #Space #Books #SciFi",
        "game":  "#RedHorizon #Space #Gaming #SpaceGames",
        "movie": "#RedHorizon #Space #Films #TV",
    }[kind]
    caption = f"{header}{tags}"

    buttons = _mk_buttons(item, kind)
    if item.get("cover"):
        send_telegram_image(item["cover"], caption, buttons)
    else:
        send_telegram_message(caption, buttons)

    if not force:
        state[idx_key] = (state[idx_key] + 1) % len(items)
        state["cycle"] = _next_cycle(cycle)
        save_json(STATE_FILE, state)

    return "ok"
