# trending-news

[![Build](https://github.com/sboe0705/trending-news-skill/actions/workflows/build.yml/badge.svg)](https://github.com/sboe0705/trending-news-skill/actions/workflows/build.yml)

An [Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) that tells you
what people around you are searching for right now. It's a short news briefing on the **currently
active Google Trends of the last 4 hours with 10000+ searches**, for your current location.
In Claude Code it can run every 4 hours on its own.

## Example

```
## 🔥 Trends in Deutschland — 10.11.2026, 21:00 Uhr
*Aktive Google-Trends der letzten 4 Std. mit 10.000+ Suchanfragen*

- **Unwetter Portugal** · 10.000+ · seit 17:30 · Klima
  Wegen Sturm „Charles“ warnt Portugal vor Böen bis 150 km/h und ruft die Bevölkerung auf,
  zu Hause zu bleiben. Für Madeira gilt Warnstufe Rot. (Berliner Zeitung)
- **Marisa Burger** · 10.000+ · seit 17:40 · Unterhaltung
  Die Schauspielerin verabschiedet sich als Frau Stockl aus den „Rosenheim-Cops“. (Joyn)

[Alle Trends ansehen](https://trends.google.com/trending?geo=DE&hours=4&sort=search-volume&status=active)
```

Use it with `/trending-news`, `/trending-news AT` (another country), `/trending-news US 5000`
(another threshold), or just ask "Was trendet gerade?" or "What's trending right now?".

## Install

The skill is the [`trending-news/`](trending-news/) folder.

**Claude Code:** clone the repository and run `install.sh`. It symlinks the skill into your
personal skills directory, so a later `git pull` updates the installed skill too:

```bash
git clone https://github.com/sboe0705/trending-news-skill.git
cd trending-news-skill
./install.sh                        # ~/.claude/skills/trending-news -> this repo
```

| Option | Effect |
|---|---|
| `--project DIR` | install into `DIR/.claude/skills` (one project only) |
| `--target DIR` | install into any skills directory, for other agents |
| `--copy` | copy the files instead of symlinking (e.g. when the repo will be moved or deleted) |
| `--force` | replace an existing `trending-news` entry |
| `--uninstall` | remove the installed skill (only a link or copy of this skill, nothing else) |

Inside this repository the skill also works without installing it, through
`.claude/skills/trending-news`, a symlink to `trending-news/`.

**Claude.ai / Claude Desktop:** download `trending-news.zip` from the
[latest release](https://github.com/sboe0705/trending-news-skill/releases/latest) and upload it
under *Settings → Capabilities → Skills*. Code execution must be turned on, and the sandbox needs
network access to `trends.google.com`, `news.google.com` and `ipinfo.io`. The sandbox runs in the
cloud, so IP detection reports the server's country, not yours. Name the country there, e.g.
"Trends in Deutschland". Or build the zip yourself:

```bash
cd trending-news-skill && ./build.sh    # → dist/trending-news.zip
```

`build.sh` checks the `SKILL.md` frontmatter (name and description limits, only keys Claude.ai
accepts), runs the unit tests, and writes a reproducible zip with the skill folder at its root and
without caches. The repo's `LICENSE` is added to the skill folder in the zip (`install.sh --copy`
copies it too). Options: `--output FILE` for another destination, `--skip-tests` to package
without testing.

**Other agents** that support the Agent Skills format: point them at the folder. The script needs
Python 3.9+ and no third-party packages.

## Run it every 4 hours

The scheduler is a systemd user timer (Linux) that runs Claude Code headless:

```bash
./scheduler/install-timer.sh               # install + enable (runs at 0, 4, 8, 12, 16, 20 h)
systemctl --user start trending-news       # run once now
systemctl --user list-timers trending-news # next run
./scheduler/install-timer.sh --uninstall   # remove
```

Each run calls `claude -p "/trending-news"` from this repository and:

- saves the briefing to `~/trending-news/YYYY-MM-DD_HHMM.md`, with `latest.md` linking to the newest one
- shows a desktop notification with the topic names

You can configure it with environment variables, using `systemctl --user edit trending-news`:

| Variable            | Default            | Purpose                                   |
|---------------------|--------------------|-------------------------------------------|
| `TRENDING_NEWS_DIR` | `~/trending-news`  | Where the briefings are saved             |
| `TRENDING_NEWS_ARGS`| *(empty)*          | Skill arguments, e.g. `AT 5000`           |
| `TRENDS_GEO`        | *(auto)*           | Fixed country code instead of IP lookup   |
| `CLAUDE_BIN`        | `claude` on PATH   | Path to the Claude Code CLI               |

The timer runs **locally on purpose**. A cloud-scheduled agent would detect the cloud server's
location instead of yours. A user timer only runs while you're logged in; `Persistent=true`
catches up on missed runs. To run it while you're logged out, use `loginctl enable-linger`.

## What's inside

| Path | Purpose |
|---|---|
| `trending-news/SKILL.md` | The workflow the agent follows (frontmatter also names the license and author) |
| `trending-news/NOTICE` | Author, license, source link and the Buy Me a Coffee link; ships in the zip but the agent doesn't load it |
| `trending-news/scripts/fetch_trends.py` | Detects the location, fetches and filters the trends, adds news headlines, prints JSON |
| `scheduler/run-trending-news.sh` | One headless run: saves the briefing and sends a desktop notification |
| `scheduler/install-timer.sh` | Installs or removes the 4-hour systemd user timer |
| `install.sh` | Symlinks (or copies) the skill into a skills directory |
| `build.sh` | Packages the skill as `dist/trending-news.zip` for upload to Claude.ai and other agents |
| `release.sh` | Tags and pushes a new release version: shows the latest release, suggests the next version |
| `tests/` | Offline unit tests for the parsing and filtering |

```bash
python3 trending-news/scripts/fetch_trends.py --help   # the script on its own
```

## How it works

1. **Location:** the `--geo` argument, then the `TRENDS_GEO` variable, then IP geolocation
   (ipinfo.io, then ipapi.co), then the system locale (`de_DE` → DE), and DE as a last resort.
2. **Trends:** the public page `trends.google.com/trending?geo=XX&hours=4&sort=search-volume`
   includes its whole trend table in the HTML: start and end time, volume, growth, related
   searches and categories. If that block is missing, the script falls back to the Trending RSS
   feed, which only has the ~10 newest trends and no active status.
3. **Filter:** active, started ≤ 4 h ago, ≥ 10000 searches, sorted by volume.
4. **Context:** headlines from the last 24 h from the Google News RSS search for each trend.
5. **Briefing:** the agent works out why each topic is trending, using a web search only when
   the headlines don't explain it, and writes the briefing in the local language. If the
   reason is unclear, it says so instead of guessing.

**Limitations:** the data comes from the public Trends page and feeds, not an official API. If
Google changes the page layout, the script falls back to RSS and reports a warning. Search
volume is Google's rough bucket (`10000+`), not an exact number.

## Development

```bash
python3 -m unittest discover tests
```

The tests run offline against small sample pages and feeds. Releases: `./release.sh` on `main`
tags `vX.Y.Z` and pushes it, and `.github/workflows/release.yml` then builds the zip and
publishes the GitHub release.

## Support

If this skill is useful to you, you can support its development with a coffee: [buymeacoffee.com/sboe0705](https://buymeacoffee.com/sboe0705) ☕

## License

[Apache 2.0](LICENSE)
