# media-shuttle monorepo

## Status

- Current delivery snapshot: `CURRENT_STATUS.md`

## Layout

- `media-shuttle-core`: parse/download/upload pipeline and worker logic.
- `media-shuttle-api`: FastAPI entrypoints for task enqueue/query/admin ops.
- `media-shuttle-tg`: Telegram adapter that calls API only.
- `specs`: OpenAPI + queue event schemas.
- `tests`: unit tests for specs/core/api/tg.

## Core site adapters

- Parsers/Downloaders currently include site-level routing for:
  - `gofile`, `bunkr`, `cyberdrop`, `cyberfile`, `pixeldrain`
  - `google drive`, `mega`, `mediafire`, `saint`, `coomer/kemono`
  - generic direct-file and ytdl-like links

## Live providers (pluggable)

- Builtin provider implementations are under `core/providers/*_builtin.py`.
- Registry entrypoints are under `core/plugins/*` and support dynamic loading.
- `core/plugins/parsers.py`: `default_registry(mode, extra_providers, extra_provider_modules)`
- `core/plugins/downloaders.py`: `default_registry(mode, extra_providers, extra_provider_modules)`
- `core/plugins/uploaders.py`: `default_registry(mode, extra_providers, extra_provider_modules)`
- `mode=live` enables live providers first, then mock providers as fallback.
- `register(...)` on each registry inserts high-priority runtime overrides.
- External provider modules can be loaded by:
  - `MEDIA_SHUTTLE_EXTRA_PROVIDER_MODULES=module_a,module_b`
  - `MEDIA_SHUTTLE_EXTRA_PROVIDER_CONFIG=/path/to/providers.json`
    - optional keys: `modules`, `parse_modules`, `download_modules`, `upload_modules`

## Stage completion status

- Stage 1: done (`ARCHITECTURE.md`, `MIGRATION_MAP.md`)
- Stage 2: done (`specs/openapi.yaml`, `specs/events/*.schema.json`)
- Stage 3-5: done (`media-shuttle-core` decoupled pipeline + JSON contract checks)
- Stage 6: done (`media-shuttle-api` FastAPI endpoints + service logic)
- Stage 7-8: done (`media-shuttle-tg` API-driven handlers and admin mappings)
- Stage 9-10: done (unit tests + deployment skeleton)
- Round 2: done (Redis/Mongo adapters + API->Core end-to-end test)

## Backend selection

- `MEDIA_SHUTTLE_STORAGE_BACKEND=memory|mongo`
- `MEDIA_SHUTTLE_QUEUE_BACKEND=memory|redis`
- `MEDIA_SHUTTLE_MONGO_URI` default: `mongodb://localhost:27017`
- `MEDIA_SHUTTLE_MONGO_DB` default: `media_shuttle`
- `MEDIA_SHUTTLE_MONGO_TASK_COLLECTION` default: `tasks`
- `MEDIA_SHUTTLE_MONGO_WORKER_COLLECTION` default: `workers`
- `MEDIA_SHUTTLE_REDIS_URL` default: `redis://localhost:6379/0`
- `MEDIA_SHUTTLE_CREATED_QUEUE_KEY` default: `media_shuttle:task_created`
- `MEDIA_SHUTTLE_RETRY_QUEUE_KEY` default: `media_shuttle:task_retry`
- `MEDIA_SHUTTLE_DOWNLOAD_QUEUE_KEY` default: `media_shuttle:task_download`
- `MEDIA_SHUTTLE_UPLOAD_QUEUE_KEY` default: `media_shuttle:task_upload`
- `MEDIA_SHUTTLE_WORKER_CONTROL_QUEUE_KEY` default: `media_shuttle:worker_control`
- `MEDIA_SHUTTLE_REDIS_PUBLISH_MODE` default: `celery` (`celery|redis_list`)
- `MEDIA_SHUTTLE_MAX_RETRIES` default: `2`
- `MEDIA_SHUTTLE_CORE_CONCURRENCY` default: `1`
- `MEDIA_SHUTTLE_CORE_POLL_SECONDS` default: `1.0`

## .env usage by module

- `media-shuttle-core`: use `media-shuttle-core/.env.example`
- `media-shuttle-api`: use `media-shuttle-api/.env.example`
- `media-shuttle-tg`: use `media-shuttle-tg/.env.example`
- `specs`: use `specs/.env.example` (no runtime env vars required)

Example:

```bash
cd media-shuttle-core
cp .env.example .env
set -a
source .env
set +a
```

## One-click deploy

Use `scripts/deploy.sh` for quick horizontal scaling.

- `core-worker` is always deployed (mandatory).
- `api` and `tg` are optional.

Examples:

```bash
# Deploy queue backends + core-worker only (consume backlog tasks quickly)
./scripts/deploy.sh --core-replicas 6

# Deploy core + api
./scripts/deploy.sh --core-replicas 3 --with-api

# Deploy core + api + tg
./scripts/deploy.sh --core-replicas 3 --with-tg

# Preview generated command without execution
./scripts/deploy.sh --core-replicas 8 --with-api --dry-run
```

## Key env vars

- Core/API backends:
  - `MEDIA_SHUTTLE_STORAGE_BACKEND=memory|mongo`
  - `MEDIA_SHUTTLE_QUEUE_BACKEND=memory|redis`
- Mongo:
  - `MEDIA_SHUTTLE_MONGO_URI`
  - `MEDIA_SHUTTLE_MONGO_DB`
  - `MEDIA_SHUTTLE_MONGO_TASK_COLLECTION`
  - `MEDIA_SHUTTLE_MONGO_WORKER_COLLECTION`
- Redis:
  - `MEDIA_SHUTTLE_REDIS_URL`
  - `MEDIA_SHUTTLE_REDIS_PUBLISH_MODE`
  - `MEDIA_SHUTTLE_CREATED_QUEUE_KEY`
  - `MEDIA_SHUTTLE_RETRY_QUEUE_KEY`
  - `MEDIA_SHUTTLE_DOWNLOAD_QUEUE_KEY`
  - `MEDIA_SHUTTLE_UPLOAD_QUEUE_KEY`
  - `MEDIA_SHUTTLE_WORKER_CONTROL_QUEUE_KEY`
  - `MEDIA_SHUTTLE_CORE_WORKER_CONTROL_TASK_NAME`
- Core runtime:
  - `MEDIA_SHUTTLE_IO_MODE=mock|live`
  - `MEDIA_SHUTTLE_GOFILE_TOKEN` (optional; if set, reuse token instead of creating temporary account token)
  - `MEDIA_SHUTTLE_MAX_RETRIES`
  - `MEDIA_SHUTTLE_CORE_CONCURRENCY`
  - `MEDIA_SHUTTLE_CORE_POLL_SECONDS`
  - `MEDIA_SHUTTLE_CORE_WORKER_ROLE=all|parse|download|upload|control`
  - `MEDIA_SHUTTLE_CORE_WORKER_QUEUES` (explicit queue override)
  - `MEDIA_SHUTTLE_WORKER_REGISTRY_ENABLED` (default `1`, persist worker status to Mongo when storage backend is `mongo`)
  - `MEDIA_SHUTTLE_SITE_QUEUE_SUFFIXES`
  - `MEDIA_SHUTTLE_UPLOAD_QUEUE_SUFFIXES`
  - `MEDIA_SHUTTLE_UPLOAD_AFFINITY` (default `1`, route upload task back to downloader host queue)
  - `MEDIA_SHUTTLE_NODE_ID` (optional, stable node identity used by affinity routing; falls back to hostname)
  - `MEDIA_SHUTTLE_DOWNLOAD_DIR`
  - `MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS` (default `1`, delete local download artifact after upload succeeds or download fails)
- Provider dynamic loading:
  - `MEDIA_SHUTTLE_EXTRA_PROVIDER_MODULES`
  - `MEDIA_SHUTTLE_EXTRA_PROVIDER_CONFIG`
- TG adapter:
  - `MEDIA_SHUTTLE_API_BASE_URL`
  - `TELEGRAM_BOT_TOKEN`
  - `TELEGRAM_API_ID`
  - `TELEGRAM_API_HASH`

## Run tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Notes

- This workspace cannot install third-party packages from network in current environment.
- FastAPI/Celery/Pyrogram runtime entrypoints are implemented but require dependency installation in deployment environment.
