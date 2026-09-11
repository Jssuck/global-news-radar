# Global News Radar — 单进程 MVP 镜像（SQLite 内嵌，无外置服务）
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    GNR_DB_PATH=/data/gnr.db

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY sources ./sources
COPY config/proxies.example.yaml ./config/proxies.example.yaml

VOLUME /data
EXPOSE 8000

# 首次启动自动建库 + 装载种子源；数据持久化在 /data 卷
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
