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
export MEDIA_SHUTTLE_GOFILE_TOKEN='' # optional static gofile token for live mode
export MEDIA_SHUTTLE_UPLOAD_AFFINITY=1 # route upload task to downloader host queue
export MEDIA_SHUTTLE_NODE_ID='' # optional stable node id; defaults to hostname
export MEDIA_SHUTTLE_WORKER_REGISTRY_ENABLED=1 # persist worker lifecycle to Mongo workers collection
export MEDIA_SHUTTLE_WORKER_CONTROL_QUEUE_KEY='media_shuttle:worker_control'
```

When running in `live` mode with `target=RCLONE`, the `rclone` CLI must be
installed on the host/container and pre-configured (for example via
`rclone config`).

Docker build installs a custom `rclone` binary via script URL (default is the
same source used by `leech-bot`):

```bash
docker build \
  --build-arg MEDIA_SHUTTLE_RCLONE_INSTALL_SCRIPT_URL=https://raw.githubusercontent.com/wiserain/rclone/mod/install.sh \
  -t media-shuttle-core .
```

If your team uses a private `rclone` build (for example with 115 support),
replace the build-arg URL with your private install script URL.

## 3. Run worker

```bash
cd media-shuttle-core
python3 -c "from core.queue.worker_process import run_forever; raise SystemExit(run_forever())"
```

Default `MEDIA_SHUTTLE_CORE_WORKER_ROLE=all` starts four child workers in one
main process: `parse`, `download`, `upload`, `control`.

To run control worker only (accept start/stop/restart commands for this node):

```bash
MEDIA_SHUTTLE_CORE_WORKER_ROLE=control \
python3 -c "from core.queue.worker_process import run_forever; raise SystemExit(run_forever())"
```

Equivalent direct celery command:

```bash
celery -A core.queue.tasks:celery_app worker \
  --loglevel=INFO \
  --without-gossip \
  --pool=solo \
  --hostname=core-worker@media-shuttle-core \
  --queues="${MEDIA_SHUTTLE_CORE_WORKER_QUEUES}" \
  --concurrency="${MEDIA_SHUTTLE_CORE_CONCURRENCY:-1}"
```

When `MEDIA_SHUTTLE_CORE_WORKER_QUEUES` is empty, `worker_process.py` auto-generates
queue subscriptions based on `MEDIA_SHUTTLE_CORE_WORKER_ROLE`.

Site-sharded queues:

- parse worker consumes: `media_shuttle:task_retry`, `media_shuttle:task_created`
- download workers consume: `media_shuttle:task_download@SITE`
- upload workers consume: `media_shuttle:task_upload@TARGET`
- when `MEDIA_SHUTTLE_UPLOAD_AFFINITY=1`, upload workers also consume
  `media_shuttle:task_upload@TARGET@NODE` for current node; download output
  is routed back to this queue to keep download/upload on same host.

Example: dedicate one worker to GOFILE download backlog only:

```bash
MEDIA_SHUTTLE_CORE_WORKER_QUEUES='media_shuttle:task_download@GOFILE' \
MEDIA_SHUTTLE_CORE_WORKER_ROLE=download \
python3 -c "from core.queue.worker_process import run_forever; raise SystemExit(run_forever())"
```

## 4. Notes

- `mock` mode does not perform real network upload/download side effects.
- `live` mode enables live providers and requires external dependencies/services.
- For `RCLONE` live upload, `rclone` must be available in `PATH`.
- Redis production path uses Celery broker queues by default.
