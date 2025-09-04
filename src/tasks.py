# tasks.py
import os, json, random
from datetime import datetime, timedelta, timezone
import requests

from src.feeds import (
    fetch_rss_news, fetch_images, fetch_launch_schedule,
    BREAKING_WHITELIST, NEGATIVE_HINTS
)
from src.formatter import (
    fmt_breaking, fmt_priority, fmt_digest, fmt_image_post,
)

# ---------- Files ----------
SEEN_FILE   = "data/seen_links.json"
BOOK_IDX    = "data/book_index.json"
FACT_IDX    = "data/fact_index.json"
LAUNCH_FILE = "data/launch_cache.json"

def _now(): return datetime.now(timezone.utc)

def _ensure_files():
    for p, init in [
        (SEEN_FILE, {}), (BOOK_IDX, {"index":0}),
        (FACT_IDX, {"index":0}), (LAUNCH_FILE, [])
    ]:
        if not os.path.exists(p):
            with open(p,"w") as f: json.dump(init, f)

def load_json(p, default=None):
    try:
        with open(p,"r") as f: return json.load(f)
    except: return default if default is not None else {}

def save_json(p, data):
    with open(p,"w") as f: json.dump(data, f, indent=2)

# ---------- Telegram ----------
def send_telegram_message(html_text, disable_preview=True, retry=True):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat  = os.getenv("TELEGRAM_CHANNEL_ID")
    url   = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat, "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": bool(disable_preview),
    }
    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code == 200: return True
        if retry:
            return send_telegram_message(html_text, disable_preview, False)
        print("TG error:", r.text)
    except Exception as e:
        print("TG exc:", e)
    return False

def send_telegram_image(image_url, caption):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat  = os.getenv("TELEGRAM_CHANNEL_ID")
    url   = f"https://api.telegram.org/bot{token}/sendPhoto"
    payload = {"chat_id": chat, "photo": image_url, "caption": caption, "parse_mode":"HTML"}
    try:
        r = requests.post(url, json=payload, timeout=25)
        return r.status_code == 200
    except Exception as e:
        print("TG image exc:", e)
        return False

def send_to_zapier(data):
    hook = os.getenv("ZAPIER_HOOK_URL")
    if not hook: return True
    try:
        r = requests.post(hook, json=data, timeout=10)
        return r.status_code in (200,201)
    except Exception as e:
        print("Zapier exc:", e)
        return False

# ---------- Helpers ----------
def _is_spacexish(a: dict) -> bool:
    text = (a.get("title","") + " " + a.get("summary","")).lower()
    keys = ["spacex","starship","falcon 9","falcon9","falcon-9","falcon heavy",
            "raptor","merlin","starbase","boca","elon musk","crew dragon","cargo dragon"]
    return any(k in text for k in keys)

def _is_blocked(text: str) -> bool:
    t = (text or "").lower()
    return any(b in t for b in NEGATIVE_HINTS)

def _allow_breaking(art: dict) -> bool:
    if _is_blocked(art["title"] + " " + art.get("summary","")):
        return False
    if art.get("source") not in BREAKING_WHITELIST:
        return False
    if art.get("priority"):
        return True
    strong = art.get("score", 0) >= 2.0
    text = (art["title"] + " " + art.get("summary","")).lower()
    core = any(k in text for k in ["spacex","starship","falcon","raptor","starbase","launch","static fire","wdr","liftoff","countdown"])
    return strong and core

# ---------- Jobs ----------
def run_breaking_news():
    _ensure_files()
    seen = load_json(SEEN_FILE, {})
    arts = fetch_rss_news()
    posted = 0
    for art in arts[:20]:
        if art["link"] in seen: continue
        if not (art.get("is_super_breaking") or art.get("is_breaking")): continue
        if not _allow_breaking(art): continue

        if art.get("is_super_breaking"):
            msg = fmt_priority(title=art["title"], url=art["link"], reason="Super-priority", tags=["Breaking","Live"])
        else:
            msg = fmt_breaking(title=art["title"], url=art["link"], summary=art.get("summary",""), tags=["Breaking"], source_hint=art.get("source",""))

        if send_telegram_message(msg, disable_preview=True):
            seen[art["link"]] = True
            posted += 1
            send_to_zapier({"text":"breaking", "url":art["link"]})
        if posted >= 2: break
    save_json(SEEN_FILE, seen)
    return "ok" if posted else "no-post"

def run_daily_digest():
    _ensure_files()
    seen = load_json(SEEN_FILE, {})
    arts = fetch_rss_news()
    now = _now()
    recent = [a for a in arts if (now - a["published"]) <= timedelta(hours=24)]
    if not recent: return "no-post"

    ordered = sorted(
        recent,
        key=lambda a: (0 if _is_spacexish(a) else 1, -float(a.get("score",0)), -a["published"].timestamp())
    )
    top = ordered[:5]
    items = [{
        "title": a["title"], "url": a["link"], "source": a.get("source"),
        "blurb": (a.get("summary") or "").strip(), "time_utc": a["published"].strftime("%H:%M")
    } for a in top]

    msg = fmt_digest(now.strftime("%b %d, %Y"), items, tags=["Daily"], footer_x="https://x.com/RedHorizonHub")
    if send_telegram_message(msg, disable_preview=True):
        for a in top: seen[a["link"]] = True
        save_json(SEEN_FILE, seen)
        send_to_zapier({"text":"digest"})
        return "ok"
    return "no-post"

def run_daily_image():
    _ensure_files()
    seen = load_json(SEEN_FILE, {})
    images = fetch_images()
    for img in images:
        if img["url"] in seen: continue
        caption = fmt_image_post(
            title=img.get("title","Space image"),
            url=img.get("source_link", img.get("url","")),
            credit=img.get("source_name",""),
            tags=["Image"]
        )
        if send_telegram_image(img["url"], caption):
            seen[img["url"]] = True
            save_json(SEEN_FILE, seen)
            send_to_zapier({"image_url": img["url"], "caption": caption})
            return "ok"
    return "no-image"

# ---- Launch scanning & reminders ----
def run_scan_launches():
    _ensure_files()
    cache = load_json(LAUNCH_FILE, [])
    launches = fetch_launch_schedule()
    # Keep last 50
    cache = launches[:50]
    save_json(LAUNCH_FILE, cache)
    return f"cached:{len(cache)}"

def run_launch_reminders():
    _ensure_files()
    cache = load_json(LAUNCH_FILE, [])
    if not cache: return "no-cache"

    now = _now()
    posted = 0
    for lc in cache[:20]:
        title = lc["title"]; url = lc["url"]; t = lc["published"]
        dtm = (t - now).total_seconds()/60.0
        key24 = f"{url}#T24"; key1 = f"{url}#T1"; keyL = f"{url}#L0"
        marks = load_json(SEEN_FILE, {})
        # T-24h
        if 60*23.5 <= (t - now).total_seconds() <= 60*24.5 and not marks.get(key24):
            msg = fmt_priority(title=f"T-24h: {title}", url=url, reason="Launch in 24h", tags=["Launch","Reminder"])
            if send_telegram_message(msg): marks[key24]=True; posted+=1
        # T-1h
        if 30 <= dtm <= 90 and not marks.get(key1):
            msg = fmt_priority(title=f"T-1h: {title}", url=url, reason="Launch in 1 hour", tags=["Launch","Reminder"])
            if send_telegram_message(msg): marks[key1]=True; posted+=1
        # Liftoff window (±10m)
        if -10 <= dtm <= 10 and not marks.get(keyL):
            msg = fmt_priority(title=f"Liftoff window: {title}", url=url, reason="Liftoff", tags=["Launch","Live"])
            if send_telegram_message(msg): marks[keyL]=True; posted+=1
        save_json(SEEN_FILE, marks)
        if posted >= 3: break
    return "ok" if posted else "no-post"
