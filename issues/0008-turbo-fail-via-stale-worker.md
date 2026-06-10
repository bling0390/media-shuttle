# #0008 — TURBO download queue missing on running worker

**Date:** 2026-06-10
**Status:** Open (verification in progress)
**Branch:** feat/openclaw
**Reporter:** jason (user verification with `https://turbo.cr/embed/qlQMU294EiaxX`)

## Symptom

`POST /v1/tasks/parse` with a turbo.cr URL returns `status=DOWNLOADING`
forever. Task record shows:
- `site: "TURBO"`
- `download_url: "https://turbo.cr/embed/qlQMU294EiaxX"` (page URL, **not** signed)
- `metadata.resolved_live: false`

`media_shuttle:task_download@TURBO` queue has the message
(`LLEN = 1`) but **no worker consumes it**.

## Root cause

`core-worker` container was last `up`'d 14 hours ago, before
`default_site_queue_suffixes()` in `core/enums.py` learned about
`FILESTER`, `TRANSFERIT`, and `TURBO`.

`generate_queue_names("download")` produced this Celery flag on
start-up (2026-06-09 17:49:32):

```
--queues=media_shuttle:task_download@GOFILE,
         media_shuttle:task_download@BUNKR,
         media_shuttle:task_download@CYBERDROP,
         media_shuttle:task_download@CYBERFILE,
         media_shuttle:task_download@PIXELDRAIN,
         media_shuttle:task_download@GD,
         media_shuttle:task_download@MEGA,
         media_shuttle:task_download@MEDIAFIRE,
         media_shuttle:task_download@SAINT,
         media_shuttle:task_download@COOMER,
         media_shuttle:task_download@YTDL,
         media_shuttle:task_download@GENERIC
```

Missing: `@FILESTER`, `@TRANSFERIT`, `@TURBO`.

`core/Dockerfile` does `COPY core ./core` at build time, so the
running container carries a frozen `enums.py` that omits the new
sites. After a `git pull` adds a new `SourceSite`, the supervisor
does not see it until `docker compose build` re-runs.

## Fix path

1. `docker compose build core-worker && docker compose up -d core-worker`
2. Confirm `core-worker` log shows the new suffixes in the
   `download` role boot line.
3. Re-queue the verification task and watch the full pipeline.

## Follow-up (defense in depth)

- The current architecture relies on the worker image being rebuilt
  whenever a new site is added. We could instead:
  - `VOLUME ./core:/app/core` in `core/Dockerfile` and rely on
    `MEDIA_SHUTTLE_FORCE_RELOAD=1`, **or**
  - Start a tiny "site_queue" supervisor that re-reads
    `default_site_queue_suffixes()` periodically and SIGTERMs the
    download worker to refresh its `--queues=` flag.
  - At minimum: add a `make verify-worker-queues` target that diffs
    the booted queues vs. the live enum and fails CI.

## Verification (post-fix)

- Parse `https://turbo.cr/embed/qlQMU294EiaxX`:
  - `download_url` should become `https://dl5.turbocdn.st/data/...mp4?exp=...&token=...`
  - `metadata.resolved_live` should be `true`
  - Final task should reach `SUCCEEDED` with a non-zero `artifacts` entry.

## Verification (post-fix) — DONE ✅

- 08:23 root cause confirmed via `docker exec` and Redis `LLEN`.
- 08:25 fixed two env defaults:
  - `/root/media-shuttle-logs/core.env`: extended
    `MEDIA_SHUTTLE_SITE_QUEUE_SUFFIXES` to 15 sites
    (added `FILESTER`, `TRANSFERIT`, `TURBO`).
  - `media-shuttle-core/docker-compose.yml` line 20: same
    extension in the inline fallback default, so a fresh
    `core.env` written by `make init` does not regress.
- 08:25 `docker compose build core-worker && up -d core-worker`.
  Boot log now shows 15 download queues
  (`@GOFILE, @BUNKR, @CYBERDROP, @CYBERFILE, @FILESTER,
  @PIXELDRAIN, @GD, @MEGA, @MEDIAFIRE, @SAINT, @TRANSFERIT,
  @TURBO, @COOMER, @YTDL, @GENERIC`).
- 08:26 re-queued `https://turbo.cr/embed/qlQMU294EiaxX`:
  - Task `286252d7-3029-4379-b08a-ca5c2563d91e` final status
    `SUCCEEDED` in 38 s.
  - `actual_download_url` =
    `https://dl4.turbocdn.st/data/qlQMU294EiaxX.mp4?exp=...&token=...`
  - `metadata.resolved_live` = `true` in worker log.
  - `size_bytes` = 24 440 834 (24 MB real .mp4).
  - `location` =
    `rclone://115:/test/turbo-2026-06-10-v2/2026-06-10/qlQMU294EiaxX/qlQMU294EiaxX.mp4`.

## Lesson (carry into #0009+)

The download queue suffix list is the **second** source of truth
for site coverage (the first being `SourceSite` enum). They must
stay in lock-step. A unit test that diffs
`default_site_queue_suffixes()` against
`MEDIA_SHUTTLE_SITE_QUEUE_SUFFIXES` would have caught this. Add
that in the next cleanup pass.
