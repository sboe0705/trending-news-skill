---
name: trending-news
description: Summarizes what people are searching for right now — the currently active Google Trends ("Trending now") of the last 4 hours with 10000+ search volume, for the user's current location (auto-detected by IP). Use when the user asks what's trending, what people are talking about / searching for right now, for the latest trending news, "Google Trends", "was ist gerade los", "was trendet gerade", "aktuelle Trends", or runs the scheduled trending-news briefing.
argument-hint: "[country code e.g. DE] [min volume e.g. 5000]"
allowed-tools: Bash(python3 *fetch_trends.py*), WebSearch, WebFetch
---

# Trending News Briefing

Produce a short, scannable briefing of the topics currently trending on Google
Search for the user's location: **active trends that started within the last
4 hours and have at least 10000 searches**.

## Step 1 — Fetch the trends

Run the bundled script (it auto-detects the country from the user's IP, falling
back to the system locale):

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/fetch_trends.py"
```

Options, if the user passed arguments (`$ARGUMENTS`) or asked for something else:

- `--geo XX` — a two-letter country code, e.g. `--geo AT` (overrides auto-detection;
  the env var `TRENDS_GEO` does the same)
- `--min-volume N` — a different search-volume threshold (default `10000`)
- `--hours {4,24,48,168}` — a different time window (default `4`)
- `--include-ended` — also include trends that are no longer active

The script prints JSON:

- `location` — the detected country, city, how it was detected, and `language`
- `source` — `trending-page`, or `rss-fallback` if the page couldn't be parsed
- `warnings` — any problems. Mention them briefly in the briefing.
- `trends[]` — sorted by volume. Each has `query`, `approx_traffic`, `growth_pct`,
  `started_utc`, `related_queries`, `categories`, and `news` (up to ~5 Google News
  headlines from the last 24 h, with `source`, `published_utc` and `url`)

If the script fails or `trends` is empty, say so in one or two lines (for example
"Keine aktiven Trends mit 10.000+ Suchanfragen in den letzten 4 Stunden"), include
the `trends_page` link, and stop.

## Step 2 — Find out why each topic is trending

The search term alone usually doesn't explain the trend (for example "verpackung"
or "radfahrer"). For each trend:

1. Read its `news` headlines. Prefer headlines published shortly before or after
   `started_utc`, since those usually explain the spike. Use the `related_queries`
   to pick the right story when the headlines cover different things.
2. Only if `news` is empty, or none of the headlines plausibly explains the spike,
   run **one** `WebSearch` with the query plus a hint from the `related_queries`.
   Use the location's language, for example `marisa burger rosenheim cops heute`.
   Run all of these searches **in parallel** in one turn.
3. If it's still unclear, write "Grund unklar" (or the equivalent in the
   briefing's language) and name the most likely candidate story, marked as
   "möglicherweise". Never present a guess as fact.
4. Link one source per entry, using its headline URL.

## Step 3 — Write the briefing

Write it in `location.language`, unless the user asked for another language. Keep
it short: one line of header, then one entry per trend, two sentences at most each.

Format (German example):

```markdown
## 🔥 Trends in Deutschland — 10.11.2026, 20:58 Uhr
*Aktive Google-Trends der letzten 4 Std. mit 10.000+ Suchanfragen*

- **Paris Saint-Germain – FC Bayern** · 10.000+ · seit 20:10 · Sport
  Die Bayern-Frauen spielen in der Champions League in Paris; …
- **Unwetter Portugal** · 10.000+ · seit 17:30 · Klima
  Schwere Stürme und Überschwemmungen in …

[Alle Trends ansehen](<trends_page>)
```

Rules:

- Convert `started_utc` to the user's local time zone (shown as "seit HH:MM").
- Show the topic as it reads naturally (proper capitalization), not as the raw lowercase query.
- Merge trends that are clearly about the same event into one entry, and list the merged search terms.
- Be neutral and factual. Don't speculate about breaking events such as accidents or crimes.
- Don't add anything after the briefing: no follow-up questions or offers. In scheduled
  runs, the output is saved as-is.
