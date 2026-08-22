FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HME_DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=5050 \
    AUTO_START_SCHEDULER=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data/results /data/logs

VOLUME ["/data"]
EXPOSE 5050

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5050/', timeout=3)"

CMD ["python", "-u", "web_ui.py", "--scheduler", "--no-sync"]
