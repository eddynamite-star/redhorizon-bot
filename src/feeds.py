# src/feeds.py — unified fetch (RSS + YouTube + X via Nitter) with filters

import feedparser
from urllib.parse import urlparse
from datetime import datetime, timedelta
import time

# -------- Feeds --------

RSS_FEEDS = [
    # Core news
    "https://www.nasa.gov/rss/dyn/breaking_news.rss",
    "https://science.nasa.gov/feed/",
    "https://mars.nasa.gov/rss/news/",
    "https://www.esa.int/rssfeed/Our_Activities/Space_News",
    "https://spacenews.com/feed/",
    "https://spaceflightnow.com/feed/",
    "https://www.nasaspaceflight.com/feed/",
    "https://everydayastronaut.com/feed/",
    "https://www.universetoday.com/feed/",
    "https://phys.org/rss-feed/space-news/",
    "https://www.teslarati.com/category/space/feed/",
    "https://www.blueorigin.com/rss/",

    # Launch schedules / trackers
    "https://spaceflightnow.com/launch-schedule/feed/",
    "https://everydayastronaut.com/launches/feed/",
    "https://rocketlaunch.live/rss",

    # Deep space / science
    "https://www.jpl.nasa.gov/feeds/news",
    "https://www.astronomy.com/feed/",
    "https://www.scientificamerican.com/feed/space/",
]

IMAGE_FEEDS = [
    "https://www.nasa.gov/rss/dyn/lg_image_of_the_day.rss",
    "https://mars.nasa.gov/rss/news/images/",
    "https://photojournal.jpl.nasa.gov/rss",
    # SpaceX Flickr (official)
    "https://www.flickr.com/services/feeds/photos_public.gne?id=130608600@N05&lang=en-us&format=rss_200",
]

# YouTube (native RSS)
YOUTUBE_FEEDS = [
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCtI0Hodo5o5dUb67FeUjDeA",  # SpaceX
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCSUu1lih2RifWkKtDOJdsBA",  # NASASpaceflight
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC6uKrU_WqJ1R2HMTY3LIx5Q",  # Everyday Astronaut
    # Add more creators as desired
]

# X (Twitter) via Nitter (free RSS proxy)
NITTER_HOSTS = [
    "https://nitter.net",
    "https://nitter.fdn.fr",
    "https://nitter.poast.org",
]
X_FEEDS = [
    "https://nitter.net/SpaceX/rss",
    "https://nitter.net/elonmusk/rss",
    "https://nitter.net/NASASpaceflight/rss",
    "https://nitter.net/RGVaerialphotos/rss",
    "https://nitter.net/SciGuySpace/rss",
    "https://nitter.net/thesheetztweetz/rss",
    "https://nitter.net/jeff_foust/rss",
]

# -------- Filters / Keywords --------

KEYWORDS = [
    # SpaceX / Starship / Starbase
    "spacex","starship","starbase","boca chica","falcon 9","falcon9","falcon-9",
    "super heavy","booster","mechazilla","chopsticks","orbital launch mount","olm","olp",
    "raptor","merlin","dragon","crew dragon","cargo dragon",
    # Mars
    "mars","terraform","habitat","isru","red planet",
    # Agencies / programs
    "nasa","esa","jpl","jwst","orion","sls","iss",
    # Industry
    "ula","vulcan","rocket lab","electron","neutron","blue origin","new glenn","new shepard",
    "arianespace","ariane 6","vega","relativity","terran r","firefly","alpha",
    # Launch-y words
    "launch","liftoff","static fire","hotfire","wdr","wet dress","stack","destack","rollout","rollback","countdown","premiere","live",
]

NEGATIVE_HINTS = [
    "opinion","editorial","sponsored","weekly","roundup","recap","newsletter","podcast"
]

PRIORITY_WORDS = [
    "launch","liftoff","static fire","hotfire","wdr","wet dress","stack","destack","rollout","rollback","countdown","premiere","live"
]

def _now_utc():
    return datetime.utcnow()

def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""

def is_english(text: str) -> bool:
    if not text:
        return True
    # crude heuristic: mostly ASCII and some spaces
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    ratio = ascii_chars / max(1, len(text))
    return ratio > 0.85

def relevance_score(title: str, summary: str) -> float:
    t = (title or "").lower()
    s = (summary or "").lower()
    score = 0.0
    for kw in KEYWORDS:
        if kw in t:
            score += 1.5
        if kw in s:
            score += 0.8
    for neg in NEGATIVE_HINTS:
        if neg in t or neg in s:
            score -= 0.8
    return score

def is_recent(published_dt: datetime, hours=48) -> bool:
    return (_now_utc() - published_dt) <= timedelta(hours=hours)

def _entry_time(entry) -> datetime | None:
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not t:
        return None
    return datetime(*t[:6])

def _trim(s: str, n: int) -> str:
    s = s or ""
    return (s[:n] + "…") if len(s) > n else s

def _parse_feed(url: str):
    return feedparser.parse(url)

def _with_nitter_failover(url: str):
    # try default first; on failure, swap host sequentially
    for host in NITTER_HOSTS:
        u = url
        try:
            parsed = urlparse(url)
            u = host + parsed.path
            yield u
        except Exception:
            yield url

def fetch_generic_feeds(feed_urls: list[str], freshness_hours=48, per_feed_limit=20):
    """Returns list of dicts with unified shape from standard RSS feeds."""
    out = []
    now = _now_utc()
    for feed_url in feed_urls:
        try:
            feed = _parse_feed(feed_url)
            entries = feed.entries[:per_feed_limit]
            for e in entries:
                t = _entry_time(e)
                if not t or not is_recent(t, hours=freshness_hours):
                    continue
                title = getattr(e, "title", "")
                link = getattr(e, "link", "")
                summary = getattr(e, "summary", "")
                if not (is_english(title) and is_english(summary)):
                    continue
                score = relevance_score(title, summary)
                if score <= 0.5:
                    continue
                out.append({
                    "title": title,
                    "link": link,
                    "summary": _trim(summary, 320),
                    "published": t,
                    "is_breaking": (_now_utc() - t) <= timedelta(minutes=15),
                    "is_super_breaking": (_now_utc() - t) <= timedelta(minutes=5),
                    "score": score,
                    "source": _domain(link) or _domain(feed_url),
                    "priority": any(w in (title.lower() + " " + summary.lower()) for w in PRIORITY_WORDS),
                })
        except Exception as ex:
            print(f"[RSS] error {feed_url}: {ex}")
            continue
    # sort by score then recency
    out.sort(key=lambda x: (x["score"], x["published"]), reverse=True)
    return out

def fetch_youtube_feeds(freshness_hours=72, per_feed_limit=15):
    """YouTube feeds are regular Atom; treat like RSS."""
    return fetch_generic_feeds(YOUTUBE_FEEDS, freshness_hours, per_feed_limit)

def fetch_nitter_feeds(freshness_hours=24, per_feed_limit=15):
    """Try Nitter hosts with basic failover."""
    out = []
    for base_u in X_FEEDS:
        success = False
        for u in _with_nitter_failover(base_u):
            try:
                feed = _parse_feed(u)
                if getattr(feed, "bozo", 0):
                    raise Exception("parse error")
                entries = feed.entries[:per_feed_limit]
                for e in entries:
                    t = _entry_time(e)
                    if not t or not is_recent(t, hours=freshness_hours):
                        continue
                    title = getattr(e, "title", "")
                    link = getattr(e, "link", "")
                    summary = getattr(e, "summary", title)
                    if not (is_english(title) and is_english(summary)):
                        continue
                    score = relevance_score(title, summary)
                    if score <= 0.5:
                        continue
                    out.append({
                        "title": title,
                        "link": link,
                        "summary": _trim(summary, 280),
                        "published": t,
                        "is_breaking": (_now_utc() - t) <= timedelta(minutes=15),
                        "is_super_breaking": (_now_utc() - t) <= timedelta(minutes=5),
                        "score": score + 0.2,  # slight boost for live signals
                        "source": "nitter.net",
                        "priority": any(w in (title.lower() + " " + summary.lower()) for w in PRIORITY_WORDS),
                    })
                success = True
                break
            except Exception as ex:
                print(f"[Nitter] fail {u}: {ex}")
                time.sleep(0.5)
        if not success:
            print(f"[Nitter] all hosts failed for {base_u}")
    out.sort(key=lambda x: (x["score"], x["published"]), reverse=True)
    return out

def fetch_images(per_feed_limit=12):
    """Return list of images with url/title/source_name/source_link/description."""
    out = []
    for feed_url in IMAGE_FEEDS:
        try:
            feed = _parse_feed(feed_url)
            entries = feed.entries[:per_feed_limit]
            for e in entries:
                t = _entry_time(e)
                title = getattr(e, "title", "") or "Space image"
                link = getattr(e, "link", "")
                desc = getattr(e, "summary", "")
                # try common media fields
                img_url = ""
                if "media_content" in e:
                    m = e.media_content
                    if isinstance(m, list) and m:
                        img_url = m[0].get("url") or img_url
                    elif isinstance(m, dict):
                        img_url = m.get("url") or img_url
                if not img_url and "links" in e:
                    for L in e.links:
                        if getattr(L, "type", "").startswith("image/"):
                            img_url = getattr(L, "href", "")
                            break
                if not img_url:
                    # some feeds (Flickr) put img in summary HTML; we skip parsing aggressively here
                    continue

                out.append({
                    "title": title,
                    "url": img_url,
                    "source_name": _domain(link) or _domain(feed_url),
                    "source_link": link or feed_url,
                    "description": _trim(desc, 300),
                    "published": t or _now_utc()
                })
        except Exception as ex:
            print(f"[IMG] error {feed_url}: {ex}")
            continue
    # most recent first
    out.sort(key=lambda x: x["published"] or _now_utc(), reverse=True)
    return out

def fetch_rss_news():
    """
    Unified news list from: core RSS + YouTube + X/Nitter.
    Returns a single list of dicts with fields used by tasks.py.
    """
    combined = []
    combined += fetch_generic_feeds(RSS_FEEDS, freshness_hours=48, per_feed_limit=20)
    combined += fetch_youtube_feeds(freshness_hours=72, per_feed_limit=12)
    combined += fetch_nitter_feeds(freshness_hours=24, per_feed_limit=12)

    # de-dupe by link
    seen = set()
    deduped = []
    for a in combined:
        if a["link"] in seen:
            continue
        seen.add(a["link"])
        deduped.append(a)

    deduped.sort(key=lambda x: (x["is_super_breaking"], x["is_breaking"], x["priority"], x["score"], x["published"]), reverse=True)
    return deduped
