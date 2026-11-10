#!/usr/bin/env python3
"""Fetch current Google Trends "Trending now" topics for the user's location.

Primary source: the public "Trending now" page
  https://trends.google.com/trending?geo=XX&hours=4&sort=search-volume
which embeds the full trend table (query, start/end time, search volume,
growth, related queries, categories) in its initial HTML.

Fallback: Google's public Trending RSS feed (only the ~10 newest trends).

News context: headlines from the trending RSS feed (when the trend is in it)
plus the public Google News RSS search (last 24h) for each selected trend.

Filters to trends that are still active, started within the last N hours and
have at least the given approximate search volume. Prints JSON to stdout.

Location resolution order:
  1. --geo argument
  2. TRENDS_GEO environment variable
  3. IP geolocation (ipinfo.io, fallback ipapi.co)
  4. Country part of the system locale (LANG / LC_ALL, e.g. de_DE -> DE)
  5. "DE"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

PAGE_URL = "https://trends.google.com/trending"
RSS_URL = "https://trends.google.com/trending/rss"
HT_NS = "https://trends.google.com/trending/rss"
UA = "Mozilla/5.0 (X11; Linux x86_64) trending-news-skill/1.0"
TIMEOUT = 20

# Main language per country, used for hl= and as the summary language.
COUNTRY_LANG = {
    "DE": "de", "AT": "de", "CH": "de", "LI": "de",
    "US": "en", "GB": "en", "IE": "en", "AU": "en", "NZ": "en", "CA": "en", "IN": "en",
    "FR": "fr", "BE": "fr", "LU": "fr",
    "ES": "es", "MX": "es", "AR": "es", "CO": "es", "CL": "es",
    "IT": "it", "NL": "nl", "PL": "pl", "PT": "pt", "BR": "pt",
    "SE": "sv", "NO": "no", "DK": "da", "FI": "fi", "CZ": "cs",
    "TR": "tr", "JP": "ja", "KR": "ko",
}

# Google Trends "Trending now" category ids.
CATEGORIES = {
    1: "Autos & Vehicles", 2: "Beauty & Fashion", 3: "Business & Finance",
    4: "Entertainment", 5: "Food & Drink", 6: "Games", 7: "Health",
    8: "Hobbies & Leisure", 9: "Jobs & Education", 10: "Law & Government",
    11: "Other", 13: "Pets & Animals", 14: "Politics", 15: "Science",
    16: "Shopping", 17: "Sports", 18: "Technology",
    19: "Travel & Transportation", 20: "Climate",
}


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


# --------------------------------------------------------------------------- location

def geo_from_ip() -> tuple[str | None, str | None]:
    """Return (country_code, city) from IP geolocation, or (None, None)."""
    for url, ckey in (("https://ipinfo.io/json", "country"), ("https://ipapi.co/json/", "country_code")):
        try:
            data = json.loads(http_get(url))
            cc = (data.get(ckey) or "").upper()
            if re.fullmatch(r"[A-Z]{2}", cc):
                return cc, data.get("city")
        except Exception:
            continue
    return None, None


def geo_from_locale() -> str | None:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        m = re.match(r"[a-z]{2,3}_([A-Z]{2})", os.environ.get(var, ""))
        if m:
            return m.group(1)
    return None


def resolve_geo(arg_geo: str | None) -> dict:
    if arg_geo:
        return {"geo": arg_geo.upper(), "source": "argument", "city": None}
    if os.environ.get("TRENDS_GEO"):
        return {"geo": os.environ["TRENDS_GEO"].upper(), "source": "TRENDS_GEO", "city": None}
    cc, city = geo_from_ip()
    if cc:
        return {"geo": cc, "source": "ip-geolocation", "city": city}
    cc = geo_from_locale()
    if cc:
        return {"geo": cc, "source": "system-locale", "city": None}
    return {"geo": "DE", "source": "default", "city": None}


# --------------------------------------------------------------------------- sources

def fetch_page_trends(geo: str, lang: str, hours: int) -> list[dict]:
    """Parse the trend table embedded in the Trending-now page (AF_initDataCallback 'ds:0')."""
    qs = urllib.parse.urlencode({"geo": geo, "hours": hours, "sort": "search-volume", "hl": lang})
    html = http_get(f"{PAGE_URL}?{qs}").decode("utf-8", "replace")
    m = re.search(r"AF_initDataCallback\(\{key: 'ds:0', hash: '\d+', data:", html)
    if not m:
        raise ValueError("trend data block not found in page (layout changed or consent/captcha page)")
    data, _ = json.JSONDecoder().raw_decode(html, m.end())
    rows = data[1] or []
    out = []
    for r in rows:
        # r = [query, _, geo, [start_ts], [end_ts]|None, _, volume, _, growth_pct,
        #      related_queries, category_ids, news_ids, explore_query]
        start = r[3][0] if r[3] else None
        end = r[4][0] if r[4] else None
        out.append({
            "query": r[0],
            "volume": int(r[6] or 0),
            "approx_traffic": f"{int(r[6] or 0)}+",
            "growth_pct": r[8],
            "active": end is None,
            "started_utc": datetime.fromtimestamp(start, timezone.utc).isoformat() if start else None,
            "ended_utc": datetime.fromtimestamp(end, timezone.utc).isoformat() if end else None,
            "related_queries": r[9] or [],
            "categories": [CATEGORIES.get(c, str(c)) for c in (r[10] or [])],
            "news": [],
        })
    return out


def parse_volume(text: str) -> int:
    """'10000+', '10.000+', '10,000+', '10K+', '2M+' -> int."""
    t = (text or "").strip().upper().replace("+", "").replace(" ", "")
    mult = 1
    if t.endswith("K"):
        mult, t = 1_000, t[:-1]
    elif t.endswith("M"):
        mult, t = 1_000_000, t[:-1]
    digits = re.sub(r"[^\d]", "", t)
    return int(digits) * mult if digits else 0


def fetch_rss_trends(geo: str, lang: str) -> list[dict]:
    qs = urllib.parse.urlencode({"geo": geo, "hl": lang})
    root = ET.fromstring(http_get(f"{RSS_URL}?{qs}"))
    items = []
    for item in root.iter("item"):
        traffic = item.findtext(f"{{{HT_NS}}}approx_traffic", "")
        try:
            started = parsedate_to_datetime(item.findtext("pubDate", "")).astimezone(timezone.utc)
        except Exception:
            started = None
        items.append({
            "query": item.findtext("title", "").strip(),
            "volume": parse_volume(traffic),
            "approx_traffic": traffic,
            "growth_pct": None,
            "active": None,  # unknown from RSS
            "started_utc": started.isoformat() if started else None,
            "ended_utc": None,
            "related_queries": [],
            "categories": [],
            "news": [
                {
                    "title": n.findtext(f"{{{HT_NS}}}news_item_title", "").strip(),
                    "source": n.findtext(f"{{{HT_NS}}}news_item_source", "").strip(),
                    "url": n.findtext(f"{{{HT_NS}}}news_item_url", "").strip(),
                }
                for n in item.findall(f"{{{HT_NS}}}news_item")
            ],
        })
    return items


def fetch_google_news(query: str, geo: str, lang: str, limit: int = 5) -> list[dict]:
    """Recent (last 24h) headlines for a query from the public Google News RSS search."""
    qs = urllib.parse.urlencode({"q": f"{query} when:1d", "hl": lang, "gl": geo, "ceid": f"{geo}:{lang}"})
    root = ET.fromstring(http_get(f"https://news.google.com/rss/search?{qs}"))
    out = []
    for item in list(root.iter("item"))[:limit]:
        source = item.findtext("source", "").strip()
        title = item.findtext("title", "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        try:
            published = parsedate_to_datetime(item.findtext("pubDate", "")).astimezone(timezone.utc).isoformat()
        except Exception:
            published = None
        out.append({"title": title, "source": source, "published_utc": published,
                    "url": item.findtext("link", "").strip().replace("/rss/articles/", "/articles/")})
    return out


def add_google_news(trends: list[dict], geo: str, lang: str, warnings: list[str]) -> None:
    from concurrent.futures import ThreadPoolExecutor

    def work(t: dict) -> None:
        try:
            seen = {n["title"] for n in t["news"]}
            t["news"] += [n for n in fetch_google_news(t["query"], geo, lang) if n["title"] not in seen]
        except Exception as e:
            warnings.append(f"Google News lookup failed for '{t['query']}': {e}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, trends))


# --------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--geo", help="Two-letter country code (overrides auto-detection)")
    ap.add_argument("--hours", type=int, default=4, choices=[4, 24, 48, 168],
                    help="Time window as offered by Google Trends (default 4)")
    ap.add_argument("--min-volume", type=int, default=10000, help="Minimum approx. search volume (default 10000)")
    ap.add_argument("--include-ended", action="store_true", help="Also include trends that are no longer active")
    ap.add_argument("--lang", help="Language (default: main language of the country)")
    ap.add_argument("--no-news", action="store_true", help="Skip the Google News headline lookup")
    args = ap.parse_args()

    loc = resolve_geo(args.geo)
    lang = args.lang or COUNTRY_LANG.get(loc["geo"], "en")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=args.hours)
    warnings: list[str] = []

    rss: list[dict] = []
    try:
        rss = fetch_rss_trends(loc["geo"], lang)
    except Exception as e:
        warnings.append(f"RSS feed unavailable: {e}")

    source = "trending-page"
    try:
        trends = fetch_page_trends(loc["geo"], lang, args.hours)
    except Exception as e:
        warnings.append(f"Trending page unavailable, fell back to RSS (only ~10 newest trends, "
                        f"active status unknown): {e}")
        trends, source = rss, "rss-fallback"

    if source == "trending-page":
        # Attach RSS news headlines to matching page trends.
        news_by_query = {r["query"].lower(): r["news"] for r in rss}
        for t in trends:
            t["news"] = news_by_query.get(t["query"].lower(), [])

    if not trends and not warnings:
        warnings.append("No trends returned at all.")
    if source == "rss-fallback" and not rss:
        print(json.dumps({"error": "Could not fetch Google Trends data", "warnings": warnings,
                          "location": loc}, ensure_ascii=False, indent=2))
        return 1

    selected = [
        t for t in trends
        if t["volume"] >= args.min_volume
        and (args.include_ended or t["active"] is not False)
        and t["started_utc"] and datetime.fromisoformat(t["started_utc"]) >= cutoff
    ]
    selected.sort(key=lambda t: (t["volume"], t["growth_pct"] or 0), reverse=True)
    if not args.no_news:
        add_google_news(selected, loc["geo"], lang, warnings)

    print(json.dumps({
        "fetched_at_utc": now.isoformat(timespec="seconds"),
        "source": source,
        "location": {**loc, "language": lang},
        "filter": {"hours": args.hours, "min_volume": args.min_volume, "active_only": not args.include_ended},
        "trends_total_in_window": len(trends),
        "trends_page": f"{PAGE_URL}?geo={loc['geo']}&hours={args.hours}&sort=search-volume&status=active",
        "warnings": warnings,
        "trends": selected,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
