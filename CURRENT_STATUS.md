# Current Status

Last updated: 2026-02-26

## Scope

This document records the current delivery status of the `media-shuttle` modular architecture and deployment readiness.

## Delivered Modules

1. `media-shuttle-core`
- Decoupled parse/download/upload worker pipeline.
- Queue consumption with retry and DLQ handling.
- Site-level parser/downloader routing for common providers (`gofile`, `bunkr`, `cyberdrop`, `pixeldrain`, `drive`, `mega`, `mediafire`, `coomer/kemono`, and generic direct links).
- `mock/live` mode support.
- Pluggable provider loading (builtin + dynamic modules).

2. `media-shuttle-api`
- FastAPI service for task creation, status query, and queue/admin endpoints.
- API enqueues parse tasks; core workers consume from queue.
- Backend abstraction in place for storage and queue adapters.

3. `media-shuttle-tg`
- Telegram adapter separated from core pipeline.
- Bot commands invoke API only.
- Button/callback interaction logic retained in TG module.

4. `specs`
- OpenAPI contract: `specs/openapi.yaml`.
- Event schemas:
  - `specs/events/task.created.v1.schema.json`
  - `specs/events/task.status.v1.schema.json`

## Configuration and Docs

1. Root `.env.example` removed by design.
2. Each module provides its own `.env.example` with module-required variables only.
3. Module-level standalone run docs are complete:
- `media-shuttle-core/README.md`
- `media-shuttle-api/README.md`
- `media-shuttle-tg/README.md`
- `specs/README.md`

## Deployment and Scaling

1. One-click deploy script added: `scripts/deploy.sh`.
2. `core-worker` is mandatory; `api` and `tg` are optional.
3. Horizontal scale supported via `--core-replicas <n>` for backlog draining.
4. `--dry-run` is supported for safe command preview.

## Test Status

Latest full test run command:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Latest observed result:

- `Ran 32 tests`
- `OK`

## Explicit Decisions

1. Stage 9 historical data migration is skipped.
2. New architecture starts with fresh task data.
3. `upload_alist_live` related logic has been removed from core and related modules.

## Known Follow-up

1. API-side config-driven dynamic provider loading is tracked in `OPTIMIZATION_BACKLOG.md`.
