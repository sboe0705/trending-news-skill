# AGENTS.md

Guidance for coding agents working in this repository.

## Git workflow

- Always work directly on the `main` branch.
- Do not create feature branches or worktrees, and do not open pull requests.

## What this is

`trending-news` is an Agent Skill: a short briefing on the currently active Google Trends of the
last 4 hours with 10000+ searches, for the user's location. See `README.md` for user docs.

| Path | Purpose |
|---|---|
| `trending-news/SKILL.md` | The skill workflow the agent follows (frontmatter + instructions) |
| `trending-news/scripts/fetch_trends.py` | Detects the location, fetches/filters trends, adds news headlines, prints JSON |
| `trending-news/NOTICE` | Author, license, links; ships in the zip |
| `.claude/skills/trending-news` | Symlink to `trending-news/`, so the skill works inside this repo without installing |
| `tests/test_fetch_trends.py` | Offline unit tests for `fetch_trends.py` |
| `install.sh` | Symlinks (or `--copy`) the skill into a skills directory |
| `build.sh` | Validates the frontmatter, runs tests, writes `dist/trending-news.zip` |
| `release.sh` | Tags `vX.Y.Z` on `main` and pushes it |
| `.github/workflows/` | `build.yml` (tests + package on push/PR), `release.yml` (on `v*` tags) |

## Commands

```bash
python3 -m unittest discover tests          # unit tests (offline)
./build.sh                                  # frontmatter check + tests + dist/trending-news.zip
./build.sh --skip-tests                     # package only
./install.sh                                # ~/.claude/skills/trending-news -> this repo (--help for options)
./release.sh --dry-run                      # preview a release; ./release.sh tags and pushes
python3 trending-news/scripts/fetch_trends.py --help
for f in *.sh; do bash -n "$f"; done   # shell syntax check, as in CI
```

CI also parses `trending-news/scripts/*.py` with `feature_version=(3,9)` and runs the tests on
Python 3.9 and 3.x.

## Conventions and constraints

- **Python 3.9+, standard library only.** No third-party packages; keep code parseable as 3.9.
- **Tests stay offline.** They run against small inline sample pages and feeds, never the network.
- **SKILL.md frontmatter:** only the top-level keys `name`, `description`, `license`,
  `allowed-tools`, `metadata`, `compatibility` (Claude.ai rejects others). `name` must equal the
  folder name (`trending-news`); `description` is at most 1024 characters. `build.sh` enforces this.
- **Zip:** reproducible (fixed timestamps), skill folder at the root, the repo `LICENSE` added
  inside it, no caches or dotfiles.
- **Shell scripts:** bash with `set -euo pipefail`, `--help` and explicit error messages to stderr.
- **Skill output:** the briefing ends without follow-up questions, because scheduled runs save
  the output as-is. Trends without a clear reason are left out, never guessed.
- **Scheduling** is left to Claude's own scheduler; there is no scheduler in this repo. Cloud
  runs need `--geo`, because IP-based location detection would report the server's country.
- **Releases** are tagged from a clean, up-to-date `main`. `release.sh` suggests the bump from
  commit messages since the last tag: `BREAKING` → major, subject starting with `add`/`remove`
  → minor, otherwise patch. Write commit subjects with that in mind.
