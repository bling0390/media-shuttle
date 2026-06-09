FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./

# Install rclone from the wiserain fork, which adds the 115 backend that is
# not (yet) in upstream rclone. We pin a specific release zip and verify the
# 115 backend is present at build time so a wrong/empty install fails fast.
#  - 2026-06-05: v1.74.3-301 tracks upstream rclone v1.74.3
#
# Note: rclone auto-binds any RCLONE_* env var to a matching flag, so we must
# unexport RCLONE_VERSION/RCLONE_BUILD before running `rclone` itself, or it
# will try to parse "1.74.3" as a --version bool.
ARG RCLONE_VERSION=1.74.3
ARG RCLONE_BUILD=301
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates unzip bash \
    && curl -fsSL -o /tmp/rclone.zip \
       "https://github.com/wiserain/rclone/releases/download/v${RCLONE_VERSION}-${RCLONE_BUILD}/rclone-v${RCLONE_VERSION}-${RCLONE_BUILD}-linux-amd64.zip" \
    && unzip -q /tmp/rclone.zip -d /tmp/rclone-install \
    && install -m 0755 /tmp/rclone-install/rclone-v${RCLONE_VERSION}-${RCLONE_BUILD}-linux-amd64/rclone /usr/local/bin/rclone \
    && rm -rf /tmp/rclone.zip /tmp/rclone-install \
    && env -u RCLONE_VERSION -u RCLONE_BUILD rclone version \
    && env -u RCLONE_VERSION -u RCLONE_BUILD rclone help backend 115 >/dev/null 2>&1 || (echo "FATAL: 115 backend not in rclone build" && exit 1) \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt
COPY core ./core
CMD ["python", "-c", "from core.queue.worker_process import run_forever; raise SystemExit(run_forever())"]
