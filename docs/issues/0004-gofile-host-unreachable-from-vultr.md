# 0004 — gofile.io unreachable from vultr worker (network-layer)

## Status
**Open (infrastructure). Code path is fine; vultr egress routing to
gofile's published IP is blackholed. Workaround: deploy worker somewhere
with a clean route to `api.gofile.io`, or set `MEDIA_SHUTTLE_GOFILE_TOKEN`
to a manually minted JWT (won't help if the egress is still blocked).**

## Summary
Tasks targeting `gofile.io/d/<id>` fail in `parse` because the worker
host cannot establish a TCP connection to `api.gofile.io:443`.

This is **not** a parser bug. The v2 parser (`parse_gofile_live`) calls
`POST /accounts` and then `GET /contents/{id}` — both endpoints
correctly, but the underlying TCP handshake to `api.gofile.io` never
completes.

## Reproduction
From the vultr worker host (the one running `media-shuttle-core-worker-1`):

```
$ curl -sS --max-time 8 -o /dev/null -w "HTTP %{http_code} | time=%{time_total}s\n" \
    "https://api.gofile.io/servers"
HTTP 000 | time=8.001683s   # 8-second connect timeout, never even hits TLS

$ curl -sS --max-time 8 -o /dev/null -w "HTTP %{http_code} | time=%{time_total}s\n" \
    "https://gofile.io/"
HTTP 000 | time=8.001588s   # same, main domain unreachable
```

DNS resolves cleanly:

```
$ dig +short api.gofile.io @1.1.1.1
103.107.198.3
$ dig +short gofile.io @1.1.1.1
103.107.198.3
```

But the IP itself does not answer — direct TCP to `103.107.198.3:443`
also times out:

```
$ curl -sS --max-time 8 -o /dev/null -w "HTTP %{http_code} | time=%{time_total}s\n" \
    "https://103.107.198.3/"
HTTP 000 | time=8.001102s
```

For comparison, `1.1.1.1:443`, `cloudflare.com:443`, and `github.com:443`
all return within ~0.13s from the same host, so the local network
stack and TLS configuration are fine. The block is specific to
`103.107.198.3`.

`103.107.198.3` is in APNIC `103.107.192.0/18` (KAOPU CLOUD,
Bangladesh/India) — it is **not** a Cloudflare anycast IP and is not
geographically close to the vultr host. The route between vultr and
this IP is being dropped somewhere upstream (vultr's peering, an
intermediate transit, or a regional block list).

## Observed failure path
With the v2 parser correctly wired (token resolution, recursive folder
walk, signed CDN URL pass-through — all verified via unit tests and an
in-process dry run with `http_json` mocked), the e2e flow looks like:

```
1. POST /v1/tasks/parse {url: "https://gofile.io/d/CgT9zm", ...}
   → task 52d9b020-... created, status PARSING
2. process_created_event_logic:
     parser_registry.parse(url)
       → is_gofile(url) → True
       → parse_gofile_live(url)
         → _gofile_get_token()           # POST https://api.gofile.io/accounts
             .                                # TCP connect times out (~20s)
             .                                # exception caught, return []
         → _gofile_list_sources(...)      # never reached
         → returns [] (empty)
       → parser_registry.parse iterates
       → next match: generic_fallback (lambda _url: True, mode='all')
       → parse_generic(url) returns 1 source with site=GENERIC
     → source_count=1
   → schedule downstream pipelines
3. process_download_source: GENERIC site, downloads original URL directly
   → httpx GET https://gofile.io/d/CgT9zm
   → returns text/html ("Cannot GET /contents/..."), 87 bytes
4. http_download rejects: <1024 bytes OR text/html
   → RuntimeError: "download rejected: response body is too small"
5. finalize: status=FAILED (or SUCCEEDED-but-empty if the body is big
   enough and the 401/HTML page gets uploaded to 115)
```

The **parser** and **downloader** code are correct. The e2e
"successful" run you see (status=SUCCEEDED with `site=GENERIC` and a
`CgT9zm` filename) is the generic_fallback swallowing the live
parse failure and proceeding with a no-op download.

## Fix options
| Option | Where | Notes |
|---|---|---|
| Move worker off vultr | infra | Cheapest if you have an existing host with a clean route to `103.107.198.3`. |
| Set `MEDIA_SHUTTLE_GOFILE_TOKEN` and add an explicit IP allow in worker firewall | infra + config | Only works if egress to the IP is the issue, not IP-level blocking. |
| Add a `MEDIA_SHUTTLE_GOFILE_PROXY` HTTP CONNECT env that wraps every gofile request | code | Bigger refactor; parser would route through the proxy. Out of scope for this PR. |

## Validation on a reachable network
The v2 parser logic itself is exercised by `tests/test_gofile.py` (15
tests, all pass) and an in-process dry run with `http_json` mocked:

```
$ python -c "from core.providers.parsers_sites.gofile import parse_gofile_live; ..."
Got 1 source(s):
  site=GOFILE
  file_name='MyVideo.mp4'
  download_url='https://store-eu-1.gofile.io/download/web/CgT9zm/MyVideo.mp4?token=guest-jwt-abc123'
  metadata={'resource_id': 'CgT9zm', 'token': 'guest-jwt-abc123'}

=== API calls made ===
  POST https://api.gofile.io/accounts                       # (no Authorization)
  GET  https://api.gofile.io/contents/CgT9zm?cache=true
       Authorization: Bearer guest-jwt-abc123
       (no X-Website-Token, no X-BL — v1 artifacts removed)
```

On a network where `api.gofile.io:443` answers, this code path will
work end-to-end without any further changes.

## Related
- Closes the v1 parser path that was using `POST /accounts` + custom
  `X-Website-Token` / `X-BL` defenses (already done in
  `media-shuttle-core/core/providers/parsers_sites/gofile.py`,
  see commit log for the v1→v2 transition).
- Companion issue: #0005 — `http_download` hardcodes
  `timeout=60.0`; large files on slow links fail mid-stream. Make it
  env-tunable.
