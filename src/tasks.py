# src/tasks.py

import os
import json
import requests
from datetime import datetime, timedelta
from random import sample
import re

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
# Culture: Books, Games, Screen (Movies/TV/Docs)
# ---------------------------

def _load_or_default(path, default_list):
    data = load_json(path, None)
    return data if isinstance(data, list) and data else default_list

# Built-in fallbacks (used if JSON files are missing)
BOOKS_DEFAULT = []  # we’ll rely on data/books.json you paste
GAMES_DEFAULT = []  # we’ll rely on data/games.json you paste
SCREEN_DEFAULT = [] # we’ll rely on data/movies.json you paste

def run_book_spotlight():
    books = _load_or_default("data/books.json", BOOKS_DEFAULT)
    if not books:
        return "No books"
    idx_data = load_json("data/book_index.json", {"index": 0})
    idx = int(idx_data.get("index", 0)) % len(books)
    b = books[idx]

    title = md_escape(b.get("title", "Unknown Title"))
    author = md_escape(b.get("author", "Unknown Author"))
    cover = b.get("cover_image") or ""
    review = b.get("review_link") or ""
    wiki = b.get("wiki_link") or ""

    caption = (
        f"📚 *Book Spotlight*\n"
        f"_{title}_ — {author}\n\n"
        f"#Books #SciFi #Space #RedHorizon"
    )
    buttons = []
    if review: buttons.append(("📖 Review", review))
    if wiki:   buttons.append(("🌐 Wiki", wiki))

    ok = False
    if cover:
        ok = send_telegram_image(cover, caption, buttons if buttons else None)
    if not ok:
        ok = send_telegram_message(caption, buttons if buttons else None)

    if ok:
        idx_data["index"] = idx + 1
        save_json("data/book_index.json", idx_data)
        send_to_zapier({"text": f"Book: {b.get('title')} — {b.get('author')}"})
        return "ok"
    return "Failed"

def run_game_spotlight():
    games = _load_or_default("data/games.json", GAMES_DEFAULT)
    if not games:
        return "No games"
    idx_data = load_json("data/game_index.json", {"index": 0})
    idx = int(idx_data.get("index", 0)) % len(games)
    g = games[idx]

    title = md_escape(g.get("title", "Unknown Game"))
    summary = md_escape(g.get("summary", "Space exploration or strategy experience."))
    cover = g.get("cover_image") or ""
    site = g.get("official_site") or ""
    trailer = g.get("trailer_link") or ""

    caption = (
        f"🎮 *Game Spotlight*\n"
        f"_{title}_\n\n"
        f"{summary}\n\n"
        f"#Gaming #Space #Exploration #RedHorizon"
    )
    buttons = []
    if site:    buttons.append(("🌐 Official Site", site))
    if trailer: buttons.append(("▶ Trailer", trailer))

    ok = False
    if cover:
        ok = send_telegram_image(cover, caption, buttons if buttons else None)
    if not ok:
        ok = send_telegram_message(caption, buttons if buttons else None)

    if ok:
        idx_data["index"] = idx + 1
        save_json("data/game_index.json", idx_data)
        send_to_zapier({"text": f"Game: {g.get('title')}"})
        return "ok"
    return "Failed"

def run_movie_spotlight():
    screen = _load_or_default("data/movies.json", SCREEN_DEFAULT)  # films/TV/docs
    if not screen:
        return "No screen items"
    idx_data = load_json("data/movie_index.json", {"index": 0})
    idx = int(idx_data.get("index", 0)) % len(screen)
    m = screen[idx]

    title = md_escape(m.get("title", "Unknown Title"))
    kind  = md_escape(m.get("type", "Screen"))
    year  = m.get("year", "")
    cover = m.get("cover_image") or ""
    trailer = m.get("trailer_link") or ""
    wiki    = m.get("wiki_link") or ""

    head = f"🎬 *{kind} Spotlight*"
    meta = f"_{title}_ ({year})" if year else f"_{title}_"
    caption = f"{head}\n{meta}\n\n#Movies #SciFi #Space #RedHorizon"

    buttons = []
    if trailer: buttons.append(("▶ Trailer", trailer))
    if wiki:    buttons.append(("🌐 Wiki", wiki))

    ok = False
    if cover:
        ok = send_telegram_image(cover, caption, buttons if buttons else None)
    if not ok:
        ok = send_telegram_message(caption, buttons if buttons else None)

    if ok:
        idx_data["index"] = idx + 1
        save_json("data/movie_index.json", idx_data)
        send_to_zapier({"text": f"Screen: {m.get('title')} ({m.get('type')})"})
        return "ok"
    return "Failed"

def run_culture_daily():
    """
    Rotates daily between: book -> game -> screen.
    Persists rotation in data/media_cycle.json.
    """
    order = ["book", "game", "screen"]
    cycle = load_json("data/media_cycle.json", {"index": 0})
    i = int(cycle.get("index", 0)) % len(order)
    pick = order[i]

    if pick == "book":
        res = run_book_spotlight()
    elif pick == "game":
        res = run_game_spotlight()
    else:
        res = run_movie_spotlight()

    cycle["index"] = i + 1
    save_json("data/media_cycle.json", cycle)
    return res or "ok"
