# #0011 — Local-download cleanup: fix two paths that leaked disk space

**Date:** 2026-06-11
**Status:** Fixed (unit tests + e2e verified)
**Branch:** feat/openclaw
**Reporter:** jason (user check on whether successful uploads leave files behind)

## Question

After a task completes, does the worker actually delete the
file it downloaded to `/tmp/media-shuttle`? Two paths looked
under-covered.

## Audit

`core/utils.py:cleanup_local_download` is the single chokepoint
that every successful path eventually funnels into. It is
already on by default (``MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS=1``)
and is called from:

| caller | when | effect |
|---|---|---|
| `downloaders_sites/common.py:download_mock` | download failed | cleans up ✓ |
| `downloaders_sites/common.py:download_live_generic` | download failed | cleans up ✓ |
| `queue/tasks.py:process_upload_result_logic` | upload succeeded | cleans up ✓ |
| `pipeline/service.py` (sync path) | upload succeeded | cleans up ✓ |

Pre-existing e2e verification: task `4ee49e4c-...` (filester
243 MB) shipped `SUCCEEDED` and `/tmp/media-shuttle` is empty
afterwards. Happy path is fine.

## Two leaks this commit closes

### 1. Permanent-failure orphan

`process_finalize_task_logic` marks a task `FAILED` and hands
off to `_route_failure`. If `attempt >= MEDIA_SHUTTLE_MAX_RETRIES`
(``MEDIA_SHUTTLE_MAX_RETRIES``, default 2) the task will never
retry, but the local file lives on forever — every failed
upload slowly fills `/tmp/media-shuttle`.

`process_finalize_task_logic` now calls
`cleanup_local_download` for every per-source `local_path`
*before* `_route_failure` when this is the last attempt. A
retry still keeps the file (the next attempt re-uploads it
cheaply), a permanent failure reclaims the space.

### 2. Mid-download crash orphan

A worker that dies mid-download (OOM kill, SIGKILL,
`docker restart`) leaves `tmp.part` in `/tmp/media-shuttle`
forever. There is no in-flight Celery task to be racing, so
the supervisor's boot is the right place to sweep it.

`queue/worker_process.py:_sweep_orphaned_downloads` walks
`MEDIA_SHUTTLE_DOWNLOAD_DIR` once before starting the worker
subprocesses and removes any per-source seed directory
(``<download_root>/<hash16>/tmp.part``) it finds, then prunes
the empty parent. The sweep is bounded by `relative_to(download_root)`
so it can never reach outside the worker's area.

## Verification

### Unit tests (6 new, all pass)

`tests/test_cleanup.py`:

- `CleanupLocalDownloadTests.test_removes_file_inside_root`
- `CleanupLocalDownloadTests.test_removes_seed_dir_and_file`
- `CleanupLocalDownloadTests.test_refuses_path_outside_root`
  (safety net: a path under ``/tmp`` but *not* under
  ``MEDIA_SHUTTLE_DOWNLOAD_DIR`` must NOT be deleted)
- `CleanupLocalDownloadTests.test_disabled_via_env`
  (``MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS=0`` keeps file)
- `SweepOrphanedDownloadsTests.test_sweep_removes_per_source_seed_dirs`
- `SweepOrphanedDownloadsTests.test_sweep_is_safe_when_root_missing`
  (no-op when the root doesn't exist yet)

Full suite: **72 / 72 pass** (was 66, +6 new).

### Live orphan sweep

Manual reproducer:

1. `docker compose build core-worker && up -d core-worker`
2. `docker exec media-shuttle-core-worker-1 mkdir -p /tmp/media-shuttle/abc123def`
3. `docker exec media-shuttle-core-worker-1 bash -c "echo orphan > /tmp/media-shuttle/abc123def/tmp.part"`
4. `docker compose restart core-worker`
5. After boot, log line:
   `download orphan sweep removed=1 root=/tmp/media-shuttle`
6. `docker exec media-shuttle-core-worker-1 ls /tmp/media-shuttle/` →
   empty (just the directory metadata).

### End-to-end happy path

Task `b4599aee-df3c-4cff-96ac-68c51d89deda` (filester 5Q9z2x8,
243 MB) ran clean:

- `download finished` at `03:42:30`,
  `size_bytes=243362268`
- `upload finished` at `03:42:45`,
  `location=rclone://115:/test/cleanup-verify-2026-06-11/.../...mp4`
- final status `SUCCEEDED` at `03:42:45`
- `/tmp/media-shuttle` after: empty.

## What is **not** fixed here

- #0010a — `http_download` loads the full response into RAM
  (`data = response.content`) before writing. For a 3 GB file
  this peak'd at 2.97 GiB on a 3.81 GiB container. Streamed
  chunked copy is the right fix; tracked separately because
  it is a worker-wide change, not a cleanup one.
- Idempotency-key retries: if a task with the same idempotency
  key re-runs while a stale `tmp.part` is still on disk, the
  new download will simply overwrite it (because
  `materialize_path` uses a per-`download_url` SHA1 seed).
  No action needed.
