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
    Fallback: the first N words from title if text is empty/garbage.
    """
    t = (text or "").strip()
    # Split on period/question/exclamation while keeping natural flow.
    parts = re.split(r"(?<=[\.!?])\s+", t)
    cand = parts[0].strip() if parts else ""
    # Filter obvious credits/bylines
    if not cand or len(cand) < 20 or "credit" in cand.lower() or "image:" in cand.lower():
        # fallback to title snippet
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
# Tasks
# ---------------------------
DISCUSS_URL = "https://x.com/RedHorizonHub"

def run_breaking_news():
    """
    Post up to 2 breaking items (<= 15 min old), with bold title, first-sentence quick read,
    image (if any), inline buttons. Prefers SpaceX-ish + higher score.
    """
    seen = load_json("data/seen_links.json", {})
    now = datetime.utcnow()

    items = [a for a in fetch_rss_news()[:40] if a["is_breaking"]]
    # prefer SpaceX + score + recency
    items.sort(
        key=lambda x: (
            any(k in (x["title"] + x["summary"]).lower()
                for k in ["spacex", "starship", "falcon", "raptor", "starbase"]),
            x["score"],
            x["published"]
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
    seen = load_json("data/seen_links.json", {})
    now = datetime.utcnow()

    # Try 24h window
    all_items = fetch_rss_news()[:80]
    fresh = [a for a in all_items if (now - a["published"]) <= timedelta(hours=24)]

    # Rank within two buckets
    spacexy = [a for a in fresh if any(k in (a["title"] + a["summary"]).lower()
                                       for k in ["spacex","starship","falcon","elon","raptor","starbase"])]
    others = [a for a in fresh if a not in spacexy]

    # Sort each bucket by score + recency
    spacexy.sort(key=lambda x: (x["score"], x["published"]), reverse=True)
    others.sort(key=lambda x: (x["score"], x["published"]), reverse=True)

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
        spacexy.sort(key=lambda x: (x["score"], x["published"]), reverse=True)
        others.sort(key=lambda x: (x["score"], x["published"]), reverse=True)

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
    seen = load_json("data/seen_links.json", {})
    cache = load_json("data/image_cache.json", {})  # {url: true}
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
        "Your daily hub for SpaceX, Starship, Mars exploration & missions.\n\n"
        "What to expect:\n"
        "• 🚨 Breaking (only when it truly breaks)\n"
        "• 📰 Daily Digest (5 hand-picked stories)\n"
        "• 📸 Daily Image\n"
        "• ❓ Polls & explainers\n\n"
        "Follow on X: @RedHorizonHub"
    )
    send_telegram_message(msg)
    return "ok"
