# src/formatter.py — attractive HTML formatting for Telegram posts

import html
import re
from urllib.parse import urlparse

TG_MAX_TEXT = 4096
TG_MAX_CAPTION = 1024

# Default hashtags
BASE_TAGS = ["Mars", "SpaceX", "Starship", "RedHorizon"]

# Map domains to labels
DOMAIN_BADGES = {
    "nasaspaceflight.com": "NSF",
    "spaceflightnow.com": "SFN",
    "spacenews.com": "SpaceNews",
    "nasa.gov": "NASA",
    "science.nasa.gov": "NASA Science",
    "esa.int": "ESA",
    "everydayastronaut.com": "Everyday Astronaut",
    "universetoday.com": "Universe Today",
    "phys.org": "Phys.org",
    "blueorigin.com": "Blue Origin",
    "ulalaunch.com": "ULA",
    "arianespace.com": "Arianespace",
    "rocketlabusa.com": "Rocket Lab",
    "youtube.com": "YouTube",
    "teslarati.com": "Teslarati"
}

def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""

def badge_for(url: str) -> str:
    d = domain_of(url)
    return DOMAIN_BADGES.get(d, d or "Source")

def clamp(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"

def build_hashtags(extra=None):
    tags = BASE_TAGS[:]
    if extra:
        for t in extra:
            t = re.sub(r"[^A-Za-z0-9_]", "", t)
            if t and t not in tags:
                tags.append(t)
    return " ".join(f"#{t}" for t in tags)

def link(title: str, url: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(title)}</a>'

def kpi_line(items: list[str]) -> str:
    joined = " · ".join(items)
    return f'<span class="tg-spoiler">{html.escape(joined)}</span>'

# ---------- Templates ----------

def fmt_breaking(title: str, url: str, summary: str = "", tags=None, source_hint: str = "") -> str:
    src = source_hint or badge_for(url)
    header = f"🚨 <b>BREAKING</b> — {html.escape(title)}"
    lines = [header]
    if summary:
        lines.append(html.escape(summary.strip()))
    lines.append(link("Read more", url))
    lines.append(kpi_line([src]))
    lines.append(build_hashtags(tags))
    return clamp("\n\n".join(lines), TG_MAX_TEXT)

def fmt_priority(title: str, url: str, reason: str = "", tags=None) -> str:
    src = badge_for(url)
    reason = f" — {reason}" if reason else ""
    header = f"🟢 <b>Live/Now</b>{html.escape(reason)}"
    lines = [header, html.escape(title), link("Open", url), kpi_line([src]), build_hashtags(tags)]
    return clamp("\n\n".join(lines), TG_MAX_TEXT)

def fmt_digest(date_label: str, items: list[dict], tags=None, footer_x: str = "") -> str:
    header = f"🚀 <b>Red Horizon Daily Digest — {html.escape(date_label)}</b>"
    bullets = []
    for it in items:
        t = clamp(it.get("title", "").strip(), 120)
        u = it.get("url", "")
        s = it.get("source") or badge_for(u)
        bullets.append(f"• {link(t, u)} — <i>{html.escape(s)}</i>")
    body = "\n".join(bullets) if bullets else "<i>No fresh items.</i>"
    footer = []
    if footer_x:
        footer.append(f"Follow on X: {link('@RedHorizonHub', footer_x)}")
    footer.append(build_hashtags(tags))
    return clamp("\n\n".join([header, body, "\n".join(footer)]), TG_MAX_TEXT)

def fmt_image_post(title: str, url: str, credit: str = "", tags=None) -> str:
    header = f"📸 <b>{html.escape(title)}</b>"
    meta = kpi_line([credit] if credit else [])
    caption = "\n\n".join([header, link("Source", url), meta, build_hashtags(tags)])
    return clamp(caption, TG_MAX_CAPTION)

def fmt_starbase_fact(title: str, body: str, ref_url: str = "", tags=None) -> str:
    header = f"🏗 <b>Starbase Highlight</b>\n{html.escape(title)}"
    lines = [header, html.escape(body.strip())]
    if ref_url:
        lines.append(link("Learn more", ref_url))
    lines.append(build_hashtags(tags or ["Starbase"]))
    return clamp("\n\n".join(lines), TG_MAX_TEXT)

def fmt_book_spotlight(title: str, author: str, blurb: str, url: str, tags=None) -> str:
    header = f"📚 <b>Book Spotlight</b>\n{html.escape(title)} — <i>{html.escape(author)}</i>"
    lines = [header, html.escape(blurb.strip()), link("Find it", url), build_hashtags((tags or []) + ["SciFi", "Books"])]
    return clamp("\n\n".join(lines), TG_MAX_TEXT)

def fmt_welcome(x_url: str = "https://x.com/RedHorizonHub") -> str:
    lines = [
        "👋 <b>Welcome to Red Horizon</b>",
        "Your daily hub for Starship/SpaceX, Mars exploration, and standout space imagery.",
        "What to expect:\n• 📰 Breaking every 15m\n• 🚀 Daily digests\n• 🏗 Starbase highlights\n• 📸 Images 3× daily\n• 📖 Book spotlights",
        f"Follow on X: {link('@RedHorizonHub', x_url)}",
        build_hashtags(["Space", "Exploration"]),
    ]
    return clamp("\n\n".join(lines), TG_MAX_TEXT)
