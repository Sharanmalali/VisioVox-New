#!/usr/bin/env bash
# Run the whole product locally for a look at it.
#
#   ./infra/local/preview.sh          # real pipeline (GPU)
#   ./infra/local/preview.sh --mock   # mock pipeline, no GPU needed
#   ./infra/local/preview.sh --stop
#
# Everything is started with setsid so it survives the shell that launched it.
# This is the development convenience; the real deployment is deploy.sh.
set -uo pipefail
export PATH="$HOME/.nvm/versions/node/v22.23.2/bin:$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/../.."

LOGS="$HOME/logs"
mkdir -p "$LOGS"
MODE=real
[[ "${1:-}" == "--mock" ]] && MODE=mock

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

stop() {
  pkill -f "uvicorn visiovox_api.main:app" 2>/dev/null || true
  pkill -f "next start -p 3000" 2>/dev/null || true
  pkill -f "next-server" 2>/dev/null || true
  pkill -f "worker_cpu.tasks:celery_app" 2>/dev/null || true
  pkill -f "worker_gpu.tasks:celery_app" 2>/dev/null || true
}

if [[ "${1:-}" == "--stop" ]]; then
  say "Stopping"
  stop
  echo "  application stopped (containers left running)"
  exit 0
fi

say "Clearing anything from a previous run"
stop
sleep 2

# --- a GPU job of someone else's must not be trampled -----------------------
if pgrep -f "[t]rain_|[e]val_overlap" > /dev/null; then
  echo "  WARNING: a training or eval job is using the GPU."
  echo "  Starting in mock mode instead so the two do not contend."
  MODE=mock
fi

say "Infrastructure"
docker compose -f infra/docker/compose.yaml --project-directory . up -d 2>&1 | tail -2

say "Waiting for Postgres"
for _ in $(seq 1 60); do
  docker exec visiovox-postgres-1 pg_isready -U visiovox >/dev/null 2>&1 && break
  sleep 1
done

say "Migrations"
uv run alembic upgrade head 2>&1 | tail -1

say "Media volume"
if ! findmnt /srv/media >/dev/null 2>&1; then
  echo "  NOT MOUNTED — run: sudo bash infra/local/setup-media-volume.sh"
else
  df -h /srv/media | tail -1 | sed 's/^/  /'
fi

say "API on :8000"
setsid nohup env PIPELINE_MODE="$MODE" \
  uv run uvicorn visiovox_api.main:app --host 0.0.0.0 --port 8000 \
  > "$LOGS/preview-api.log" 2>&1 < /dev/null &

if [[ "$MODE" == "real" ]]; then
  say "GPU worker (real pipeline, concurrency 1)"
  setsid nohup env PIPELINE_MODE=real \
    uv run celery -A worker_gpu.tasks:celery_app worker \
    --loglevel=info --concurrency=1 --queues=gpu \
    > "$LOGS/preview-worker-gpu.log" 2>&1 < /dev/null &
fi

# Always running: the mock queue is what the seeded demo project and the /demo
# route rely on, and it costs nothing when idle.
say "Mock worker"
setsid nohup uv run celery -A worker_cpu.tasks:celery_app worker \
  --loglevel=warning --concurrency=1 \
  > "$LOGS/preview-worker-cpu.log" 2>&1 < /dev/null &

say "Web on :3000"
( cd apps/web && setsid nohup pnpm start -p 3000 -H 0.0.0.0 \
    > "$LOGS/preview-web.log" 2>&1 < /dev/null & )

say "Waiting for readiness"
for _ in $(seq 1 90); do
  curl -fsS -o /dev/null http://127.0.0.1:8000/readyz 2>/dev/null && break
  sleep 0.5
done
for _ in $(seq 1 90); do
  curl -fsS -o /dev/null http://127.0.0.1:3000/ 2>/dev/null && break
  sleep 0.5
done

echo
say "Status"
printf '  api  /readyz -> %s\n' "$(curl -s http://127.0.0.1:8000/readyz || echo DOWN)"
for p in / /upload /projects /styleguide /demo; do
  printf '  web  %-11s -> %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:3000$p")"
done
printf '  pipeline mode  -> %s\n' "$MODE"
[[ "$MODE" == "real" ]] && printf '  gpu worker     -> %s\n' \
  "$(pgrep -f 'worker_gpu.tasks' >/dev/null && echo running || echo DOWN)"
printf '  mock worker    -> %s\n' \
  "$(pgrep -f 'worker_cpu.tasks' >/dev/null && echo running || echo DOWN)"

echo
say "Open http://localhost:3000"
