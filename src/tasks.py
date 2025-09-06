import os, json, requests
from datetime import datetime, timedelta
from random import sample
from src.feeds import fetch_rss_news, fetch_images, DEFAULT_TAGS

# -------------- persistence --------------
def load_json(path, fallback):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return fallback

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# -------------- telegram --------------
def send_telegram_message(text, buttons=None, retry=True):
    bot = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHANNEL_ID")
    url = f"https://api.telegram.org/bot{bot}/sendMessage"
    payload = {"chat_id": chat, "text": text, "parse_mode": "Markdown"}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": b[0], "url": b[1]}] for b in buttons]}
    try:
        r = requests.post(url, json=payload, timeout=15)
        ok = (r.status_code == 200)
        if not ok and retry:
            return send_telegram_message(text, buttons, retry=False)
        return ok
    except Exception as e:
        print("[TG] sendMessage", e)
        return False

def send_telegram_image(image_url, caption, buttons=None, retry=True):
    bot = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHANNEL_ID")
    url = f"https://api.telegram.org/bot{bot}/sendPhoto"
    payload = {"chat_id": chat, "photo": image_url, "caption": caption, "parse_mode": "Markdown"}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": b[0], "url": b[1]}] for b in buttons]}
    try:
        r = requests.post(url, json=payload, timeout=20)
        ok = (r.status_code == 200)
        if not ok and retry:
            return send_telegram_image(image_url, caption, buttons, retry=False)
        return ok
    except Exception as e:
        print("[TG] sendPhoto", e)
        return False

def send_to_zapier(data):
    hook = os.getenv("ZAPIER_HOOK_URL")
    if not hook:
        return True
    try:
        r = requests.post(hook, json=data, timeout=10)
        return r.status_code in (200, 201)
    except Exception as e:
        print("[Zapier]", e)
        return False

# -------------- tasks --------------
DISCUSS_URL = "https://x.com/RedHorizonHub"

def run_breaking_news():
    seen = load_json("data/seen_links.json", {})
    now = datetime.utcnow()
    items = [a for a in fetch_rss_news()[:40] if a["is_breaking"]]

    # Prefer SpaceX + high score + newest
    items.sort(key=lambda x: (
        any(k in (x["title"] + x["summary"]).lower() for k in ["spacex","starship","falcon","raptor","starbase"]),
        x["score"], x["published"]), reverse=True)

    posted = 0
    for a in items[:2]:
        if a["link"] in seen:
            continue
        title = f"🚨 *BREAKING* — {a['title']}"
        body  = (
            f"{a['summary'][:420].rstrip()}…\n\n"
            f"Read more on {a['source']}\n"
        )
        buttons = [("Open", a["link"]), ("Discuss on X", DISCUSS_URL)]
        ok = False
        if a.get("image"):
            ok = send_telegram_image(a["image"], f"{title}\n\n{body}#Breaking #SpaceX #Starship #RedHorizon", buttons)
        if not ok:
            ok = send_telegram_message(f"{title}\n\n{body}#Breaking #SpaceX #Starship #RedHorizon", buttons)
        if ok:
            seen[a["link"]] = True
            posted += 1

    save_json("data/seen_links.json", seen)
    return "ok" if posted else "no-post"

def run_daily_digest():
    seen = load_json("data/seen_links.json", {})
    now = datetime.utcnow()

    # Try 24h window first
    all_items = fetch_rss_news()[:60]
    fresh = [a for a in all_items if (now - a["published"]) <= timedelta(hours=24)]

    # bias SpaceX
    spacexy = [a for a in fresh if any(k in (a["title"] + a["summary"]).lower()
                                       for k in ["spacex","starship","falcon","elon","raptor","starbase"])]
    others = [a for a in fresh if a not in spacexy]
    # Cap Reddit in digest
    reddit = [a for a in others if "reddit.com" in a["source_host"]][:1]
    non_reddit = [a for a in others if "reddit.com" not in a["source_host"]]

    items = (spacexy[:3] + non_reddit[:4] + reddit)[:5]

    # Fallback once/day: if items < 3, extend to 72h
    if len(items) < 3:
        fresh72 = [a for a in all_items if (now - a["published"]) <= timedelta(hours=72)]
        spacexy = [a for a in fresh72 if any(k in (a["title"] + a["summary"]).lower()
                                             for k in ["spacex","starship","falcon","elon","raptor","starbase"])]
        others = [a for a in fresh72 if a not in spacexy]
        reddit = [a for a in others if "reddit.com" in a["source_host"]][:1]
        non_reddit = [a for a in others if "reddit.com" not in a["source_host"]]
        items = (spacexy[:3] + non_reddit[:4] + reddit)[:5]

    if not items:
        print("[DIGEST] no-post")
        return "no-post"

    extra_tags = " ".join(sample(DEFAULT_TAGS, k=3))
    top = items[0]
    head = f"🚀 *Red Horizon Daily Digest — {now.strftime('%b %d, %Y')}*"
    blocks = [head, ""]

    for a in items:
        clock = a["published"].strftime("%H:%M")
        block = (
            f"• *{a['title']}* — _{a['source']}_ · 🕒 {clock} UTC\n"
            f"  _Quick read:_ {a['summary'][:240].rstrip()}…\n"
        )
        blocks.append(block)
        blocks.append(f"  ➡️ [Open]({a['link']})\n")

    blocks.append(f"Follow on X: @RedHorizonHub\n#Daily {extra_tags}")
    msg = "\n".join(blocks)

    # Send with top image if present
    buttons = [("Open top story", top["link"]), ("Discuss on X", DISCUSS_URL)]
    if top.get("image"):
        send_telegram_image(top["image"], head, buttons)
        # send the list as text after the image
        send_telegram_message("\n".join(blocks[2:]))  # skip header already in caption
    else:
        send_telegram_message(msg, buttons=[("Discuss on X", DISCUSS_URL)])

    for a in items:
        seen[a["link"]] = True
    save_json("data/seen_links.json", seen)
    send_to_zapier({"text": msg})
    return "ok"

def run_daily_image():
    seen = load_json("data/seen_links.json", {})
    cache = load_json("data/image_cache.json", {})  # url: true
    imgs = fetch_images()

    for im in imgs:
        if im["url"] in seen or cache.get(im["url"]):
            continue
        title = im["title"] or "Space Image"
        date = im["published"].strftime("%b %d, %Y")
        caption = f"📸 *Red Horizon Daily Image*\n{title}\n_{date}_\n#Space #Mars #RedHorizon"
        if send_telegram_image(im["url"], caption, buttons=[("Source", im["source_link"] or im["url"])]):

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
        "Your daily hub for SpaceX, Starship, Mars exploration & missions.\n\n"
        "What to expect:\n"
        "• 🚨 Breaking (when it truly breaks)\n"
        "• 📰 Daily Digest (5 hand-picked items)\n"
        "• 📸 Daily Image\n"
        "• ❓ Polls & explainers\n\n"
        "Follow on X: @RedHorizonHub"
    )
    send_telegram_message(msg)
    return "ok"
