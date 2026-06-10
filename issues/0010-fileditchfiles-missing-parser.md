# #0010 — FILEDITCHFILES has no parser; falls through to GENERIC and ships a 109-byte HTML page marked SUCCEEDED

**Date:** 2026-06-10
**Status:** Fixed (parser + downloader + tests + e2e verified for the link-extraction path)
**Branch:** feat/openclaw
**Reporter:** jason (user verification with `https://fileditchfiles.me/alpha6/eb901716274064c926c3/brammed.26.06.03.scarlet.chase.4k.mp4`)

## Symptom

`POST /v1/tasks/parse` for a `fileditchfiles.me` link
silently fell through to `direct_file` (because the path
ends with `.mp4` and `is_direct_file_url` is true) and
uploaded the landing page's adblocker-bait HTML to 115 as
if it were the real file.

Task `2984a339-428b-468a-99e8-e3db2b468e5d`:

- `site: "GENERIC"` (no parser matched)
- `size_bytes: 109` (the adblocker-bait HTML body, not a video)
- `file_name: "brammed.26.06.03.scarlet.chase.4k.mp4"` (right name, wrong bytes)
- `status: SUCCEEDED` ❌
- artifact location: `rclone://115:/test/fileditchfiles-2026-06-10/2026-06-10/fileditchfiles.me/<file>.mp4`

This is the same shape as #0007 (COOMER no live parser) and
#0009 (filester.sh matcher miss), but worse: a new site with
no matcher at all. The existing WARNING in `ParserRegistry.parse`
only fires when **some** site-specific provider ran first and
returned `[]`; it is silent when the URL matches no site at
all and the URL ends in a recognised media extension (so
`is_direct_file_url` short-circuits before `generic_fallback`).

## Investigation

Manual fetch with a real-browser User-Agent:

```
curl -A "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) ..." \
  https://fileditchfiles.me/alpha6/eb901716274064c926c3/brammed.26.06.03.scarlet.chase.4k.mp4
```

returns a static "File Viewer" page whose `<body>` contains:

```html
<video controls>
  <source src="https://donotsharethesetemplinksyouidiot.st/alpha6/.../file.mp4?md5=...&expires=1781159792" type="video/mp4">
</video>
<a href="https://donotsharethesetemplinksyouidiot.st/alpha6/.../file.mp4?md5=...&expires=1781159792" class="btn btn-main" id="..." download>⬇ Download</a>
```

`HEAD` on the CDN URL with the signed query string returns
`200`, `Content-Type: video/mp4`, `Content-Disposition: attachment`,
`Content-Length: 3036202299` (3.04 GB). No auth, no
cookie, no JS challenge — the page is fully parseable and
the CDN URL is the whole integration.

## Fix

New parser + downloader + tests + enum + queue registration:

- `core/providers/parsers_sites/fileditchfiles.py`:
  - `is_fileditchfiles()` accepts `fileditchfiles.me` and any
    `*.fileditchfiles.me` subdomain.
  - `parse_fileditchfiles_live()` fetches the landing page
    with a real-browser User-Agent, pulls the signed CDN URL
    from the `<source src=...>` tag, and promotes it into
    `download_url` (with `&amp;` decoded to `&`). The real
    filename is pulled from the CDN URL's last path segment.
    `metadata.resolved_live = True` once the CDN URL is
    extracted.
  - `_looks_like_fileditchfiles_cdn_url()` short-circuits the
    page fetch if the user pastes the CDN URL directly
    (`donotsharethesetemplinksyouidiot.st`).
- `core/providers/downloaders_sites/fileditchfiles.py`:
  thin wrapper around `download_live_generic` that sets
  `User-Agent: Mozilla/5.0 ...` and `Referer: <landing URL>`
  (the CDN gates anonymous traffic by Referer).
- `core/enums.py`: `SourceSite.FILEDITCHFILES = "FILEDITCHFILES"`,
  plus the same suffix added to `default_site_queue_suffixes()`.
- `core/providers/parsers_builtin.py`: registered
  `fileditchfiles_live` (live) and `fileditchfiles` (mock).
- `core/providers/downloaders_builtin.py`: registered the
  same pair in the downloader registry.
- `core/providers/parsers_sites/__init__.py` and
  `downloaders_sites/__init__.py`: re-export the new symbols.
- `core.env` + `media-shuttle-core/docker-compose.yml`
  fallback: `MEDIA_SHUTTLE_SITE_QUEUE_SUFFIXES` extended to
  16 suffixes with `FILEDITCHFILES` in the right slot. (Lesson
  from #0008: the env var is a *second* source of truth that
  must be kept in lock-step with `default_site_queue_suffixes()`.)
- `tests/test_fileditchfiles.py`: 5 unit tests covering the
  matcher (positive `.me` / subdomain, negative `fileditch.com` /
  `filester.me` / empty) and the live parser (CDN URL promoted,
  filename pulled, `&amp;` decoded, `resolved_live=True`).
  Local pytest run: 5/5 pass. Full suite: 66/66 pass.

## Verification (post-fix)

Worker boot log after rebuild shows 16 download queues
including `@FILEDITCHFILES`.

Task `5c27ce00-ac1a-4420-a28e-485bdd6a475e` (the same 3.04 GB
URL) trace:

- `parse task received site=FILEDITCHFILES
  file_name=brammed.26.06.03.scarlet.chase.4k.mp4`
- `download started site=FILEDITCHFILES
  file_name=brammed.26.06.03.scarlet.chase.4k.mp4`
- `GET https://fileditchfiles.me/.../file.mp4 "200 OK"`
- `GET https://donotsharethesetemplinksyouidiot.st/.../file.mp4?md5=...&expires=... "200 OK"`
- `metadata.resolved_live = true`,
  `file_token = eb901716274064c926c3`.

The task was then left in `DOWNLOADING` because the file is
3.04 GB and `downloaders_sites/common.py:http_download` does
`data = response.content` (loads the whole body into RAM).
Worker memory climbed to 2.97 GiB / 3.81 GiB before the task
was abandoned; that is a separate bug — see #0010a below.

The point of the verification: **every step of the
fileditchfiles pipeline ran with the right site tag, the right
file name, the right CDN URL, and the right referer**. No
silent fallback, no 109-byte HTML upload. The module logic is
correct.

## #0010a — follow-up: `http_download` loads full body into RAM

`core/providers/downloaders_sites/common.py:http_download` does
`data = response.content` and then `path.write_bytes(data)`.
For a 3 GB CDN file, this allocates 3 GB of heap (peak observed:
2.97 GiB on a 3.81 GiB container) and will OOM on bigger
files / smaller hosts.

A streamed `response.stream()` chunked copy would be a
localized fix. Track in a separate issue — it is not a
fileditchfiles-specific defect.
