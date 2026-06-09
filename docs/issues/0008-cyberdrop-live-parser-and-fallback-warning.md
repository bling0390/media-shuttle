# 0008 — CYBERDROP live parser + generic_fallback safety log

## Status
**Resolved (2026-06-09 17:49 UTC).** Both gaps that #0007 and #0005
flagged are now closed:

1. `parse_cyberdrop_live` is implemented and registered. The single
   `/f/<id>` (or `/e/<id>`, or `/api/file/d/<id>`) URL pattern goes
   through `https://api.cyberdrop.cr/api/file/auth/<id>` to upgrade
   the page URL to a signed CDN URL. `parse_cyberdrop_album_live`
   was also upgraded to call the same auth API for every child file
   on an album page instead of returning the bare page URLs.
2. `ParserRegistry.parse` now logs a `WARNING` whenever the
   `generic_fallback` provider ends up returning a source because
   every site-specific provider above it returned `[]`. The warning
   names the URL and the providers that were tried first, so a
   silent upload of a 76-byte NXDOMAIN HTML body or a 500-byte error
   page is now loud.

## What was wrong
The user asked to verify the CYBERDROP logic with
`https://cyberdrop.me/f/2GChmlMprRdizu`. Two things were broken:

1. **No live parser.** `parsers_builtin.py` had
   `ParseProvider("cyberdrop_album_live", ...)` for `/a/<id>` but
   **no** `cyberdrop_live` for `/f/<id>`. The `/f/<id>` URL fell
   straight through to `generic_fallback`, which returned a
   `ParsedSource(site=GENERIC, download_url=page URL)` and the
   worker dutifully uploaded whatever the upstream returned (a
   55-byte NXDOMAIN body in this case, 0.05s "download").
2. **Album parser was half-right.** Even where the album live
   parser *did* exist, it returned `download_url` = the page URL
   for each child (`/f/<id>`) instead of a signed CDN URL. The
   downloader would then have to follow the page → click handler
   → CDN URL chain for every file, which it doesn't.
3. **Generic fallback ate failures silently.** Both
   `parse_cyberdrop_live` (missing) and the half-working album
   parser (sub-URLs that need a second hop) led to the same
   e2e-with-fallback behaviour, and that behaviour returned a
   1-source SUCCEEDED task with the error body as the artifact.
   Two real task runs (`50291f12` and `4884a7e5`) had `size_bytes=76`
   and `size_bytes=55` respectively.

## What changed
| File | Change |
|---|---|
| `media-shuttle-core/core/providers/parsers_sites/cyberdrop.py` | rewritten: helper `_cyberdrop_file_id` for `/f/`, `/e/`, `/api/file/d/`; `_cyberdrop_request_signed_url` and `_cyberdrop_request_file_info` wrap `http_json` against `https://api.cyberdrop.cr/api/file/auth/<id>` and `/file/info/<id>`; new `parse_cyberdrop_live` for single files; `parse_cyberdrop_album_live` now calls the auth API for every child and only falls back to the page URL if the auth call fails. `is_cyberdrop` still uses the `"cyberdrop" in host(url)` substring so `.me`, `.cr`, `.to`, `.cc`, `.su`, and subdomains all match. |
| `media-shuttle-core/core/providers/parsers_sites/__init__.py` | re-exports `parse_cyberdrop_live` |
| `media-shuttle-core/core/providers/parsers_builtin.py` | registers `ParseProvider("cyberdrop_live", "live", is_cyberdrop, parse_cyberdrop_live)` |
| `media-shuttle-core/core/plugins/parsers.py` | `ParserRegistry.parse` now logs `WARNING parser_registry.parse fell through to generic_fallback url=… tried_empty=[…]` whenever `generic_fallback` produces a result after one or more specific providers returned `[]`. Quiet when generic_fallback is the only match or when the first matching provider returns a real result. |
| `media-shuttle-core/tests/test_cyberdrop.py` (new) | 19 unit tests: matcher tests (4), single-file resolver (5), API helpers (5), album child resolution + fallback (5) |
| `media-shuttle-core/tests/test_parser_fallback_warning.py` (new) | 4 unit tests: warning fires on fall-through, no warning on real success, no warning when no provider matches, no warning when generic_fallback is the only provider |

## Behaviour matrix

| URL | Before | After |
|---|---|---|
| `https://cyberdrop.me/f/<existing-id>` | falls through to `generic_fallback`, 55-byte NXDOMAIN body uploaded, no log | `parse_cyberdrop_live` calls `/api/file/auth/<id>` + `/api/file/info/<id>`, returns a `ParsedSource(site=CYBERDROP, download_url=<signed CDN URL>, file_name=<info.name>)` |
| `https://cyberdrop.me/f/<missing-id>` | falls through silently, NXDOMAIN uploaded, no log | `parse_cyberdrop_live` returns `[]` (auth returns 404/500), then `generic_fallback` is hit. **`WARNING parser_registry.parse fell through to generic_fallback url=… tried_empty=['cyberdrop_live']`** |
| `https://cyberdrop.cr/f/<existing-id>` | same as `.me` | same — `is_cyberdrop` is host-substring based |
| `https://cyberdrop.cr/a/<album-id>` | each child had `download_url = page URL` (needs second hop) | each child now has `download_url = signed CDN URL` (one hop). Falls back to the page URL with `metadata.resolved_live=False` if the auth call fails. |

## Verification

### Unit tests
```
$ python -m unittest discover tests
........................................................
Ran 56 tests in 0.044s
OK
```
(33 pre-existing core + 19 cyberdrop + 4 fallback-warning.)

### End-to-end behaviour change
Before (#0007 task `50291f12`):
```
status=SUCCEEDED, site=GENERIC, size_bytes=76, no warning logged
```

After this commit (case I task `d34175cc`):
```
status=SUCCEEDED, site=GENERIC, size_bytes=?  # NXDOMAIN body, same silent path
[2026-06-09 17:49:41,095: WARNING/MainProcess] parser_registry.parse fell through to generic_fallback url='https://cyberdrop.me/f/2GChmlMprRdizu' tried_empty=['cyberdrop_live']
[2026-06-09 17:49:40,727: INFO/MainProcess] HTTP Request: GET https://api.cyberdrop.cr/api/file/info/2GChmlMprRdizu "HTTP/1.1 404 Not Found"
[2026-06-09 17:49:41,094: INFO/MainProcess] HTTP Request: GET https://api.cyberdrop.cr/api/file/auth/2GChmlMprRdizu "HTTP/1.1 500 Internal Server Error"
```

So now you can tell, from the worker log, that the e2e
"SUCCEEDED" is bogus and which providers tried first. The
file-id in this URL is fake (`2GChmlMprRdizu` returns 404 /
"Failed to generate signed URL" from the auth API), so
`parse_cyberdrop_live` is correctly returning `[]` and the
fallback takes over. A real Cyberdrop file id would have
returned a signed CDN URL and the upload would have used it
directly.

## What I did NOT do
- **DDG bypass for the album page.** The `cyberdrop.cr/a/<id>`
  page itself is behind DDG, but `http_text` with a random
  User-Agent gets through (verified in earlier probes — the
  page returns 30KB+ HTML). If this stops working, the fix is
  the same shape as the `coomer.st` hint: first request with
  `Accept: text/css`, capture the `__ddg1_` cookie, retry.
  Out of scope for this commit; a future hardening pass.
- **A real e2e with an existing file id.** All the `cyberdrop.cr/f/...`
  ids I could find via web search (e.g. `YZC6rRbiCO1zn`,
  `Ch2OOmlvxEnbD`) return 404 from the auth API — Cyberdrop
  purges files fast. Without a real working URL, the e2e proof
  is "the live parser actually calls the auth API and the
  fallback warning fires when it should".
- **Telling the downloader to fail loudly when the artifact is
  obviously not media** (e.g. <1KB body or HTML content-type).
  The warning log is the first half of the fix; the second
  half (turning it into a `reason` on the artifact and a
  `FAILED` final status) is out of scope.
- **Cleanup of `115:/2026-06-09/cyberdrop.me/` and
  `115:/2026-06-09/coomer.su/`.** `rclone delete` returns
  success on those paths but `rclone lsf` still shows them —
  same pre-existing rclone-115 delete limitation that already
  affects `115:/media-shuttle-test:bunkr.si/`. Non-blocking.

## Decision still open
- COOMER (`coomer.su` / `coomer.st` / `kemono.*`) is still on
  the to-do list (#0007). The fallback warning now makes
  COOMER's failure mode visible too, so the symptom is less
  confusing; the actual live parser is the same shape of work
  as what was done for cyberdrop here.
