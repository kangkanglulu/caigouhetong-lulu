# 仓库根目录 Dockerfile —— 专为「阿里云 ACR 个人版自动构建」准备。
#
# 阿里云 ACR 个人版「构建规则」要求 Dockerfile 文件名只能是 `Dockerfile`，
# 不允许带路径前缀；同时构建上下文必须能拷到整个项目代码（COPY . .），
# 因此把 Dockerfile 放到仓库根目录，ACR 控制台填：
#   - 构建上下文目录：/
#   - Dockerfile 文件名：Dockerfile
#
# 本文件内容与 deploy/fc/Dockerfile 保持一致；若需要本机或 CI 构建，仍可执行：
#   docker build -f deploy/fc/Dockerfile -t contract-fc:v1 .
# 或直接：
#   docker build -t contract-fc:v1 .

# 使用 daocloud 提供的 Docker Hub 公共镜像加速（避免 ACR 构建机被 docker.io 限流 429）
FROM docker.m.daocloud.io/library/python:3.12-slim-bookworm

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
# 仓库中 config.py 被 .gitignore 排除，镜像内不存在会导致 import config 失败、进程秒退。
# 云上密钥一律用 FC 环境变量注入；此处用示例文件占位，运行时读 os.environ。
RUN if [ ! -f config.py ]; then cp config.example.py config.py; fi

EXPOSE 9000

# 单 worker：降低 LibreOffice 与飞书写接口并发冲突概率
# - timeout 300s：覆盖 LibreOffice 冷启动 + PDF 转换 + IM 上传 + Bitable 写回的最长链路
# - graceful-timeout 300s：worker 被回收时也给足时间，避免半截事务
# - log-level info：让 [INFO] DEBUG raw body ... 这些日志可以稳定出现在 FC 函数日志
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
