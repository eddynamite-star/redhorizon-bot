# tasks.py
import os, json, random
from datetime import datetime, timedelta, timezone
import requests

from src.feeds import (
    fetch_rss_news, fetch_images, fetch_launch_schedule,  # launch schedule via rocketlaunch.live RSS
    fetch_nitter_signals,
    BREAKING_WHITELIST, NEGATIVE_HINTS
)
from src.formatter import (
    fmt_breaking, fmt_priority, fmt_digest, fmt_image_post, fmt_welcome,
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
        if r.status_code == 200:
            return True
        print(f"[TG] sendMessage failed: {r.status_code} {r.text}")
        if retry:
            return send_telegram_message(html_text, disable_preview, False)
    except Exception as e:
        print("[TG] exception:", e)
    return False

def send_telegram_image(image_url, caption):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat  = os.getenv("TELEGRAM_CHANNEL_ID")
    url   = f"https://api.telegram.org/bot{token}/sendPhoto"
    payload = {"chat_id": chat, "photo": image_url, "caption": caption, "parse_mode":"HTML"}
    try:
        r = requests.post(url, json=payload, timeout=25)
        if r.status_code != 200:
            print(f"[TG] sendPhoto failed: {r.status_code} {r.text}")
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

def _allow_breaking(art: dict, signals: list) -> bool:
    """Only trusted domains; allow Nitter/X signals to boost but never post them directly."""
    if _is_blocked(art["title"] + " " + art.get("summary","")):
        return False
    if art.get("source") not in BREAKING_WHITELIST:
        return False
    # strong keyword score OR priority
    strong = art.get("score", 0) >= 2.0 or art.get("priority")
    if not strong:
        return False
    # recent Nitter signal can help push it over the edge (within 30 min)
    # (we already check domain whitelist here for the actual post)
    if not art.get("priority"):
        for s in signals:
            if abs((s["published"] - art["published"]).total_seconds()) <= 1800:
                return True
    return True

def _micro_explainer_for_image(title: str, source: str) -> str:
    t = (title or "").lower() + " " + (source or "")
    if any(k in t for k in ["jwst","webb","hubble","eso","galaxy","nebula","cluster"]):
        return "Infrared views reveal dust-shrouded stars and galaxies invisible in visible light."
    if any(k in t for k in ["mars","curiosity","perseverance","hirise","viking","jezero","gale"]):
        return "Mars imagery helps map safe routes, study geology, and guide future landing sites."
    if any(k in t for k in ["starbase","starship","falcon","liftoff","wdr","static fire","raptor"]):
        return "Tracking build and test milestones is key to rapid reuse and lowering launch costs."
    return ""

# ---------- Core jobs ----------
def run_breaking_news():
    _ensure_files()
    seen = load_json(SEEN_FILE, {})
    arts = fetch_rss_news()
    signals = fetch_nitter_signals()  # digest-only signals to bias breaking
    posted = 0
    for art in arts[:25]:
        if art["link"] in seen: continue
        if not (art.get("is_super_breaking") or art.get("is_breaking")): continue
        if not _allow_breaking(art, signals): continue

        msg = fmt_breaking(
            title=art["title"], url=art["link"], summary=art.get("summary",""),
            tags=["Breaking"], source_hint=art.get("source","")
        )
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
        expl = _micro_explainer_for_image(img.get("title",""), img.get("source_name",""))
        caption = fmt_image_post(
            title=img.get("title","Space image"),
            url=img.get("source_link", img.get("url","")),
            credit=img.get("source_name",""),
            tags=["Image"],
            explainer=expl
        )
        if send_telegram_image(img["url"], caption):
            seen[img["url"]] = True
            save_json(SEEN_FILE, seen)
            send_to_zapier({"image_url": img["url"], "caption": caption})
            return "ok"
    return "no-image"

# ---------- Launch scanning & reminders ----------
def fetch_launch_schedule():
    # kept here for import compatibility; RocketLaunch.Live feed is in NEWS_FEEDS
    from src.feeds import _parse, _entry_time
    url = "https://www.rocketlaunch.live/rss"
    try:
        feed = _parse(url)
        launches = []
        for e in feed.entries[:30]:
            t = _entry_time(e)
            if not t: continue
            launches.append({
                "title": getattr(e,"title",""),
                "url": getattr(e,"link",""),
                "published": t,
                "source": "rocketlaunch.live",
            })
        return launches
    except Exception as ex:
        print(f"[LAUNCH] rss -> {ex}")
        return []

def run_scan_launches():
    _ensure_files()
    launches = fetch_launch_schedule()
    launches = sorted(launches, key=lambda x: x["published"])
    save_json(LAUNCH_FILE, launches[:50])
    return f"cached:{len(launches[:50])}"

def run_launch_reminders():
    _ensure_files()
    cache = load_json(LAUNCH_FILE, [])
    if not cache: return "no-cache"
    now = _now()
    marks = load_json(SEEN_FILE, {})  # reuse as flags
    posted = 0

    for lc in cache[:20]:
        title, url, t = lc["title"], lc["url"], lc["published"]
        dtm = (t - now).total_seconds()/60.0
        key24 = f"{url}#T24"; key1 = f"{url}#T1"; keyL = f"{url}#L0"

        if 60*23.5 <= (t - now).total_seconds() <= 60*24.5 and not marks.get(key24):
            msg = fmt_priority(title=f"T-24h: {title}", url=url, reason="Launch in 24h", tags=["Launch","Reminder"])
            if send_telegram_message(msg): marks[key24]=True; posted+=1
        if 30 <= dtm <= 90 and not marks.get(key1):
            msg = fmt_priority(title=f"T-1h: {title}", url=url, reason="Launch in 1 hour", tags=["Launch","Reminder"])
            if send_telegram_message(msg): marks[key1]=True; posted+=1
        if -10 <= dtm <= 10 and not marks.get(keyL):
            msg = fmt_priority(title=f"Liftoff window: {title}", url=url, reason="Liftoff", tags=["Launch","Live"])
            if send_telegram_message(msg): marks[keyL]=True; posted+=1

        if posted >= 3: break

    save_json(SEEN_FILE, marks)
    return "ok" if posted else "no-post"

# ---------- Engagement tasks ----------
POLL_BANK = [
    ("Most critical Starship milestone before Mars?",
     ["Orbital refueling", "Rapid reuse", "Heat shield reliability", "Mechazilla catch"]),
    ("Which test excites you most?", ["Static fire", "WDR", "Full stack", "Flight"]),
    ("Next decade: first humans land on…?", ["Moon", "Mars", "Neither", "Both"]),
    ("Best telescope image type?", ["Nebulae", "Galaxies", "Exoplanets", "Deep fields"]),
    ("Which rover image would you frame?", ["Perseverance", "Curiosity", "Spirit", "Opportunity"]),
    ("Most underrated Mars challenge?", ["Dust", "EDL", "ISRU", "Radiation"]),
    ("What would you test first at Starbase?", ["Raptor ops", "Tile/TPS", "Catch arms", "OLP/OLM systems"]),
]

def send_poll(question: str, options: list[str]) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat  = os.getenv("TELEGRAM_CHANNEL_ID")
    url   = f"https://api.telegram.org/bot{token}/sendPoll"
    payload = {"chat_id": chat, "question": question, "options": options, "is_anonymous": True, "allows_multiple_answers": False}
    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code != 200:
            print(f"[TG] sendPoll failed: {r.status_code} {r.text}")
        return r.status_code == 200
    except Exception as e:
        print("[TG] poll exception:", e)
        return False

def run_weekly_poll():
    q, opts = random.choice(POLL_BANK)
    return "ok" if send_poll(q, opts) else "no-post"

EXPLAINERS = [
    ("What is a WDR (Wet Dress Rehearsal)?",
     "A full countdown rehearsal with propellant loaded. It verifies tanks, valves, ground systems, and procedures before flight."),
    ("Raptor vs Merlin in one minute",
     "Raptor uses full-flow staged combustion with methane and oxygen—higher efficiency and reusability—while Merlin uses RP-1/LOX and is flight-proven."),
    ("Why stainless steel for Starship?",
     "It’s strong at cryogenic temps, handles high heat better, and is cheaper to manufacture at scale—useful for rapid reusability."),
    ("TPS tiles (heat shield) explained",
     "Thermal Protection System tiles protect Starship during reentry. Gaps and attachment reliability are constant engineering focus."),
    ("EDL on Mars",
     "Entry-Descent-Landing on Mars is hard due to thin atmosphere and dust. Heavy payloads need supersonic retropropulsion."),
    ("ISRU on Mars",
     "In-situ resource utilization: making methane and oxygen from Martian CO₂ and water via the Sabatier reaction to refuel Starship."),
]

def run_weekly_explainer():
    title, body = random.choice(EXPLAINERS)
    msg = f"🧭 <b>{title}</b>\n{body}\n\n#Explainer #RedHorizon"
    return "ok" if send_telegram_message(msg, disable_preview=True) else "no-post"

CHALLENGES = [
    "Sketch a Mars habitat module for 4 people. What’s your power source?",
    "Design a Starbase test you’d run before the next flight.",
    "Write a 100-word story: first night on Mars.",
    "Map a week of ISRU ops with 2 robots.",
    "Choose: bigger heat shield or lighter payload—and justify.",
    "Your Mars EVA kit: what 5 non-standard items do you pack?",
]

def run_monthly_challenge():
    prompt = random.choice(CHALLENGES)
    msg = f"🧪 <b>Monthly Challenge</b>\n{prompt}\n\nShare your idea in the comments!\n#Challenge #RedHorizon"
    return "ok" if send_telegram_message(msg, disable_preview=True) else "no-post"

QNAS = [
    "No such thing as a dumb question: what Mars acronym stumps you?",
    "If you could ask SpaceX one question about Starship, what is it?",
    "What’s your favorite JWST/Hubble image and why?",
    "What would you test first if you ran Starbase for a day?",
    "Which Mars mission changed your mind about something?",
]

def run_friday_qna():
    q = random.choice(QNAS)
    msg = f"💬 <b>Open Thread</b>\n{q}\n\n#Community #RedHorizon"
    return "ok" if send_telegram_message(msg, disable_preview=True) else "no-post"

# ---------- Welcome & Ping ----------
def run_welcome_message():
    msg = fmt_welcome("https://x.com/RedHorizonHub")
    return "ok" if send_telegram_message(msg, disable_preview=True) else "no-post"

def run_ping():
    return "ok" if send_telegram_message("✅ Red Horizon is live", disable_preview=True) else "no-post"
