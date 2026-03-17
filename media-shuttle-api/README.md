# media-shuttle-api

FastAPI module for task creation/query and admin control endpoints.

## 1. Install

```bash
cd media-shuttle-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Required env

Create module-local env file:

```bash
cd media-shuttle-api
cp .env.example .env
```

Load env in current shell:

```bash
set -a
source .env
set +a
```

`.env.example` only contains the minimum variables required for this module.

Optional vars (not required):

```bash
export MEDIA_SHUTTLE_MONGO_DB='media_shuttle'
export MEDIA_SHUTTLE_MONGO_TASK_COLLECTION='tasks'
export MEDIA_SHUTTLE_MONGO_WORKER_COLLECTION='workers'
export MEDIA_SHUTTLE_CREATED_QUEUE_KEY='media_shuttle:task_created'
export MEDIA_SHUTTLE_REDIS_PUBLISH_MODE='celery'   # celery | redis_list
export MEDIA_SHUTTLE_CORE_CREATED_TASK_NAME='core.queue.tasks.process_created_event'
export MEDIA_SHUTTLE_WORKER_CONTROL_QUEUE_KEY='media_shuttle:worker_control'
export MEDIA_SHUTTLE_CORE_WORKER_CONTROL_TASK_NAME='core.queue.tasks.apply_worker_control'
export MEDIA_SHUTTLE_API_LOG_DIR='/var/log/media-shuttle-api'
export MEDIA_SHUTTLE_API_LOG_LEVEL='INFO'
export MEDIA_SHUTTLE_API_LOG_ROTATION='100 MB'
export MEDIA_SHUTTLE_API_LOG_RETENTION='7 days'
```

## 2.1 Docker Compose deploy

Module-local compose file:

```bash
cd media-shuttle-api
export MEDIA_SHUTTLE_REDIS_URL='redis://your-redis-host:6379/0'
export MEDIA_SHUTTLE_MONGO_URI='mongodb://your-mongo-host:27017'
docker compose up -d --build
```

Compose file path: `media-shuttle-api/docker-compose.yml`

Deployment notes:

- Compose only deploys `api`. Redis and MongoDB are expected to be external services.
- You must provide `MEDIA_SHUTTLE_REDIS_URL` and `MEDIA_SHUTTLE_MONGO_URI` explicitly before `docker compose up`.
- Host port mapping defaults to `8000:8000`; override with `MEDIA_SHUTTLE_API_PORT`.
- Default publish mode is `MEDIA_SHUTTLE_REDIS_PUBLISH_MODE=celery`, so the external Redis broker must be reachable by the API container and the core workers.
- API request logs are written to stdout and to `MEDIA_SHUTTLE_API_LOG_DIR/api.log`. Compose mounts this path as a named volume.

## 3. Run standalone

```bash
cd media-shuttle-api
uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log
```

## 4. Quick check

```bash
curl -X POST 'http://127.0.0.1:8000/v1/tasks/parse' \
  -H 'content-type: application/json' \
  -d '{"url":"https://example.com/file.mp4","requester_id":"u1","target":"RCLONE","destination":"bucket"}'
```
