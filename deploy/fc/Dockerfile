# 阿里云函数计算：自定义镜像（Python + Flask Webhook + LibreOffice PDF）
# 构建上下文必须是仓库根目录： docker build -f deploy/fc/Dockerfile -t your-tag .

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PDF_ENGINE=libreoffice \
    DELIVERY_FORMAT=pdf \
    LISTEN_PORT=9000

# LibreOffice + 中文字体（减小与 Word 的视觉差异）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        fonts-noto-cjk \
        fontconfig \
        ca-certificates \
    && fc-cache -f -v \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码、模板、静态资源（构建上下文为项目根目录）
COPY . .
RUN if [ ! -f config.py ]; then cp config.example.py config.py; fi

EXPOSE 9000

# 单 worker：降低 LibreOffice 与飞书写接口并发冲突概率
CMD exec gunicorn \
    --bind "0.0.0.0:${LISTEN_PORT}" \
    --workers 1 \
    --threads 4 \
    --timeout 300 \
    --graceful-timeout 300 \
    --keep-alive 75 \
    --log-level info \
    --access-logfile - \
    --error-logfile - \
    webhook_app:app
