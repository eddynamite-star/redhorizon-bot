# src/feeds.py
import feedparser
import requests
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse, urljoin

# -----------------------------
# CONFIG: Feeds & simple tags
# -----------------------------

# Core news feeds (English-first, high-signal)
NEWS_FEEDS = [
    # Official / agencies
    "https://www.nasa.gov/rss/dyn/breaking_news.rss",
    "https://science.nasa.gov/feed/",
    "https://mars.nasa.gov/rss/news/",
    "https://www.esa.int/rssfeed/Our_Activities/Space_News",
    "https://www.blueorigin.com/rss/",
    # Industry / media
    "https://spacenews.com/feed/",
    "https://www.space.com/feeds/all",
    "https://www.spaceflightnow.com/feed/",
    "https://www.nasaspaceflight.com/feed/",
    "https://everydayastronaut.com/feed/",
    "https://www.universetoday.com/feed/",
    "https://phys.org/rss-feed/space-news/",
    "https://www.planetary.org/articles/feed",         # Planetary Society
    "https://www.thespacereview.com/rss.xml",
    "https://www.orbitaltoday.com/feed/",
    "https://astronomy.com/feed",
    "https://www.skyatnightmagazine.com/feed/",
    "https://www.sciencedaily.com/rss/space_time.xml",
    # NASA/JPL mission blogs (good images + context)
    "https://www.jpl.nasa.gov/multimedia/rss/news",
]

# Image-heavy feeds (NASA IOTD, APOD mirror-like, Flickr sources)
IMAGE_FEEDS = [
    "https://www.nasa.gov/rss/dyn/lg_image_of_the_day.rss",
    "https://www.nasa.gov/rss/dyn/chandra_images.rss",
    "https://mars.nasa.gov/rss/news/images/",
    # SpaceX Flickr (public feed)
    "https://www.flickr.com/services/feeds/photos_public.gne?id=130608600@N05&format=rss_200",
]

# X/Twitter influencers via Nitter (public RSS)
NITTER_HANDLES = [
    "Erdayastronaut", "SciGuySpace", "LorenGrush", "NASASpaceflight",
    "planetarysociety", "ThePlanetaryGuy", "FraserCain", "BadAstronomer",
]
NITTER_FEEDS = [f"https://nitter.net/{h}/rss" for h in NITTER_HANDLES]

# Negative hints to suppress tired/duplicate meme stories
NEGATIVE_HINTS = [
    "turtles and the nudists", "nudists will have to migrate", "little red dots",
]

# Scoring keywords
SPACE_X_KEYWORDS = [
    "spacex","starship","starbase","boca chica","falcon 9","falcon9","falcon-9",
    "falcon heavy","raptor","merlin","dragon","crew dragon","cargo dragon","mechazilla",
    "boostback","hot stage","super heavy","elon",
]
GENERAL_KEYWORDS = [
    "nasa","esa","jaxa","isro","space","mars","moon","lunar","iss","jwst","hubble",
    "orion","sls","launch","payload","booster","rocket","satellite","probe","lander",
    "asteroid","comet","planet","exoplanet","astronomy","observatory","cosmic",
]
KEYWORDS = SPACE_X_KEYWORDS + GENERAL_KEYWORDS

# Hashtag pool used by tasks.py (sampled)
DEFAULT_TAGS = [
    "#Space", "#Mars", "#SpaceX", "#Starship", "#RedHorizon",
    "#Astronomy", "#NASA", "#ESA", "#JWST", "#Launch",
]

# Limits / windows
MAX_PER_FEED = 8
FRESH_NEWS_HOURS = 72          # we’ll filter again per task
BREAKING_MINUTES = 15
SUPER_BREAKING_MINUTES = 5

HTTP_HEADERS = {"User-Agent": "RedHorizonBot/1.0 (+https://t.me/RedHorizonHub)"}

# -----------------------------
# Utilities
# -----------------------------

def _now_utc():
    return datetime.utcnow()

def _to_datetime(struct_time):
    # feedparser returns time.struct_time; guard if missing
    try:
        return datetime(*struct_time[:6])
    except Exception:
        return None

def _source_name(feed, fallback):
    try:
        return feed.feed.title.strip()
    except Exception:
        try:
            host = urlparse(fallback).netloc
            return host.replace("www.", "")
        except Exception:
            return "source"

def _host(url):
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""

def _is_english(text: str) -> bool:
    """Ultra-light language heuristic: ascii ratio + stopword hit."""
    if not text:
        return True
    ascii_ratio = sum(c < '\x80' for c in text) / max(1, len(text))
    if ascii_ratio < 0.85:
        return False
    lowers = text.lower()
    hits = 0
    for w in (" the ", " and ", " for ", " with ", " to ", " of ", " in "):
        if w in lowers:
            hits += 1
    return hits >= 1

def _score(title: str, summary: str) -> int:
    t = (title or "").lower()
    s = (summary or "").lower()
    sc = 0
    for k in KEYWORDS:
        if k in t:
            sc += 2 if k in ("starship","spacex","falcon 9","super heavy") else 1
        if k in s:
            sc += 1
    # spaceX bias
    if any(k in t+s for k in SPACE_X_KEYWORDS):
        sc += 2
    return sc

IMG_TAG = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)
OG_IMAGE = re.compile(r'"og:image"\s*content=["\']([^"\']+)["\']', re.I)

def extract_image_from_entry(entry, base_link=None):
    """
    Best-effort image discovery:
      1) media_content / media_thumbnail
      2) <img src> in summary/content
      3) OpenGraph og:image (single GET, optional)
    """
    # 1) media content
    media = getattr(entry, "media_content", None) or []
    if media:
        for m in media:
            u = m.get("url")
            if u: return u

    thumbs = getattr(entry, "media_thumbnail", None) or []
    if thumbs:
        for m in thumbs:
            u = m.get("url")
            if u: return u

    # 2) inline img tags
    for field in ("summary", "content"):
        raw = getattr(entry, field, None)
        if isinstance(raw, list) and raw:
            raw = raw[0].get("value")
        if not raw:
            continue
        m = IMG_TAG.search(raw)
        if m:
            u = m.group(1)
            if base_link and u.startswith("/"):
                try:
                    u = urljoin(base_link, u)
                except Exception:
                    pass
            return u

    # 3) OG image (optional, cheap HEAD/GET)
    link = getattr(entry, "link", None) or base_link
    if not link:
        return None
    try:
        html = requests.get(link, headers=HTTP_HEADERS, timeout=6).text
        m = OG_IMAGE.search(html)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None

def _clean_summary(raw):
    if not raw:
        return ""
    if isinstance(raw, list) and raw:
        raw = raw[0].get("value", "")
    # strip tags & condense
    txt = re.sub(r"<[^>]+>", " ", raw)
    txt = re.sub(r"\s+", " ", txt).strip()
    # clip very long
    return txt[:800]

def _looks_bad(title):
    t = (title or "").lower()
    return any(h in t for h in NEGATIVE_HINTS)

def _dedupe(items):
    seen = set()
    out = []
    for a in items:
        key = (a["title"].strip().lower(), a["source_host"])
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out

# -----------------------------
# Fetchers
# -----------------------------

def _parse_feed(url):
    try:
        d = feedparser.parse(url)
        return d
    except Exception:
        return None

def fetch_rss_news():
    """
    Return list of dicts:
      {title, link, summary, published, is_breaking, is_super_breaking,
       source, source_host, image, score}
    Sorted by score desc then recency. Includes Nitter feeds (lower weighted).
    """
    now = _now_utc()
    items = []

    # Build the pool: news + nitter (nitter last)
    all_feeds = list(dict.fromkeys(NEWS_FEEDS + NITTER_FEEDS))  # dedupe while preserving order

    for feed_url in all_feeds:
        d = _parse_feed(feed_url)
        if not d or not getattr(d, "entries", None):
            continue

        src_name = _source_name(d, feed_url)
        is_nitter = "nitter.net" in feed_url
        for e in d.entries[:MAX_PER_FEED]:
            pub = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
            dt = _to_datetime(pub)
            if not dt:
                continue
            if (now - dt) > timedelta(hours=FRESH_NEWS_HOURS):
                continue

            title = getattr(e, "title", "").strip()
            if not title or _looks_bad(title):
                continue
            summary = _clean_summary(getattr(e, "summary", "") or getattr(e, "description", ""))
            if not _is_english(title + " " + summary):
                continue

            link = getattr(e, "link", "").strip()
            if not link:
                continue

            sc = _score(title, summary)
            # Slightly downrank Nitter items so publisher sites win ties
            if is_nitter:
                sc -= 1

            image = extract_image_from_entry(e, base_link=link)
            host = _host(link)
            is_break = (now - dt) <= timedelta(minutes=BREAKING_MINUTES)
            is_super = (now - dt) <= timedelta(minutes=SUPER_BREAKING_MINUTES)

            items.append({
                "title": title,
                "link": link,
                "summary": summary,
                "published": dt,
                "is_breaking": is_break,
                "is_super_breaking": is_super,
                "source": src_name,
                "source_host": host,
                "image": image,
                "score": sc,
            })

    # Dedupe identicals, then sort by score & recency
    items = _dedupe(items)
    items.sort(key=lambda x: (x["score"], x["published"]), reverse=True)
    return items

def fetch_images():
    """
    Return list of dicts:
      {title, url, description, published, source_name, source_link}
    """
    now = _now_utc()
    out = []
    for feed_url in IMAGE_FEEDS:
        d = _parse_feed(feed_url)
        if not d or not getattr(d, "entries", None):
            continue
        src_name = _source_name(d, feed_url)
        for e in d.entries[:MAX_PER_FEED]:
            pub = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
            dt = _to_datetime(pub) or now
            if (now - dt) > timedelta(days=30):
                continue
            title = getattr(e, "title", "").strip() or "Space Image"
            url = extract_image_from_entry(e, base_link=getattr(e, "link", None))
            if not url:
                continue
            desc = _clean_summary(getattr(e, "summary", ""))
            out.append({
                "title": title,
                "url": url,
                "description": desc,
                "published": dt,
                "source_name": src_name,
                "source_link": getattr(e, "link", None) or feed_url,
            })
    # newest first
    out.sort(key=lambda x: x["published"], reverse=True)
    return out
