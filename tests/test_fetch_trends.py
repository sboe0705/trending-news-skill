"""Offline unit tests for trending-news/scripts/fetch_trends.py (no network)."""

import importlib.util
import json
import pathlib
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fetch_trends", ROOT / "trending-news" / "scripts" / "fetch_trends.py")
ft = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ft)

NOW = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)
TS = lambda h: int(NOW.timestamp() - h * 3600)  # noqa: E731  (timestamp h hours before NOW)


def page_html(rows):
    data = json.dumps([None, rows], ensure_ascii=False)
    return (
        "<html><script>AF_initDataCallback({key: 'ds:1', hash: '1', data:[\"Deutschland\"], sideChannel: {}});"
        "</script><script>AF_initDataCallback({key: 'ds:0', hash: '2', data:" + data +
        ", sideChannel: {}});</script></html>"
    )


def row(query, hours_ago, volume, ended_hours_ago=None, growth=1000, related=None, cats=(11,)):
    end = [TS(ended_hours_ago)] if ended_hours_ago is not None else None
    return [query, None, "DE", [TS(hours_ago)], end, None, volume, None, growth,
            related or [query], list(cats), [[1, "de", "DE"]], query]


TRENDS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>hornissen</title><ht:approx_traffic>1000+</ht:approx_traffic>
<pubDate>Tue, 10 Nov 2026 11:30:00 -0800</pubDate>
<ht:news_item><ht:news_item_title>Hornissen-Plage</ht:news_item_title>
<ht:news_item_url>https://example.org/a</ht:news_item_url>
<ht:news_item_source>Example</ht:news_item_source></ht:news_item></item>
<item><title>big one</title><ht:approx_traffic>20.000+</ht:approx_traffic>
<pubDate>Tue, 10 Nov 2026 10:00:00 -0800</pubDate></item>
</channel></rss>""".encode()

NEWS_RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>Sturm Charles trifft Portugal - Berliner Zeitung</title>
<link>https://news.google.com/rss/articles/ABC?oc=5</link>
<pubDate>Tue, 10 Nov 2026 15:00:00 GMT</pubDate><source url="https://x">Berliner Zeitung</source></item>
<item><title>Ohne Quelle</title><link>https://news.google.com/rss/articles/DEF</link>
<pubDate>garbage</pubDate></item>
</channel></rss>""".encode()


class ParseVolumeTest(unittest.TestCase):
    def test_formats(self):
        for text, expected in [("10000+", 10000), ("10.000+", 10000), ("10,000+", 10000),
                               ("200+", 200), ("10K+", 10000), ("2M+", 2_000_000),
                               ("", 0), (None, 0), ("abc", 0)]:
            with self.subTest(text=text):
                self.assertEqual(ft.parse_volume(text), expected)


class ParsePageTest(unittest.TestCase):
    def test_rows(self):
        trends = ft.parse_page(page_html([
            row("unwetter portugal", 2.5, 10000, cats=(20,)),
            row("unfall a8", 3, 2000, ended_hours_ago=1, related=["unfall a8", "a8"], cats=(11, 99)),
        ]))
        self.assertEqual(len(trends), 2)
        a, b = trends
        self.assertEqual(a["query"], "unwetter portugal")
        self.assertEqual(a["volume"], 10000)
        self.assertEqual(a["approx_traffic"], "10000+")
        self.assertTrue(a["active"])
        self.assertIsNone(a["ended_utc"])
        self.assertEqual(a["categories"], ["Climate"])
        self.assertEqual(datetime.fromisoformat(a["started_utc"]).timestamp(), TS(2.5))
        self.assertFalse(b["active"])
        self.assertEqual(b["related_queries"], ["unfall a8", "a8"])
        self.assertEqual(b["categories"], ["Other", "99"])  # unknown ids pass through

    def test_picks_ds0_not_other_blocks(self):
        self.assertEqual([t["query"] for t in ft.parse_page(page_html([row("x", 1, 500)]))], ["x"])

    def test_empty_table(self):
        self.assertEqual(ft.parse_page(page_html(None)), [])

    def test_missing_block(self):
        with self.assertRaises(ValueError):
            ft.parse_page("<html>consent page</html>")


class ParseRssTest(unittest.TestCase):
    def test_trends_feed(self):
        items = ft.parse_trends_rss(TRENDS_RSS)
        self.assertEqual([i["query"] for i in items], ["hornissen", "big one"])
        self.assertEqual(items[1]["volume"], 20000)
        self.assertIsNone(items[0]["active"])
        self.assertEqual(items[0]["started_utc"], "2026-11-10T19:30:00+00:00")
        self.assertEqual(items[0]["news"][0]["title"], "Hornissen-Plage")
        self.assertEqual(items[1]["news"], [])

    def test_news_feed(self):
        news = ft.parse_news_rss(NEWS_RSS)
        self.assertEqual(news[0]["title"], "Sturm Charles trifft Portugal")  # " - Source" stripped
        self.assertEqual(news[0]["source"], "Berliner Zeitung")
        self.assertEqual(news[0]["url"], "https://news.google.com/articles/ABC?oc=5")
        self.assertEqual(news[0]["published_utc"], "2026-11-10T15:00:00+00:00")
        self.assertEqual(news[1]["title"], "Ohne Quelle")
        self.assertIsNone(news[1]["published_utc"])

    def test_news_limit(self):
        self.assertEqual(len(ft.parse_news_rss(NEWS_RSS, limit=1)), 1)


class SelectTrendsTest(unittest.TestCase):
    def setUp(self):
        self.trends = ft.parse_page(page_html([
            row("small", 1, 5000),
            row("old", 5, 50000),
            row("ended", 2, 20000, ended_hours_ago=1),
            row("b", 3, 10000, growth=500),
            row("a", 1, 10000, growth=1000),
            row("top", 2, 100000),
        ]))

    def test_default_filter_and_order(self):
        sel = ft.select_trends(self.trends, NOW, 4, 10000)
        self.assertEqual([t["query"] for t in sel], ["top", "a", "b"])

    def test_include_ended(self):
        sel = ft.select_trends(self.trends, NOW, 4, 10000, include_ended=True)
        self.assertEqual([t["query"] for t in sel], ["top", "ended", "a", "b"])

    def test_threshold_and_window(self):
        sel = ft.select_trends(self.trends, NOW, 24, 0)
        self.assertEqual(len(sel), 5)  # all but "ended"

    def test_rss_items_with_unknown_status_are_kept(self):
        items = ft.parse_trends_rss(TRENDS_RSS)
        sel = ft.select_trends(items, NOW, 24, 10000)
        self.assertEqual([t["query"] for t in sel], ["big one"])


class MergeNewsTest(unittest.TestCase):
    def test_dedupes_by_title(self):
        target = [{"title": "A"}]
        ft.merge_news(target, [{"title": "A"}, {"title": "B"}])
        self.assertEqual([n["title"] for n in target], ["A", "B"])


class ResolveGeoTest(unittest.TestCase):
    def no_ip(self):
        return None, None

    def test_order(self):
        ip = lambda: ("AT", "Wien")  # noqa: E731
        self.assertEqual(ft.resolve_geo("us", {"TRENDS_GEO": "FR"}, ip)["geo"], "US")
        self.assertEqual(ft.resolve_geo(None, {"TRENDS_GEO": "fr"}, ip)["geo"], "FR")
        loc = ft.resolve_geo(None, {"LANG": "de_DE.UTF-8"}, ip)
        self.assertEqual((loc["geo"], loc["source"], loc["city"]), ("AT", "ip-geolocation", "Wien"))
        self.assertEqual(ft.resolve_geo(None, {"LANG": "de_CH.UTF-8"}, self.no_ip)["source"], "system-locale")
        self.assertEqual(ft.resolve_geo(None, {"LANG": "de_CH.UTF-8"}, self.no_ip)["geo"], "CH")
        self.assertEqual(ft.resolve_geo(None, {"LANG": "C.UTF-8"}, self.no_ip),
                         {"geo": "DE", "source": "default", "city": None})


if __name__ == "__main__":
    unittest.main()
