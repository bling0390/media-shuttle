# Migration Map

## core

- `module/leech/parsers/*` -> `media-shuttle-core/core/plugins/parsers/*`
- `module/leech/downloaders/*` -> `media-shuttle-core/core/plugins/downloaders/*`
- `module/leech/uploaders/*` -> `media-shuttle-core/core/plugins/uploaders/*`
- `module/leech/adaptors/parser.py` -> `media-shuttle-core/core/pipeline/parse_stage.py`
- `module/leech/adaptors/downloader.py` -> `media-shuttle-core/core/pipeline/download_stage.py`
- `module/leech/adaptors/uploader.py` -> `media-shuttle-core/core/pipeline/upload_stage.py`
- `module/leech/beans/leech_file.py` -> `media-shuttle-core/core/models.py`
- `module/leech/beans/leech_task.py` -> `media-shuttle-core/core/models.py`
- `module/leech/constants/*` -> `media-shuttle-core/core/enums.py`
- `tool/celery_client.py` -> `media-shuttle-core/core/queue/celery_app.py`
- `tool/mongo_client.py` -> `media-shuttle-core/core/storage/repository.py`

## api

- New module with FastAPI for:
  - `POST /v1/tasks/parse`
  - `GET /v1/tasks/{task_id}`
  - `GET /v1/tasks`
  - `GET /v1/stats/queue`
- Existing TG command-path enqueue behavior replaced by API enqueue endpoint.

## tg

- `bot.py` -> `media-shuttle-tg/tg/bot.py`
- `module/leech/leech.py` -> `media-shuttle-tg/tg/handlers.py`
- `module/leech/commands/*` -> `media-shuttle-tg/tg/handlers.py` and API calls
- `module/menu/menu.py` -> `media-shuttle-tg/tg/menu.py`
- `module/monitor/monitor.py` -> API-backed monitor command
- `module/leech/utils/button.py` -> `media-shuttle-tg/tg/buttons.py`

## specs

- `specs/openapi.yaml` is REST source.
- `specs/events/task.created.v1.schema.json` and `specs/events/task.status.v1.schema.json` are queue/event source.

## Non-goals

- No historical data migration.
- New system starts with empty task dataset.
