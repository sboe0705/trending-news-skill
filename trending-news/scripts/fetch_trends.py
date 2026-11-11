#!/usr/bin/env python3
"""Fetch current Google Trends "Trending now" topics for the user's location.

Primary source: the public "Trending now" page
  https://trends.google.com/trending?geo=XX&hours=4&sort=search-volume
which embeds the full trend table (query, start/end time, search volume,
growth, related queries, categories) in its initial HTML.

Fallback: Google's public Trending RSS feed (only the ~10 newest trends).

News context: headlines from the trending RSS feed (when the trend is in it)
plus the public Google News RSS search (last 24h) for each selected trend.
Trends that still have no headline are looked up in the GDELT DOC API.

Optional (--wikipedia): the most-read Wikipedia articles of the last full day
in the location's language (Wikimedia pageviews API), with news context too.

Filters to trends that are still active, started within the last N hours and
have at least the given approximate search volume. Prints JSON to stdout.

Location resolution order:
  1. --geo argument
  2. TRENDS_GEO environment variable
  3. IP geolocation (ipinfo.io, fallback ipapi.co)
  4. Country part of the system locale (LANG / LC_ALL, e.g. de_DE -> DE)
  5. "DE"

Standard library only, Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

PAGE_URL = "https://trends.google.com/trending"
RSS_URL = "https://trends.google.com/trending/rss"
NEWS_RSS_URL = "https://news.google.com/rss/search"
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_DELAY = 5.5  # GDELT allows one request every 5 seconds
GDELT_MAX_LOOKUPS = 8
WIKI_TOP_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top"
HT_NS = "https://trends.google.com/trending/rss"
UA = "Mozilla/5.0 (X11; Linux x86_64) trending-news-skill/1.0"
TIMEOUT = 20
HOURS_CHOICES = (4, 24, 48, 168)

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

# GDELT sourcelang names for the languages above.
GDELT_LANG = {
    "de": "german", "en": "english", "fr": "french", "es": "spanish", "it": "italian",
    "nl": "dutch", "pl": "polish", "pt": "portuguese", "sv": "swedish", "no": "norwegian",
    "da": "danish", "fi": "finnish", "cs": "czech", "tr": "turkish", "ja": "japanese",
    "ko": "korean",
}

# Wikipedia namespaces (English and local names) that are not articles.
WIKI_NON_ARTICLE = re.compile(
    r"^(Special|Spezial|Wikipedia|WP|File|Datei|Help|Hilfe|Portal|User|Benutzer|Category|"
    r"Kategorie|Template|Vorlage|Talk|Diskussion|Spécial|Fichier|Aide|Especial|Archivo|"
    r"Ayuda|Speciale|Speciaal|Specjalna|Plik|Pomoc)(_talk|_Diskussion)?:", re.I)
WIKI_MAIN_PAGES = {"-", "Main_Page", "Hauptseite", "Wikipedia:Hauptseite", "Accueil",
                   "Wikipédia:Accueil_principal", "Portada", "Pagina_principale",
                   "Hoofdpagina", "Wikipedia:Strona_główna", "Wikipédia:Página_principal"}

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


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def _rfc822_to_iso(text: str | None) -> str | None:
    try:
        return parsedate_to_datetime(text or "").astimezone(timezone.utc).isoformat()
    except Exception:
        return None


# --------------------------------------------------------------------------- location

def country_from_locale(env: dict) -> str | None:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        m = re.match(r"[a-z]{2,3}_([A-Z]{2})", env.get(var, ""))
        if m:
            return m.group(1)
    return None


def country_from_ip() -> tuple[str | None, str | None]:
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


def resolve_geo(arg_geo: str | None, env: dict | None = None, ip_lookup=country_from_ip) -> dict:
    env = os.environ if env is None else env
    if arg_geo:
        return {"geo": arg_geo.upper(), "source": "argument", "city": None}
    if env.get("TRENDS_GEO"):
        return {"geo": env["TRENDS_GEO"].upper(), "source": "TRENDS_GEO", "city": None}
    cc, city = ip_lookup()
    if cc:
        return {"geo": cc, "source": "ip-geolocation", "city": city}
    cc = country_from_locale(env)
    if cc:
        return {"geo": cc, "source": "system-locale", "city": None}
    return {"geo": "DE", "source": "default", "city": None}


# --------------------------------------------------------------------------- parsing

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


def parse_page(html: str) -> list[dict]:
    """Parse the trend table embedded in the Trending-now page (AF_initDataCallback 'ds:0')."""
    m = re.search(r"AF_initDataCallback\(\{key: 'ds:0', hash: '\d+', data:", html)
    if not m:
        raise ValueError("trend data block not found in page (layout changed or consent/captcha page)")
    data, _ = json.JSONDecoder().raw_decode(html, m.end())
    rows = (data[1] if isinstance(data, list) and len(data) > 1 else None) or []
    out = []
    for r in rows:
        # r = [query, _, geo, [start_ts], [end_ts]|None, _, volume, _, growth_pct,
        #      related_queries, category_ids, news_ids, explore_query]
        start = r[3][0] if r[3] else None
        end = r[4][0] if r[4] else None
        volume = int(r[6] or 0)
        out.append({
            "query": r[0],
            "volume": volume,
            "approx_traffic": f"{volume}+",
            "growth_pct": r[8],
            "active": end is None,
            "started_utc": _iso(start),
            "ended_utc": _iso(end),
            "related_queries": r[9] or [],
            "categories": [CATEGORIES.get(c, str(c)) for c in (r[10] or [])],
            "news": [],
        })
    return out


def parse_trends_rss(xml: bytes) -> list[dict]:
    items = []
    for item in ET.fromstring(xml).iter("item"):
        traffic = item.findtext(f"{{{HT_NS}}}approx_traffic", "")
        items.append({
            "query": item.findtext("title", "").strip(),
            "volume": parse_volume(traffic),
            "approx_traffic": traffic,
            "growth_pct": None,
            "active": None,  # unknown from RSS
            "started_utc": _rfc822_to_iso(item.findtext("pubDate")),
            "ended_utc": None,
            "related_queries": [],
            "categories": [],
            "news": [
                {
                    "title": n.findtext(f"{{{HT_NS}}}news_item_title", "").strip(),
                    "source": n.findtext(f"{{{HT_NS}}}news_item_source", "").strip(),
                    "published_utc": None,
                    "url": n.findtext(f"{{{HT_NS}}}news_item_url", "").strip(),
                }
                for n in item.findall(f"{{{HT_NS}}}news_item")
            ],
        })
    return items


def parse_news_rss(xml: bytes, limit: int = 5) -> list[dict]:
    out = []
    for item in list(ET.fromstring(xml).iter("item"))[:limit]:
        source = item.findtext("source", "").strip()
        title = item.findtext("title", "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        out.append({
            "title": title,
            "source": source,
            "published_utc": _rfc822_to_iso(item.findtext("pubDate")),
            # /rss/articles/ is a feed redirect; /articles/ opens in a browser
            "url": item.findtext("link", "").strip().replace("/rss/articles/", "/articles/"),
        })
    return out


def parse_gdelt(body: bytes, limit: int = 5) -> list[dict]:
    """Parse a GDELT DOC API artlist response. GDELT answers errors as plain text."""
    text = body.decode("utf-8", "replace").strip()
    if not text:
        return []  # no matches
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError(f"GDELT: {text[:200]}")
    out = []
    for a in (data.get("articles") or [])[:limit]:
        try:
            published = datetime.strptime(a.get("seendate", ""), "%Y%m%dT%H%M%SZ") \
                .replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            published = None
        out.append({
            "title": (a.get("title") or "").strip(),
            "source": a.get("domain") or "",
            "published_utc": published,
            "url": a.get("url") or "",
            "via": "gdelt",
        })
    return out


def gdelt_query(query: str, lang: str) -> str:
    """Quote multi-word queries as a phrase and restrict to the location's language."""
    q = query.replace('"', "").strip()
    q = f'"{q}"' if " " in q else q
    src = GDELT_LANG.get(lang)
    return f"{q} sourcelang:{src}" if src else q


def parse_wiki_top(body: bytes, lang: str, limit: int = 10) -> list[dict]:
    """Most-viewed articles from a Wikimedia pageviews 'top' response, without non-articles."""
    items = json.loads(body)["items"][0]
    out = []
    for a in items["articles"]:
        title = a["article"]
        if title in WIKI_MAIN_PAGES or WIKI_NON_ARTICLE.match(title):
            continue
        out.append({
            "title": title.replace("_", " "),
            "rank": len(out) + 1,
            "views": a["views"],
            "url": f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}",
            "news": [],
        })
        if len(out) >= limit:
            break
    return out


def select_trends(trends: list[dict], now: datetime, hours: float, min_volume: int,
                  include_ended: bool = False) -> list[dict]:
    cutoff = now - timedelta(hours=hours)
    selected = [
        t for t in trends
        if t["volume"] >= min_volume
        and (include_ended or t["active"] is not False)
        and t["started_utc"] and datetime.fromisoformat(t["started_utc"]) >= cutoff
    ]
    selected.sort(key=lambda t: (t["volume"], t["growth_pct"] or 0), reverse=True)
    return selected


def merge_news(target: list[dict], extra: list[dict]) -> None:
    seen = {n["title"] for n in target}
    target.extend(n for n in extra if n["title"] not in seen)


# --------------------------------------------------------------------------- network

def fetch_page_trends(geo: str, lang: str, hours: int) -> list[dict]:
    qs = urllib.parse.urlencode({"geo": geo, "hours": hours, "sort": "search-volume", "hl": lang})
    return parse_page(http_get(f"{PAGE_URL}?{qs}").decode("utf-8", "replace"))


def fetch_rss_trends(geo: str, lang: str) -> list[dict]:
    qs = urllib.parse.urlencode({"geo": geo, "hl": lang})
    return parse_trends_rss(http_get(f"{RSS_URL}?{qs}"))


def fetch_google_news(query: str, geo: str, lang: str, window: str = "1d") -> list[dict]:
    """Recent headlines (default last 24h) for a query from the public Google News RSS search."""
    qs = urllib.parse.urlencode({"q": f"{query} when:{window}", "hl": lang, "gl": geo, "ceid": f"{geo}:{lang}"})
    return parse_news_rss(http_get(f"{NEWS_RSS_URL}?{qs}"))


def fetch_gdelt(query: str, lang: str, timespan: str = "1d") -> list[dict]:
    qs = urllib.parse.urlencode({"query": gdelt_query(query, lang), "mode": "artlist",
                                 "maxrecords": 5, "format": "json", "timespan": timespan,
                                 "sort": "datedesc"})
    return parse_gdelt(http_get(f"{GDELT_URL}?{qs}"))


def fetch_wiki_top(lang: str, day: date) -> list[dict]:
    url = f"{WIKI_TOP_URL}/{lang}.wikipedia/all-access/{day:%Y/%m/%d}"
    return parse_wiki_top(http_get(url), lang)


def wiki_top_latest(lang: str, today: date, warnings: list[str]) -> tuple[str | None, list[dict]]:
    """Top articles of yesterday, or the day before if yesterday isn't published yet."""
    err = None
    for back in (1, 2):
        day = today - timedelta(days=back)
        try:
            return day.isoformat(), fetch_wiki_top(lang, day)
        except Exception as e:
            err = e
    warnings.append(f"Wikipedia top articles unavailable: {err}")
    return None, []


def add_google_news(items: list[dict], geo: str, lang: str, warnings: list[str],
                    key: str = "query", window: str = "1d") -> None:
    def work(t: dict) -> None:
        try:
            merge_news(t["news"], fetch_google_news(t[key], geo, lang, window))
        except Exception as e:
            warnings.append(f"Google News lookup failed for '{t[key]}': {e}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, items))


def add_gdelt_news(items: list[dict], lang: str, warnings: list[str], key: str = "query",
                   timespan: str = "1d", fetch=fetch_gdelt, sleep=time.sleep) -> None:
    """GDELT headlines for items without any news. Sequential, GDELT rate-limits hard."""
    todo = [t for t in items if not t["news"]]
    if len(todo) > GDELT_MAX_LOOKUPS:
        warnings.append(f"GDELT lookup limited to {GDELT_MAX_LOOKUPS} of {len(todo)} items without news")
        todo = todo[:GDELT_MAX_LOOKUPS]
    for i, t in enumerate(todo):
        if i:
            sleep(GDELT_DELAY)
        try:
            merge_news(t["news"], fetch(t[key], lang, timespan))
        except Exception as e:
            warnings.append(f"GDELT lookup failed for '{t[key]}': {e}")


# --------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--geo", help="Two-letter country code (overrides auto-detection)")
    ap.add_argument("--hours", type=int, default=4, choices=HOURS_CHOICES,
                    help="Time window as offered by Google Trends (default 4)")
    ap.add_argument("--min-volume", type=int, default=10000, help="Minimum approx. search volume (default 10000)")
    ap.add_argument("--include-ended", action="store_true", help="Also include trends that are no longer active")
    ap.add_argument("--lang", help="Language (default: main language of the country)")
    ap.add_argument("--no-news", action="store_true", help="Skip the news lookups (Google News and GDELT)")
    ap.add_argument("--wikipedia", action="store_true",
                    help="Also list the most-read Wikipedia articles of the last full day")
    args = ap.parse_args(argv)

    if args.geo and not re.fullmatch(r"[A-Za-z]{2}", args.geo):
        ap.error("--geo must be a two-letter country code, e.g. DE")

    loc = resolve_geo(args.geo)
    lang = args.lang or COUNTRY_LANG.get(loc["geo"], "en")
    now = datetime.now(timezone.utc)
    warnings: list[str] = []

    rss: list[dict] = []
    try:
        rss = fetch_rss_trends(loc["geo"], lang)
    except Exception as e:
        warnings.append(f"RSS feed unavailable: {e}")

    source = "trending-page"
    try:
        trends = fetch_page_trends(loc["geo"], lang, args.hours)
        news_by_query = {r["query"].lower(): r["news"] for r in rss}
        for t in trends:
            t["news"] = list(news_by_query.get(t["query"].lower(), []))
    except Exception as e:
        warnings.append(f"Trending page unavailable, fell back to RSS (only ~10 newest trends, "
                        f"active status unknown): {e}")
        trends, source = rss, "rss-fallback"
        if not rss:
            print(json.dumps({"error": "Could not fetch Google Trends data", "warnings": warnings,
                              "location": loc}, ensure_ascii=False, indent=2))
            return 1

    selected = select_trends(trends, now, args.hours, args.min_volume, args.include_ended)
    if not args.no_news:
        add_google_news(selected, loc["geo"], lang, warnings)

    wiki_day, wiki = None, []
    if args.wikipedia:
        wiki_day, wiki = wiki_top_latest(lang, now.date(), warnings)
        if not args.no_news:
            add_google_news(wiki, loc["geo"], lang, warnings, key="title", window="2d")

    if not args.no_news:
        add_gdelt_news(selected, lang, warnings)
        add_gdelt_news(wiki, lang, warnings, key="title", timespan="2d")

    out = {
        "fetched_at_utc": now.isoformat(timespec="seconds"),
        "source": source,
        "location": {**loc, "language": lang},
        "filter": {"hours": args.hours, "min_volume": args.min_volume, "active_only": not args.include_ended},
        "trends_total_in_window": len(trends),
        "trends_page": f"{PAGE_URL}?geo={loc['geo']}&hours={args.hours}&sort=search-volume&status=active",
        "warnings": warnings,
        "trends": selected,
    }
    if args.wikipedia:
        out["wikipedia"] = {"project": f"{lang}.wikipedia", "date": wiki_day, "articles": wiki}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
