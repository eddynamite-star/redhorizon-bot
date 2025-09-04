# formatter.py
import html, re

TG_MAX_TEXT = 3900
TG_MAX_CAPTION = 1024

def clamp(s: str, n: int) -> str:
    s = s or ""
    return s[:n-1] + "…" if len(s) > n else s

def link(text: str, url: str) -> str:
    t = html.escape(text or "Open")
    u = html.escape(url or "")
    return f'<a href="{u}">{t}</a>'

def build_hashtags(tags):
    tags = [t for t in (tags or []) if t]
    return " ".join(f"#{t.replace(' ','')}" for t in tags)

DOMAIN_RE = re.compile(r"https?://(www\.)?([^/]+)")

def badge_for(url: str) -> str:
    m = DOMAIN_RE.match(url or "")
    host = (m.group(2) if m else "source").lower()
    return host

def kpi_line(items):
    items = [i for i in (items or []) if i]
    return " · ".join(items)

# -----------------------------
# Breaking (tight, clean copy)
# -----------------------------
def fmt_breaking(title: str, url: str, summary: str = "", tags=None, source_hint: str = "") -> str:
    src = source_hint or badge_for(url)
    header = f"🚨 <b>BREAKING</b> — {html.escape(title)}"
    lines = [header]
    if summary:
        lines.append(summary)  # already cleaned upstream
    lines.append(link("Read more", url))
    lines.append(kpi_line([src]))
    lines.append(build_hashtags(tags))
    return clamp("\n\n".join(lines), TG_MAX_TEXT)

def fmt_priority(title: str, url: str, reason: str = "Live/Now", tags=None) -> str:
    head = f"🟢 <b>{html.escape(reason)}</b> — {html.escape(title)}"
    body = f"{link('Watch/Follow', url)}\n{build_hashtags(tags)}"
    return clamp("\n\n".join([head, body]), TG_MAX_TEXT)

# -----------------------------
# Daily Digest (5 items)
# -----------------------------
def fmt_digest(date_label: str, items: list[dict], tags=None, footer_x: str = "") -> str:
    """
    items: [{ 'title','url','source','blurb','time_utc' }]
    """
    header = f"🚀 <b>Red Horizon Daily Digest — {html.escape(date_label)}</b>"

    blocks = []
    for it in items:
        title = clamp((it.get("title") or "").strip(), 120)
        url   = it.get("url", "")
        src   = it.get("source") or badge_for(url)
        blurb = (it.get("blurb") or "").strip()
        time_ = it.get("time_utc") or ""

        line1 = f"• {link(title, url)} — <i>{html.escape(src)}</i>"
        if time_:
            line1 += f" · 🕒 {html.escape(time_)} UTC"

        block = [line1]
        if blurb:
            block.append(f"  <i>Quick read:</i> {html.escape(clamp(blurb, 180))}")
        block.append(f"  {link('➡️ Open', url)}")
        blocks.append("\n".join(block))

    body = "\n\n".join(blocks) if blocks else "<i>No fresh items.</i>"

    footer = []
    if footer_x:
        footer.append(f"Follow on X: {link('@RedHorizonHub', footer_x)}")
    footer.append(build_hashtags(tags))

    return clamp("\n\n".join([header, body, "\n".join(footer)]), TG_MAX_TEXT)

# -----------------------------
# Images (dynamic captions)
# -----------------------------
IMAGE_VARIANTS = [
    {"emoji":"📷", "label":"Red Horizon Daily Image", "keys":[]},
    {"emoji":"🚀", "label":"Launch Flashback",        "keys":["launch","liftoff","falcon","starship","booster","pad","cape","vandenberg"]},
    {"emoji":"🌅", "label":"Martian Horizon",         "keys":["mars","curiosity","perseverance","hirise","viking","insight","gale","jezero"]},
    {"emoji":"🌌", "label":"Cosmic View",             "keys":["jwst","webb","hubble","eso","galaxy","nebula","cluster","exoplanet"]},
    {"emoji":"🛠️","label":"Starbase Progress",       "keys":["starbase","boca","mechazilla","olm","olp","raptor","stack","static fire","wdr"]},
]

def choose_image_variant(text: str) -> dict:
    t = (text or "").lower()
    for v in IMAGE_VARIANTS[1:]:
        if any(k in t for k in v["keys"]):
            return v
    return IMAGE_VARIANTS[0]

def fmt_image_post(title: str, url: str, credit: str = "", tags=None, explainer: str = "") -> str:
    variant = choose_image_variant(f"{title} {credit} {url}")
    header = f'{variant["emoji"]} <b>{variant["label"]}</b>'
    title_line = (html.escape(title or "Space image"))
    lines = [header, title_line]
    if credit:
        lines.append(f"<i>{html.escape(credit)}</i>")
    if explainer:
        lines.append(html.escape(clamp(explainer, 180)))
    base = ["Space","Mars","RedHorizon"]
    extras = tags or []
    lines.append(build_hashtags(base + [t for t in extras if t not in base]))
    return clamp("\n".join(lines), TG_MAX_CAPTION)

# -----------------------------
# Welcome (panel-informed)
# -----------------------------
def fmt_welcome(x_url: str = "https://x.com/RedHorizonHub") -> str:
    lines = [
        "👋 <b>Welcome to Red Horizon</b>",
        "Your hub for SpaceX, Starship, and Mars exploration — plus the science, stories, and imagination that bring space closer.",
        "Here’s what you’ll find:\n• 🚨 Timely <i>breaking news</i> from trusted sources\n• 📰 A <b>Daily Digest</b> of the 5 biggest stories\n• 📸 Stunning <b>space images</b> posted throughout the day\n• 🗳 Weekly polls, explainers, and challenges to spark curiosity\n• 💡 Community threads for questions, ideas, and creative visions",
        "We’re building a community that’s:\n✨ Positive and inspiring\n🌍 Open and welcoming\n🚀 Anchored in the dream of reaching Mars",
        f'Follow on X: <a href="{html.escape(x_url)}">@RedHorizonHub</a>',
        build_hashtags(["Space","Mars","Starship","RedHorizon"]),
    ]
    return clamp("\n\n".join(lines), TG_MAX_TEXT)
