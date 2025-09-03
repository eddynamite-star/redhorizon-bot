import feedparser
import requests
from datetime import datetime, timedelta

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
    "https://www.reddit.com/r/spacex.rss"
]

IMAGE_FEEDS = [
    "https://www.nasa.gov/rss/dyn/lg_image_of_the_day.rss",
    "https://mars.nasa.gov/rss/news/images/",
    "https://www.flickr.com/services/feeds/photos_public.gne?id=28634332@N05&lang=en-us&format=rss_200"
]

KEYWORDS = ["SpaceX", "Starship", "Elon Musk", "Mars", "Starbase", "NASA", "space exploration", "terraforming", "habitability", "Mars colonization"]

def score_article(title, summary):
    score = 0
    title_lower = title.lower()
    summary_lower = summary.lower()
    for keyword in KEYWORDS:
        if keyword.lower() in title_lower:
            score += 2 if keyword.lower() in ["starship launch", "terraforming"] else 1
        if keyword.lower() in summary_lower:
            score += 1
    return score

def fetch_rss_news(is_terraforming=False):
    articles = []
    now = datetime.utcnow()
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                published = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None)
                if not published:
                    continue
                published_dt = datetime(*published[:6])
                if (now - published_dt) > timedelta(hours=48):  # Filter <48 hours
                    continue
                is_breaking = (now - published_dt) <= timedelta(minutes=15)
                is_super_breaking = (now - published_dt) <= timedelta(minutes=5)
                title = entry.title
                link = entry.link
                summary = getattr(entry, 'summary', '')
                score = score_article(title, summary)
                if score > 5 or (is_terraforming and any(k.lower() in (title.lower() + summary.lower()) for k in ["terraforming", "habitability", "Mars colonization"])):
                    articles.append({
                        "title": title,
                        "link": link,
                        "summary": summary,
                        "published": published_dt,
                        "is_breaking": is_breaking,
                        "is_super_breaking": is_super_breaking,
                        "score": score,
                        "source": feed.feed.title if hasattr(feed, 'feed') and hasattr(feed.feed, 'title') else feed_url
                    })
        except Exception as e:
            print(f"Error fetching {feed_url}: {e}")
    return sorted(articles, key=lambda x: x["score"], reverse=True)

def fetch_images():
    images = []
    now = datetime.utcnow()
    for feed_url in IMAGE_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                published = getattr(entry, 'published_parsed', None)
                if published and (now - datetime(*published[:6])) > timedelta(hours=48):  # Filter <48 hours
                    continue
                media = getattr(entry, 'media_content', []) or [entry.get('link')]
                for item in media:
                    url = item.get('url') if isinstance(item, dict) else item
                    if url and (url.endswith('.jpg') or url.endswith('.png')):
                        images.append({"url": url, "source": feed.feed.title if hasattr(feed, 'feed') and hasattr(feed.feed, 'title') else feed_url})
        except Exception as e:
            print(f"Error fetching {feed_url}: {e}")
    return images
