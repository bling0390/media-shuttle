# media-shuttle-tg

Telegram adapter module. It only calls `media-shuttle-api` and does not run core pipeline logic directly.

## 1. Install

```bash
cd media-shuttle-tg
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Required env

Create module-local env file:

```bash
cd media-shuttle-tg
cp .env.example .env
```

Load env in current shell:

```bash
set -a
source .env
set +a
```

`MEDIA_SHUTTLE_API_BASE_URL` has a default (`http://localhost:8000`), but it is still provided in `.env.example` for explicit deployment config.

## 3. Run standalone

```bash
cd media-shuttle-tg
python3 -m tg.bot
```

## 4. Command behavior

- `/leech <url>` creates a parse task via API (default target is `RCLONE`).
- `/monitor` reads queue stats from API.
