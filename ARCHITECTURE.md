# Media Shuttle Architecture

## Modules

- `media-shuttle-core`
  - Responsibility: parse/download/upload pipeline execution and task lifecycle management.
  - Dependency rule: no Telegram-specific dependency.
- `media-shuttle-api`
  - Responsibility: external REST API, request validation, auth/rate-limit hooks, task enqueue, task query.
  - Dependency rule: never call parser/downloader/uploader implementation directly.
- `media-shuttle-tg`
  - Responsibility: Telegram commands/buttons/callbacks only. Uses API as backend.
  - Dependency rule: no direct Celery control and no direct core task execution.
- `specs`
  - Responsibility: contract source of truth.
  - Contains OpenAPI and event schemas.

## Runtime

- Queue broker/result backend: Redis (Celery)
- Metadata store: MongoDB or compatible repository adapter
- Task flow: `parse -> download -> upload`

## Contract Versioning

- Event contracts use `spec_version` such as `task.created.v1` and `task.status.v1`.
- Backward-compatible additions only within the same major version.
- Breaking change must use a new major version.

## Deploy Topology

- `api` service exposes REST.
- `core-worker` service consumes queued jobs.
- `tg` service consumes Telegram update and calls `api`.
- Shared `redis` and `mongo`.
