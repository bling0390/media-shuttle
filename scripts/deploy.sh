#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.yml"

CORE_REPLICAS=1
WITH_API=0
WITH_TG=0
DETACH=1
BUILD=0
DRY_RUN=0
PROJECT_NAME=""

usage() {
  cat <<'EOF'
One-click deploy for media-shuttle services.

Usage:
  ./scripts/deploy.sh [options]

Options:
  --core-replicas <n>  Number of core-worker replicas (required >= 1, default: 1)
  --with-api           Deploy media-shuttle-api
  --with-tg            Deploy media-shuttle-tg (implies --with-api)
  --build              Build images before starting
  --foreground         Run in foreground (default is detached)
  --project-name <n>   Docker Compose project name
  --dry-run            Print generated docker compose command only
  -h, --help           Show this help
EOF
}

fail() {
  echo "Error: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --core-replicas)
      [[ $# -ge 2 ]] || fail "--core-replicas requires a value"
      CORE_REPLICAS="$2"
      shift 2
      ;;
    --with-api)
      WITH_API=1
      shift
      ;;
    --with-tg)
      WITH_TG=1
      WITH_API=1
      shift
      ;;
    --build)
      BUILD=1
      shift
      ;;
    --foreground)
      DETACH=0
      shift
      ;;
    --project-name)
      [[ $# -ge 2 ]] || fail "--project-name requires a value"
      PROJECT_NAME="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
done

[[ "$CORE_REPLICAS" =~ ^[1-9][0-9]*$ ]] || fail "--core-replicas must be an integer >= 1"

SERVICES=(redis mongo core-worker)
if [[ "$WITH_API" -eq 1 ]]; then
  SERVICES+=(api)
fi
if [[ "$WITH_TG" -eq 1 ]]; then
  SERVICES+=(tg)
fi

CMD=(docker compose -f "$COMPOSE_FILE")
if [[ -n "$PROJECT_NAME" ]]; then
  CMD+=(-p "$PROJECT_NAME")
fi
CMD+=(up)
if [[ "$DETACH" -eq 1 ]]; then
  CMD+=(-d)
fi
if [[ "$BUILD" -eq 1 ]]; then
  CMD+=(--build)
fi
CMD+=(--scale "core-worker=${CORE_REPLICAS}")
CMD+=("${SERVICES[@]}")

COMMAND_STRING="$(printf '%q ' "${CMD[@]}")"

echo "core replicas: ${CORE_REPLICAS}"
echo "optional services: api=${WITH_API}, tg=${WITH_TG}"
echo "services to deploy: ${SERVICES[*]}"
echo "command: ${COMMAND_STRING}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  exit 0
fi

command -v docker >/dev/null 2>&1 || fail "docker command is required"
docker compose version >/dev/null 2>&1 || fail "docker compose is required"

"${CMD[@]}"

echo "Deploy finished."
