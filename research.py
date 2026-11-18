#!/usr/bin/env python3
"""Search the web for every car in data/cars.json and add new finds to data/articles.json.

Sources (free, no keys): Google News RSS, Bing News RSS, Bing web search RSS.
A result is kept only if its title + summary contains at least one word from
every group in the car's "require" list. Articles are never deleted, so the
file becomes the car's history. Standard library only.

    python3 research.py            # all cars
    python3 research.py bmw-x5-le-mans
"""
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CARS = ROOT / "data" / "cars.json"
ARTICLES = ROOT / "data" / "articles.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"

SOURCES = {
    "Google News": "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
    "Bing News": "https://www.bing.com/news/search?q={q}&format=rss&count=50",
    "Web": "https://www.bing.com/search?q={q}&format=rss&count=50",
}
SALE_WORDS = re.compile(
    r"auction|for sale|\bsold\b|bring a trailer|bringatrailer|cars ?& ?bids|carsandbids|"
    r"sotheby|bonhams|gooding|mecum|barrett|artcurial|broad arrow|listing|hammer|bid\b",
    re.I,
)
TAG_RE = re.compile(r"<[^>]+>")


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def clean(text):
    return html.unescape(TAG_RE.sub(" ", text or "")).replace("\xa0", " ").strip()


def norm_url(url):
    p = urllib.parse.urlsplit(url.strip())
    query = "&".join(
        kv for kv in p.query.split("&") if kv and not kv.lower().startswith(("utm_", "fbclid", "ocid"))
    )
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), query, ""))


def norm_title(title):
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def parse_rss(raw, via):
    items = []
    for it in ET.fromstring(raw).iter("item"):
        title = clean(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        if "bing.com/news/apiclick" in link:  # Bing News wraps the real link in a redirect
            link = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query).get("url", [link])[0]
        source = clean(it.findtext("source")) or urllib.parse.urlsplit(link).netloc.removeprefix("www.")
        # Google News appends " - Publisher" to every title
        if via == "Google News" and source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        date = None
        if it.findtext("pubDate"):
            try:
                date = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc).date().isoformat()
            except (TypeError, ValueError):
                pass
        items.append({
            "title": title,
            "url": link,
            "source": source,
            "summary": clean(it.findtext("description"))[:300],
            "date": date,
            "via": via,
        })
    return items


def matches(item, require):
    text = (item["title"] + " " + item["summary"]).lower()
    return all(any(word.lower() in text for word in group) for group in require)


def research(car, known):
    """Return the car's articles with any new matches added."""
    searches = car.get("search") or [car["name"]]
    require = car.get("require") or [[w] for w in car["name"].split() if not w.isdigit()]
    hidden = {norm_url(u) for u in car.get("hidden", [])}
    seen_urls = {norm_url(a["url"]) for a in known}
    seen_titles = {norm_title(a["title"]) for a in known}
    found = list(known)
    stamp = now()
    for phrase in searches:
        for via, template in SOURCES.items():
            url = template.format(q=urllib.parse.quote(phrase))
            try:
                items = parse_rss(fetch(url), via)
            except Exception as e:  # one dead source should not stop the run
                print(f"  ! {via} '{phrase}': {e}", file=sys.stderr)
                continue
            for item in items:
                u, t = norm_url(item["url"]), norm_title(item["title"])
                if u in hidden or u in seen_urls or t in seen_titles or not matches(item, require):
                    continue
                seen_urls.add(u)
                seen_titles.add(t)
                item["first_seen"] = stamp
                if via == "Web":
                    item["date"] = None  # Bing's web date is a crawl date, not the publish date
                if SALE_WORDS.search(item["title"] + " " + item["summary"]):
                    item["tags"] = ["sale"]
                found.append(item)
                print(f"  + [{via}] {item['title']}")
            time.sleep(1)
    return found


def main():
    cars = json.loads(CARS.read_text())["cars"]
    data = json.loads(ARTICLES.read_text()) if ARTICLES.exists() else {"articles": {}}
    only = set(sys.argv[1:])
    ids = {c["id"] for c in cars}
    articles = {k: v for k, v in data["articles"].items() if k in ids}
    for car in cars:
        if only and car["id"] not in only:
            continue
        print(car["name"])
        before = len(articles.get(car["id"], []))
        hidden = {norm_url(u) for u in car.get("hidden", [])}
        kept = [a for a in articles.get(car["id"], []) if norm_url(a["url"]) not in hidden]
        articles[car["id"]] = research(car, kept)
        print(f"  {len(articles[car['id']]) - before:+d} new, {len(articles[car['id']])} total")
    ARTICLES.write_text(json.dumps({"updated": now(), "articles": articles}, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
