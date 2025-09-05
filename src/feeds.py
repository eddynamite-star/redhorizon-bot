# feeds.py
import feedparser, re, html
from datetime import datetime, timedelta, timezone

from urllib.parse import urlparse
import re

# Prefer official/news domains over aggregators
SOURCE_WEIGHTS = {
    "nasa.gov": 5, "science.nasa.gov": 5, "mars.nasa.gov": 5,
    "spacex.com": 5, "esa.int": 5, "jaxa.jp": 5, "blueorigin.com": 4,
    "spacenews.com": 5, "space.com": 4, "spaceflightnow.com": 4,
    "nasaspaceflight.com": 4, "everydayastronaut.com": 4,
    "universetoday.com": 4, "phys.org": 3,
    "reddit.com": 1, "old.reddit.com": 1, "www.reddit.com": 1,
}

# Extra hashtags to append (rotate a few for variety on the task side)
DEFAULT_TAGS = ["#SpaceX", "#Starship", "#Falcon9", "#Mars", "#NASA", "#RedHorizon", "#Spaceflight", "#Launch"]


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
    if not text: return True
    nonlatin = re.findall(r"[^\x00-\x7F]", text)
    return len(nonlatin) < max(4, len(text)//12)
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
    # remove boilerplate words
    t = re.sub(r"\b(spacex|nasa|esa|news|update|launch|rocket|starship|falcon|mars)\b", "", t)
    return re.sub(r"\s+", " ", t).strip()

def _entry_image(entry) -> str:
    # Try common RSS media fields
    for key in ("media_content", "media_thumbnail"):
        if getattr(entry, key, None):
            cand = getattr(entry, key)[0].get("url")
            if cand: return cand
    # scan summary/content for first img
    html = getattr(entry, "summary", "") or ""
    if not html and getattr(entry, "content", None):
        html = " ".join([c.get("value","") for c in entry.content])
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I)
    return m.group(1) if m else ""


# ---------- Source lists ----------
# NEWS: Your 30 sources mapped into feeds (robust → direct; weak → best-available)
RSS_FEEDS = [
    # Official orgs
    "https://www.nasa.gov/rss/dyn/breaking_news.rss",
    "https://science.nasa.gov/feed/",
    "https://mars.nasa.gov/rss/news/",
    "https://www.esa.int/rssfeed/Our_Activities/Space_News",
    "https://www.blueorigin.com/rss/",
    # JAXA EN newsroom can be spotty—kept digest-only if parse works:
    "https://global.jaxa.jp/rss/en/index.xml",
    # Major outlets
    "https://www.space.com/feeds/all",
    "https://spacenews.com/feed/",
    "https://www.spacepolicyonline.com/feed/",
    "https://www.universetoday.com/feed/",
    "https://feeds.arstechnica.com/arstechnica/space",
    "https://www.planetary.org/rss/feed",
    # Mars / exploration orgs (digest only weight; some feeds may be sparse)
    "https://www.marssociety.org/feed/",
    "https://marsinstitute.net/feed/",
    "https://exploremars.org/feed/",
    "https://astrobiology.nasa.gov/rss/news/",
    "https://www.seti.org/rss.xml",
    # Think tanks / research (digest-low)
    "https://sei.media.mit.edu/feed/",                  # MIT SEI (if present)
    "https://www.caltech.edu/about/news/rss",          # Caltech news (broad)
    "https://www.jhuapl.edu/Content/rss/press-releases.xml",
    "https://www.rand.org/topics/space/rss.xml",
    "https://aerospace.csis.org/feed/",
    # Sci-comm / creators (digest; breaking via trusted sites instead)
    "https://everydayastronaut.com/feed/",
    "https://www.nasaspaceflight.com/feed/",
    # Community (filtered)
    "https://www.reddit.com/r/spacex.rss",
    "https://www.reddit.com/r/space.rss",
    # Schedule/launches (also used by launch scanner)
    "https://www.rocketlaunch.live/rss",
]

# Nitter (X) feeds — digest-only; used as signals to boost trusted links for breaking
# (Safe defaults; you may add/remove)
NITTER_FEEDS = [
    "https://nitter.net/SpaceX/rss",
    "https://nitter.net/NASASpaceflight/rss",
    "https://nitter.net/SpaceflightNow/rss",
    "https://nitter.net/Erdayastronaut/rss",
    "https://nitter.net/DJSnM/rss",
    "https://nitter.net/SciGuySpace/rss",
    "https://nitter.net/MarcusHouseGame/rss",
    "https://nitter.net/RGVaerialphotos/rss",
]

# IMAGES (expanded)
IMAGE_FEEDS = [
    "https://www.nasa.gov/rss/dyn/lg_image_of_the_day.rss",
    "https://mars.nasa.gov/rss/news/images/",
    "https://www.flickr.com/services/feeds/photos_public.gne?id=28634332@N05&lang=en-us&format=rss_200", # SpaceX Flickr
    "https://esahubble.org/rss/image_of_the_week/",
    "https://webb.nasa.gov/content/webbLaunch/rss.xml",
    "https://www.eso.org/public/rss/image_index.xml",
    "https://apod.nasa.gov/apod.rss",
    "https://www.uahirise.org/rss/",
    "https://www.planetary.org/rss/feed",
]

# ---------- Scoring ----------
KEYWORDS = [
    "spacex","starship","starbase","boca chica","falcon 9","falcon9","falcon-9",
    "falcon heavy","super heavy","booster","mechazilla","chopsticks","olm","olp",
    "raptor","merlin","dragon","crew dragon","cargo dragon",
    "launch","liftoff","static fire","hotfire","wdr","wet dress","stack","destack",
    "rollout","rollback","countdown","premiere","live","pad","orbital",
    "mars","terraform","habitat","isru","red planet","jezero","gale","perseverance","curiosity"
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
    "nasa.gov","science.nasa.gov","esa.int",
    "nasaspaceflight.com","spaceflightnow.com","spacenews.com",
    "arstechnica.com","universetoday.com","payloadspace.com",
    "rocketlaunch.live"
}

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

# ---------- Fetchers ----------
def fetch_rss_news(is_terraforming=False):
    articles = []
    now = datetime.utcnow()
    seen_keys = set()   # to kill near-duplicates

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
                link  = getattr(e, "link", "").strip()
                summary = getattr(e, "summary", "") or ""
                source_host = _host(link) or _host(feed_url)
                source_name = src_title or source_host
                img = _entry_image(e)

                # basic relevance
                s = score_article(title, summary)
                if is_terraforming and any(k in (title+summary).lower()
                                           for k in ["terraform", "habitability", "colonization", "isru"]):
                    s += 3

                # weight by source
                s += SOURCE_WEIGHTS.get(source_host, 2)

                # duplicate key (host + title key + slug)
                dkey = (source_host, _title_key(title), _slug(link))
                if dkey in seen_keys:
                    continue
                seen_keys.add(dkey)

                # store
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

    # prefer higher score, then newer
    return sorted(articles, key=lambda x: (x["score"], x["published"]), reverse=True)


def fetch_nitter_signals():
    """Digest-only; use as a signal to boost trusted items in breaking if very fresh."""
    sigs = []
    for url in NITTER_FEEDS:
        try:
            feed = _parse(url)
            for e in feed.entries[:15]:
                t = _entry_time(e)
                if not t or not is_recent(t, hours=1):  # only very recent signals
                    continue
                title = getattr(e,"title","")
                link  = getattr(e,"link","")
                text  = clean_html_to_text(getattr(e,"summary","") or title, 200).lower()
                if any(w in text for w in PRIORITY_WORDS):
                    sigs.append({"title": title, "link": link, "published": t})
        except Exception as ex:
            print(f"[NITTER] {url} -> {ex}")
    sigs.sort(key=lambda x: x["published"], reverse=True)
    return sigs

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
                # try media_content first
                img_url = None
                mc = getattr(e, "media_content", None)
                if mc and isinstance(mc, list) and mc[0].get("url"):
                    img_url = mc[0]["url"]
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
    imgs.sort(key=lambda x: x["published"], reverse=True)
    return imgs
