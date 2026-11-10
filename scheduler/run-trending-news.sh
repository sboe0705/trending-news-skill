#!/usr/bin/env bash
# Runs the trending-news skill headless with Claude Code, saves the briefing as
# Markdown and shows a desktop notification. Called by the systemd user timer.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${TRENDING_NEWS_DIR:-$HOME/trending-news}"
CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude || echo "$HOME/.local/bin/claude")}"

mkdir -p "$OUT_DIR"
STAMP="$(date +%Y-%m-%d_%H%M)"
OUT_FILE="$OUT_DIR/$STAMP.md"

cd "$REPO_DIR"
if "$CLAUDE_BIN" -p "/trending-news ${TRENDING_NEWS_ARGS:-}" \
    --allowedTools "Bash(python3 *fetch_trends.py*)" "WebSearch" "WebFetch" "Skill" \
    > "$OUT_FILE.tmp" 2> "$OUT_DIR/last-error.log"; then
  mv "$OUT_FILE.tmp" "$OUT_FILE"
  ln -sfn "$OUT_FILE" "$OUT_DIR/latest.md"
  STATUS="ok"
else
  mv "$OUT_FILE.tmp" "$OUT_FILE.failed" 2>/dev/null || true
  STATUS="failed"
fi

# Desktop notification (best effort; works when a graphical session is running).
if command -v notify-send >/dev/null 2>&1; then
  if [[ "$STATUS" == ok ]]; then
    # Body: the topic names (bold markdown items), max 6 lines.
    BODY="$(grep -oP '^\s*-\s+\*\*\K[^*]+' "$OUT_FILE" | head -n 6 | sed 's/^/• /')"
    notify-send -a "Trending News" -i dialog-information \
      "Google Trends – $(date +%H:%M)" "${BODY:-Keine neuen Top-Trends}"$'\n'"$OUT_FILE" || true
  else
    notify-send -a "Trending News" -u critical "Trending News fehlgeschlagen" \
      "Siehe $OUT_DIR/last-error.log" || true
  fi
fi

[[ "$STATUS" == ok ]]
