# 0006 — Simplify 115 upload path: drop per-task intermediate prefix

## Status
**Resolved (2026-06-09 16:42 UTC).** Every new task now uploads to
`115:/<date>/<folder>/<file>` (or `115:/<sub>/<date>/<folder>/<file>`
when the caller opts in with a custom subpath). The `media-shuttle-xxx`
intermediate prefix that was present on every previous task is gone by
default.

## Background
Every previous task in this repo's history has uploaded to something
like:

```
115:/media-shuttle-album-postsecurity/2026-06-09/AliceMay/AliceMay-RA4.mp4
115:/media-shuttle-strong-auth/2026-06-09/How to Lick.../How to Lick....mp4
115:/media-shuttle-pixeldrain/2026-06-09/AliceMay/AliceMay-RA4.mp4
```

The `media-shuttle-<task-name>` segment was added to every task's
`destination` field by hand (or by curl) to namespace tasks from each
other on the 115 drive. In practice the date prefix is the only useful
namespace — task names just added an extra path component that ended up
in the way once the 115 drive started filling up.

The user has now asked for the upload path to be `115:/<date>/<folder>/<file>`
(plus `<file>` for single-file tasks) with no intermediate prefix.

## What changed
| File | Before | After |
|---|---|---|
| `media-shuttle-api/app/models.py` `CreateTaskRequest.destination` | required `str` | optional, default `""` |
| `media-shuttle-api/app/contracts.py` `validate_create_request` | required `destination` (any target) | RCLONE target defaults to `DEFAULT_RCLONE_DESTINATION="115:/"`; TELEGRAM target still requires it (no sensible default chat) |
| `media-shuttle-api/app/contracts.py` | — | new constant `DEFAULT_RCLONE_DESTINATION = "115:/"` |
| `media-shuttle-api/app/service.py` `create_parse_task` | persisted `request.destination` directly | persists the **resolved** destination so the task record and downstream worker logs show what the upload actually targets (`115:/`) rather than the caller's empty string |
| `media-shuttle-api/app/main.py` `create_parse_task` | caught only `ValueError` | also catches `TypeError` so a malformed body that omits a required field (e.g. `{}`) returns 400 instead of 500 |
| `media-shuttle-core/core/providers/uploaders_sites/rclone.py` `upload_rclone_live` | `location = f"rclone://{destination.rstrip('/')}/{remote_name}"` (double-slash bug when `destination=""`) | `location = f"rclone://{target.lstrip('/')}"` where `target` is the actual rclone destination (`115:<full_path>`), so the round-trip is exact and there's no leading `///` |
| `media-shuttle-tg/tg/api_client.py` `create_parse_task` | required `destination` keyword | `destination` is now optional; the body omits the key when it's falsy so the api can apply the default |
| `media-shuttle-tg/tg/handlers.py` `on_leech_command` | required `destination` keyword | `destination` is now optional, default `None` |
| `media-shuttle-tg/tg/bot.py` `/leech` handler | hard-coded `destination="/"` (broken: this is a bare path, not `<remote>:<path>`, so the worker fell back to local-fs rclone) | omits `destination` so the api default kicks in; the optional 3rd arg `/leech <url> <destination>` still works for callers that want a custom subpath |
| `media-shuttle-api/tests/test_contracts.py` (new) | — | 12 unit tests covering default-applied-missing/empty/whitespace, explicit-destination-preserved, target-required, target-unknown-rejected, TELEGRAM-required-and-no-default, TELEGRAM-valid-canonical-form |

The `MEDIA_SHUTTLE_USE_DATE_CATEGORY=1` env var and `build_remote_name`
function did not need any changes. They were already producing
`<date>/<folder>/<file>`; the only thing that needed to change was the
caller-supplied `destination` argument.

## Behaviour matrix

| `destination` in request | target | result |
|---|---|---|
| omitted (key absent) | RCLONE | defaults to `115:/` |
| `""` (empty string) | RCLONE | defaults to `115:/` |
| `"   "` (whitespace) | RCLONE | defaults to `115:/` |
| `"115:/"` | RCLONE | kept as `115:/` |
| `"115:/my-app"` | RCLONE | kept as `115:/my-app` |
| `"115:/some/path"` | RCLONE | kept as `115:/some/path` |
| omitted | TELEGRAM | 400 (`invalid field: destination`) |
| `""` | TELEGRAM | 400 (`invalid field: destination`) |
| `"tg://chat/1234567890"` | TELEGRAM | kept, accepted |

## Resulting upload paths

| Old | New (with new default) |
|---|---|
| `115:/media-shuttle-album-postsecurity/2026-06-09/<folder>/<file>` | `115:/2026-06-09/<folder>/<file>` |
| `115:/media-shuttle-strong-auth/2026-06-09/<folder>/<file>` | `115:/2026-06-09/<folder>/<file>` |
| `115:/media-shuttle-pixeldrain/2026-06-09/<folder>/<file>` | `115:/2026-06-09/<folder>/<file>` |
| (no such path) | `115:/my-app/2026-06-09/<folder>/<file>` (custom subpath, opt-in) |

The `/<folder>/` segment is the album/list title from the parser's
`remote_folder` (e.g. `AliceMay` for the pixeldrain list, the album
title for bunkr, the file's own name for single-file tasks). The
parser code is unchanged.

## Verification
```
$ curl -sS -X POST http://localhost:8000/v1/tasks/parse \
    -H "Content-Type: application/json" \
    -d '{"url":"https://bunkr.cr/f/xkdwMPFHh372y",
         "requester_id":"jason_v3_no_dest",
         "target":"RCLONE"}'
{"task_id":"592b4a95-5f48-4a58-905b-d3e0df9cf67f","status":"QUEUED"}

$ curl -sS http://localhost:8000/v1/tasks/592b4a95-5f48-4a58-905b-d3e0df9cf67f | jq
{
  "status": "SUCCEEDED",
  "destination": "115:/",                  # <-- default landed in mongo
  "artifacts": [
    {
      "location": "rclone://115:2026-06-09/How to Lick Pussy Right Let Lana & Luna Teach You/How to Lick Pussy Right Let Lana & Luna Teach You.mp4",
      "ok": true
    }
  ]
}

$ rclone lsf 115:/2026-06-09/
How to Lick Pussy Right Let Lana & Luna Teach You/
# ^^ no media-shuttle-xxx intermediate; just the date and the file
```

Unit tests:
```
$ python3 -m unittest tests.test_contracts -v
... 12 tests in 0.002s OK

$ cd ../media-shuttle-core && python3 -m unittest discover tests
... 33 tests in 0.035s OK
```

## Why I left `MEDIA_SHUTTLE_USE_DATE_CATEGORY=1` on
The date prefix is a useful bucket for cleanup (you can `rclone rmdir
115:/<stale-date>` to bulk-delete). It's already a knob, set to 1 in
`core.env`. The user's request was "no `media-shuttle-xxx` prefix",
not "no date prefix", so I left the date behaviour alone.

## Side fix: `tg/bot.py` was passing `destination="/"`
The Telegram bot's `/leech` handler used to hard-code `destination="/"`
because the previous contract required a non-empty value. This is
broken: `rclone copyto /tmp/.../x 115:/` works, but with `destination="/"`
the worker actually saw `("", "/")` from `_split_remote_and_path("/")`
and rclone fell back to local-fs copies. The new behaviour — let the
api default to `115:/` — fixes the TG bot's `/leech` end-to-end path
as a side effect.
