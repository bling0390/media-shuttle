FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY core ./core
CMD ["python", "-c", "from core.runtime import run_forever; run_forever()"]
