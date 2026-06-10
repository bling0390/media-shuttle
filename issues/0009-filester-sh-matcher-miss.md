# #0009 — FILESTER matcher missed the `filester.sh` mirror domain

**Date:** 2026-06-10
**Status:** Fixed (e2e verified)
**Branch:** feat/openclaw
**Reporter:** jason (user verification with `https://filester.sh/d/eRtZ6DI`)

## Symptom

A filester link under the `filester.sh` mirror domain was
silently treated as GENERIC by `parser_registry.parse` and the
47-byte HTML page was uploaded to 115, marked `SUCCEEDED`.

`POST /v1/tasks/parse` task `af8c572e-7eba-45e7-b9cc-c5a87efbe323`:

- `site: "GENERIC"` (should be `FILESTER`)
- `size_bytes: 47` (47 B HTML, not a 2.45 GB mp4)
- `file_name: "eRtZ6DI"` (slug, not real title)
- `status: SUCCEEDED` ❌
- artifact location:
  `rclone://115:/test/filester-2026-06-10/2026-06-10/filester.sh/eRtZ6DI`

## Root cause

`is_filester()` in
`core/providers/parsers_sites/filester.py` accepted **only**
`filester.me` (and subdomains of it). Filester aliases the same
backend under several landing TLDs (`.sh` is the regional
mirror that survives the most blocks) and the matcher was
never updated. The new URL fell through to
`generic_fallback` (see #0007 for the same shape of bug on
COOMER) which downloads the page HTML and marks the task
succeeded.

The lack of any unit test for `is_filester` (a quick
`tests/test_core_site_plugins.py` grep returned zero hits) is
the underlying defect that let this ship.

## Fix

1. `is_filester()` now accepts any `filester.<tld>` from a
   known-TLDs tuple (currently `("me", "sh")). The default
   API origin stays `filester.me` (canonical) but new mirrors
   Just Work because the same `/api/public/download` endpoint
   and `cache1.filester.me` CDN serve all landing domains.
   This was confirmed live: a token issued by
   `POST https://filester.sh/api/public/download` is
   download-ready on `https://cache1.filester.me/d/<token>?download=true`.

2. The `cache1.filester.sh` host does not exist, so the CDN
   default stays `cache1.filester.me` (cross-domain). The
   override `MEDIA_SHUTTLE_FILESTER_CDN_ORIGIN` still works
   for any future standalone mirror.

3. `parse_filester_live` and `resolve_filester_source` did
   not need code changes: both delegate to the (now-wider)
   matcher and reuse the existing default origin.

## Verification

| task | URL | bytes | result |
|---|---|---|---|
| `af8c572e-...` (pre-fix) | `https://filester.sh/d/eRtZ6DI` | 47 (HTML) | SUCCEEDED — wrong |
| `787392b6-...` (post-fix, large) | `https://filester.sh/d/eRtZ6DI` | aborted at 2.45 GB to keep verification snappy | (see #0009a) |
| `4ee49e4c-...` (post-fix, small) | `https://filester.sh/d/5Q9z2x8` | 243 362 268 (243 MB mp4) | SUCCEEDED ✅ |

The small-file task `4ee49e4c` trace:

- `download started site=FILESTER
  file_name=hr_260220_COVERED_IN_CUM_IN_BUSTY_BUKKAKE_MASTURBATION_SOLO_1080p_vert.mp4`
- `file_uuid=04cbe73e-e0fb-4467-8d04-65bc7b3ed150`
- `metadata.resolved_live=true`
- `download finished size_bytes=243362268`
- `upload finished location=rclone://115:/test/filester-2026-06-10-small/2026-06-10/<uuid>/<file>.mp4`
- final status `SUCCEEDED`.

## Follow-up

- A small unit test (`tests/test_core_site_plugins.py`) for
  each `is_<site>` matcher would have caught this. Adding
  one for filester (and one for transfer, see #0008 lesson)
  is on the queue for the next cleanup pass.
- The same pattern (single landing domain, growing alias
  set) is likely true for bunkr / cyberdrop / mediafire.
  Audit them next time those sites fail to parse.
