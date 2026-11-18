#!/usr/bin/env python3
"""Search the web for every car in data/cars.json and add new finds to data/articles.json.

Sources (free, no keys): Google News RSS, Bing News RSS, Bing web search RSS.

A car can be a model ("Mercedes-Benz 300 SLR") or one specific car that is
"part of" another entry ("parent") and named by its identifiers ("ids":
chassis numbers, nicknames, race numbers).

A result is kept only if its title + summary matches every "Must mention"
group (one word from each). A car without its own groups uses its parent's,
and a car with ids must also mention one of them. Words match whole words,
ignoring case and accents, so "red" does not match "offered" and "Coupe"
matches "Coupé". A number like "0008/55" also matches "00008/55" and "8/55".

Articles are never deleted, so the file becomes each car's history. Standard
library only.

    python3 research.py            # all cars
    python3 research.py bmw-x5-le-mans
"""
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
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
ALERT_DAYS = 14   # sale news older than this is history, not an alert
TAG_RE = re.compile(r"<[^>]+>")
SEP = r"[\s\-–—/.]*"   # "0008/55", "0008-55" and "0008 55" are the same chassis


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def clean(text):
    return html.unescape(TAG_RE.sub(" ", text or "")).replace("\xa0", " ").strip()


def fold(text):
    """Lower case without accents: "Coupé" -> "coupe"."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def word_re(term):
    parts = [p for p in re.split(r"[\s\-–—/.]+", fold(term).strip()) if p]
    if not parts:
        return None
    lead = ""
    if parts[0].isdigit():   # chassis numbers: ignore leading zeros
        parts[0] = parts[0].lstrip("0") or "0"
        lead = "0*"
    return re.compile(r"(?<!\w)" + lead + SEP.join(re.escape(p) for p in parts) + r"(?!\w)")


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


class Plan:
    """Works out each car's searches and Must-mention groups, following "parent" links."""

    def __init__(self, cars):
        self.by_id = {c["id"]: c for c in cars}

    def parent(self, car, seen):
        p = self.by_id.get(car.get("parent") or "")
        if p is None or p["id"] in seen:   # missing or circular parent: treat as a model
            return None
        seen.add(p["id"])
        return p

    def depth(self, car):
        seen, n = {car["id"]}, 0
        while (car := self.parent(car, seen)):
            n += 1
        return n

    def require(self, car, seen=None):
        seen = seen or {car["id"]}
        parent = self.parent(car, seen)
        groups = car.get("require") or []
        if not groups:
            if parent:
                groups = self.require(parent, seen)
            elif not car.get("ids"):
                groups = [[w] for w in car["name"].split() if not w.isdigit()]
        if car.get("ids"):
            groups = groups + [car["ids"]]
        return groups

    def searches(self, car, seen=None):
        seen = seen or {car["id"]}
        parent = self.parent(car, seen)
        inherited = self.searches(parent, seen) if parent else []
        own = car.get("search") or ([] if parent else [car["name"]])
        return list(dict.fromkeys(own + self.id_searches(car, own or inherited) + inherited))

    def id_searches(self, car, base=None):
        """One search per identifier on the main search, e.g. '"300 SLR" "0008/55"'.
        Google loosens a search that ORs several identifiers together, so they are kept apart."""
        if not car.get("ids"):
            return []
        if base is None:
            seen = {car["id"]}
            parent = self.parent(car, seen)
            base = car.get("search") or (self.searches(parent, seen) if parent else [car["name"]])
        return ['%s "%s"' % (base[0], i.replace('"', "")) for i in car["ids"]]


def matches(item, groups):
    text = fold(item["title"] + " " + item["summary"])
    return all(any(rx.search(text) for rx in group) for group in groups)


_cache = {}


def search(phrase, via):
    key = (phrase, via)
    if key not in _cache:
        url = SOURCES[via].format(q=urllib.parse.quote(phrase))
        for attempt in range(3):
            try:
                _cache[key] = parse_rss(fetch(url), via)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:   # too many requests: wait, then try again
                    time.sleep(60 * (attempt + 1))
                    continue
                print(f"  ! {via} '{phrase}': {e}", file=sys.stderr)
            except Exception as e:  # one dead source should not stop the run
                print(f"  ! {via} '{phrase}': {e}", file=sys.stderr)
            _cache[key] = []
            break
        time.sleep(1.5)
    return _cache[key]


def research(car, plan, known):
    """Return the car's articles with any new matches added."""
    groups = [[rx for rx in map(word_re, g) if rx] for g in plan.require(car)]
    groups = [g for g in groups if g]
    # Google and Bing News search the whole article, so a result from a search that names the
    # car's identifiers already mentions one, even when the headline doesn't. Bing web ignores
    # quotes and OR, so its results still have to show the identifier.
    by_text = set(plan.id_searches(car)) if car.get("ids") else set()
    hidden = {norm_url(u) for u in car.get("hidden", [])}
    seen_urls = {norm_url(a["url"]) for a in known}
    seen_titles = {norm_title(a["title"]) for a in known}
    found = list(known)
    stamp = now()
    for phrase in plan.searches(car):
        for via in SOURCES:
            if phrase in by_text and via == "Web":
                continue   # Bing web ignores the quotes, so an identifier search there only adds noise
            need = groups[:-1] if phrase in by_text else groups
            for item in search(phrase, via):
                u, t = norm_url(item["url"]), norm_title(item["title"])
                if u in hidden or u in seen_urls or t in seen_titles or not matches(item, need):
                    continue
                seen_urls.add(u)
                seen_titles.add(t)
                item = dict(item, first_seen=stamp)
                if via == "Web":
                    item["date"] = None  # Bing's web date is a crawl date, not the publish date
                if SALE_WORDS.search(item["title"] + " " + item["summary"]):
                    item["tags"] = ["sale"]
                found.append(item)
                print(f"  + [{via}] {item['title']}")
    return found


def main():
    cars = json.loads(CARS.read_text())["cars"]
    plan = Plan(cars)
    data = json.loads(ARTICLES.read_text()) if ARTICLES.exists() else {"articles": {}}
    only = set(sys.argv[1:])
    ids = {c["id"] for c in cars}
    articles = {k: v for k, v in data["articles"].items() if k in ids}
    by_id = {c["id"]: c for c in cars}
    recent = (datetime.now(timezone.utc) - timedelta(days=ALERT_DAYS)).date().isoformat()
    alerts = {}
    for car in cars:
        if only and car["id"] not in only:
            continue
        print(car["name"])
        before = len(articles.get(car["id"], []))
        hidden = {norm_url(u) for u in car.get("hidden", [])}
        kept = [a for a in articles.get(car["id"], []) if norm_url(a["url"]) not in hidden]
        articles[car["id"]] = research(car, plan, kept)
        print(f"  {len(articles[car['id']]) - before:+d} new, {len(articles[car['id']])} total")
        parent = by_id.get(car.get("parent") or "")
        for a in articles[car["id"]][len(kept):]:
            # only fresh sale news: a newly added car's first search shouldn't email decades of old sales
            if "sale" in a.get("tags", []) and (a.get("date") or "") >= recent:
                alert = {"car_id": car["id"], "car": car["name"], "model": parent["name"] if parent else "",
                         "title": a["title"], "url": a["url"], "source": a["source"], "date": a["date"]}
                # an article found for several cars of one family is listed once, under the most general one
                u, depth = norm_url(a["url"]), plan.depth(car)
                if u not in alerts or depth < alerts[u][0]:
                    alerts[u] = (depth, alert)
    ARTICLES.write_text(json.dumps({"updated": now(), "articles": articles}, indent=1, ensure_ascii=False) + "\n")
    if os.environ.get("ALERTS_FILE"):
        Path(os.environ["ALERTS_FILE"]).write_text(json.dumps([a for _, a in alerts.values()], indent=1, ensure_ascii=False))
        print(f"{len(alerts)} sale alert(s)")


if __name__ == "__main__":
    main()
