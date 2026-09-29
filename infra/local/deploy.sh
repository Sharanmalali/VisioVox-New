#!/usr/bin/env bash
# One-command deploy (docs/28 §W9).
#
#   ./infra/local/deploy.sh            # build, migrate, start
#   ./infra/local/deploy.sh --tunnel   # ...and bring up cloudflared
#   ./infra/local/deploy.sh --local    # ...and publish the edge proxy on 127.0.0.1:8080
#   ./infra/local/deploy.sh --rollback # previous images, same data
#
# Without --tunnel or --local nothing is reachable from a browser: the only
# host ports are loopback ones for the GPU worker, so the tunnel is normally
# the only way in.
#
# Configuration is .env.production, not .env - see compose.prod.yaml for why.
# The tunnel token is read from secrets/cloudflare-tunnel-token.txt when
# .env.production does not set it, so it lives in exactly one gitignored file.
#
# Refuses rather than guesses: a missing secret, a root-owned media volume or a
# failed migration stops the deploy instead of producing a half-running stack
# that looks fine until someone uploads something.
set -euo pipefail

cd "$(dirname "$0")/../.."
ENV_FILE=.env.production
FILES=(-f infra/docker/compose.prod.yaml)
PROFILES=()
case "${1:-}" in
  --tunnel) PROFILES=(--profile tunnel) ;;
  # The edge proxy on loopback, so the production images can be exercised
  # before a domain exists.
  --local) FILES+=(-f infra/docker/compose.prod.local.yaml) ;;
esac
COMPOSE=(docker compose "${FILES[@]}" --project-directory . --env-file "$ENV_FILE")

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mERROR\033[0m %s\n' "$*" >&2; exit 1; }
env_get() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }

# ---------------------------------------------------------------- preflight --
say "Preflight"

[[ -f "$ENV_FILE" ]] || die "$ENV_FILE is missing. Copy .env.production.example and fill it in."

required=(
  POSTGRES_PASSWORD
  S3_ACCESS_KEY_ID
  S3_SECRET_ACCESS_KEY
  PUBLIC_API_URL
  PUBLIC_MEDIA_BASE_URL
  NEXT_PUBLIC_APP_URL
  AUDIT_IP_SALT
  FIREBASE_PROJECT_ID
  NEXT_PUBLIC_FIREBASE_API_KEY
  NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN
  NEXT_PUBLIC_FIREBASE_PROJECT_ID
  NEXT_PUBLIC_FIREBASE_APP_ID
)
missing=()
for key in "${required[@]}"; do
  grep -qE "^${key}=.+" "$ENV_FILE" || missing+=("$key")
done
[[ ${#missing[@]} -eq 0 ]] || die "missing in $ENV_FILE: ${missing[*]}"

# The dev defaults ship in this repo, so they are the ones most likely to reach
# production by accident.
if grep -qE '^AUDIT_IP_SALT=dev-only' "$ENV_FILE"; then
  die "AUDIT_IP_SALT is still the development default. Generate one: openssl rand -hex 32"
fi

# Same reasoning for the placeholder host, which fails in a more confusing way:
# the stack comes up healthy, and then every sign-in is rejected as an
# unauthorized domain.
if grep -qE '^(PUBLIC_API_URL|PUBLIC_MEDIA_BASE_URL|NEXT_PUBLIC_APP_URL)=.*visiovox\.example\.com' "$ENV_FILE"; then
  die "the public URLs are still visiovox.example.com. Set your real hostname."
fi

if [[ "${1:-}" == "--tunnel" && -z "$(env_get CLOUDFLARE_TUNNEL_TOKEN)" ]]; then
  token_file=secrets/cloudflare-tunnel-token.txt
  [[ -s "$token_file" ]] || die "no tunnel token: set CLOUDFLARE_TUNNEL_TOKEN or save it to $token_file"
  CLOUDFLARE_TUNNEL_TOKEN=$(tr -d '[:space:]' < "$token_file")
  export CLOUDFLARE_TUNNEL_TOKEN
fi

# Firebase verifies every authenticated request. Without this key the API starts
# and then answers 500 to all of them, which reads as a broken deploy rather
# than a missing file.
sa=secrets/firebase-service-account.json
[[ -f "$sa" ]] || die "$sa is missing. Firebase console -> Project settings -> Service accounts -> Generate new private key."

# Firebase rejects sign-in from a host that is not on its allow list, and
# nothing here can read that list - so state it every deploy rather than let it
# be discovered by someone who cannot log in. localhost is on that list by
# default, which is what makes --local work with no console changes.
app_host=$(env_get NEXT_PUBLIC_APP_URL | sed -E 's#^https?://##; s#(:[0-9]+)?/.*##; s#:[0-9]+$##')
if [[ "$app_host" == "localhost" || "$app_host" == "127.0.0.1" ]]; then
  say "Firebase authorized domains: localhost is allowed by default - nothing to add"
else
  say "Firebase authorized domains must include: ${app_host}"
fi

MEDIA_ROOT=$(env_get MEDIA_ROOT)
MEDIA_ROOT=${MEDIA_ROOT:-/srv/media}
[[ -d "$MEDIA_ROOT" ]] || die "$MEDIA_ROOT does not exist. Run infra/local/setup-media-volume.sh"

# The reading must come from the media volume, not from /. See docs/track-w/W0.
if [[ "$(stat -c %d "$MEDIA_ROOT")" == "$(stat -c %d /)" ]]; then
  die "$MEDIA_ROOT is on the same filesystem as /. The media volume is not mounted."
fi

# The media volume is a sparse image on a Windows drive, so its own df reports
# the image's size, not what the drive can still hold. Measure the backing
# drive when there is one: that is the number that runs out.
avail=$(df -BG --output=avail "$MEDIA_ROOT" | tail -1 | tr -dc '0-9')
backing=$(losetup -nO BACK-FILE "$(findmnt -no SOURCE "$MEDIA_ROOT")" 2>/dev/null | head -1 || true)
if [[ -n "$backing" ]]; then
  host_avail=$(df -BG --output=avail "$(dirname "$backing")" | tail -1 | tr -dc '0-9')
  if (( host_avail < avail )); then avail=$host_avail; fi
  say "Media volume: ${avail} GB free (backing drive of ${backing})"
else
  say "Media volume: ${avail} GB free"
fi
[[ "$avail" -ge 20 ]] || die "under 20 GB free for media; refusing to deploy"

mkdir -p "$MEDIA_ROOT"/{work,projects,exports} "$MEDIA_ROOT"/prod-data/{minio,postgres,redis}

# The GPU worker runs on the host and reaches the data services on their
# loopback ports (compose.prod.yaml). Its connection strings are derived here,
# every deploy, so a changed password cannot leave it talking to nothing.
pg_user=$(env_get POSTGRES_USER); pg_user=${pg_user:-visiovox}
pg_db=$(env_get POSTGRES_DB); pg_db=${pg_db:-visiovox}
umask 077
cat > .env.worker <<EOF
# Generated by infra/local/deploy.sh - do not edit; change .env.production.
ENVIRONMENT=production
DATABASE_URL=postgresql+asyncpg://${pg_user}:$(env_get POSTGRES_PASSWORD)@127.0.0.1:15432/${pg_db}
REDIS_URL=redis://127.0.0.1:16379/0
CELERY_BROKER_URL=redis://127.0.0.1:16379/1
CELERY_RESULT_BACKEND=redis://127.0.0.1:16379/2
S3_ENDPOINT_URL=http://127.0.0.1:19000
EOF
umask 022

# ------------------------------------------------------------------ rollback --
if [[ "${1:-}" == "--rollback" ]]; then
  say "Rolling back to the previously tagged images"
  docker image inspect visiovox-prod-api:previous >/dev/null 2>&1 \
    || die "no previous image tagged; nothing to roll back to"
  docker tag visiovox-prod-api:previous visiovox-prod-api:latest
  docker tag visiovox-prod-web:previous visiovox-prod-web:latest
  "${COMPOSE[@]}" up -d --no-build api web
  say "Rolled back. Data and migrations are untouched - roll forward if a migration is the problem."
  exit 0
fi

# --------------------------------------------------------------------- build --
say "Tagging the current images as :previous"
for svc in api web; do
  if docker image inspect "visiovox-prod-${svc}:latest" >/dev/null 2>&1; then
    docker tag "visiovox-prod-${svc}:latest" "visiovox-prod-${svc}:previous"
  fi
done

say "Building"
"${COMPOSE[@]}" build

say "Starting data services"
"${COMPOSE[@]}" up -d postgres redis minio minio-init

# ----------------------------------------------------------------- migrate --
# Separate from the API so a failed migration stops the deploy rather than
# crash-looping a service behind a healthcheck.
say "Running migrations"
"${COMPOSE[@]}" run --rm migrate || die "migrations failed; the previous release is still running"

# ------------------------------------------------------------------- start --
say "Starting application services"
"${COMPOSE[@]}" "${PROFILES[@]}" up -d

say "Waiting for readiness"
for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T api curl -fsS http://localhost:8000/readyz >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

"${COMPOSE[@]}" exec -T api curl -fsS http://localhost:8000/readyz >/dev/null 2>&1 \
  || die "the API never became ready. Logs: ${COMPOSE[*]} logs api"

# /readyz does not touch Firebase, and an anonymous request is refused before
# the key is read - so a key the API cannot open passes both and then fails
# every signed-in request with a 500. Check it from inside the container.
"${COMPOSE[@]}" exec -T api python -c "open('/run/secrets/firebase-service-account.json').read()" \
  >/dev/null 2>&1 \
  || die "the API cannot read secrets/firebase-service-account.json (container user vs file owner)"
"${COMPOSE[@]}" exec -T api sh -c 'test -w /srv/media/work' \
  || die "the API cannot write the media volume (container user vs /srv/media owner)"

say "GPU worker (runs on the host, not in compose)"
if systemctl cat visiovox-worker-gpu >/dev/null 2>&1; then
  # Restarted every deploy so it picks up a regenerated .env.worker. A running
  # job is allowed to finish first (TimeoutStopSec in the unit).
  if sudo -n systemctl restart visiovox-worker-gpu 2>/dev/null; then
    echo "  restarted"
  else
    echo "  could not restart without a password - sudo systemctl restart visiovox-worker-gpu"
  fi
else
  echo "  NOT installed - see infra/local/visiovox-worker-gpu.service"
fi

say "Deployed"
case "${1:-}" in
  --tunnel) say "Reachable through the tunnel at https://${app_host}" ;;
  --local)  say "Reachable at http://localhost:8080 - loopback only, not from another machine" ;;
  *)        say "No browser ingress. Use --tunnel or --local." ;;
esac
"${COMPOSE[@]}" ps --format 'table {{.Name}}\t{{.Status}}'
