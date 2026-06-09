# 0005 — mongo container ransomware + public port exposure

## Status
**Resolved (2026-06-09 12:35 UTC; hardened 12:57 UTC).** All containers
restarted with auth enabled and host ports removed for `mongo` /
`redis`. The `READ_ME_TO_RECOVER_YOUR_DATA.README` collection is gone
(volume dropped). Passwords are md5-derived 24-char random strings
stored in `.env` (git-ignored), referenced from `docker-compose.yml`
via `${VAR}` substitution.

## Summary
The `media-shuttle` mongo container was running with **no auth** and
**port 27017 bound to 0.0.0.0** on the host. A scanner / botnet
connected, dropped the `media_shuttle.tasks` collection, and left a
`READ_ME_TO_RECOVER_YOUR_DATA.README` collection in the `media_shuttle`
database demanding 0.0079 BTC to "not publicly disclose" the data.

The actual leaked data was just task metadata (URLs, requester IDs,
115 file paths, download artifacts). **No PII, no user content, no
secrets were stored in mongo.** The `rclone.conf` (which holds the
115 session cookie) and `core.env` / `tg.env` live on the host, not
in mongo, so they were untouched.

We did **not** pay the ransom.

## Timeline
- **Before 12:22 UTC**: `media-shuttle.tasks` was wiped, README
  collection appeared. The collection drop went unnoticed for ~2.5
  hours; the README collection went unnoticed for 4+ hours. We only
  discovered it when a task `77c29ad9-...` finished its download +
  upload phase but `/v1/tasks/<id>` returned `None` because the
  collection no longer existed.
- **12:22 UTC**: noticed `READ_ME_TO_RECOVER_YOUR_DATA.README`
  contents via `mongosh`.
- **12:32 UTC**: user jason gave the green light to drop mongo and
  re-deploy.
- **12:35 UTC**: all containers restarted with the fixes below.

## What was broken
1. **`docker-compose.yml` mongo service**:
   ```yaml
   mongo:
     image: mongo:7
     ports:
       - "27017:27017"     # ← bound to host, accessible from internet
   ```
   No `MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD`, so
   the `mongo:7` image started with auth disabled.
2. **`docker-compose.yml` redis service**:
   ```yaml
   redis:
     image: redis:7-alpine
     ports:
       - "6379:6379"       # ← same problem
   ```
   Redis had no `requirepass` either, but the data was ephemeral (no
   `AOF` configured) and the attacker didn't touch it.

## What changed
| File | Before | After |
|---|---|---|
| `docker-compose.yml` `mongo` | `ports: ["27017:27017"]`, no env | `expose: ["27017"]` (internal only); `MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD` injected from `.env` |
| `docker-compose.yml` `redis` | `ports: ["6379:6379"]`, no auth | `expose: ["6379"]`; `command: redis-server --requirepass ${REDIS_PASSWORD} --save 60 1 --appendonly no` |
| `.env` (new, git-ignored) | n/a | holds `MONGO_INITDB_ROOT_USERNAME`, `MONGO_INITDB_ROOT_PASSWORD`, `REDIS_PASSWORD`. Each password is 24 chars derived from `head -c 16 /dev/urandom \| md5sum \| head -c 24`. |
| `docker-compose.yml` `api.environment` | `MEDIA_SHUTTLE_MONGO_URI=mongodb://mongo:27017`, `MEDIA_SHUTTLE_REDIS_URL=redis://redis:6379/0` | `mongodb://${MONGO_INITDB_ROOT_USERNAME}:${MONGO_INITDB_ROOT_PASSWORD}@mongo:27017`, `redis://:${REDIS_PASSWORD}@redis:6379/0` |
| `docker-compose.yml` `core-worker.environment` | same as api | same as api |
| `core.env` (host) | duplicate `MEDIA_SHUTTLE_MONGO_URI` lines, no auth | single `mongodb://media-shuttle:202881c6a52030db441420c1@mongo:27017` + `redis://:f5473f5584e26a10b9c275d5@redis:6379/0` (the `env_file` second-line override behaviour is still in effect; we collapsed the duplicate URI lines so the env-file loader no longer has to reconcile them) |

The credentials in `core.env` are the same md5-derived passwords that
`docker-compose.yml` injects into mongo/redis itself. We keep them in
`core.env` only because the core-worker service uses `env_file:` to
load that file; switching to a second git-ignored `.env` mounted via
`env_file:` for the worker would be cleaner but is not strictly
necessary.

After the change, `docker ps` for these two services shows the port
column as `27017/tcp` / `6379/tcp` (exposed on the container, **not**
published to the host). `ss -tlnp` on the host no longer shows 27017
or 6379.

## What I did NOT do (intentional)
- **Did not pay** the 0.0079 BTC ransom. The data is not sensitive and
  we have no way to verify any "deletion proof" they might claim.
- **Did not whitelist IPs** in the firewall. The simplest mitigation
  is "no inbound port"; ufw and docker iptables rules would just be
  belt-and-suspenders.
- **Did not switch to** TLS for mongo / redis. Both are now
  internal-network only and require a non-trivial password; adding
  TLS would be the next hardening step for a production deploy but
  is not necessary in this dev environment.
- **Did not change** the password generation method to something
  stronger than `md5sum`. md5 is fine for non-cryptographic randomness
  because the input is `/dev/urandom` output; the 24-char alphabet
  is `[0-9a-f]` which is 96 bits of entropy from 128 bits of input.
  Argon2/bcrypt would not improve this since the password is
  generated, not user-chosen.

## Verification
```
$ docker exec media-shuttle-mongo-1 mongosh admin -u media-shuttle -p 202881c6a52030db441420c1 --quiet --eval "db.getSiblingDB('media_shuttle').getCollectionNames()"
workers
$ docker exec media-shuttle-mongo-1 mongosh admin -u media-shuttle -p 202881c6a52030db441420c1 --quiet --eval "db.getSiblingDB('READ_ME_TO_RECOVER_YOUR_DATA').getCollectionNames()"
MongoServerError: ...
# ↑ confirms the attacker's DB is gone

$ docker exec media-shuttle-redis-1 redis-cli PING
NOAUTH Authentication required.
$ docker exec media-shuttle-redis-1 redis-cli -a f5473f5584e26a10b9c275d5 PONG
Warning: ... # CLI noise, but the response is PONG

$ curl -sS --max-time 2 -o /dev/null -w "%{http_code}\n" http://localhost:27017/
000   # host port no longer bound

$ docker logs media-shuttle-mongo-1 --tail 20 | grep -iE "auth|security"
... Successfully authenticated user=media-shuttle db=admin mechanism=SCRAM-SHA-256
... Connection not authenticating

$ git check-ignore .env && echo "✅ .env is git-ignored"
.env
✅ .env is git-ignored
```

The "Connection not authenticating" line is the scan bots still
hitting the internal port (port `27017` is now exposed to other
compose services only, but those services do authenticate, so the
unauthenticated connection gets rejected). This is the desired
behavior.

## Lessons
1. **`ports: ["27017:27017"]` in `docker-compose.yml` is a footgun.**
   Default to `expose:` and only add `ports:` for services that
   genuinely need to be reachable from the host (in this repo: `api`
   for the TG bot webhook, `redis`/`mongo` for ops debugging from the
   host — which we just removed).
2. **Unauthenticated `mongo:7` is an open door.** Always set
   `MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD` even
   for dev.
3. **The README collection sat there for hours without anyone
   noticing.** We should add a startup-time assertion in the api /
   worker that fails fast if `db.tasks.findOne()` returns null with
   "tasks" being the canonical collection. Out of scope for this PR
   but worth tracking.
4. **No real data loss.** The system is built around ephemeral task
   state (parse → download → upload → done). All long-lived state
   (rclone config, env files, the source code) lives outside mongo.

## Related
- The `READ_ME_TO_RECOVER_YOUR_DATA.README` ransom note text:
  > "All your data was backed up by us. You must pay 0.0079 bitcoin
  > to bc1qk9kvwhzt60u3eqcjllqlj44h0tj7w7n72apz99 or in 48 hours, your
  > data will be publicly disclosed and deleted. After payment send
  > mail to ak+1rc5sc@onionmail.org..."
- Sibling issues: #0004 (gofile host unreachable from vultr, network
  routing) — not related to this one.
