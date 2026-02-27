FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./

ARG MEDIA_SHUTTLE_RCLONE_INSTALL_SCRIPT_URL=https://raw.githubusercontent.com/wiserain/rclone/mod/install.sh
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates bash \
    && curl -fsSL "${MEDIA_SHUTTLE_RCLONE_INSTALL_SCRIPT_URL}" | bash \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt
COPY core ./core
CMD ["python", "-c", "from core.queue.worker_process import run_forever; raise SystemExit(run_forever())"]
