# feeds.py
import feedparser, requests, re, html
from datetime import datetime, timedelta, timezone

# ---------- Helpers ----------

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
DOMAIN_RE = re.compile(r"https?://(www\.)?([^/]+)")

def _now(): return datetime.now(timezone.utc)

def clean_html_to_text(s: str, limit: int = 320) -> str:
    if not s: return ""
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = WS_RE.sub(" ", s).strip()
    return s[:limit] + ("…" if len(s) > limit else "")

def _domain(url: str) -> str:
    m = DOMAIN_RE.match(url or "")
    return (m.group(2) if m else "").lower()

def _entry_time(e):
    t = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
    if not t: return None
    return datetime(*t[:6], tzinfo=timezone.utc)

def is_recent(dt: datetime, hours=48) -> bool:
    return (_now() - dt) <= timedelta(hours=hours)

def is_english(text: str) -> bool:
    # very light heuristic
    if not text: return True
    nonlatin = re.findall(r"[^\x00-\x7F]", text)
    return len(nonlatin) < max(4, len(text)//12)

# ---------- Feeds ----------

# News (expanded)
NEWS_FEEDS = [
    "https://www.nasa.gov/rss/dyn/breaking_news.rss",
    "https://science.nasa.gov/feed/",
    "https://mars.nasa.gov/rss/news/",
    "https://spacenews.com/feed/",
    "https://www.spaceflightnow.com/feed/",
    "https://everydayastronaut.com/feed/",
    "https://www.nasaspaceflight.com/feed/",
    "https://www.teslarati.com/category/space/feed/",
    "https://www.universetoday.com/feed/",
    "https://phys.org/rss-feed/space-news/",
    "https://www.esa.int/rssfeed/Our_Activities/Space_News",
    # Added high-signal sources
    "https://feeds.arstechnica.com/arstechnica/space",
    "https://www.theverge.com/rss/space/index.xml",
    "https://www.ieee-oss.blackbarlabs.com/spectrum/space/feed",  # fallback mirror; IEEE RSS is fickle
    "https://payloadspace.com/feed/",
    "https://www.rocketlaunch.live/rss",
]

# Images (expanded)
IMAGE_FEEDS = [
    "https://www.nasa.gov/rss/dyn/lg_image_of_the_day.rss",
    "https://mars.nasa.gov/rss/news/images/",
    "https://www.flickr.com/services/feeds/photos_public.gne?id=28634332@N05&lang=en-us&format=rss_200", # SpaceX
    "https://esahubble.org/rss/image_of_the_week/",
    "https://webb.nasa.gov/content/webbLaunch/rss.xml",
    "https://www.eso.org/public/rss/image_index.xml",
    "https://apod.nasa.gov/apod.rss",
    "https://www.uahirise.org/rss/",
    "https://www.planetary.org/rss/feed"
]

# Breaking / priority logic
KEYWORDS = [
    "spacex","starship","starbase","boca chica","falcon 9","falcon9","falcon-9",
    "falcon heavy","super heavy","booster","mechazilla","chopsticks","olm","olp",
    "raptor","merlin","dragon","crew dragon","cargo dragon",
    "launch","liftoff","static fire","hotfire","wdr","wet dress","stack","destack",
    "rollout","rollback","countdown","premiere","live","pad","orbital",
    "mars","terraform","habitat","isru","red planet"
]
NEGATIVE_HINTS = [
    "opinion","editorial","sponsored","newsletter","podcast","weekly","roundup","recap",
    "jobs","hiring","appointment","promoted","funding round","earnings","stock",
]
PRIORITY_WORDS = [
    "launch","liftoff","static fire","hotfire","wdr","wet dress","stack","destack",
    "rollout","rollback","countdown","premiere","live"
]
BREAKING_WHITELIST = {
    "nasaspaceflight.com","spaceflightnow.com","spacenews.com",
    "nasa.gov","science.nasa.gov","esa.int",
    "everydayastronaut.com","universetoday.com","rocketlaunch.live",
    "arstechnica.com","theverge.com","payloadspace.com"
}

# ---------- Scoring & Parsing ----------

def relevance_score(title: str, summary: str) -> float:
    t = (title or "").lower()
    s = (summary or "").lower()
    score = 0.0
    for kw in KEYWORDS:
        if kw in t: score += 1.6
        if kw in s: score += 0.9
    for neg in NEGATIVE_HINTS:
        if neg in t or neg in s: score -= 1.2
    return score

def _parse(url): return feedparser.parse(url)

def fetch_rss_news():
    out = []
    for url in NEWS_FEEDS:
        try:
            feed = _parse(url)
            for e in feed.entries[:25]:
                t = _entry_time(e)
                if not t or not is_recent(t, 48): continue
                title = getattr(e,"title","")
                link  = getattr(e,"link","")
                raw   = getattr(e,"summary","") or getattr(e,"description","")
                summary = clean_html_to_text(raw, 320)
                if not (is_english(title) and is_english(summary)): continue
                dom = _domain(link) or _domain(url)
                score = relevance_score(title, summary)
                text_mix = (title + " " + summary).lower()
                priority = any(w in text_mix for w in PRIORITY_WORDS)
                out.append({
                    "title": title, "link": link, "summary": summary,
                    "published": t, "source": dom,
                    "is_breaking": (_now() - t) <= timedelta(minutes=15),
                    "is_super_breaking": (_now() - t) <= timedelta(minutes=5),
                    "score": score + (0.8 if priority else 0),
                    "priority": priority
                })
        except Exception as ex:
            print(f"[RSS] {url} -> {ex}")
    out.sort(key=lambda x: (x["score"], x["published"]), reverse=True)
    return out

def fetch_images():
    imgs = []
    for url in IMAGE_FEEDS:
        try:
            feed = _parse(url)
            for e in feed.entries[:20]:
                t = _entry_time(e)
                if not t or not is_recent(t, 96): continue
                title = getattr(e,"title","")
                link  = getattr(e,"link","")
                raw   = getattr(e,"summary","") or getattr(e,"description","")
                summary = clean_html_to_text(raw, 260)
                # crude image URL guess
                img = getattr(e, "media_content", None)
                img_url = None
                if img and isinstance(img, list) and img[0].get('url'):
                    img_url = img[0]['url']
                else:
                    m = re.search(r'(https?://\S+\.(?:jpg|jpeg|png))', raw or "", re.I)
                    if m: img_url = m.group(1)
                if not img_url: continue
                imgs.append({
                    "title": title, "url": img_url,
                    "source_link": link, "source_name": _domain(link) or _domain(url),
                    "description": summary, "published": t
                })
        except Exception as ex:
            print(f"[IMG] {url} -> {ex}")
    # prefer newer
    imgs.sort(key=lambda x: x["published"], reverse=True)
    return imgs

# --- Launch schedule (RocketLaunch.Live RSS) ---

def fetch_launch_schedule():
    # The general RSS is already in NEWS_FEEDS; we parse here for schedule cache
    url = "https://www.rocketlaunch.live/rss"
    try:
        feed = _parse(url)
        launches = []
        for e in feed.entries[:30]:
            title = getattr(e,"title","")
            link  = getattr(e,"link","")
            t = _entry_time(e)
            if not t: continue
            launches.append({
                "title": title, "url": link, "published": t,
                "source": "rocketlaunch.live"
            })
        return launches
    except Exception as ex:
        print(f"[LAUNCH] rss -> {ex}")
        return []
