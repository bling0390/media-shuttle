# media-shuttle-core

Core worker module for parse/download/upload pipeline execution.

## 1. Install

```bash
cd media-shuttle-core
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Required env

Create module-local env file:

```bash
cd media-shuttle-core
cp .env.example .env
```

Load env in current shell:

```bash
set -a
source .env
set +a
```

`.env.example` only contains the minimum variables required for this module.

Optional tuning (not required):

```bash
export MEDIA_SHUTTLE_IO_MODE=mock
export MEDIA_SHUTTLE_CORE_CONCURRENCY=3
export MEDIA_SHUTTLE_MAX_RETRIES=2
export MEDIA_SHUTTLE_CREATED_QUEUE_KEY='media_shuttle:task_created'
export MEDIA_SHUTTLE_RETRY_QUEUE_KEY='media_shuttle:task_retry'
export MEDIA_SHUTTLE_DLQ_QUEUE_KEY='media_shuttle:task_dlq'
```

## 3. Run standalone

```bash
cd media-shuttle-core
python3 -c "from core.runtime import run_forever; run_forever()"
```

## 4. Notes

- `mock` mode does not perform real network upload/download side effects.
- `live` mode enables live providers and requires external dependencies/services.
