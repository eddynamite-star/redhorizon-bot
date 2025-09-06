# src/feeds.py

import feedparser
import requests
import time
import re
import html
from urllib.parse import urlparse, urlunparse
from datetime import datetime, timedelta

# ------------- CONFIG -------------

# High-quality space news feeds (RSS-friendly)
NEWS_FEEDS = [
    # Official / Agencies
    "https://www.nasa.gov/rss/dyn/breaking_news.rss",
    "https://www.nasa.gov/rss/dyn/mission_pages.rss",
    "https://science.nasa.gov/feed/",
    "https://mars.nasa.gov/rss/news/",
    "https://www.esa.int/rssfeed/Our_Activities/Space_News",
    "https://www.jaxa.jp/rss/jaxa_e.xml",  # JAXA English
    "https://www.blueorigin.com/rss/",

    # Major outlets
    "https://www.space.com/feeds/all",
    "https://spacenews.com/feed/",
    "https://www.spaceflightnow.com/feed/",
    "https://www.universetoday.com/feed/",
    "https://phys.org/rss-feed/space-news/",
    "https://www.planetary.org/planetary-insider/rss.xml",  # Planetary Society
    "https://www.spacepolicyonline.com/feed/",
    "https://www.skyatnightmagazine.com/feed/",  # Sky & Telescope (BBC S@N)
    "https://astronomynow.com/feed/",
    "https://www.npr.org/sections/space/rss.xml",
    "https://www.sciencedaily.com/rss/space_time.xml",
    "https://www.spacedaily.com/index.html?section=atom",
    "https://www.orbitaltoday.com/feed/",
    "https://www.nasawatch.com/feed/",
    "https://www.astrobio.net/feed/",
    "https://www.centaurea.org/feed",  # Centauri Dreams (sometimes /feed/ works)
    "https://www.thespacereview.com/rss/rss.xml",
    "https://www.spaceref.com/feed/",
    "https://www.spacedotcom.com/feeds/topic/mars.xml",  # Mars topic
    "https://www.spaceq.ca/feed/",  # Space Q (Canada)
    "https://earthsky.org/space/feed/",
    "https://www.newscientist.com/subject/space/feed/",  # may be partial/paywalled
    "https://www.nssdca.gsfc.nasa.gov/nssdca_news/rss.xml",  # NASA NSSDCA news

    # SpaceX-focused/adjacent
    "https://www.nasaspaceflight.com/feed/",
    "https://everydayastronaut.com/feed/",
    "https://www.teslarati.com/category/space/feed/",
    "https://www.teslarati.com/category/spacex/feed/",
    "https://www.reddit.com/r/spacex.rss",  # Reddit (cap in tasks.py)
]

# Image-heavy feeds (for Image of the Day)
IMAGE_FEEDS = [
    "https://www.nasa.gov/rss/dyn/lg_image_of_the_day.rss",
    "https://apod.nasa.gov/apod.rss",  # APOD
    "https://mars.nasa.gov/rss/news/images/",
    # SpaceX Flickr public feed (crew/Starship images sometimes show up)
    "https://www.flickr.com/services/feeds/photos_public.gne?id=130608600@N05&lang=en-us&format=rss_200",
    # NASA HQ Photo Flickr
    "https://www.flickr.com/services/feeds/photos_public.gne?id=24662369@N07&lang=en-us&format=rss_200",
]

# Nitter feeds for influential X/Twitter accounts (digest-only; avoid for breaking)
# NOTE: Nitter instances can rate-limit. The default nitter.net sometimes throttles.
# If reliability issues appear, swap domain to a known live mirror.
NITTER_FEEDS = [
    "https://nitter.net/elonmusk/rss",
    "https://nitter.net/SpaceX/rss",
    "https://nitter.net/NASASpaceflight/rss",
    "https://nitter.net/Erdayastronaut/rss",     # Tim Dodd
    "https://nitter.net/SciGuySpace/rss",        # Eric Berger
    "https://nitter.net/lorengrush/rss",
    "https://nitter.net/FraserCain/rss",
    "https://nitter.net/starhopperrss/rss",      # NSF Starbase updates (if active)
    "https://nitter.net/planetarysociety/rss",
    "https://nitter.net/NASA/rss",
]

# Default hashtags pool (used to rotate a few in tasks)
DEFAULT_TAGS = [
    "#Space", "#Mars", "#SpaceX", "#Starship",
    "#Astronomy", "#Exploration", "#Science", "#RedHorizon"
]

# Keywords for relevance scoring
KEYWORDS_PRIMARY = [
    # SpaceX / Starship
    "spacex", "starship", "super heavy", "falcon 9", "falcon-9", "falcon9",
    "raptor", "starbase", "boca chica", "mechazilla", "chopsticks", "orbital",
    "dragon", "crew dragon", "cargo dragon",
    # Missions / Mars
    "mars", "terraform", "habitability", "isru", "mars sample return",
    # Agencies / programs
    "nasa", "esa", "jaxa", "jwst", "webb", "orion", "sls", "iss", "gateway",
    # Industry
    "ula", "vulcan", "tory bruno", "rocket lab", "electron", "neutron",
    "blue origin", "new shepard", "new glenn", "booster", "launch",
]

# Hints to down-rank or skip (repetitive dramas/headlines)
NEGATIVE_HINTS = [
    "turtles", "nudists", "tourism complaints", "viral meme",
]

# Per-feed fetch cap (avoid memory bloat)
PER_FEED_LIMIT = 8

# ------------- HELPERS -------------

def canonical_url(url: str) -> str:
    try:
        p = urlparse(url)
        # strip tracking params very lightly
        clean = p._replace(query=re.sub(r"(utm_[^&]+|utm|ref|ref_src)=[^&]+&?", "", p.query))
        return urlunparse(clean)
    except Exception:
        return url

def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""

def clean_summary(text: str) -> str:
    """Strip HTML tags, unescape entities, collapse whitespace."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def is_english(text: str) -> bool:
    """Very light language heuristic: mostly ASCII & common punctuation."""
    if not text:
        return True
    # allow em dash etc.; reject when too many non-latin letters
    non_basic = sum(1 for c in text if ord(c) > 127 and c not in "–—’“”")
    return non_basic < max(8, len(text) // 20)

def has_negative_hint(title: str) -> bool:
    t = (title or "").lower()
    return any(h in t for h in NEGATIVE_HINTS)

def score_article(title: str, summary: str) -> int:
    """Keyword scoring; primary keywords give points, title hits are heavier."""
    score = 0
    t = (title or "").lower()
    s = (summary or "").lower()
    for k in KEYWORDS_PRIMARY:
        if k in t:
            score += 2
        if k in s:
            score += 1
    # small boost for obvious SpaceX/Mars
    if any(k in t for k in ["spacex", "starship", "mars", "falcon"]):
        score += 2
    # slight penalty for negative topics
    if has_negative_hint(title):
        score -= 2
    return score

def extract_image(entry) -> str:
    """Try to pull an image URL from common places."""
    # media:content / media_thumbnail
    for key in ("media_content", "media_thumbnail", "links", "enclosures"):
        obj = getattr(entry, key, None)
        if not obj:
            continue
        if isinstance(obj, list):
            for it in obj:
                url = it.get("url") if isinstance(it, dict) else it.get("href") if isinstance(it, dict) else None
                if url and url.startswith("http"):
                    return url
        elif isinstance(obj, dict):
            url = obj.get("url") or obj.get("href")
            if url and url.startswith("http"):
                return url
    # summary img tag
    summary = getattr(entry, "summary", "") or ""
    m = re.search(r'src=["\'](http[^"\']+\.(jpg|jpeg|png|gif|webp)[^"\']*)["\']', summary, re.I)
    if m:
        return m.group(1)
    return ""

def parse_datetime_struct(ts):
    try:
        return datetime(*ts[:6])
    except Exception:
        return None

# ------------- FETCHERS -------------

def _pull_feed(url):
    """Wrapper to fetch/parse a feed with tiny retry for flaky endpoints."""
    for i in range(2):
        try:
            return feedparser.parse(url)
        except Exception:
            time.sleep(0.8)
    return feedparser.parse(url)  # best effort

def _collect_from_feeds(feed_urls, max_per_feed=PER_FEED_LIMIT):
    """Yield normalized entries from a list of feeds."""
    out = []
    for feed_url in dict.fromkeys(feed_urls):  # dedupe
        try:
            feed = _pull_feed(feed_url)
            ftitle = getattr(feed.feed, "title", feed_url)
            entries = getattr(feed, "entries", [])[:max_per_feed]
            for e in entries:
                title = getattr(e, "title", "") or ""
                link = canonical_url(getattr(e, "link", "") or "")
                if not title or not link:
                    continue

                pub = parse_datetime_struct(getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None))
                if not pub:
                    # bias unknown dates to older to avoid accidental breaking
                    pub = datetime.utcnow() - timedelta(days=3)

                summary_raw = getattr(e, "summary", "") or ""
                summary = clean_summary(summary_raw)

                # light language & reldev filters
                if not is_english(title + " " + summary):
                    continue
                if has_negative_hint(title):
                    continue

                img = extract_image(e)
                host = host_of(link)
                out.append({
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "published": pub,
                    "source": ftitle,
                    "source_host": host,
                    "image": img,
                })
        except Exception as ex:
            print(f"[feed error] {feed_url}: {ex}")
    return out

def fetch_rss_news():
    """
    Return a relevance-sorted list of news items from:
      - NEWS_FEEDS (primary)
      - NITTER_FEEDS (secondary; digest-friendly)
    Each item contains title, link, summary, source, source_host, image,
    is_breaking, is_super_breaking, score.
    """
    now = datetime.utcnow()
    items = _collect_from_feeds(NEWS_FEEDS)
    # Add creators via Nitter (lower weight)
    nitter_items = _collect_from_feeds(NITTER_FEEDS, max_per_feed=5)
    for it in nitter_items:
        # many Nitter entries have tweet text in title; keep short
        it["summary"] = it["summary"][:220]

    merged = items + nitter_items

    # score + breaking flags + simple dedupe on link
    seen_links = set()
    normalized = []
    for a in merged:
        if a["link"] in seen_links:
            continue
        seen_links.add(a["link"])

        sc = score_article(a["title"], a["summary"])
        age = now - a["published"]
        a["score"] = sc
        a["is_super_breaking"] = age <= timedelta(minutes=5)
        a["is_breaking"] = age <= timedelta(minutes=15)
        normalized.append(a)

    # Sort: SpaceX/Mars + score + recency
    normalized.sort(
        key=lambda x: (
            any(k in (x["title"] + x["summary"]).lower()
                for k in ["spacex", "starship", "falcon", "raptor", "starbase", "mars"]),
            x["score"],
            x["published"]
        ),
        reverse=True
    )
    return normalized

def fetch_images():
    """
    Return recent (<= 7 days) images from IMAGE_FEEDS with best-effort metadata.
    """
    now = datetime.utcnow()
    out = []
    for a in _collect_from_feeds(IMAGE_FEEDS, max_per_feed=10):
        if not a.get("image"):
            # for APOD sometimes the link is an image page; keep anyway
            continue
        if (now - a["published"]) > timedelta(days=7):
            continue
        out.append({
            "title": a["title"],
            "url": a["image"],
            "published": a["published"],
            "source_name": a["source"],
            "source_link": a["link"],
        })
    # newest first
    out.sort(key=lambda x: x["published"], reverse=True)
    return out
