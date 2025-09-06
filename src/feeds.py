import feedparser
from datetime import datetime, timedelta
from urllib.parse import urlparse
import re

# --------------------------
# FEED SOURCES (keep name as RSS_FEEDS)
# --------------------------
RSS_FEEDS = [
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
    "https://www.blueorigin.com/rss/",
    "https://www.esa.int/rssfeed/Our_Activities/Space_News",
    "https://www.reddit.com/r/spacex.rss",
    "https://www.reddit.com/r/space.rss",
]

IMAGE_FEEDS = [
    "https://www.nasa.gov/rss/dyn/lg_image_of_the_day.rss",
    "https://mars.nasa.gov/rss/news/images/",
    "https://www.flickr.com/services/feeds/photos_public.gne?id=28634332@N05&lang=en-us&format=rss_200"
]

# Bias good sources over aggregators
SOURCE_WEIGHTS = {
    "nasa.gov": 5, "science.nasa.gov": 5, "mars.nasa.gov": 5,
    "spacex.com": 5, "esa.int": 5, "blueorigin.com": 4, "jaxa.jp": 4,
    "spacenews.com": 5, "space.com": 4, "spaceflightnow.com": 4,
    "nasaspaceflight.com": 4, "everydayastronaut.com": 4,
    "universetoday.com": 4, "phys.org": 3,
    "reddit.com": 1, "old.reddit.com": 1, "www.reddit.com": 1,
}

# Filter out low-signal memes/repeats
NEGATIVE_HINTS = [
    "turtles", "nudists", "meme", "shitpost", "off topic",
]

DEFAULT_TAGS = ["#SpaceX", "#Starship", "#Falcon9", "#Mars", "#NASA", "#RedHorizon", "#Spaceflight", "#Launch"]

KEYWORDS = ["spacex", "starship", "elon", "falcon", "raptor", "starbase", "mars", "terraform", "habitability", "isru"]

# --------------------------
# Helpers
# --------------------------
import html, re

def clean_summary(text: str) -> str:
    if not text:
        return ""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Unescape HTML entities (&amp;, &#32;, etc.)
    text = html.unescape(text)
    # Collapse whitespace
    return re.sub(r"\s+", " ", text).strip()

def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""

def _slug(url: str) -> str:
    try:
        p = urlparse(url).path.lower()
        p = re.sub(r"/+", "/", p)
        return p.strip("/").rsplit("/", 1)[-1]
    except Exception:
        return ""

def _title_key(t: str) -> str:
    t = re.sub(r"[^a-z0-9 ]+", " ", (t or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\b(spacex|nasa|esa|news|update|launch|rocket|starship|falcon|mars)\b", "", t)
    return re.sub(r"\s+", " ", t).strip()

def _entry_image(entry) -> str:
    for key in ("media_content", "media_thumbnail"):
        if getattr(entry, key, None):
            cand = getattr(entry, key)[0].get("url")
            if cand:
                return cand
    html = getattr(entry, "summary", "") or ""
    if not html and getattr(entry, "content", None):
        html = " ".join([c.get("value", "") for c in entry.content])
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I)
    return m.group(1) if m else ""

def score_article(title: str, summary: str) -> int:
    score = 0
    tl = (title or "").lower()
    sl = (summary or "").lower()
    for kw in KEYWORDS:
        if kw in tl: score += 2
        if kw in sl: score += 1
    return score

# --------------------------
# Main fetcher with de-dup + weighting
# --------------------------
def fetch_rss_news(is_terraforming: bool = False):
    articles = []
    now = datetime.utcnow()
    seen_keys = set()

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            src_title = getattr(feed.feed, "title", _host(feed_url))
            for e in getattr(feed, "entries", [])[:50]:
                published = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
                if not published:
                    continue
                tdt = datetime(*published[:6])
                if (now - tdt) > timedelta(hours=48):
                    continue

                title = getattr(e, "title", "").strip()
                link = getattr(e, "link", "").strip()
                summary = clean_summary(getattr(e, "summary", "") or "")
                low_title = title.lower()

                # negative hints filter
                if any(h in low_title for h in NEGATIVE_HINTS):
                    continue

                source_host = _host(link) or _host(feed_url)
                source_name = src_title or source_host
                img = _entry_image(e)

                s = score_article(title, summary) + SOURCE_WEIGHTS.get(source_host, 2)
                if is_terraforming and any(k in (title + summary).lower() for k in ["terraform", "habitability", "colonization", "isru"]):
                    s += 3

                dkey = (source_host, _title_key(title), _slug(link))
                if dkey in seen_keys:
                    continue
                seen_keys.add(dkey)

                articles.append({
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "published": tdt,
                    "source": source_name,
                    "source_host": source_host,
                    "image": img,
                    "score": s,
                    "is_breaking": (now - tdt) <= timedelta(minutes=15),
                    "is_super_breaking": (now - tdt) <= timedelta(minutes=5),
                })
        except Exception as ex:
            print(f"[RSS] {feed_url} -> {ex}")

    return sorted(articles, key=lambda x: (x["score"], x["published"]), reverse=True)

# --------------------------
# Images (simple)
# --------------------------
def fetch_images():
    pics = []
    now = datetime.utcnow()
    for feed_url in IMAGE_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            src_title = getattr(feed.feed, "title", _host(feed_url))
            for e in getattr(feed, "entries", [])[:40]:
                published = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
                if not published:
                    continue
                tdt = datetime(*published[:6])
                img = _entry_image(e)
                if not img:
                    continue
                pics.append({
                    "title": getattr(e, "title", "").strip(),
                    "url": img,
                    "source_name": src_title,
                    "source_link": getattr(e, "link", ""),
                    "description": getattr(e, "summary", ""),
                    "published": tdt,
                })
        except Exception as ex:
            print(f"[IMG] {feed_url} -> {ex}")
    # newest first
    return sorted(pics, key=lambda x: x["published"], reverse=True)
