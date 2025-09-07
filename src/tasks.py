# src/tasks.py

import os
import re
import json
import html
import requests
from datetime import datetime, timedelta
from random import sample, choice
from urllib.parse import urlparse, unquote

from src.feeds import (
    fetch_rss_news, fetch_images, fetch_nitter_posts,
    DEFAULT_TAGS,
)

# =========================
# JSON helpers & constants
# =========================
DATA_DIR = "data"
SEEN_FILE = os.path.join(DATA_DIR, "seen_links.json")
IMAGE_CACHE_FILE = os.path.join(DATA_DIR, "image_cache.json")
CULTURE_STATE_FILE = os.path.join(DATA_DIR, "culture_state.json")
LAUNCHES_FILE = os.path.join(DATA_DIR, "launches.json")
LAUNCH_STATE_FILE = os.path.join(DATA_DIR, "launch_state.json")

DISCUSS_URL = "https://x.com/RedHorizonHub"
HEADERS = {"User-Agent": "Mozilla/5.0 (RedHorizonBot)"}

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

def _safe_html(text: str) -> str:
    return html.escape(text or "")

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
# Helpers for consensus/momentum
# =========================
AUTHORITIES = {
    # +2 authority sources
    "nasa.gov": 2, "esa.int": 2, "blueorigin.com": 2, "spacex.com": 2,
    # +1 reputable industry
    "spacenews.com": 1, "spaceflightnow.com": 1, "nasaspaceflight.com": 1, "space.com": 1,
    "everydayastronaut.com": 1, "universetoday.com": 1, "planetary.org": 1,
}

TREND_KEYWORDS = [
    "starship", "falcon", "static fire", "scrub", "net", "launch", "rollout", "raptor",
    "booster", "stack", "hotfire", "liftoff", "payload", "stage 0", "starbase",
]

def _host(url):
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""

def _norm_title(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

# =========================
# BREAKING NEWS (high-signal)
# =========================
def run_breaking_news():
    """
    Post up to 2 breaking items (<=15 min).
    - Base relevance (keywords, freshness, English happens upstream).
    - Consensus: boost if multiple distinct hosts publish similar titles within 60 min.
    - Authority bias: NASA/ESA/SpaceX +2; SpaceNews/NSF/etc +1.
    - Nitter momentum: if >=2 TREND_KEYWORDS echoed across Nitter last 30 min, +2.
    - Post only if total score >= 8 AND (consensus >=2 OR (authority and momentum)).
    """
    seen = load_json(SEEN_FILE, {}) or {}
    now = datetime.utcnow()

    # Pool
    items = [a for a in fetch_rss_news()[:120] if a.get("is_breaking")]
    if not items:
        return "no-post"

    # Build similarity clusters (title-based) within 60 min
    clusters = {}  # norm_title -> set(hosts)
    for a in items:
        if (now - a["published"]) > timedelta(minutes=60):
            continue
        key = _norm_title(a["title"])
        clusters.setdefault(key, set()).add(a.get("source_host",""))

    # Nitter momentum in last 30 minutes
    posts = fetch_nitter_posts(minutes=30)
    momentum_hits = 0
    for p in posts:
        txt = (p["text"] or "").lower()
        if any(k in txt for k in TREND_KEYWORDS):
            momentum_hits += 1
    momentum = 2 if momentum_hits >= 2 else 0

    # Score with consensus/authority/momentum
    scored = []
    for a in items:
        key = _norm_title(a["title"])
        consensus = len([h for h in clusters.get(key, set()) if h])
        authority = AUTHORITIES.get(a.get("source_host",""), 0)
        score = a.get("score", 0) + (consensus * 2) + authority + momentum
        scored.append((score, consensus, authority, a))

    # Sort by score then recency
    scored.sort(key=lambda t: (t[0], t[3]["published"]), reverse=True)

    posted = 0
    for score, consensus, authority, a in scored:
        if posted >= 2:
            break
        if a["link"] in seen:
            continue

        # Threshold gates
        if score < 8:
            continue
        if not (consensus >= 2 or (authority >= 1 and momentum >= 2)):
            continue

        title = md_escape(a["title"])
        quick = md_escape(first_sentence(a["summary"], a["title"]))
        tags = "#Breaking #Space #SpaceX #Starship #RedHorizon"
        body = (
            f"🚨 *BREAKING* — *{title}*\n\n"
            f"_Quick read:_ {quick}\n\n"
            f"Read more on {md_escape(a['source'])}\n"
            f"{tags}"
        )
        buttons = [("Open", a["link"]), ("Discuss on X", DISCUSS_URL)]

        ok = False
        if a.get("image"):
            ok = send_telegram_image(a["image"], body, buttons)
        if not ok:
            ok = send_telegram_message(body, buttons, disable_preview=True)

        if ok:
            seen[a["link"]] = True
            posted += 1

    save_json(SEEN_FILE, seen)
    return "ok" if posted else "no-post"

# =========================
# DAILY DIGEST (5 items)
# =========================
def _first_with_image(items):
    for it in items:
        if it.get("image"):
            return it
    return items[0] if items else None

def run_daily_digest():
    """
    5-item digest, SpaceX-weighted, Reddit capped to 1 total.
    Bold titles, 'Quick read' 1-sentence. Sends a hero image for the top story
    (uses article image or falls back to text if none).
    Falls back to 72h if 24h yields <3 items.
    """
    seen = load_json(SEEN_FILE, {}) or {}
    now = datetime.utcnow()

    all_items = fetch_rss_news()[:150]

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

    extra_tags = " ".join(DEFAULT_TAGS[:3])
    blocks.append(f"Follow on X: @RedHorizonHub\n#Daily {extra_tags}")
    full_text = "\n".join(blocks)

    # Hero image
    top = _first_with_image(final)
    buttons = [("Open top story", top["link"]), ("Discuss on X", DISCUSS_URL)]
    if top.get("image"):
        send_telegram_image(top["image"], head, buttons)
        send_telegram_message("\n".join(blocks[2:]), disable_preview=True)  # skip head line printed with image
    else:
        send_telegram_message(full_text, buttons=[("Discuss on X", DISCUSS_URL)], disable_preview=True)

    for a in final:
        seen[a["link"]] = True
    save_json(SEEN_FILE, seen)
    send_to_zapier({"text": full_text})
    return "ok"

# =========================
# DAILY IMAGE (no repeats)
# =========================
IMAGE_TITLES = [
    "📸 *Daily Image*",
    "🛰️ *Mission Moment*",
    "🌠 *Cosmic View*",
    "🔭 *Sky Highlight*",
    "🚀 *Launch Window*",
]

def run_daily_image():
    """
    Post newest not-yet-seen image. Cache posted URLs to avoid repeats.
    Tries to vary source host day-to-day.
    """
    seen  = load_json(SEEN_FILE, {}) or {}
    cache = load_json(IMAGE_CACHE_FILE, {}) or {}  # {url: timestamp}
    imgs = fetch_images()

    # last source host (for variety)
    last_host = cache.get("_last_host", "")

    def _host_of(src_link):
        try:
            return urlparse(src_link or "").netloc.lower().replace("www.","")
        except Exception:
            return ""

    picked = None
    for im in imgs:
        if im["url"] in seen or im["url"] in cache:
            continue
        this_host = _host_of(im.get("source_link"))
        if this_host and last_host and this_host == last_host:
            # try to vary; skip this and maybe pick later if nothing else
            continue
        picked = im
        break

    if not picked and imgs:
        # fallback: first unseen ignoring host variety
        for im in imgs:
            if im["url"] not in seen and im["url"] not in cache:
                picked = im
                break

    if not picked:
        print("[IMAGE] no-post")
        return "no-post"

    title = md_escape(picked.get("title") or "Space Image")
    date  = picked["published"].strftime("%b %d, %Y")
    hashtags = " ".join(["#Space", "#Mars", "#RedHorizon"])
    caption = f"{choice(IMAGE_TITLES)}\n{title}\n_{date}_\n{hashtags}"

    buttons = []
    if picked.get("source_link"):
        buttons.append(("Source", picked["source_link"]))
    else:
        buttons.append(("Open", picked["url"]))

    if send_telegram_image(picked["url"], caption, buttons):
        seen[picked["url"]] = True
        # remember recent URL + last host (keep cache to ~300)
        cache[picked["url"]] = datetime.utcnow().isoformat()
        cache["_last_host"] = _host_of(picked.get("source_link"))
        # trim cache (exclude _last_host)
        keys = [k for k in cache.keys() if k != "_last_host"]
        if len(keys) > 300:
            for k in keys[:-300]:
                cache.pop(k, None)
        save_json(SEEN_FILE, seen)
        save_json(IMAGE_CACHE_FILE, cache)
        return "ok"
    return "fail"

# =========================
# WELCOME MESSAGE
# =========================
def run_welcome_message():
    msg = (
        "👋 *Welcome to Red Horizon!*\n"
        "Your daily hub for SpaceX, Starship, Mars exploration & culture.\n\n"
        "What to expect:\n"
        "• 🚨 Breaking (only when it truly breaks)\n"
        "• 📰 Daily Digest (5 hand-picked stories)\n"
        "• 📸 Daily Image\n"
        "• 🎭 Culture Spotlights (books, games, movies/TV)\n"
        "• 📈 Trending pulses on launch weeks\n\n"
        "Follow on X: @RedHorizonHub"
    )
    send_telegram_message(msg, disable_preview=True)
    return "ok"

# =========================
# CULTURE SPOTLIGHT (rotate)
# =========================
BOOKS_FILE  = os.path.join(DATA_DIR, "books.json")
GAMES_FILE  = os.path.join(DATA_DIR, "games.json")
MOVIES_FILE = os.path.join(DATA_DIR, "movies.json")

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
        h = requests.get(url, headers=HEADERS, timeout=10)
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

def _culture_state():
    return load_json(CULTURE_STATE_FILE, {
        "cycle":"book","book_index":0,"game_index":0,"movie_index":0
    })

def _next_cycle(cur: str) -> str:
    return {"book": "game", "game": "movie", "movie": "book"}[cur]

def run_culture_spotlight():
    """
    Rotates book → game → movie. Persisted in data/culture_state.json.
    Override with env CULTURE_FORCE = book|game|movie (no index advance).
    """
    state = _culture_state()
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
        items = load_json(MOVIES_FILE, [])
        idx_key = "movie_index"; icon = "🎬"; title_line = "Screen Spotlight"
        kind = "movie"

    if not items:
        print("[CULTURE] list empty — skipping post")
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
        save_json(CULTURE_STATE_FILE, state)

    return "ok"

# Back-compat alias if a workflow still calls this name
def run_culture_daily():
    return run_culture_spotlight()

# =========================
# TRENDING PULSE (Nitter)
# =========================
def run_trending_pulse():
    """
    Posts a 'Trending now' pulse iff clear momentum exists:
    - Last 30 min Nitter posts
    - If >=5 total hits mentioning TREND_KEYWORDS, cluster the common word and post once.
    """
    posts = fetch_nitter_posts(minutes=30)
    if not posts:
        print("[TREND] no posts")
        return "no-post"

    hits = []
    for p in posts:
        txt = (p["text"] or "").lower()
        if any(k in txt for k in TREND_KEYWORDS):
            hits.append(p)

    if len(hits) < 5:
        print("[TREND] below threshold")
        return "no-post"

    # crude cluster: count most common keyword
    counts = {}
    for p in hits:
        txt = (p["text"] or "").lower()
        for k in TREND_KEYWORDS:
            if k in txt:
                counts[k] = counts.get(k, 0) + 1
    if not counts:
        return "no-post"

    top_kw = sorted(counts.items(), key=lambda x: x[1], reverse=True)[0][0]
    # choose a representative link
    link = hits[0]["link"]

    msg = (
        f"📈 *Trending now* — *{md_escape(top_kw.title())}*\n"
        f"Community chatter is spiking in the last 30 minutes.\n"
        f"#Trending #Space #RedHorizon"
    )
    send_telegram_message(msg, buttons=[("Open sample", link), ("Discuss on X", DISCUSS_URL)], disable_preview=True)
    return "ok"

# =========================
# LAUNCH COVERAGE (T-24h / T-1h / T-10m)
# =========================
def run_launch_coverage():
    """
    Reads data/launches.json and posts countdown reminders at T-24h, T-1h, T-10m.
    JSON format:
    [
      {
        "id": "starlink-XX-YY",
        "title": "Falcon 9 | Starlink Group 12-3",
        "provider": "SpaceX",
        "window_start_utc": "2025-09-01T12:30:00Z",
        "url": "https://www.spaceflightnow.com/...",
        "image": "https://..."
      }
    ]
    State persisted in data/launch_state.json:
    { "<id>": { "t24": true, "t1": true, "t10": true } }
    """
    launches = load_json(LAUNCHES_FILE, []) or []
    if not launches:
        print("[LAUNCH] no launches in file")
        return "no-post"

    state = load_json(LAUNCH_STATE_FILE, {}) or {}
    now = datetime.utcnow()

    posted_any = False

    for L in launches:
        lid = L.get("id") or L.get("title")
        if not lid or not L.get("window_start_utc"):
            continue
        try:
            t0 = datetime.fromisoformat(L["window_start_utc"].replace("Z","+00:00")).replace(tzinfo=None)
        except Exception:
            continue

        delta = t0 - now
        mins = int(delta.total_seconds() // 60)

        done = state.get(lid, {"t24": False, "t1": False, "t10": False})

        def _post(stage, label, emoji):
            title = md_escape(L.get("title","Upcoming Launch"))
            prov  = md_escape(L.get("provider",""))
            when  = t0.strftime("%b %d, %H:%M UTC")
            msg = (
                f"{emoji} *Launch Reminder — {label}*\n"
                f"*{title}*{(' — ' + prov) if prov else ''}\n"
                f"_Liftoff:_ {when}\n"
                f"#Launch #Space #RedHorizon"
            )
            buttons = []
            if L.get("url"):
                buttons.append(("Details", L["url"]))
            buttons.append(("Discuss on X", DISCUSS_URL))

            ok = False
            if L.get("image"):
                ok = send_telegram_image(L["image"], msg, buttons)
            if not ok:
                ok = send_telegram_message(msg, buttons, disable_preview=True)
            if ok:
                done[stage] = True
                state[lid] = done
                save_json(LAUNCH_STATE_FILE, state)
                return True
            return False

        # T-24h window (within ±5 min of exact)
        if not done.get("t24") and 60*24 - 5 <= mins <= 60*24 + 5:
            if _post("t24", "T-24 hours", "🗓️"):
                posted_any = True
                continue

        # T-1h window
        if not done.get("t1") and 60 - 5 <= mins <= 60 + 5:
            if _post("t1", "T-1 hour", "⏰"):
                posted_any = True
                continue

        # T-10m window
        if not done.get("t10") and 10 - 3 <= mins <= 10 + 3:
            if _post("t10", "T-10 minutes", "🚀"):
                posted_any = True
                continue

    return "ok" if posted_any else "no-post"

# =========================
# ON THIS DAY (Space History)
# =========================
SPACE_HINTS = [
    "nasa","esa","roscosmos","spacex","jaxa","isro","apollo","mercury","gemini","soyuz","vostok",
    "hubble","jwst","voyager","pioneer","cassini","curiosity","perseverance","opportunity","spirit",
    "lander","orbiter","satellite","launch","moon","mars","venus","mercury","saturn","uranus","neptune",
    "astronaut","cosmonaut","taikonaut","spacewalk","eva","iss","skylab"
]

def run_on_this_day():
    """
    Pull 2–3 space-relevant events from Wikipedia's On This Day REST feed.
    API: https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{month}/{day}
    """
    today = datetime.utcnow()
    url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{today.month}/{today.day}"
    try:
        r = requests.get(url, headers={"Accept":"application/json"}, timeout=10)
        if r.status_code != 200:
            print("[OTD] API status", r.status_code)
            return "no-post"
        data = r.json()
    except Exception as e:
        print("[OTD] fetch error", e)
        return "no-post"

    events = data.get("events", []) or []
    picks = []
    for ev in events:
        txt = (ev.get("text") or "").lower()
        if any(h in txt for h in SPACE_HINTS):
            year = ev.get("year")
            text = ev.get("text") or ""
            pages = ev.get("pages") or []
            link = None
            thumb = None
            if pages:
                link = pages[0].get("content_urls", {}).get("desktop", {}).get("page")
                thumb = pages[0].get("thumbnail", {}).get("source")
            picks.append({
                "year": year,
                "text": text,
                "link": link,
                "image": thumb
            })
        if len(picks) >= 3:
            break

    if not picks:
        print("[OTD] no space events today")
        return "no-post"

    head = f"🗓️ *On This Day in Space* — {today.strftime('%b %d')}"
    lines = [head, ""]
    for p in picks:
        line = f"• *{p['year']}* — {md_escape(p['text'])}"
        if p["link"]:
            line += f"\n  ➡️ [Read more]({p['link']})"
        lines.append(line)
    lines.append("\n#OnThisDay #Space #RedHorizon")
    text = "\n".join(lines)

    # Prefer first image if any
    if picks[0].get("image"):
        send_telegram_image(picks[0]["image"], head, buttons=[("Read more", picks[0]["link"] or "https://en.wikipedia.org")])
        send_telegram_message("\n".join(lines[2:]), disable_preview=True)
    else:
        send_telegram_message(text, disable_preview=True)
    return "ok"
