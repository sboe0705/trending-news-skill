# trending-news-skill

A [Claude Code](https://claude.com/claude-code) skill that tells you what people around you
are searching for right now. It's a short news briefing on the **currently active Google
Trends of the last 4 hours with 10000+ searches**, for your current location.

```
## 🔥 Trends in Deutschland — 10.11.2026, 21:00 Uhr
*Aktive Google-Trends der letzten 4 Std. mit 10.000+ Suchanfragen*

- **Unwetter Portugal** · 10.000+ · seit 17:30 · Klima
  Wegen Sturm „Charles“ warnt Portugal vor Böen bis 150 km/h …
```

## How it works

1. `scripts/fetch_trends.py` (Python standard library only):
   - detects your country from your IP address (ipinfo.io, then ipapi.co), falling back
     to the system locale
   - reads the trend table from the public Google Trends page
     `trends.google.com/trending?geo=XX&hours=4&sort=search-volume`. If that fails, it
     falls back to the Trending RSS feed, which only has the ~10 newest trends.
   - keeps trends that are **active**, started **≤ 4 h ago** and have **≥ 10000** searches
   - adds headlines from the last 24 h from the Google News RSS search for each trend
2. Claude reads the JSON, works out why each topic is trending (and runs a web search
   when the headlines don't explain it), then writes the briefing in the local language.

## Usage

Inside this repo, the skill is picked up automatically from `.claude/skills/`:

```
/trending-news            # auto-detected location
/trending-news AT         # Austria
/trending-news US 5000    # USA, 5000+ searches
```

You can also just ask "Was trendet gerade?" or "What's trending right now?".

To use it in every project, link it into your personal skills folder:

```bash
ln -s "$PWD/.claude/skills/trending-news" ~/.claude/skills/trending-news
```

The fetch script also works on its own:

```bash
python3 .claude/skills/trending-news/scripts/fetch_trends.py --help
```

## Run it every 4 hours (systemd user timer)

```bash
./scheduler/install-timer.sh               # install + enable (runs at 0, 4, 8, 12, 16, 20 h)
systemctl --user start trending-news       # run once now
systemctl --user list-timers trending-news # next run
./scheduler/install-timer.sh --uninstall   # remove
```

Each run calls `claude -p "/trending-news"` headless and:

- saves the briefing to `~/trending-news/YYYY-MM-DD_HHMM.md`, with `latest.md` linking to the newest one
- shows a desktop notification with the topic names

You can configure it with environment variables, using `systemctl --user edit trending-news`:

| Variable            | Default            | Purpose                                   |
|---------------------|--------------------|-------------------------------------------|
| `TRENDING_NEWS_DIR` | `~/trending-news`  | Where the briefings are saved             |
| `TRENDING_NEWS_ARGS`| *(empty)*          | Skill arguments, e.g. `AT 5000`           |
| `TRENDS_GEO`        | *(auto)*           | Fixed country code instead of IP lookup   |
| `CLAUDE_BIN`        | `claude` on PATH   | Path to the Claude Code CLI               |

The timer runs **locally on purpose**. A cloud-scheduled agent would detect the cloud
server's location instead of yours. A user timer only runs while you're logged in;
`Persistent=true` catches up on missed runs. To run it while you're logged out, use
`loginctl enable-linger`.

## Limitations

- The data comes from the public Trends page and feeds, not an official API. If Google
  changes the page layout, the script falls back to RSS and reports a warning.
- Search volume is Google's rough bucket (`10000+`), not an exact number.
