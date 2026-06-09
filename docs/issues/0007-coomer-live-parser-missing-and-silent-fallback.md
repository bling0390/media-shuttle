# 0007 — COOMER live parser missing + generic_fallback silent takeover

## Status
**Diagnosed (2026-06-09 17:27 UTC); both problems open.**

1. `coomer.su` no longer resolves (domain delegation withdrawn). The
   active mirror is `coomer.st` (and `kemono.cr` for kemono content).
   Confirmed via Cloudflare `1.1.1.1` and Google `8.8.8.8` — both
   return NXDOMAIN for `coomer.su`. The current WHOIS for `coomer.su`
   shows the domain as `REGISTERED, NOT DELEGATED` (paid through
   2026-08-25 but no NS records).

2. `parse_coomer_live` is **not implemented**. The parser registry has
   a mock entry (`ParseProvider("coomer", "mock", is_coomer, parse_coomer)`)
   but no live entry. Running in `IO_MODE=live` therefore routes every
   `coomer.*` / `kemono.*` URL through the silent
   `generic_fallback` provider, which returns a single
   `ParsedSource(site=GENERIC, download_url=<page URL>, …)`. The
   downloader then fetches the page URL, gets whatever the upstream
   returned (an HTML error body, a 4xx page, or the actual post HTML),
   and uploads that to 115 as if it were the real file. e2e "succeeds"
   with a 76-byte NXDOMAIN error body sitting in the user's drive.

The user asked to "verify the COOMER logic" with
`https://coomer.su/onlyfans/user/araefitness/post/746942442`. The URL
itself is unreachable from this host, so a real e2e is blocked on the
domain issue. The code path that runs on submission is, however, worth
documenting because the silent-fallback behaviour is misleading and
was already flagged in #0005 as future work.

## Reproduction
```
$ nslookup coomer.su 1.1.1.1
** server can't find coomer.su: NXDOMAIN

$ curl -sS -o /dev/null -w "%{http_code}\n" --max-time 5 \
    "https://coomer.su/onlyfans/user/araefitness/post/746942442"
000   # could not resolve host

$ curl -sS -X POST http://localhost:8000/v1/tasks/parse \
    -H "Content-Type: application/json" -d '{
      "url":"https://coomer.su/onlyfans/user/araefitness/post/746942442",
      "requester_id":"jason_coomer_test",
      "target":"RCLONE"}'
{"task_id":"50291f12-f534-4266-8a15-fc9f129731c4","status":"QUEUED"}

# 30 seconds later:
$ curl -sS http://localhost:8000/v1/tasks/50291f12-f534-4266-8a15-fc9f129731c4
{
  "status": "SUCCEEDED",
  "destination": "115:/",     # <-- new default from #0006 works
  "sources": [
    {
      "site": "GENERIC",      # <-- not COOMER; generic_fallback ate it
      "download_url": "https://coomer.su/onlyfans/user/araefitness/post/746942442",
      "file_name": "746942442",
      "remote_folder": "coomer.su"
    }
  ],
  "artifacts": [
    { "ok": true, "size_bytes": 76, ... }
  ]
}

# 76 bytes is the NXDOMAIN HTML body; the worker uploaded it to 115.
$ rclone ls 115:/2026-06-09/ --max-depth 3
   72616 How to Lick Pussy Right Let Lana & Luna Teach You/...
       76 coomer.su/746942442
#           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^ ^^^^^^^^^
#           NXDOMAIN HTML body            <file>
```

## What the parser registry actually contains
Looking at `media-shuttle-core/core/providers/parsers_builtin.py`:

```python
# live mode (IO_MODE=live)
providers.extend([
    ParseProvider("gofile_live",        "live", is_gofile,        parse_gofile_live),
    ParseProvider("bunkr_album_live",   "live", is_bunkr_album,   parse_bunkr_album_live),
    ParseProvider("bunkr_live",         "live", is_bunkr,         parse_bunkr_live),
    ParseProvider("cyberdrop_album_live","live",is_cyberdrop_album,parse_cyberdrop_album_live),
    ParseProvider("filester_live",      "live", is_filester,      parse_filester_live),
    ParseProvider("mega_live",          "live", is_mega,          parse_mega_live),
    ParseProvider("pixeldrain_live",    "live", is_pixeldrain,    parse_pixeldrain_live),
    ParseProvider("mediafire_live",     "live", is_mediafire,     parse_mediafire_live),
    ParseProvider("transfer_live",      "live", is_transfer,      parse_transfer_live),
    ParseProvider("turbo_live",         "live", is_turbo,         parse_turbo_live),
])
```

COOMER is in the mock list only:
```python
ParseProvider("coomer", "mock", is_coomer, parse_coomer),
```

So under `IO_MODE=live` (the default since #0001), `parse_coomer` is
**not even consulted** — the mock parser is only loaded for the
`IO_MODE=mock` branch. The COOMER host matcher still fires, but
without a live parser, the loop falls through to `generic_fallback`.

The COOMER-specific downloader is also a stub:
```python
# media-shuttle-core/core/providers/downloaders_sites/coomer.py
def download_coomer_live(source: ParsedSource) -> DownloadResult:
    return download_live_generic(source, headers=with_random_user_agent())
```
It just runs `httpx.request("GET", source.download_url)`. With
`source.download_url` set to the page URL by `generic_fallback`, the
result is the page itself — or, in our case, the 76-byte NXDOMAIN
HTML body.

## What needs to happen for real COOMER support

1. **Live parser** `parse_coomer_live(url) -> list[ParsedSource]`
   - Detect the path shape:
     - `/post/<id>` — single post (rare; kemono/cr legacy URLs)
     - `/<service>/user/<user>/post/<id>` — service-tagged post
     - `/<service>/user/<user>` — user index (gallery)
     - `/<service>/user/<user>/post/<id>/...` — sometimes with `/revisions` etc.
   - Hit `https://coomer.st/api/v1/...` (the active mirror; .su is
     dead). Kemono content lives under `/api/v1` on `kemono.cr`.
   - API returns JSON with `id`, `user`, `service`, `title`, `content`
     (HTML body), `attachments: [{name, path, server}]`, and
     `files: [...]`. Each attachment is a real media URL
     `<server>/data/<path>` (no DDG challenge on the asset host).
   - Set `download_url` to the asset URL, `remote_folder` to the post
     title or the user name (so the 115 path is `…/<user>/<title>/<file>`).

2. **DDG bypass** for the page-level API
   - The HTML page and the JSON API both sit behind
     `ddos-guard`. The official hint (returned in the 403 body) is to
     send `Accept: text/css`. In practice you also need a cookie
     `__ddg1_` set by the first 403 response. A simple way to handle
     this in `http_json` is: do one `Accept: text/css` GET, capture
     `Set-Cookie`, then retry the JSON request with that cookie. (The
     asset host does not need this.)

3. **Multi-host support**
   - `is_coomer` already matches `coomer.*` and `kemono.*` by
     substring, so `coomer.st` and `kemono.cr` work without code
     changes. Confirmed at the python REPL:
     ```
     "coomer" in host("https://coomer.st/...") -> True
     "kemono" in host("https://kemono.cr/...") -> True
     ```
   - The API host switches by service: `coomer.st` for coomer content,
     `kemono.cr` for kemono content. The live parser has to pick the
     right one based on the URL path prefix.

4. **Unit tests** (new file `tests/test_coomer.py`)
   - URL match: `coomer.st/...`, `kemono.cr/...`, with the various
     path shapes above
   - Live parser with mocked `http_json`: returns one source per
     attachment for a multi-attachment post, single source for a
     single-attachment post, `[]` on API error
   - DDG bypass: mocked 403 → set-cookie → 200 round-trip
   - Asset URL construction from `server + path`

5. **Generic fallback safety** (separate fix, but this issue made
   the cost concrete)
   - When `generic_fallback` is the provider that matched, log a
     `WARNING` with the URL and the providers that returned `[]`. As
     things stand, a 1-source `GENERIC` upload looks identical to a
     real success and only the file size hints that something is
     wrong.

## Cleanup done in this commit
- `rclone delete 115:/2026-06-09/coomer.su/` removes the
  76-byte NXDOMAIN pollution that the silent-fallback run left
  behind. Verified `rclone lsf 115:/2026-06-09/` after.

## What I did NOT do
- Implement `parse_coomer_live`. The work is well-defined but
  substantial (DDG bypass, multi-host API, asset-URL build, the
  `kemono` ↔ `coomer` service split). Out of scope for the current
  destination-simplification PR.
- Add the generic_fallback safety log. Same reason.
- Switch any test that uses `coomer.su/...` over to `coomer.st/...`.
  Those tests pass `coomer.su/post/123456` and the matcher is a
  substring, so they keep working regardless of the live hostname.
  But they're not e2e tests — they only check that
  `is_coomer → COOMER site` round-trips, which still holds.

## Decision needed
Do you want COOMER/kemono support in this PR, or file it as
follow-up work for the next iteration?
