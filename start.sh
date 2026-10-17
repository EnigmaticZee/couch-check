#!/bin/bash
# Runs the addon and a Cloudflare quick tunnel, then prints the https address to install in Stremio.
# The address changes on every run, so reinstall the addon in Stremio after restarting.
set -euo pipefail
cd "$(dirname "$0")"

# AI keys and other settings live in .env (git-ignored), never in the code.
if [ -f .env ]; then set -a; . ./.env; set +a; fi

PORT="${PORT:-7001}"
# No third-party packages are needed; use the project's .venv if there is one, else python3.
PYTHON="${PYTHON:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )}"
CLOUDFLARED="bin/cloudflared"
LOG="$(mktemp -t pg-tunnel)"

if [ ! -x "$CLOUDFLARED" ]; then
  echo "Missing $CLOUDFLARED — see README (Using it in Stremio)." >&2
  exit 1
fi

cleanup() { kill "${SERVER_PID:-}" "${TUNNEL_PID:-}" 2>/dev/null || true; rm -f "$LOG"; }
trap cleanup EXIT
trap 'exit 0' INT TERM

"$CLOUDFLARED" tunnel --no-autoupdate --url "http://localhost:$PORT" >"$LOG" 2>&1 &
TUNNEL_PID=$!

URL=""
for _ in $(seq 1 60); do
  URL="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG" | head -1 || true)"
  [ -n "$URL" ] && break
  sleep 0.5
done
if [ -z "$URL" ]; then
  echo "Tunnel didn't start:" >&2
  cat "$LOG" >&2
  exit 1
fi

echo
echo "  Configure page:  $URL/configure"
echo "  Manifest:        $URL/manifest.json"
echo
echo "  Open the configure page and click Install in Stremio. Ctrl+C to stop."
echo

# If the server exits (crash, or killed to load code changes), restart it; the tunnel and its address stay up.
while true; do
  PG_PUBLIC_URL="$URL" PORT="$PORT" "$PYTHON" server.py &
  SERVER_PID=$!
  wait "$SERVER_PID" || true
  echo "Server stopped; restarting in 1s (Ctrl+C to quit)..."
  sleep 1
done
