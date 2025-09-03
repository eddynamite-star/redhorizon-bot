import os
import json
import requests
from datetime import datetime
from src.feeds import fetch_rss_news, fetch_images, score_article

def load_json(file_path):
    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {} if "seen_links" in file_path else {"index": 0}

def save_json(file_path, data):
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)

def send_telegram_message(message, retry=True):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": channel_id, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("ok")
            return True
        print(f"Error: Failed to send Telegram message: {response.text}")
        if retry:
            print("Retrying once...")
            return send_telegram_message(message, retry=False)
    except Exception as e:
        print(f"Error: Telegram request failed: {e}")
    return False

def send_telegram_image(image_url, caption, retry=True):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    payload = {"chat_id": channel_id, "photo": image_url, "caption": caption}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("ok")
            return True
        print(f"Error: Failed to send Telegram image: {response.text}")
        if retry:
            print("Retrying once...")
            return send_telegram_image(image_url, caption, retry=False)
    except Exception as e:
        print(f"Error: Telegram image request failed: {e}")
    return False

def send_to_zapier(data):
    zapier_url = os.getenv("ZAPIER_HOOK_URL")
    if zapier_url:
        try:
            response = requests.post(zapier_url, json=data, timeout=10)
            if response.status_code in [200, 201]:
                print("ok [Zapier]")
                return True
            print(f"Error: Failed to send to Zapier: {response.text}")
        except Exception as e:
            print(f"Error: Zapier request failed: {e}")
    return True

def run_breaking_news():
    seen_links = load_json("data/seen_links.json")
    articles = fetch_rss_news()
    now = datetime.utcnow()
    posted_count = 0
    for article in articles[:2]:  # Throttle to 2/hour
        if (article["is_super_breaking"] or article["is_breaking"]) and article["link"] not in seen_links:
            message = f"🚨 *Breaking News*: {article['title']}\n{article['summary'][:200]}...\n[Read more]({article['link']})\nSource: {article['source']}\nReact fast! What do you think about this? Discuss on X: https://x.com/RedHorizonHub #RedHorizonHub"
            if send_telegram_message(message):
                seen_links[article["link"]] = True
                send_to_zapier({"text": message, "url": article["link"]})
                posted_count += 1
    if posted_count == 0:
        send_telegram_message("No breaking news right now—stay tuned! Discuss recent space events on X: https://x.com/RedHorizonHub #RedHorizonHub")
    save_json("data/seen_links.json", seen_links)
    return "ok" if posted_count > 0 else "No new breaking news"

def run_daily_digest():
    seen_links = load_json("data/seen_links.json")
    articles = fetch_rss_news()[:7]
    now = datetime.utcnow()
    filtered_articles = [a for a in articles if (now - a["published"]) <= timedelta(hours=24)]  # Only last 24 hours
    message = "*Daily Space Digest* 🌌\n\n"
    for i, article in enumerate(filtered_articles, 1):
        if article["link"] not in seen_links:
            message += f"{i}. 🚀 *{article['title']}* (Published {article['published'].strftime('%H:%M UTC')})\nQuick read: {article['summary'][:100]}...\n[Read more]({article['link']})\nSource: {article['source']}\n\n"
            seen_links[article["link"]] = True
    if len(filtered_articles) == 0:
        message = "No new articles in the last 24 hours. Stay tuned for more space adventures!\nDiscuss recent news on X: https://x.com/RedHorizonHub #RedHorizonHub"
    else:
        message += "What's your favorite story today? Share your thoughts! Discuss on X: https://x.com/RedHorizonHub #RedHorizonHub"
    if send_telegram_message(message):
        send_to_zapier({"text": message})
    save_json("data/seen_links.json", seen_links)
    return "ok"

def run_daily_image():
    seen_links = load_json("data/seen_links.json")
    fact_index = load_json("data/fact_index.json")
    images = fetch_images()
    for image in images:
        if image["url"] not in seen_links:
            caption = f"🌠 Image #{fact_index['index'] + 1}\nWhat a stunning view! This shows [brief context, e.g., Starship assembly]—key for Mars missions.\nSource: {image['source']} [link if available]\nWhat do you think? Discuss on X: https://x.com/RedHorizonHub #RedHorizonHub"
            if send_telegram_image(image["url"], caption):
                seen_links[image["url"]] = True
                fact_index["index"] += 1
                send_to_zapier({"image_url": image["url"], "caption": caption})
                save_json("data/seen_links.json", seen_links)
                save_json("data/fact_index.json", fact_index)
                return "ok"
    send_telegram_message("No new images today—check these sources for more: NASA, SpaceX Flickr. Discuss space visuals on X: https://x.com/RedHorizonHub #RedHorizonHub")
    return "No new images"

def run_starbase_highlight():
    facts = [
        "High Bay: Used for stacking Starship sections.",
        "Mega Bay: Larger facility for taller Starship stacking.",
        "Launch Integration Tower: Mounts Starship atop booster.",
        "Chopsticks: 'Mechazilla' arms for catching boosters.",
        "Orbital Launch Pad: Features flame trench and water deluge system.",
        "Mid Bay: Used for earlier stacking operations.",
        "Propellant Farm: Stores cryogenic fuel for Starship.",
        "Suborbital Pads: Test stands for Starship prototypes.",
        "Sanchez Site: Storage yard for Starship components.",
        "Production Tents: Initial assembly areas for Starship parts."
    ]
    fact_index = load_json("data/fact_index.json")
    fact = facts[fact_index["index"] % len(facts)]
    message = f"🏗 *Starbase Fact*: {fact}\nCool, right? This is crucial for SpaceX's Mars plans. Source: SpaceX official docs.\nShare your Starbase theories! Discuss on X: https://x.com/RedHorizonHub #RedHorizonHub"
    if send_telegram_message(message):
        fact_index["index"] += 1
        send_to_zapier({"text": message})
        save_json("data/fact_index.json", fact_index)
        return "ok"
    return "Failed"

def run_book_spotlight():
    book_index = load_json("data/book_index.json")
    books = load_json("data/book_list.json")
    book = books[book_index["index"] % len(books)]
    preview = {
        "Red Mars": "A pioneering group of 100 settlers begins terraforming Mars, facing political and environmental challenges.",
        "Green Mars": "An underground resistance fights for Martian independence amid ongoing terraforming efforts.",
        "Blue Mars": "Mars achieves freedom, but new societies must balance ecology, politics, and human evolution.",
        "The Three-Body Problem": "Humanity's first contact with aliens during China's Cultural Revolution leads to cosmic threats.",
        "How We’ll Live on Mars": "Explores the science and challenges of human colonization on the Red Planet.",
        "Packing for Mars": "A humorous look at the quirky human aspects of space travel, from biology to psychology.",
        "The Martian": "Astronaut Mark Watney survives alone on Mars using ingenuity and science.",
        "Artemis": "Smuggler Jazz Bashara uncovers a conspiracy in the Moon's first city.",
        "Project Hail Mary": "Ryland Grace races to save Earth from a stellar crisis with unexpected allies.",
        "Saturn Run": "US and China race to Saturn for alien tech in a high-stakes sci-fi thriller.",
        "Moon: A History for the Future": "Humanity's past and future relationship with the Moon, from art to exploration.",
        "Case for Mars": "Zubrin's plan for affordable Mars missions using local resources.",
        "Mars Direct": "A cost-effective strategy for human Mars landings and settlement.",
        "Mission to Mars": "Buzz Aldrin's vision for sustainable space exploration and Mars colonies.",
        "Ender’s Game": "Child genius Ender trains to defend Earth from aliens in a futuristic war.",
        "Foundation": "Hari Seldon predicts the Galactic Empire's fall and establishes a foundation to shorten dark ages.",
        "Neuromancer": "Hacker Case pulls off a cyber-heist in a dystopian future with AIs and megacorps.",
        "Contact": "Astronomer Ellie Arroway detects alien signals, leading to first contact.",
        "Exhalation": "Mechanical beings discover the universe's entropy and the beauty of existence.",
        "Children of Time": "Humans and evolved spiders clash on a terraformed planet in a tale of evolution.",
        "2312": "In a colonized solar system, a mystery unfolds amid advanced tech and human diversity.",
        "Limit": "Intrigue on the Moon as billionaires compete for resources in 2025."
    }.get(book['title'], "A captivating read on space themes.")  # Default if missing
    message = f"📚 *Book Spotlight*: _{book['title']}_ by {book['author']}\nPreview: {preview}\nSource: Book summaries from LitCharts/SuperSummary.\n[Get it here]({book['affiliate_link']})\nWorth a read? What are your thoughts? Discuss on X: https://x.com/RedHorizonHub #RedHorizonHub"
    if send_telegram_message(message):
        book_index["index"] += 1
        send_to_zapier({"text": message, "url": book["affiliate_link"]})
        save_json("data/book_index.json", book_index)
        return "ok"
    return "Failed"

def run_welcome_message():
    message = "🌌 *Welcome to Red Horizon Hub*!\nYour source for SpaceX, Starship, and Mars exploration news.\nFollow us on X: https://x.com/RedHorizonHub\nSupport us: https://buymeacoffee.com/redhorizon\nWhat do you think about Mars terraforming? Share your ideas below! #RedHorizonHub"
    if send_telegram_message(message):
        send_to_zapier({"text": message})
        return "ok"
    return "Failed"

def run_terraforming_post():
    seen_links = load_json("data/seen_links.json")
    articles = fetch_rss_news(is_terraforming=True)[:3]
    message = "*Weekly Terraforming Update* 🪐\n\n"
    for i, article in enumerate(articles, 1):
        if article["link"] not in seen_links:
            message += f"{i}. 🪐 *{article['title']}*\nQuick read: {article['summary'][:100]}...\n[Read more]({article['link']})\nSource: {article['source']}\n\n"
            seen_links[article["link"]] = True
    if len(articles) == 0:
        message = "No new terraforming articles this week. Explore more on NASA Mars site!\nWhat are your thoughts on Mars colonization? Discuss on X: https://x.com/RedHorizonHub #RedHorizonHub"
    else:
        message += "Fascinating stuff on making Mars habitable! What are your thoughts? Discuss on X: https://x.com/RedHorizonHub #RedHorizonHub"
    if send_telegram_message(message):
        send_to_zapier({"text": message})
        save_json("data/seen_links.json", seen_links)
        return "ok"
    return "Failed"
